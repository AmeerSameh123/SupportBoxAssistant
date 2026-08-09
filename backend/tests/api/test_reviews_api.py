"""The human review endpoint — the half that makes this a review queue."""

from __future__ import annotations

import pytest

BASE = "/api/v1"


def approve(client, ticket_id="T-001", version=0, **extra):
    return client.patch(
        f"{BASE}/reviews/{ticket_id}",
        json={"status": "approved", "version": version, **extra},
    )


class TestHappyPath:
    def test_approve(self, client):
        response = approve(client)
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "approved"
        assert body["version"] == 1
        assert body["updated_at"] is not None

    def test_reject_with_a_note(self, client):
        response = client.patch(
            f"{BASE}/reviews/T-002",
            json={"status": "rejected", "note": "Wrong category", "version": 0},
        )
        assert response.status_code == 200
        assert response.json()["note"] == "Wrong category"

    def test_edited_reply_is_stored(self, client):
        response = approve(client, edited_reply="Rewritten by a human.")
        assert response.json()["edited_reply"] == "Rewritten by a human."

    def test_decision_survives_a_reload(self, client):
        """The property that separates a review queue from a toy."""
        approve(client, edited_reply="kept")
        body = client.get(f"{BASE}/tickets/T-001").json()["review"]
        assert body["status"] == "approved"
        assert body["edited_reply"] == "kept"
        assert body["version"] == 1

    def test_original_draft_is_not_destroyed_by_an_edit(self, client, fake_client):
        """The eval and the audit trail still need what the model actually
        produced, so the edit lives on the review record, not on the triage."""
        from tests.conftest import VALID_DRAFT

        fake_client.queue(VALID_DRAFT)
        original = client.post(f"{BASE}/tickets/T-001/triage").json()["suggested_reply"]
        approve(client, edited_reply="totally different text")
        after = client.get(f"{BASE}/tickets/T-001").json()
        assert after["review"]["edited_reply"] == "totally different text"
        if after["triage"] is not None:  # only present when cached
            assert after["triage"]["suggested_reply"] == original


class TestOptimisticConcurrency:
    def test_stale_version_is_409(self, client):
        """The two-browser-tabs case. The losing write is an agent's edited
        reply, which is a real bug (PRD §10.2)."""
        approve(client, version=0)
        response = approve(client, version=0, edited_reply="second tab")
        assert response.status_code == 409
        assert response.headers["content-type"].startswith("application/problem+json")

    def test_conflict_leaves_the_store_unchanged(self, client):
        approve(client, version=0, edited_reply="first")
        approve(client, version=0, edited_reply="second tab")
        body = client.get(f"{BASE}/tickets/T-001").json()["review"]
        assert body["edited_reply"] == "first"
        assert body["version"] == 1

    def test_sequential_edits_with_correct_versions_both_apply(self, client):
        assert approve(client, version=0).status_code == 200
        assert approve(client, version=1).status_code == 200
        assert client.get(f"{BASE}/tickets/T-001").json()["review"]["version"] == 2


class TestValidation:
    def test_unknown_ticket_is_404(self, client):
        assert approve(client, ticket_id="T-999").status_code == 404

    def test_invalid_status_is_422(self, client):
        response = client.patch(f"{BASE}/reviews/T-001", json={"status": "maybe", "version": 0})
        assert response.status_code == 422

    def test_missing_version_is_422(self, client):
        """Version is required, so an unversioned write cannot silently clobber."""
        response = client.patch(f"{BASE}/reviews/T-001", json={"status": "approved"})
        assert response.status_code == 422

    @pytest.mark.parametrize(
        "payload",
        [
            {"status": "approved", "version": 0, "surprise": 1},
            {"status": "approved", "version": 0, "confidence": 0.9},
            {"status": "approved", "version": 0, "escalate": False},
        ],
    )
    def test_extra_and_server_owned_fields_rejected(self, client, payload):
        assert client.patch(f"{BASE}/reviews/T-001", json=payload).status_code == 422

    def test_oversized_reply_is_rejected(self, client):
        response = approve(client, edited_reply="x" * 5000)
        assert response.status_code == 422


def test_there_is_no_send_endpoint(client):
    """The brief's core product constraint: nothing auto-sends. Enforced by the
    absence of the capability, and asserted by reading the published inventory
    rather than by trusting a comment (PRD §10, API6)."""
    paths = client.get("/openapi.json").json()["paths"]
    flat = " ".join(paths).lower()
    for forbidden in ("send", "email", "deliver", "dispatch", "reply/send"):
        assert forbidden not in flat
