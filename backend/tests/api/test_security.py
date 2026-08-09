"""API security controls, mapped to the OWASP API Security Top 10 (2023)."""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.errors import AuthenticationError
from app.core.security import TokenBucketRateLimiter, verify_token

BASE = "/api/v1"


class TestAuthentication:
    """OWASP API2. Honest limitation: a shared bearer token is a doorstop, not
    authentication. What it does buy is that the endpoint is not open to anything
    on the machine, and that the compare does not leak the token byte by byte."""

    def test_auth_disabled_in_development_by_default(self, client):
        assert client.post(f"{BASE}/triage", json={"body": "hello there"}).status_code != 401

    def test_missing_token_is_401(self, authed_client):
        response = authed_client.post(f"{BASE}/triage", json={"body": "hello there"})
        assert response.status_code == 401
        assert response.headers["content-type"].startswith("application/problem+json")

    @pytest.mark.parametrize(
        "header", ["", "wrong", "Bearer wrong", "Basic test-token-abc", "test-token-abc"]
    )
    def test_bad_tokens_are_rejected(self, authed_client, header):
        response = authed_client.post(
            f"{BASE}/triage",
            json={"body": "hello there"},
            headers={"Authorization": header},
        )
        assert response.status_code == 401

    def test_correct_token_is_accepted(self, authed_client, fake_client):
        from tests.conftest import VALID_DRAFT

        fake_client.queue(VALID_DRAFT)
        response = authed_client.post(
            f"{BASE}/triage",
            json={"body": "I was charged twice this month."},
            headers={"Authorization": "Bearer test-token-abc"},
        )
        assert response.status_code == 200

    def test_reads_do_not_require_auth(self, authed_client):
        assert authed_client.get(f"{BASE}/tickets").status_code == 200

    def test_token_never_appears_in_a_response(self, authed_client):
        response = authed_client.post(
            f"{BASE}/triage",
            json={"body": "hello there"},
            headers={"Authorization": "Bearer test-token-abc"},
        )
        assert "test-token-abc" not in response.text

    def test_empty_expected_token_disables_the_check(self):
        verify_token(None, "")  # must not raise

    def test_verify_token_rejects_missing_credential(self):
        with pytest.raises(AuthenticationError):
            verify_token(None, "expected")


class TestResourceConsumption:
    """OWASP API4."""

    def test_oversized_body_is_413(self, client):
        response = client.post(f"{BASE}/triage", json={"body": "x" * 40_000})
        assert response.status_code == 413

    def test_rate_limiter_allows_then_blocks(self):
        clock = iter([0.0] * 10)
        limiter = TokenBucketRateLimiter(3, clock=lambda: next(clock))
        assert [limiter.allow("1.2.3.4") for _ in range(4)] == [True, True, True, False]

    def test_rate_limiter_refills_over_time(self):
        times = iter([0.0, 0.0, 0.0, 60.0])
        limiter = TokenBucketRateLimiter(2, clock=lambda: next(times))
        assert limiter.allow("ip") is True
        assert limiter.allow("ip") is True
        assert limiter.allow("ip") is False
        assert limiter.allow("ip") is True  # a minute later

    def test_limits_are_per_client(self):
        limiter = TokenBucketRateLimiter(1, clock=lambda: 0.0)
        assert limiter.allow("a") is True
        assert limiter.allow("a") is False
        assert limiter.allow("b") is True

    def test_rate_limit_returns_429_with_retry_after(
        self, test_settings, service, reviews, fake_client
    ):
        from fastapi.testclient import TestClient

        from tests.conftest import VALID_DRAFT

        fake_client.queue(*([VALID_DRAFT] * 4))

        from app.api import deps
        from app.main import create_app

        app = create_app(test_settings.model_copy(update={"rate_limit_per_minute": 2}))
        app.dependency_overrides[deps.get_triage_service] = lambda: service
        app.dependency_overrides[deps.get_review_repository] = lambda: reviews
        with TestClient(app) as c:
            codes = [
                c.post(f"{BASE}/triage", json={"body": "hello world here"}).status_code
                for _ in range(4)
            ]
        assert codes[:2] == [200, 200], f"first two should pass: {codes}"
        assert codes[2:] == [429, 429], f"the rest should be limited: {codes}"


