"""Human review endpoint (app.api.v1.reviews).

The human-in-the-loop half of the brief: "let a human edit the reply and
approve/reject". These tests cover the part that makes it real rather than a toy —
the edit surviving a refresh, and two tabs not silently destroying each other.

PATCH /api/v1/reviews/{ticket_id}
  - 200: status -> approved, persisted and returned
  - 200: status -> rejected with a reviewer note
  - 200: edited_reply replaces the draft; the ORIGINAL draft is retained so the
         eval and the audit trail still show what the model actually produced
  - partial update leaves untouched fields unchanged
  - 404 for an unknown ticket id
  - 422 for an invalid status value
  - 422 for extra fields (extra="forbid")
  - 422 when the client tries to set confidence / escalate / degraded

Optimistic concurrency (PRD §10.2):
  - PATCH with the version last read -> 200, version increments
  - PATCH with a stale version -> 409 Conflict, store unchanged
  - two sequential edits with correct versions both apply
  This is the two-browser-tabs case. Losing an agent's edited reply is a real bug,
  so it gets a real test rather than a note in the README.

Durability:
  - written record is readable after the repository is reconstructed
    (proves the atomic temp-file + os.replace write, not just an in-memory dict)
  - a corrupt/partial store file is detected rather than silently returning {}

Safety invariant, asserted directly:
  - there is no endpoint, field, or side effect anywhere that sends a reply to a
    customer. Approving records a decision; it does not transmit anything.
    The brief's core product constraint (PRD §10, API6).

Audit:
  - every mutation records what changed and when
"""
