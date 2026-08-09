"""API security controls (app.core.security, app.main middleware).

Mapped to the OWASP API Security Top 10 (2023 edition). Local single-user tooling
does not get to skip a threat model; it gets to scope one, out loud (PRD §10).

Authentication (API2):
  - API_TOKEN unset in development -> auth disabled, requests succeed
  - API_TOKEN set -> mutating routes 401 without a bearer token
  - wrong token -> 401
  - correct token -> 200
  - comparison uses secrets.compare_digest (constant time). A timing-leaky
    compare in a security-themed submission is an own goal.
  - the token never appears in a response body, error, or log record

Property-level authorization (API3):
  - every request model rejects unknown fields with 422
  - server-owned fields (confidence, escalate, degraded, version) rejected on input
  - responses contain only fields declared on the response_model

Resource consumption (API4):
  - body over MAX_REQUEST_BYTES -> 413
  - rate limit: RATE_LIMIT_PER_MINUTE + 1 requests -> 429 with Retry-After
  - LLM concurrency semaphore bounds in-flight calls to LLM_CONCURRENCY
  - a hanging ChatClient is cut off at LLM_TIMEOUT_SECONDS, not left to block

SSRF (API7):
  - no endpoint accepts a URL in any field
  - a malformed LLM_BASE_URL fails at startup, loudly, rather than at ticket 17

Misconfiguration (API8):
  - CORS allows FRONTEND_ORIGIN and rejects an unlisted origin
  - never "*", and never "*" together with credentials
  - security headers present: X-Content-Type-Options, X-Frame-Options, Referrer-Policy
  - APP_ENV=production disables /docs and /openapi.json (404)
  - APP_ENV=development exposes them
  - an unhandled exception returns a generic problem+json body; the detail is
    logged server-side only

Unsafe consumption of the LLM (API10):
  - an oversized model response is rejected before JSON parsing
  - model output is never executed, never interpolated into a path or query
  Covered end-to-end in tests/contract/test_triage_service.py; asserted here at
  the HTTP boundary — no LLM-controlled string reaches the client unvalidated.

Privacy (PRD §10.3):
  - ticket bodies are not logged at INFO; only ticket IDs
  - the redaction filter scrubs email addresses and card-like patterns
  This dataset contains a VAT number (T-009) and a GDPR erasure request (T-021).
  Logging bodies at INFO would be a data-protection incident in a system whose
  own test data is asking to be forgotten.
"""
