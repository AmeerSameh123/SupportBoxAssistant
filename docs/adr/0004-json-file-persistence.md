# ADR-0004 — JSON files for persistence, behind a repository interface

**Date**: 2026-08-09 · **Status**: Accepted

## Context

Two things need storing: human review decisions (30 records, mutable) and triage
results (30 records, derived and regenerable). A database would be more code than
the data it holds.

## Decision

- `JsonTicketRepository` — read-only, loaded once into memory.
- `JsonReviewRepository` — one JSON file, **temp-file + `Path.replace`** (atomic
  on Windows and POSIX), guarded by an `asyncio.Lock`, with an integer `version`
  per record for optimistic concurrency.
- `FileTriageCache` — **one file per key** under `.cache/triage/`, keyed on
  `sha256(prompt_version ‖ model ‖ normalized_text)`.

All three sit behind Protocols in `app/domain/ports.py`.

## Consequences

- Swapping to SQLite or Postgres touches one file per concern and no caller.
- Atomicity is real, not aspirational: a crash mid-write leaves the previous good
  file. Tested (`test_storage.py::test_write_is_atomic_and_leaves_no_temp_file`).
- A stale `version` on PATCH returns **409** rather than silently overwriting.
  The losing write in that race is an agent's edited reply — a real bug, and two
  browser tabs is a realistic demo.
- The cache is one file per key because entries are written concurrently during
  an eval run; separate files mean no lock contention and no read-modify-write
  race. They are also inspectable with `cat`, which matters when debugging why a
  cached answer looks wrong.
- Including `prompt_version` in the cache key means editing the prompt correctly
  invalidates everything. A cache that survives a prompt change is worse than no
  cache: it makes an eval report the previous prompt's numbers.

## Notes and limits

- **Not concurrent-safe across processes.** One `asyncio.Lock` guards one
  process. Two `uvicorn` workers could interleave writes. Acceptable for a
  single-process demo; stated rather than discovered.
- An empty store file is treated as an empty store; genuinely malformed content
  **raises** rather than returning `{}`, because "no reviews yet" and "your work
  is unreadable" must not look the same.
