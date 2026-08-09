"""The Adapter: everything that knows the wire format lives here and nowhere else.

The brief demands provider-agnosticism through environment variables. This class
is the seam that makes that real — headers, payload shape, `response_format`
quirks, HTTP status mapping. Swapping Ollama for OpenAI or vLLM is a change to
LLM_BASE_URL, not a change to any code above this file (PRD §6.1).

No SDK. The OpenAI chat-completions wire format is about forty lines of JSON over
HTTP, and owning those forty lines means no SDK's retry policy, timeout defaults,
or exception hierarchy is quietly imposed on the reliability design in §7.
"""

from __future__ import annotations

import time
from types import TracebackType
from typing import Any, Self

import httpx

from app.core.errors import LlmProtocolError, LlmTimeoutError, LlmTransportError
from app.domain.enums import ResponseFormatMode
from app.domain.models import ChatRequest, ChatResponse


class OpenAICompatibleChatClient:
    """Speaks POST {base_url}/chat/completions and nothing else."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 45.0,
        max_response_bytes: int = 65_536,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._max_response_bytes = max_response_bytes
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=timeout_seconds)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def complete(self, request: ChatRequest) -> ChatResponse:
        """One round trip. Raises only LlmTransportError / LlmTimeoutError /
        LlmProtocolError — never a raw httpx exception.

        That containment is load-bearing: the retry policy above dispatches on
        these types, so an adapter that leaked `httpx.ConnectError` would turn a
        retryable blip into an unretried failure without anyone noticing.
        """
        payload = self._build_payload(request)
        started = time.perf_counter()

        try:
            response = await self._http.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            )
        except httpx.TimeoutException as exc:
            raise LlmTimeoutError(f"LLM request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            # Connection refused, DNS failure, broken pipe. All transient by
            # nature, all worth a retry with backoff.
            raise LlmTransportError(f"LLM transport failure: {exc}") from exc

        latency_ms = (time.perf_counter() - started) * 1000
        self._raise_for_status(response)
        self._enforce_size_cap(response)

        content, finish_reason = self._extract(response)
        return ChatResponse(content=content, finish_reason=finish_reason, latency_ms=latency_ms)

    # -----------------------------------------------------------------------

    def _build_payload(self, request: ChatRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": False,
        }
        if request.seed is not None:
            payload["seed"] = request.seed

        # Ollama accepts `response_format` on /v1 but has historically ignored the
        # OpenAI `json_schema` variant (ollama#10001), honouring only the native
        # /api/chat `format` parameter for true grammar-constrained decoding.
        # The brief requires an OpenAI-compatible endpoint, so the native API is
        # not the portable path — and the design never depends on constrained
        # decoding anyway. This is opportunistic; the repair layer is the
        # guarantee (PRD §7.1 stage 3).
        if request.response_format is ResponseFormatMode.JSON_SCHEMA and request.json_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "triage",
                    "strict": True,
                    "schema": request.json_schema,
                },
            }
        elif request.response_format is ResponseFormatMode.JSON_OBJECT:
            payload["response_format"] = {"type": "json_object"}
        # ResponseFormatMode.NONE sends no hint at all, which is how the repair
        # layer gets exercised standing alone.

        return payload

    def _raise_for_status(self, response: httpx.Response) -> None:
        status = response.status_code
        if status < 400:
            return
        # 429 and 5xx are the server's problem and may pass. 4xx is ours and
        # will not, so it must not be retried.
        detail = response.text[:500]
        if status == 429 or status >= 500:
            raise LlmTransportError(f"LLM returned {status}: {detail}")
        raise LlmProtocolError(f"LLM rejected the request with {status}: {detail}")

    def _enforce_size_cap(self, response: httpx.Response) -> None:
        """Cap the body BEFORE parsing it (OWASP API10).

        The LLM is an untrusted third party. A 400MB response from a
        misconfigured or hostile endpoint should be a bounded error, not an
        out-of-memory kill of the API process.
        """
        size = len(response.content)
        if size > self._max_response_bytes:
            raise LlmProtocolError(
                f"LLM response of {size} bytes exceeds the {self._max_response_bytes}-byte cap"
            )

    def _extract(self, response: httpx.Response) -> tuple[str, str | None]:
        try:
            data = response.json()
        except ValueError as exc:
            raise LlmProtocolError(f"LLM response was not JSON: {exc}") from exc

        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
            finish_reason = choice.get("finish_reason")
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmProtocolError(f"Unexpected chat-completions envelope: {exc}") from exc

        if not isinstance(content, str) or not content.strip():
            # A refusal or an empty completion. Deterministic at temperature 0,
            # so this is NOT retried — it goes to the fallback (PRD §7.1).
            raise LlmProtocolError("LLM returned an empty completion")

        return content, finish_reason
