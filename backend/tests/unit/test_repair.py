"""Salvaging JSON from malformed model output (app.triage.repair).

Table-driven against tests/data/malformed_outputs.json — REAL captured llama3.2:3b
failures from the Day-0 spike, not invented ones. Fixtures I made up would only
prove I can defeat my own imagination.

Cases to cover (PRD §6.1 stage 4, §12):
  - bare valid JSON passes through unchanged
  - ```json fenced block
  - bare ``` fenced block
  - leading prose: "Here is the triage: {...}"
  - trailing prose after the closing brace
  - both leading and trailing prose
  - nested objects survive the balanced-bracket scan (not a greedy regex)
  - trailing comma before } or ]
  - single-quoted keys/values
  - unterminated string at EOF (max_tokens truncation)
  - truncated mid-object -> unrecoverable, raises SchemaViolation
  - no JSON object at all (flat refusal) -> unrecoverable
  - empty string / whitespace only -> unrecoverable
  - duplicate keys -> last value wins (json module semantics), asserted explicitly
  - response exceeding the byte cap is rejected before parsing
"""
