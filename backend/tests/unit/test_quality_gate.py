"""Pre-LLM quality gate (app.triage.quality_gate).

Refusing to guess is the correct product behaviour: sending an empty string to an
LLM and asking for a category invites confident nonsense (PRD §6.1 stage 1).

Short-circuits (no LLM call, result is other/low/confidence 0.0/escalate True):
  - T-030: subject "Help please", body "" -> empty body
  - body is whitespace only
  - T-004: "asdkjhasd test test ignore" -> below MIN_SIGNAL_CHARS, no dictionary-shaped content
  - subject and body both empty

Does NOT fire — the gate is deliberately conservative, because a false positive
here silently drops a real ticket:
  - T-028: "my card shows 58 but the plan is 49? where does the extra come from"
      short, but unambiguously a real billing question
  - T-018: "doesnt work anymore. please fix asap"
      low-information but a genuine complaint; handled by confidence penalty
      (test_policy.py), not by the gate
  - T-023: "nvm figured it out, thanks anyway"
      short and self-resolved, but real -> reaches the LLM and is triaged other/low
  - T-010: Spanish body -> non-English is not low-signal

Asserted invariants:
  - a short-circuit result is schema-valid and carries the fixed
    "Insufficient information — human review required." reply
  - the gate performs zero I/O and never calls the ChatClient
"""