class TestMisconfiguration:
    """OWASP API7 and API8."""

    def test_security_headers_are_present(self, client):
        headers = client.get(f"{BASE}/healthz").headers
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["Referrer-Policy"] == "no-referrer"
        assert "default-src 'none'" in headers["Content-Security-Policy"]

    def test_cors_allows_only_the_configured_origin(self, client, test_settings):
        allowed = client.get(f"{BASE}/tickets", headers={"Origin": test_settings.frontend_origin})
        assert allowed.headers.get("access-control-allow-origin") == test_settings.frontend_origin

    def test_cors_rejects_an_unlisted_origin(self, client):
        response = client.get(f"{BASE}/tickets", headers={"Origin": "https://evil.example"})
        assert response.headers.get("access-control-allow-origin") != "https://evil.example"

    def test_cors_is_never_wildcard(self, client, test_settings):
        response = client.get(f"{BASE}/tickets", headers={"Origin": test_settings.frontend_origin})
        assert response.headers.get("access-control-allow-origin") != "*"
        assert response.headers.get("access-control-allow-credentials") != "true"

    def test_production_disables_docs(self, test_settings):
        from fastapi.testclient import TestClient

        from app.main import create_app

        prod = test_settings.model_copy(update={"app_env": "production", "api_token": "x" * 32})
        with TestClient(create_app(prod)) as c:
            assert c.get("/docs").status_code == 404
            assert c.get("/openapi.json").status_code == 404

    def test_production_requires_a_token(self, tmp_path):
        """A default that is safe in dev and unsafe in prod is how
        misconfiguration happens; this removes the choice."""
        with pytest.raises(ValueError, match="API_TOKEN is required"):
            Settings(app_env="production", api_token="", data_dir=tmp_path)

    def test_malformed_llm_base_url_fails_at_startup(self, tmp_path):
        """OWASP API7. LLM_BASE_URL is operator config, never request input, and
        a bad value must fail at boot rather than at ticket 17."""
        for bad in ("file:///etc/passwd", "ftp://x", "not-a-url", "gopher://x"):
            with pytest.raises(ValueError):
                Settings(llm_base_url=bad, data_dir=tmp_path)

    def test_no_endpoint_accepts_a_url(self, client):
        """The classic LLM-app SSRF is a user-supplied base URL or a
        fetch-this-link tool. Neither exists, and this asserts it against the
        published inventory."""
        spec = client.get("/openapi.json").json()
        for schema in spec.get("components", {}).get("schemas", {}).values():
            for field in schema.get("properties", {}):
                assert "url" not in field.lower()
                assert "endpoint" not in field.lower()


class TestPrivacy:
    """PRD §10.3. This corpus contains a VAT number and a GDPR erasure request."""

    def test_redaction_scrubs_emails_and_numbers(self):
        from app.core.logging import redact

        assert "marta.kovac@example.com" not in redact("from marta.kovac@example.com")
        assert "[email]" in redact("from marta.kovac@example.com")
        assert "4111 1111 1111 1111" not in redact("card 4111 1111 1111 1111")
        assert "[redacted]" in redact("Authorization: Bearer sk-abc123")

    def test_secret_named_fields_are_replaced_wholesale(self):
        import logging

        from app.core.logging import RedactionFilter

        record = logging.LogRecord("t", logging.INFO, "p", 1, "msg", None, None)
        record.api_key = "sk-super-secret"
        record.ticket_id = "T-001"
        RedactionFilter().filter(record)
        assert record.api_key == "[redacted]"
        assert record.ticket_id == "T-001"

    def test_ticket_bodies_are_not_logged_at_info(self, client, fake_client, caplog):
        from app.core.errors import LlmTransportError

        fake_client.queue(*([LlmTransportError("down")] * 3))
        with caplog.at_level("INFO"):
            client.post(f"{BASE}/tickets/T-009/triage")
        # T-009 contains a VAT number.
        assert "GB123456789" not in caplog.text
        assert "Helios" not in caplog.text
