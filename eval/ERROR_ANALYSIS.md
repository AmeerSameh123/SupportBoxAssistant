# Error analysis

Hand-written from the run in [`results.json`](./results.json); the generated
evidence is in [`report.md`](./report.md). This file is the argument, not the
output — a script can produce a confusion matrix but it cannot tell you which
disagreements it would defend.

**Run**: `llama3.2:3b` via Ollama 0.32.6, `response_format=json_schema`,
temperature 0, seed 42, prompt `triage/v2`, 30 tickets in 62s.

---

## Headline numbers, with their denominators

| Metric | Value | 95% CI (Wilson) |
| --- | --- | --- |
| `category_accuracy` | **87.5%** (14/16) | 64.0% – 96.5% |
| `priority_agreement` | **50.0%** (8/16) | 28.0% – 72.0% |
| priority within one level | 93.8% (15/16) | 71.7% – 98.9% |
| Cohen's κ, category | 0.837 | — |
| Cohen's κ, priority | 0.316 | — |

**n = 16.** One ticket is 6.25 points. The category interval spans 32 points and
the priority interval spans 44. Every claim below is made with that in mind, and
none of them should be treated as established by this sample alone.

### The number that matters most

| Category classifier | Score |
| --- | --- |
| Always predict the majority class (`bug`) | 37.5% (6/16) |
| **Regex heuristic fallback, no model at all** | **81.2% (13/16)** |
| llama3.2:3b pipeline | 87.5% (14/16) |

**The LLM beats a keyword regex by exactly one ticket.** On this sample that
difference is far inside the noise — the intervals overlap almost completely.

I am reporting this because it is the most useful thing the eval found, not
despite it. Two honest readings:

1. This corpus is keyword-friendly. "refund", "charged", "IDOR", "GDPR" are
   near-deterministic signals, and 30 hand-written tickets are unusually clean
   compared to a real inbox.
2. The LLM's real contribution here is **not** category. It is the summary, the
   drafted reply, and handling messages with no keyword signal — none of which
   the regex can do at all, and none of which this eval scores.

If category accuracy were the only requirement, the honest recommendation would
be to ship the regex and skip the model. It is not the only requirement, so the
model stays — but that argument has to be made explicitly rather than assumed.

---

## Where it fails

### Cluster 1 — Priority is over-called (6 of 10 disagreements)

The single dominant failure mode. Eight of ten disagreements are priority, and
six of those eight are the model rating a ticket **one level too high**.

| Ticket | Gold | Predicted | Subject |
| --- | --- | --- | --- |
| T-002 | high | **urgent** | App crashes on export |
| T-009 | low | **medium** | Invoice needed for accounting |
| T-012 | high | **urgent** | Third time asking |
| T-013 | low | **medium** | how do I add a teammate |
| T-017 | medium | **urgent** | Cancel my subscription |
| T-021 | high | **urgent** | Data deletion request |

**Why.** The model is reacting to emotional intensity and topic gravity rather
than to *current impact on this customer*, which is what the rubric defines
priority to be. T-012 ("THIRD time I'm writing", threatening to cancel) is
genuinely annoyed but nothing is on fire; a calendar sync bug is not an outage.
T-017 is a routine cancellation with the word "immediately" in it. T-021 is
legally binding but has a statutory clock measured in weeks, not minutes.

This is a **prompt/taxonomy problem, not a capability problem**. The model
consistently identifies the right topic (κ=0.84 on category) and then mis-scales
urgency. That distinction matters because the fixes are completely different.

**What I would do next**, in expected-value order:

1. Give the model few-shot anchors — one worked example per priority level,
   drawn from tickets *outside* the labelled 16 so the eval stays honest. Cheap,
   and directly targets the observed failure.
