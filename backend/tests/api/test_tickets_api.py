"""Ticket and triage endpoints (app.api.v1.tickets, app.api.v1.triage).

FastAPI TestClient with dependency overrides from tests/conftest.py — an
in-memory repository and a FakeChatClient. No disk, no network, no Ollama.

GET /api/v1/tickets
  - 200, returns all 30 with triage and review state
  - filters: status, category, escalated — including combinations
  - pagination: limit/offset honoured, total returned
  - unknown filter value -> 422, not a silent empty list
  - response matches the declared response_model exactly

GET /api/v1/tickets/{id}
  - 200 for T-001
  - 404 for a well-formed but unknown id (T-999)
  - 422 for an id failing the ^T-\\d{3}$ pattern
  - path traversal attempt ("../../etc/passwd", "..%2F..") -> 422, never a file read.
    IDs are dict lookups, not path fragments (PRD §10, API1).

POST /api/v1/triage
  - 200 on valid free text
  - 413 when the body exceeds MAX_REQUEST_BYTES
  - 422 on missing required fields
  - 422 on extra fields (extra="forbid")
  - client-supplied `confidence` / `escalate` / `degraded` -> 422, never accepted.
    These are server-owned; this is the mass-assignment defence (PRD §10, API3).

POST /api/v1/tickets/{id}/triage
  - 200 and result is cached; a second call does not hit the ChatClient
  - ?force=true bypasses the cache and does call it
  - LLM failure yields 200 with degraded=true, NOT 503 — a degraded answer beats
    no answer, and the fallback is the whole point (PRD §9)

GET /healthz, /readyz
  - healthz is 200 with no dependencies touched
  - readyz is 503 when the ChatClient is unreachable

Cross-cutting:
  - X-Request-ID echoed when supplied, generated when not
  - errors are application/problem+json per RFC 9457
  - a 500 body contains no stack trace or internal path
"""
