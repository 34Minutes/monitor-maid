"""Thin Ollama HTTP client. The chat payload is the same shape other OpenAI-compatible backends use."""

from __future__ import annotations

import httpx

from monitor_maid.models import LLMReply


class OllamaError(Exception):
    def __init__(self, message: str, kind: str):
        super().__init__(message)
        self.kind = kind  # offline, timeout, model_missing, http, bad_response


class OllamaClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def _client(self, timeout: float) -> httpx.Client:
        return httpx.Client(
            timeout=httpx.Timeout(connect=5.0, read=timeout, write=30.0, pool=5.0)
        )

    def health(self) -> str:
        """Return 'online' or raise OllamaError."""
        try:
            with self._client(10) as client:
                response = client.get(f"{self.base_url}/api/tags")
        except httpx.TimeoutException as exc:
            raise OllamaError(f"Timed out contacting Ollama at {self.base_url}.", "timeout") from exc
        except httpx.HTTPError as exc:
            raise OllamaError(
                f"Ollama is not reachable at {self.base_url}. Start it with `ollama serve`.",
                "offline",
            ) from exc
        if response.status_code >= 400:
            raise OllamaError(
                f"Ollama at {self.base_url} returned HTTP {response.status_code}.",
                "http",
            )
        return "online"

    def list_models(self) -> list[str]:
        try:
            with self._client(15) as client:
                response = client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                payload = response.json()
        except httpx.TimeoutException as exc:
            raise OllamaError(f"Timed out contacting Ollama at {self.base_url}.", "timeout") from exc
        except httpx.HTTPError as exc:
            raise OllamaError(
                f"Ollama is not reachable at {self.base_url}.",
                "offline",
            ) from exc
        names = []
        seen: set[str] = set()
        for item in payload.get("models") or []:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("model")
            if isinstance(name, str):
                name = name.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            names.append(name)
        return names

    def chat(self, model: str, messages: list[dict[str, str]], timeout: float) -> LLMReply:
        body = {"model": model, "messages": messages, "stream": False}
        try:
            with self._client(timeout) as client:
                response = client.post(f"{self.base_url}/api/chat", json=body)
        except httpx.TimeoutException as exc:
            raise OllamaError(
                f"Ollama did not answer within {int(timeout)}s. The model may still be loading.",
                "timeout",
            ) from exc
        except httpx.HTTPError as exc:
            raise OllamaError(
                f"Ollama is not reachable at {self.base_url}. Start it with `ollama serve`.",
                "offline",
            ) from exc
        if response.status_code == 404:
            raise OllamaError(
                f"Model {model!r} is not installed. "
                "Open Settings, click Refresh models, choose an installed model, and save.",
                "model_missing",
            )
        if response.status_code >= 400:
            detail = _error_detail(response) or f"HTTP {response.status_code}"
            kind = "model_missing" if "not found" in detail.lower() else "http"
            raise OllamaError(detail, kind)
        try:
            payload = response.json()
        except ValueError as exc:
            raise OllamaError("Ollama returned a response that was not JSON.", "bad_response") from exc
        message = payload.get("message") if isinstance(payload, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise OllamaError("Ollama returned a chat payload without message content.", "bad_response")
        return LLMReply(text=content)


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        text = response.text.strip()
        return text[:400]
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, str):
            return error
        if isinstance(error, dict):
            return str(error.get("message") or error)
    return ""
