"""Ticket and triage endpoints. TestClient with dependency overrides, no network."""

from __future__ import annotations

import pytest

from tests.conftest import VALID_DRAFT

BASE = "/api/v1"


class TestListTickets:
    def test_returns_the_whole_corpus(self, client):
        body = client.get(f"{BASE}/tickets", params={"limit": 100}).json()
        assert body["total"] == 30
        assert len(body["items"]) == 30

    def test_corpus_order_is_preserved(self, client):
        items = client.get(f"{BASE}/tickets", params={"limit": 100}).json()["items"]
        ids = [i["ticket"]["id"] for i in items]
        assert ids == sorted(ids)

    def test_listing_never_triggers_a_model_call(self, client, fake_client):
        """Thirty synchronous model calls on first paint would make the queue
        unusable (PRD §16)."""
        client.get(f"{BASE}/tickets", params={"limit": 100})
        assert fake_client.call_count == 0

    def test_pagination(self, client):
        body = client.get(f"{BASE}/tickets", params={"limit": 5, "offset": 10}).json()
        assert len(body["items"]) == 5
        assert body["offset"] == 10
        assert body["total"] == 30

    def test_status_filter(self, client):
        body = client.get(f"{BASE}/tickets", params={"status": "pending", "limit": 100}).json()
        assert body["total"] == 30

    @pytest.mark.parametrize(
        ("param", "value"),
        [("status", "banana"), ("category", "nonsense"), ("limit", 0), ("limit", 500)],
    )
    def test_invalid_filter_is_422_not_an_empty_list(self, client, param, value):
        """An API that answers 'no results' to a typo trains people to distrust it."""
        assert client.get(f"{BASE}/tickets", params={param: value}).status_code == 422


class TestGetTicket:
    def test_found(self, client):
        body = client.get(f"{BASE}/tickets/T-001").json()
        assert body["ticket"]["id"] == "T-001"
        assert body["review"]["status"] == "pending"
        assert body["review"]["version"] == 0

    def test_unknown_id_is_404_problem_json(self, client):
        response = client.get(f"{BASE}/tickets/T-999")
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/problem+json")
        assert set(response.json()) >= {"type", "title", "status", "detail", "instance"}

    @pytest.mark.parametrize(
        "bad_id", ["abc", "T-1", "T-0001", "'; DROP TABLE tickets;--", "T-01a"]
    )
    def test_malformed_id_is_rejected(self, client, bad_id):
        assert client.get(f"{BASE}/tickets/{bad_id}").status_code in (404, 422)

    @pytest.mark.parametrize(
        "attack",
        ["..%2F..%2Fetc%2Fpasswd", "....//....//etc/passwd", "%2e%2e%2f%2e%2e%2fwindows"],
    )
    def test_path_traversal_never_reads_a_file(self, client, attack):
        """IDs are dict lookups, never path fragments — traversal is structurally
        impossible rather than filtered (OWASP API1)."""
        response = client.get(f"{BASE}/tickets/{attack}")
        assert response.status_code in (404, 422)
        assert "root:" not in response.text


class TestTriageEndpoints:
    def test_ad_hoc_triage(self, client, fake_client):
        fake_client.queue(VALID_DRAFT)
        response = client.post(
            f"{BASE}/triage",
            json={"subject": "Charged twice", "body": "I was billed $49 twice in June."},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["category"] == "billing"
        assert "escalate" in body and "confidence" in body

    def test_rerun_for_a_corpus_ticket(self, client, fake_client):
        fake_client.queue(VALID_DRAFT)
        response = client.post(f"{BASE}/tickets/T-001/triage")
        assert response.status_code == 200
        assert response.json()["telemetry"]["stage"] == "llm"

    def test_llm_failure_is_200_degraded_not_503(self, client, fake_client):
        """A degraded answer beats no answer. 503 here would be the wrong
        engineering choice, so it is asserted against (PRD §9)."""
        from app.core.errors import LlmTransportError

        fake_client.queue(*([LlmTransportError("down")] * 3))
        response = client.post(f"{BASE}/triage", json={"body": "The app crashes on export."})
        assert response.status_code == 200
        body = response.json()
        assert body["degraded"] is True
        assert body["escalate"] is True
        assert body["telemetry"]["stage"] == "fallback"

    def test_empty_body_is_422(self, client):
        assert client.post(f"{BASE}/triage", json={"body": ""}).status_code == 422

    def test_missing_body_is_422(self, client):
        assert client.post(f"{BASE}/triage", json={"subject": "hi"}).status_code == 422

    @pytest.mark.parametrize(
        "payload",
        [
            {"body": "x", "unknown_field": 1},
            {"body": "x", "confidence": 0.99},
            {"body": "x", "escalate": False},
            {"body": "x", "degraded": False},
        ],
    )
    def test_extra_and_server_owned_fields_are_rejected(self, client, payload):
        """Mass-assignment defence and the LLM-containment boundary in the same
        mechanism (OWASP API3)."""
        assert client.post(f"{BASE}/triage", json=payload).status_code == 422


class TestHealth:
    def test_healthz_touches_nothing(self, client):
        response = client.get(f"{BASE}/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_readyz_reports_degraded_when_llm_unreachable(self, client, monkeypatch):
        async def unreachable() -> bool:
            return False

        monkeypatch.setattr(client.app.state.container, "llm_reachable", unreachable)
        response = client.get(f"{BASE}/readyz")
        assert response.status_code == 503
        assert response.json()["llm_reachable"] is False
        assert response.json()["tickets_loaded"] == 30


class TestCrossCutting:
    def test_request_id_is_echoed_when_supplied(self, client):
        response = client.get(f"{BASE}/healthz", headers={"X-Request-ID": "abc123"})
        assert response.headers["X-Request-ID"] == "abc123"

    def test_request_id_is_generated_when_absent(self, client):
        assert client.get(f"{BASE}/healthz").headers.get("X-Request-ID")

    def test_openapi_is_available_in_development(self, client):
        assert client.get("/openapi.json").status_code == 200
