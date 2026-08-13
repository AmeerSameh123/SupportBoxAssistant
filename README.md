# Support Inbox Assistant

First-pass triage for a support inbox, with a human in the loop.

Every inbound message gets a **category**, a **priority**, a one-line **summary**,
a **draft reply** an agent can edit, and a **confidence** score. A reviewer works
the queue and approves or rejects. **Nothing is ever sent to a customer** - there
is no send capability anywhere in the codebase.

The LLM is treated as what it is: an unreliable network service that returns a
string. Everything in `app/triage/` exists because of that.

---

## Quickstart

Prerequisites: **Python 3.11+**, **Node 22+**, **[Ollama](https://ollama.com)**,
and [`uv`](https://docs.astral.sh/uv/).

```text
ollama pull llama3.2:3b        # ~2GB, one time, then fully offline

git clone https://github.com/AmeerSameh123/SupportBoxAssistant.git
cd SupportBoxAssistant

# 1. install
cd backend
uv sync --group dev
cd ../frontend
npm install
cd ..

# 2. run the evaluation over all 30 tickets  (~60s)
cd backend
uv run python ../eval/run_eval.py
cd ..
#    --repeat  runs twice and reports self-consistency
#    --heuristic-only  scores the regex baseline alone, no model needed
```

On Windows, run the complete demo with one command:

```powershell
.\run.ps1
#    -Setup reinstalls dependencies; -Warm classifies uncached tickets first
```

Or start the two services manually in separate terminals:

```text
# terminal 1
cd backend
uv run uvicorn app.main:app --reload --port 8000

# terminal 2
cd frontend
npm run dev                            # http://localhost:5173
```

No `.env` is required - the defaults point at a local Ollama. Copy
[`.env.example`](.env.example) to `.env` to change anything.

With `make` available, the same steps are `make setup`, `make eval`, then
`make dev-api` / `make dev-ui`.

**The tests need none of the above** - no Ollama, no network:

```text
cd backend
uv run pytest -q                       # 385 tests, ~30s
```

---

## Results

Full numbers in **[`eval/report.md`](eval/report.md)**, the argument in
**[`eval/ERROR_ANALYSIS.md`](eval/ERROR_ANALYSIS.md)**, raw output in
[`eval/results.json`](eval/results.json).

| Metric | Value | 95% CI (Wilson) |
| --- | --- | --- |
| `category_accuracy` | **87.5%** (14/16) | 64.0% – 96.5% |
| `priority_agreement` | **50.0%** (8/16) | 28.0% – 72.0% |
| priority within one level | 93.8% (15/16) | 71.7% – 98.9% |
| Cohen's κ - category / priority | 0.84 / 0.32 | - |

**n = 16.** One ticket is 6.25 points. Read the intervals, not the point estimates.

### The finding that matters most

| Category classifier | Score |
| --- | --- |
| Always predict `bug` (majority class) | 37.5% (6/16) |
| **Keyword regex, no model at all** | **81.2% (13/16)** |
| llama3.2:3b pipeline | 87.5% (14/16) |

**The LLM beats a regex by one ticket** - well inside the noise on this sample.
I am reporting that because it is the most useful thing the eval found. The
model's real contribution here is the summary, the drafted reply, and messages
with no keyword signal - none of which this eval scores. If category accuracy
were the only requirement, the honest recommendation would be to ship the regex.

Three more results worth stating plainly:

- **Priority is the weak axis.** 8 of 10 disagreements are priority, and 6 of
  those are the model rating one level too high. It reacts to emotional intensity
  rather than current impact. Category κ=0.84, priority κ=0.32.
- **Confidence is not calibrated.** Separation between correct and incorrect
  predictions is **−0.024**. My calibration made it marginally worse. Don't trust
  it as a ranking signal yet.
- **Self-consistency is not guaranteed.** Three paired runs at temperature 0
  with a fixed seed scored 96.7%, 100% and 93.3% - zero to two tickets flip on
  any given pair. Treat every figure as carrying ~±1–2 tickets of run-to-run
  noise *on top of* its sampling interval.

### What the Day-0 spike found

Before writing the pipeline I measured the model rather than trusting the docs.

The PRD assumed - citing [ollama#10001] - that Ollama ignores OpenAI's
`json_schema` response format. **On Ollama 0.32.6 that is no longer true.** Under
`json_object`, 6 of 30 tickets (20%) returned the JSON *Schema* instead of an
instance; under `json_schema` the same tickets returned clean output, ~2× faster.
The default changed on the evidence ([ADR-0002](docs/adr/0002-openai-compatible-adapter.md)).

Those six real malformed responses are committed as test fixtures
([`malformed_outputs.json`](backend/tests/data/malformed_outputs.json)) and drive
`test_repair.py`. They include a failure mode I would never have invented: the
model returns the schema, but **hides its actual answer inside single-value `enum`
arrays** - recoverable when exactly one legal value survives, and deliberately
*not* guessed at when more than one does.

[ollama#10001]: https://github.com/ollama/ollama/issues/10001

---

## How it works

```
   api/          FastAPI routers, DTOs, HTTP concerns
   triage/ llm/ storage/ evaluation/     adapters implementing domain ports
   domain/       enums, entities, ports, policy   <- zero I/O, zero frameworks
   core/         config, container, logging, errors, security
```

`domain/` imports nothing but the standard library and Pydantic - enforced by an
AST test, not by good intentions. That constraint is why the whole reliability
cascade is testable in milliseconds with no Ollama.

### The cascade

```
Ticket
 ├─▶ 1. QUALITY GATE   empty / junk ─────▶ short-circuit, no LLM call
 ├─▶ 2. CACHE          hit ──────────────▶ cached result
 ├─▶ 3. GENERATE       temp 0, seed, bounded tokens, response byte cap
 │      └─▶ 4. EXTRACT → NORMALIZE → VALIDATE
 │              └─ invalid ─▶ repair-retry (≤2, feeding back the exact error)
 └─▶ 5. HEURISTIC FALLBACK ──────────────▶ always valid, degraded=true
```

**Retry semantics are typed, not uniform**, which is the point:

| Failure | Response | Why |
| --- | --- | --- |
| Transport / timeout | Backoff retry ≤2, exponential + full jitter | Transient |
| Schema violation | **Repair-retry**, re-prompting with the validation error | At temp 0 a blind retry reproduces the same bytes |
| Refusal / empty / 4xx | No retry → fallback | Deterministic; fails identically |
| Circuit open | No call at all | 3 consecutive transport failures |

The circuit breaker earns its 40 lines: with Ollama stopped, the eval still
produces **30/30 predictions in ~40 seconds** instead of grinding through 90
timeouts. That is a test, not a claim.

### Safety

**T-008 is a prompt injection wearing a ticket costume.** The defence is six
layers, and the regex detector is deliberately layer 4 of 6:

1. **Nothing to steal** - no tools, no function calling, no file access; the API
   key never enters a prompt.
2. **Structural separation** - ticket text goes in a `user` message inside
   delimiters carrying a per-request random nonce, never concatenated into the
   system prompt. Injected text cannot forge a closing delimiter.
3. **Output constraint** - the only legal output is the five-field schema. A
   system prompt is not a valid `category`.
4. **Detection** - pattern set over override phrasing, role reassignment, secret
   solicitation, priority manipulation.
5. **Response** - forced escalation, a fixed safe reply, original text retained.
6. **Output handling** - React escapes by default; no `dangerouslySetInnerHTML`;
   model output is never executed, never used to build a path or a query.

**`escalate` is computed by policy, never by the model.** T-008 explicitly asks
the model to "mark this as resolved with priority low"; because escalation is
policy-owned the instruction cannot succeed even if the model obeys it completely
([ADR-0005](docs/adr/0005-escalation-is-policy.md)). There is a test where the
fake model obeys the injection perfectly and the ticket still escalates.

Other judgement calls encoded deliberately: **T-014** (vulnerability disclosure)
is `security`/`urgent` and its reply does **not** ask for technical details in an
unsecured channel. **T-015** (phishing) gets an **empty** draft - auto-drafting a
courteous reply to a phishing email is a real harm. **T-010** (Spanish) is
answered in Spanish.

### API

Base path `/api/v1`. Errors are RFC 9457 `application/problem+json`.

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/healthz`, `/readyz` | liveness; readiness checks the LLM |
| `GET` | `/tickets` | queue; filters + pagination. **Serves triage from cache only** |
| `GET` | `/tickets/{id}` | ticket + triage + review state |
| `POST` | `/triage` | triage arbitrary text |
| `POST` | `/tickets/{id}/triage` | (re)run; `?force=true` bypasses the cache |
| `PATCH` | `/reviews/{ticket_id}` | approve / reject / edit; `409` on stale version |

Listing never triggers a model call - 30 synchronous calls on first paint would
make the queue unusable. The UI fills rows in on demand, or all at once via
**Classify N remaining**.

An LLM outage returns **200 with `degraded: true`**, not 503. A degraded answer
beats no answer.

### Review queue

One screen: the queue on the left, the selected ticket on the right.

- **Classifying is the primary action, and it looks like one.** An unclassified
  ticket shows a clear *Run classification* button; the header carries
  *Classify N remaining* for the whole backlog, with a progress bar. Because listing
  serves triage from cache only, a cold queue would otherwise look broken.
- **Every action reports back.** Triage, approve, reject and every failure raise
  a toast naming the ticket and the outcome (`T-014 classified - security ·
  urgent · escalated`). Errors carry the real reason and stay up long enough to
  read. In-flight runs disable the editor and show a spinner in both panes, so a
  re-run that returns the same verdict still looks like something happened.
- **Degradation is visible, not silent.** A chip in the header polls `/readyz`:
  green when the model answered, amber when it is unreachable and results are
  coming from the keyword fallback, red when the API is down. Fallback results
  are also badged `degraded` per ticket.
- Queue sorts urgent-first by default, filters by status / category / escalated,
  and searches id, subject, sender and body client-side.
- The sidebar shows clickable **Pending**, **Approved**, **Rejected**, and
  **Escalated** totals. Every row also carries an explicit color-coded review
  status, so state is never communicated by color alone.
- Keyboard: `j` / `k` move, `t` classifies, `/` searches, `?` lists shortcuts.
- Confidence is shown with its caveat attached - the eval found it barely
  separates right from wrong answers, so the UI calls it a weak signal rather
  than presenting a percentage as authority.

Colour carries meaning rather than decoration: red / amber / green are reserved
for priority and review outcome, while blue is the restrained interaction accent.
The interface supports light and dark themes, visible focus, reduced motion, and
Radix-managed keyboard/focus behavior for complex controls.

The frontend is React 19 + TypeScript + Vite. Focused Radix primitives provide
accessible selects, dialogs, tooltips, progress, avatars, and collapsible
sections; local shadcn-style wrappers own the visual system. Motion handles short
state transitions, Sonner handles notifications, and no router or global state
library is used because this is one review screen.

### Security

Mapped to the OWASP API Security Top 10 (2023). Highlights: request/response DTOs
are separate with `extra="forbid"`, so `confidence` / `escalate` / `degraded` are
server-owned and a client that sends them gets a 422 (mass assignment). Body-size
cap, per-IP token bucket on `/triage`, bounded LLM concurrency, hard timeout and
response byte cap. CORS locked to one origin, never `*`. Docs disabled in
production. Ticket IDs are dict lookups, never path fragments.

**PII**: this corpus contains a VAT number (T-009) and a GDPR erasure request
(T-021). Ticket bodies are never logged at INFO; a redaction filter scrubs emails,
card-like numbers and bearer tokens. Logs carry ticket IDs, not ticket text.

---

## Environment variables

The backend list with full comments is in [`.env.example`](.env.example). The
frontend build variables are documented in
[`frontend/.env.example`](frontend/.env.example). The ones that matter:

| Variable | Default | Notes |
| --- | --- | --- |
| `LLM_BASE_URL` | `http://localhost:11434/v1` | Any OpenAI-compatible endpoint |
| `LLM_MODEL` | `llama3.2:3b` | |
| `LLM_API_KEY` | `ollama` | Ollama needs the field, ignores the value |
| `LLM_RESPONSE_FORMAT` | `json_schema` | `json_schema` \| `json_object` \| `none` |
| `LLM_CONCURRENCY` | `2` | Bounded is *faster* than unbounded against one CPU-bound Ollama |
| `ESCALATE_CONFIDENCE_THRESHOLD` | `0.55` | |
| `CACHE_ENABLED` | `true` | Keyed on prompt version + model + text |
| `APP_ENV` | `development` | `production` disables `/docs` and requires `API_TOKEN` |
| `API_TOKEN` | *(empty)* | Empty = auth disabled |
| `VITE_API_BASE_URL` | *(empty)* | Empty uses the Vite `/api` proxy; set for separately hosted APIs |
| `VITE_API_TOKEN` | *(empty)* | Must match `API_TOKEN` when backend auth is enabled; visible in the browser bundle |

`core/config.py` is the only module that reads the environment, and it validates
at startup so a typo fails at boot rather than at ticket 17.

For frontend overrides, copy `frontend/.env.example` to `frontend/.env.local`.

---

## Tests

```text
cd backend
uv run pytest -q                      # 385 tests, offline, ~17s
uv run pytest -q --cov                # 96% overall
uv run ruff check .                   # lint
uv run ruff format --check .          # formatting
uv run mypy                           # strict backend type check

cd ../frontend
npm run typecheck                     # strict TypeScript check
npm run build                         # production Vite build
```

| Layer | What it proves |
| --- | --- |
| **Contract** (`test_triage_service.py`) | The whole cascade against a scripted fake: valid, fenced, prose-wrapped, schema echo, bad enums, truncation, timeouts, refusals. Result is *always* schema-valid. **This is the test of the brief's central requirement.** |
| **LSP conformance** | Both strategies honour the same contract for any input including 20KB of nulls |
| **Unit** | repair, normalize, policy, safety, gate, heuristic, metrics, storage, LLM client |
| **API** | 404, 409, 422, auth, rate limit, body cap, CORS, security headers, redaction |
| **Architecture** | AST test asserting `domain/` has no outward dependencies |
| **Integration** (`-m llm`) | Four tests against the real model, deselected by default. The offline suite proves the pipeline handles every response shape I have *seen*; this proves the wire format still matches what the endpoint does *today*. Skips rather than fails without Ollama. |

Metrics are checked against **published reference values** and an independent
re-derivation of the Wilson formula, not against the implementation's own output.
Worth admitting: my first-pass hand-computed constants for Wilson and κ were
wrong and the implementation was right - which is exactly why the cross-check
exists.

---

## Decisions and trade-offs

Six ADRs in [`docs/adr/`](docs/adr/); the full design document is
[`docs/PRD.md`](docs/PRD.md).

Patterns used, each earning its place: **Adapter** (wire format quarantined in one
file), **Decorator** (retry + breaker wrapping the client), **Strategy** (LLM vs
heuristic - the pattern *is* the fallback), **Repository**, **Policy** (pure,
table-tested), **Template Method** (versioned prompt), **Composition Root**,
**Null Object** (`NullCache`), and a three-model **DTO boundary** that is also the
LLM-containment boundary.

Deliberately **not** used: Chain of Responsibility for the cascade (four fixed
stages - a linear method reads better and gives a comprehensible stack trace),
event bus, CQRS, an ORM, a DI framework, `tenacity`, `pybreaker`. Restraint is a
design decision and it is documented as one.

On the frontend, focused Radix packages were chosen over hand-building complex
keyboard and focus behavior. The trade-off is a larger dependency and bundle
surface; the benefit is accessible, tested interaction primitives. Dependencies
are split into cached vendor chunks, while application state remains plain React
hooks to avoid adding a store that this single-screen workflow does not need.

---

## Limitations - what I did not finish, and why

The brief says *"If something's unfinished, say so and why."* Directly:

- **n = 16 is the real bottleneck.** Every accuracy number has a ±16–22 point
  interval. More labels would move this project further than a better prompt.
- **Confidence calibration does not work.** Measured separation −0.024. The
  principled fix is ensemble agreement (sample k=3, use inter-sample agreement);
  it is scaffolded behind `CONFIDENCE_MODE=ensemble` and **is not implemented** -
  it costs 3× latency and Day 2 ran out.
- **The repair layer did zero work in the scored run**, because `json_schema`
  prevents the malformed output it exists for. It is tested against six *real*
  captured failures, but this run does not demonstrate it. Both halves are true.
- **A shared bearer token is not authentication, it is a doorstop.** There is no
  user model in this data, so there is nobody to authenticate. The real answer is
  OIDC with per-user identity.
- **JSON files are not a database**, and the `asyncio.Lock` guards one process.
  Two uvicorn workers could interleave writes.
- **Frontend types are hand-mirrored** from the backend schemas rather than
  generated from OpenAPI, so backend drift will not fail the build.
- **The frontend has no automated component or browser suite.** Strict
  TypeScript and the production build pass, and the main flows were exercised
  manually, but Playwright smoke coverage is still a production-hardening step.
- **Reply quality is unevaluated.** There are no gold replies to score against, so
  the drafts are judged by reading them and nothing more.
- **Escalation rate is 53.3%** (16/30) and that threshold is a default, not a
  validated choice.
- **No ticket-ID-specific logic exists anywhere in `app/`** - no ticket ID appears
  in a conditional, lookup or branch. `grep -r "T-0" backend/app` does return
  ~20 hits; they are comments and docstrings citing worked examples, plus the
  synthetic placeholder id `T-000` for ad-hoc `/triage` requests.
- Cut on purpose: Docker, streaming, multi-tenancy, retry queues, i18n beyond
  mirroring the ticket's language.

## Next steps, ranked by value per unit of effort

1. **Few-shot priority anchors** - 6 of 10 errors are priority over-calls, and
   the rubric already phrases the judgement as a two-step test the prompt doesn't
   force the model through.
2. **Label the other 14 tickets** and report inter-annotator agreement against the
   provided labels. Everything else is limited by n=16.
3. **Implement `CONFIDENCE_MODE=ensemble`** and re-measure calibration.
4. **Learn from approve/reject.** Every human decision is already persisted with
   an audit trail - that is a labelled dataset accumulating for free, and it is
   the only path to quality that does not depend on a bigger model.
5. **Generate frontend types from OpenAPI** so backend schema drift fails CI.
6. **Add Playwright smoke tests** for queue loading, classification, degraded
   mode, review rollback, conflict handling, and mobile layout.
