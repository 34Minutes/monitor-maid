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

    def running_models(self) -> list[str]:
        """Models currently loaded in memory. This is `ollama ps`."""
        try:
            with self._client(15) as client:
                response = client.get(f"{self.base_url}/api/ps")
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

    def stop_model(self, model: str) -> None:
        """Unload a model. This is `ollama stop`."""
        self._generate({"model": model, "keep_alive": 0, "stream": False}, timeout=30)

    def start_model(self, model: str) -> None:
        """Load a model into memory without asking it to write.

        A large model can take a few minutes to leave disk. The read timeout
        is long on purpose; connecting to a stopped server still fails fast.
        """
        self._generate({"model": model, "stream": False}, timeout=600)

    def _generate(self, body: dict, timeout: float) -> None:
        try:
            with self._client(timeout) as client:
                response = client.post(f"{self.base_url}/api/generate", json=body)
        except httpx.TimeoutException as exc:
            raise OllamaError(
                f"Ollama did not finish changing to {body.get('model')} within {int(timeout)}s.",
                "timeout",
            ) from exc
        except httpx.HTTPError as exc:
            raise OllamaError(
                f"Ollama is not reachable at {self.base_url}. Start it with `ollama serve`.",
                "offline",
            ) from exc
        if response.status_code >= 400:
            detail = _error_detail(response) or f"HTTP {response.status_code}"
            kind = "model_missing" if "not found" in detail.lower() else "http"
            raise OllamaError(detail, kind)

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
                "Choose an installed model from the Model list.",
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


def canonical_model(name: str) -> str:
    """`llama3.2` and `llama3.2:latest` are the same Ollama model."""
    name = name.strip()
    if name and ":" not in name:
        return f"{name}:latest"
    return name


def same_model(left: str, right: str) -> bool:
    if not left.strip() or not right.strip():
        return False
    return canonical_model(left) == canonical_model(right)


def models_to_stop(running: list[str], target: str) -> list[str]:
    return [name for name in running if not same_model(name, target)]


def model_needs_start(running: list[str], target: str) -> bool:
    return not any(same_model(name, target) for name in running)


def apply_model_switch(client: OllamaClient, target: str, previous: str | None = None) -> None:
    """Unload every other loaded model, then load `target` if it is not loaded.

    `ollama ps` is the list of what is actually in memory. If that list is
    unavailable, unload the model this session was using and load the new one.
    """
    try:
        running = client.running_models()
    except OllamaError:
        if previous and not same_model(previous, target):
            try:
                client.stop_model(previous)
            except OllamaError:
                pass
        client.start_model(target)
        return
    for name in models_to_stop(running, target):
        client.stop_model(name)
    if model_needs_start(running, target):
        client.start_model(target)


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
