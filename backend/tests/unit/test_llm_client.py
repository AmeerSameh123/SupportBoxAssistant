"""The OpenAI-compatible adapter and its resilience decorator.

Driven with httpx.MockTransport, so the real request-building and status mapping
run without a server. The error mapping is the load-bearing part: the retry policy
one layer up dispatches on exception TYPE, so an adapter that leaked
httpx.ConnectError would silently turn a retryable blip into an unretried failure.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.errors import (
    CircuitOpenError,
    LlmProtocolError,
    LlmTimeoutError,
    LlmTransportError,
)
from app.domain.enums import ResponseFormatMode
from app.domain.models import ChatMessage, ChatRequest
from app.llm.client import OpenAICompatibleChatClient
from app.llm.resilience import BreakerState, CircuitBreaker, ResilientChatClient

REQUEST = ChatRequest(
    messages=(ChatMessage(role="user", content="hello"),),
    response_format=ResponseFormatMode.JSON_OBJECT,
    seed=42,
)


def completion(content: str = '{"ok":true}') -> dict:
    return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}


def client_with(handler) -> OpenAICompatibleChatClient:
    return OpenAICompatibleChatClient(
        base_url="http://llm.test/v1",
        api_key="secret-key",
        model="test-model",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


class TestRequestBuilding:
    async def test_sends_model_temperature_seed_and_auth(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            captured["auth"] = request.headers["Authorization"]
            captured["url"] = str(request.url)
            return httpx.Response(200, json=completion())

        await client_with(handler).complete(REQUEST)

        assert captured["url"] == "http://llm.test/v1/chat/completions"
        assert captured["auth"] == "Bearer secret-key"
        assert captured["body"]["model"] == "test-model"
        assert captured["body"]["seed"] == 42
        assert captured["body"]["stream"] is False

    @pytest.mark.parametrize(
        ("mode", "expected"),
        [
            (ResponseFormatMode.JSON_OBJECT, {"type": "json_object"}),
            (ResponseFormatMode.NONE, None),
        ],
    )
    async def test_response_format_modes(self, mode, expected):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=completion())

        await client_with(handler).complete(REQUEST.model_copy(update={"response_format": mode}))
        assert captured["body"].get("response_format") == expected

    async def test_json_schema_mode_sends_a_strict_schema(self):
        """The Day-0 spike found Ollama 0.32.6 honours this and that it fixes the
        schema-echo failure, which is why it is the default."""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=completion())

        await client_with(handler).complete(
            REQUEST.model_copy(
                update={
                    "response_format": ResponseFormatMode.JSON_SCHEMA,
                    "json_schema": {"type": "object"},
                }
            )
        )
        response_format = captured["body"]["response_format"]
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["strict"] is True


class TestErrorMapping:
    """Status class determines retryability, so the mapping is the policy."""

    @pytest.mark.parametrize("status", [500, 502, 503, 429])
    async def test_server_side_statuses_are_retryable_transport_errors(self, status):
        def handler(request):
            return httpx.Response(status, text="upstream unhappy")

        with pytest.raises(LlmTransportError):
            await client_with(handler).complete(REQUEST)

    @pytest.mark.parametrize("status", [400, 401, 404, 422])
    async def test_client_side_statuses_are_non_retryable_protocol_errors(self, status):
        """Our request is wrong and will still be wrong on a retry."""

        def handler(request):
            return httpx.Response(status, text="bad request")

        with pytest.raises(LlmProtocolError):
            await client_with(handler).complete(REQUEST)

    async def test_timeout_maps_to_timeout_error(self):
        def handler(request):
            raise httpx.ReadTimeout("too slow")

        with pytest.raises(LlmTimeoutError):
            await client_with(handler).complete(REQUEST)

    async def test_connection_error_maps_to_transport_error(self):
        def handler(request):
            raise httpx.ConnectError("refused")

        with pytest.raises(LlmTransportError):
            await client_with(handler).complete(REQUEST)

    async def test_no_raw_httpx_exception_escapes(self):
        """The containment guarantee the retry policy depends on."""

        def handler(request):
            raise httpx.ConnectError("refused")

        try:
            await client_with(handler).complete(REQUEST)
        except httpx.HTTPError:  # pragma: no cover
            pytest.fail("adapter leaked an httpx exception")
        except LlmTransportError:
            pass

    async def test_non_json_body_is_a_protocol_error(self):
        def handler(request):
            return httpx.Response(200, text="<html>oops</html>")

        with pytest.raises(LlmProtocolError, match="not JSON"):
            await client_with(handler).complete(REQUEST)

    async def test_unexpected_envelope_is_a_protocol_error(self):
        def handler(request):
            return httpx.Response(200, json={"result": "surprise"})

        with pytest.raises(LlmProtocolError, match="envelope"):
            await client_with(handler).complete(REQUEST)

    @pytest.mark.parametrize("content", ["", "   ", None])
    async def test_empty_completion_is_not_retried(self, content):
        """Deterministic at temperature 0, so a retry burns time to fail
        identically. It goes straight to the fallback (PRD §7.1)."""

        def handler(request):
            return httpx.Response(200, json=completion(content))

        with pytest.raises(LlmProtocolError, match="empty completion"):
            await client_with(handler).complete(REQUEST)

    async def test_oversized_response_is_capped_before_parsing(self):
        """OWASP API10: the LLM is an untrusted third party, and a huge body
        should be a bounded error rather than an OOM kill."""

        def handler(request):
            return httpx.Response(200, json=completion("x" * 200_000))

        client = OpenAICompatibleChatClient(
            base_url="http://llm.test/v1",
            api_key="k",
            model="m",
            max_response_bytes=1024,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        with pytest.raises(LlmProtocolError, match="exceeds"):
            await client.complete(REQUEST)


class TestResilientChatClient:
    async def test_retries_transport_errors_then_succeeds(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 3:
                raise httpx.ConnectError("refused")
            return httpx.Response(200, json=completion())

        resilient = ResilientChatClient(client_with(handler), max_retries=2, sleep=_no_sleep)
        assert (await resilient.complete(REQUEST)).content == '{"ok":true}'
        assert calls["n"] == 3

    async def test_protocol_errors_are_not_retried(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(400, text="bad")

        resilient = ResilientChatClient(client_with(handler), max_retries=2, sleep=_no_sleep)
        with pytest.raises(LlmProtocolError):
            await resilient.complete(REQUEST)
        assert calls["n"] == 1, "a 4xx will still be a 4xx on retry"

    async def test_retries_are_bounded(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            raise httpx.ConnectError("refused")

        resilient = ResilientChatClient(client_with(handler), max_retries=2, sleep=_no_sleep)
        with pytest.raises(LlmTransportError):
            await resilient.complete(REQUEST)
        assert calls["n"] == 3

    async def test_backoff_is_jittered(self):
        """Full jitter, because thirty tickets retrying in lockstep is itself the
        second outage."""
        import random

        resilient = ResilientChatClient(
            client_with(lambda r: httpx.Response(200, json=completion())),
            rng=random.Random(1),
        )
        delays = [resilient._backoff(attempt) for attempt in range(3)]
        assert all(0 <= d <= 0.5 * 2**i for i, d in enumerate(delays))
        assert len(set(delays)) > 1


class TestCircuitBreaker:
    def test_opens_after_the_threshold(self):
        breaker = CircuitBreaker(threshold=3, cooldown_seconds=10, clock=lambda: 0.0)
        for _ in range(2):
            breaker.record_failure()
        assert breaker.state is BreakerState.CLOSED
        breaker.record_failure()
        assert breaker.state is BreakerState.OPEN
        assert breaker.allows_call() is False

    def test_half_opens_after_cooldown(self):
        now = {"t": 0.0}
        breaker = CircuitBreaker(threshold=1, cooldown_seconds=10, clock=lambda: now["t"])
        breaker.record_failure()
        assert breaker.state is BreakerState.OPEN
        now["t"] = 11.0
        assert breaker.state is BreakerState.HALF_OPEN
        assert breaker.allows_call() is True

    def test_success_closes_and_resets(self):
        breaker = CircuitBreaker(threshold=2, clock=lambda: 0.0)
        breaker.record_failure()
        breaker.record_success()
        assert breaker.failures == 0
        assert breaker.state is BreakerState.CLOSED

    async def test_open_circuit_skips_the_call_entirely(self):
        """This is what turns 'Ollama isn't running' from a 15-minute eval into
        a 40-second one."""
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            raise httpx.ConnectError("refused")

        breaker = CircuitBreaker(threshold=1, cooldown_seconds=60, clock=lambda: 0.0)
        resilient = ResilientChatClient(
            client_with(handler), max_retries=0, breaker=breaker, sleep=_no_sleep
        )
        with pytest.raises(LlmTransportError):
            await resilient.complete(REQUEST)
        before = calls["n"]

        with pytest.raises(CircuitOpenError):
            await resilient.complete(REQUEST)
        assert calls["n"] == before, "no network call should have been attempted"

    async def test_protocol_errors_do_not_trip_the_breaker(self):
        """The server answered perfectly well and the model wrote nonsense. That
        is not a reason to stop calling a working endpoint."""

        def handler(request):
            return httpx.Response(200, json=completion(""))

        breaker = CircuitBreaker(threshold=1, clock=lambda: 0.0)
        resilient = ResilientChatClient(
            client_with(handler), max_retries=0, breaker=breaker, sleep=_no_sleep
        )
        for _ in range(3):
            with pytest.raises(LlmProtocolError):
                await resilient.complete(REQUEST)
        assert breaker.state is BreakerState.CLOSED


async def _no_sleep(_seconds: float) -> None:
    return None