2. Ask for priority as an explicit two-step judgement ("is anyone blocked right
   now? is money already lost?") rather than a single label. The rubric already
   phrases it that way; the prompt should force the model through it.
3. Consider a deterministic post-rule: cap priority at `high` unless the text
   contains an active-breakage marker. I have **not** done this, because it would
   be tuning against the visible 16 and would paper over a prompt problem with a
   heuristic.

### Cluster 2 — Two category disagreements, both defensible

Only two of sixteen categories were wrong, and I would argue about both rather
than engineer them away (PRD §11.6, rule 4).

**T-019 — "Webhook signature mismatch"** · gold `bug`, predicted `security`
> "every payload fails HMAC verification. Did you rotate the signing secret?"

The model read *HMAC*, *signature*, *signing secret* and routed to `security`. I
think that is a reasonable call, and arguably the safer one: a signature
verification failure is either a deploy bug or a key compromise, and you cannot
tell from the ticket. The gold label is also right — it turned out to be a bug.
This is a genuine taxonomy overlap and the cost of the error is one extra pair of
eyes, which is the direction you want to err in.

**T-007 — "Question about your data policy"** · gold `other`, predicted `account`
> "do you train models on customer data? where is data stored? We're in the EU."

A pre-sales GDPR question. `other` is correct by the letter of the taxonomy;
`account` is where the model put it because the corpus's other GDPR ticket
(T-021) genuinely is `account`. This is the taxonomy being underspecified, not
the model being wrong — "questions about policy" and "requests about my personal
data" are different things that share vocabulary.

### Cluster 3 — Confidence is uninformative

| Confidence | Mean when correct | Mean when incorrect | Separation |
| --- | --- | --- | --- |
| Raw (model self-report) | 0.850 | 0.850 | **−0.000** |
| Calibrated (shipped) | 0.833 | 0.850 | **−0.017** |

**The calibration in PRD §7.2 did not work, and the raw signal it was built on
does not exist.** The model reports 0.85 with essentially no variance whether it
is right or wrong, and my multiplicative penalties moved the number in the wrong
direction by 1.7 points.

Two caveats, both of which cut against reading too much into this: there are only
**2** incorrect category predictions in the sample, so the "incorrect" mean is an
average of two numbers; and the penalties are aimed at signals (short body,
multi-intent, non-English) that mostly did not co-occur with the errors.

Still, the honest summary is: **do not trust `confidence` as a ranking signal
today.** It is currently only useful through the escalation policy's floor, where
it works because the quality gate and the fallback drive it to 0.0 rather than
because the model's self-report is meaningful.

The principled fix is ensemble agreement — sample k=3 at temperature 0.7 and use
inter-sample agreement as the confidence. That is genuinely better calibrated and
costs 3× latency (~3 minutes for 30 tickets). It is scaffolded behind
`CONFIDENCE_MODE=ensemble` and **is not implemented**; see the README's
limitations section.

---

## What the pipeline health numbers say

| Metric | Value |
| --- | --- |
| Stage distribution | 28 `llm`, 2 `quality_gate` |
| `repair_rate` | **0.0** |
| `fallback_rate` | 0.0 |
| Escalation rate | 53.3% (16/30) |
| Mean / p95 latency | 2135 ms / 2821 ms |
| Self-consistency across two runs | **100%** (this run) / **96.7%** (an earlier pair) |

### The repair layer did no work in this run, and that needs saying

`repair_rate` is zero. Nothing was salvaged, because with
`response_format=json_schema` every one of the 28 model calls returned clean,
schema-valid JSON. It would be easy to present that as "the reliability layer
works". It is more accurate to say **the reliability layer was not exercised by
this configuration**.

It is not unexercised code, though. The Day-0 spike measured the same model under
`response_format=json_object` and found **6 of 30 tickets (20%) returned the JSON
*Schema* instead of an instance** — a failure mode I would not have invented.
Those six real responses are committed as fixtures in
`backend/tests/data/malformed_outputs.json` and drive `test_repair.py`, and the
contract test drives the whole cascade through fenced JSON, prose-wrapped JSON,
truncation, bad enums, timeouts and refusals.

So the accurate claim is: the repair layer is tested against real failures, and
the default configuration currently prevents those failures from occurring. Both
halves matter — the second is why the first must keep working, since any
endpoint that ignores `json_schema` puts the 20% straight back.

### Self-consistency varies between runs, which is the actual finding

The committed run scored **100%** — two passes at temperature 0 with a fixed seed
agreed on all 30 tickets. An earlier paired run scored **96.7%**: one ticket in
thirty flipped.

Reporting only the 100% would be the more flattering choice and the less honest
one. Local inference is *usually* but not *reliably* deterministic here, so:
**every number in this document carries roughly ±1 ticket of run-to-run noise on
top of its sampling interval.** That is precisely why n=16 point estimates should
not be read to one decimal place.

### Escalation rate is 53.3% — deliberately high, and worth questioning

16 of 30 tickets are flagged for human attention. That is high enough to be worth
challenging: an escalation rate that approaches 100% is the same as no triage at
all. The drivers are `low_confidence` (threshold 0.55 against a model that
reports ~0.85 for real tickets and 0.0 for gated ones), plus every `urgent` and
every `security` ticket by policy.

For a first-pass system where the alternative is a human reading all 30, I think
erring toward escalation is right. But it is a **tuning parameter presented as a
default**, not a validated choice, and the threshold deserves to be set against a
real queue rather than against 16 labels.

---

## What I would *not* do

- **Fine-tune on 16 labels.** That is not a training set, it is a rounding error.
- **Add per-ticket rules.** There is no ticket-ID-specific logic anywhere in the
  codebase and I invite `grep -r "T-0" backend/app` to confirm it.
- **Chase the remaining 12.5% of category accuracy.** Both misses are defensible
  taxonomy overlaps; "fixing" them means overfitting to two examples.
- **Report the within-one-level priority number as the headline.** The brief asked
  for agreement and agreement is 50%. The adjacency figure is reported alongside
  it because it changes the operational reading, not to make the number look
  better.

## If I had one more day

1. Few-shot priority anchors (Cluster 1) — the highest-value change by a distance.
2. Implement `CONFIDENCE_MODE=ensemble` and re-measure calibration properly.
3. Label the remaining 14 tickets myself and report inter-annotator agreement
   against the provided labels. With n=16 the honest bottleneck is **labels, not
   the model**, and everything above is limited by that.
