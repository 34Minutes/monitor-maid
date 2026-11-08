"""Thin client for the xAI Responses API (https://api.x.ai/v1/responses).

Calls are sent with store=false so the exchange is not kept on xAI servers.
The default model id follows the models page on https://docs.x.ai.
"""

from __future__ import annotations

import httpx

from monitor_maid.models import LLMReply


class XAIError(Exception):
    def __init__(self, message: str, kind: str, retry_after: float | None = None):
        super().__init__(message)
        self.kind = kind  # network, timeout, rate_limit, auth, http, bad_response, refusal
        self.retry_after = retry_after


class XAIClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()

    def complete(
        self,
        model: str,
        messages: list[dict[str, str]],
        timeout: float,
    ) -> LLMReply:
        if not self.api_key:
            raise XAIError(
                "No xAI API key. Set XAI_API_KEY or add a key in Settings.",
                "auth",
            )
        body = {
            "model": model,
            "input": messages,
            "store": False,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(
                timeout=httpx.Timeout(connect=8.0, read=timeout, write=30.0, pool=8.0)
            ) as client:
                response = client.post(f"{self.base_url}/responses", headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise XAIError(
                f"The xAI request timed out after {int(timeout)}s.",
                "timeout",
            ) from exc
        except httpx.HTTPError as exc:
            raise XAIError(f"Could not reach the xAI API ({exc.__class__.__name__}).", "network") from exc

        if response.status_code == 429:
            retry_after = _retry_after(response)
            raise XAIError(
                "xAI rate limit reached. The request was not sent again yet.",
                "rate_limit",
                retry_after=retry_after,
            )
        if response.status_code in {401, 403}:
            raise XAIError(
                "xAI rejected the API key (HTTP %s)." % response.status_code,
                "auth",
            )
        if response.status_code >= 400:
            raise XAIError(_http_message(response), "http")
        try:
            payload = response.json()
        except ValueError as exc:
            raise XAIError("xAI returned a response that was not JSON.", "bad_response") from exc
        if not isinstance(payload, dict):
            raise XAIError("xAI returned an unexpected payload.", "bad_response")
        text, refused = extract_output_text(payload)
        if refused and not text:
            text = "Grok refused this request."
        if not text and not refused:
            raise XAIError("xAI returned a response with no text.", "bad_response")
        return LLMReply(text=text, refused=refused)


def extract_output_text(payload: dict) -> tuple[str, bool]:
    """Pull assistant text out of a Responses API body. Also notices refusal objects."""
    refused = False
    pieces: list[str] = []
    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "refusal":
                refused = True
                pieces.append(str(item.get("refusal") or item.get("content") or ""))
                continue
            if item_type != "message":
                continue
            content = item.get("content")
            if isinstance(content, str):
                pieces.append(content)
                continue
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_type = part.get("type")
                if part_type == "refusal":
                    refused = True
                    pieces.append(str(part.get("refusal") or part.get("text") or ""))
                elif part_type in {"output_text", "text"}:
                    pieces.append(str(part.get("text") or ""))
    text = "\n".join(piece.strip() for piece in pieces if piece and piece.strip()).strip()
    if not text:
        convenience = payload.get("output_text")
        if isinstance(convenience, str):
            text = convenience.strip()
    status = str(payload.get("status") or "")
    if status in {"failed", "incomplete"} and not text:
        reason = payload.get("incomplete_details") or payload.get("error") or status
        raise XAIError(f"xAI response {status}: {reason}", "bad_response")
    error = payload.get("error")
    if isinstance(error, dict) and not text:
        code = str(error.get("code") or error.get("type") or "")
        message = str(error.get("message") or "xAI returned an error.")
        if "refus" in code.lower() or "policy" in code.lower():
            return message, True
        raise XAIError(message, "http")
    return text, refused


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _http_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        snippet = response.text.strip()[:400]
        return snippet or f"xAI returned HTTP {response.status_code}."
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)
        if isinstance(error, str):
            return error
    return f"xAI returned HTTP {response.status_code}."
