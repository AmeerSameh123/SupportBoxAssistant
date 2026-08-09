# ADR-0001 — Layered architecture with ports and adapters

**Date**: 2026-08-09 · **Status**: Accepted

## Context

The core problem is "an unreliable external dependency behind a stable contract".
The LLM is slow (~2s), non-deterministic even at temperature 0, and returns a
string rather than an object. The brief's central requirement is that the system
behave sensibly when that dependency misbehaves — which means the misbehaviour
has to be *testable*, and a suite that needs a running Ollama is not a suite.

## Decision

Four layers with a one-directional dependency rule:

```
api/                     HTTP concerns
triage/ llm/ storage/    adapters implementing domain ports
domain/                  enums, entities, ports, policy — zero I/O
core/                    config, container, logging, errors, security
```

`app/domain/` imports only the standard library and Pydantic.

## Consequences

**Good.** The entire reliability cascade is driven by a `FakeChatClient` in
milliseconds — 385 tests run offline in 17 seconds, and CI is green on a machine
that has never heard of llama3.2. The escalation policy is verified by
table-driven assertions with no fixtures at all.

**Cost.** More files than a flat script, and one extra indirection between a
route and the thing it calls. On a 30-ticket exercise that is a real cost and it
is only justified by the testability above.

**Enforcement.** The import rule is checked by `tests/unit/test_domain_purity.py`
(AST-based), not by good intentions. PRD §3.1 proposed a ruff banned-import rule;
ruff's ban list is global rather than per-directory, so it could not express
"banned in domain/, required in api/". The AST test is stricter and names the
offending module and import.

## Alternatives rejected

- **Flat `main.py` + helpers** — faster to write, but the client, the repair
  logic and the HTTP layer entangle, and the reliability core becomes untestable
  offline. That fails the brief's central requirement.
- **Full hexagonal with a use-case class per operation** — four operations do not
  justify four use-case classes plus their request/response objects.
- **Event-driven / CQRS** — 30 tickets, one process, one writer. Theatre.
