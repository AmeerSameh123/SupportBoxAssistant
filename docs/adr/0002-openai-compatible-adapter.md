# ADR-0002 — OpenAI-compatible adapter, and `json_schema` by default

**Date**: 2026-08-09 · **Status**: Accepted · **Supersedes a PRD assumption**

## Context

The brief requires an OpenAI-compatible endpoint configured through
`LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY`. Ollama also exposes a *native*
`/api/chat` endpoint whose `format` parameter does grammar-constrained decoding.

Before building, the PRD assumed — citing [ollama/ollama#10001], filed against
v0.6.2 in March 2025 — that Ollama's `/v1` endpoint **ignores** OpenAI's
`json_schema` response format, and therefore defaulted to `json_object`.

## Decision

Write a hand-rolled adapter over `POST {base_url}/chat/completions`, with the
response format selectable via `LLM_RESPONSE_FORMAT`
(`json_schema` | `json_object` | `none`).

**Default to `json_schema`** — changed from the PRD's `json_object` on measured
evidence, not documentation.

## Evidence (Day-0 spike, Ollama 0.32.6, llama3.2:3b)

Under `json_object`, **6 of 30 tickets (20%)** returned the JSON *Schema* instead
of an instance — a failure mode no fixture I invented would have contained. Under
`json_schema`, the same tickets returned clean instances:

| Ticket | `json_object` | `json_schema` |
| --- | --- | --- |
| T-004 | SCHEMA_ECHO (2133 ms) | CLEAN (1026 ms) |
| T-008 | SCHEMA_ECHO (1917 ms) | CLEAN (910 ms) |
| T-023 | SCHEMA_ECHO (2327 ms) | CLEAN (1134 ms) |
| T-030 | SCHEMA_ECHO (2177 ms) | CLEAN (927 ms) |

4/4 fixed, and roughly **2× faster** on those tickets — constrained decoding
stops the model spending tokens emitting the schema. The upstream issue no longer
describes current behaviour.

An early measurement suggesting `json_schema` cost 88 seconds was a **cold
model-load artefact**; re-measured warm, all three modes run at ~1.5 s.

## Consequences

- The scored eval run needed **zero repairs** (`repair_rate` 0.0). That is a
  property of the configuration, not proof the repair layer works, and
  `eval/ERROR_ANALYSIS.md` says so explicitly.
- Portability is preserved: any endpoint that ignores `json_schema` degrades to
  the `json_object` path, where the repair layer handles the 20%.
- **The design never depends on constrained decoding.** It is an optimization;
  the repair layer is the guarantee. `LLM_RESPONSE_FORMAT=none` exists to prove
  the repair layer standing alone.

## Alternatives rejected

- **Ollama's native `/api/chat` `format`** — better constraint, but Ollama-only,
  and the brief explicitly asks for an OpenAI-compatible target.
- **An LLM SDK** — the wire format is ~40 lines of JSON over HTTP. Owning it means
  no SDK's retry policy, timeout defaults or exception hierarchy is silently
  imposed on the reliability design in ADR-0003.

[ollama/ollama#10001]: https://github.com/ollama/ollama/issues/10001
