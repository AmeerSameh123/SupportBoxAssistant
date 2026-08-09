"""The evaluation harness. The only eval entrypoint.

    python eval/run_eval.py [--repeat] [--no-cache] [--heuristic-only]

Writes:
    eval/results.json   the required deliverable, in exactly the specified shape
    eval/report.md      generated evidence for the hand-written error analysis

A thin CLI on purpose. All the maths lives in app/evaluation/metrics.py, where it
is importable and unit-tested against published reference values — a metric you
have not tested is a number you cannot defend (PRD §4.1).

Requires `pip install -e ./backend` (or `uv sync`), which is what makes
`from app...` work here without sys.path manipulation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = REPO_ROOT / "eval"

from app.core.config import Settings, get_settings  # noqa: E402
from app.core.container import Container  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.domain.models import Ticket, TriageResult  # noqa: E402
from app.evaluation.metrics import (  # noqa: E402
    CATEGORY_LABELS,
    PRIORITY_LABELS,
    PipelineHealth,
    Score,
    calibration,
    cohens_kappa,
    exact_agreement,
    macro_f1,
    majority_baseline,
    within_one_priority,
)
from app.evaluation.report import render_report  # noqa: E402
from app.triage.heuristic_strategy import HeuristicTriageStrategy  # noqa: E402


def load_tickets(settings: Settings) -> list[Ticket]:
    raw = json.loads(settings.tickets_path.read_text(encoding="utf-8"))
    return [Ticket.model_validate(item) for item in raw]


def load_labels(settings: Settings) -> dict[str, dict[str, str]]:
    payload = json.loads(settings.labels_path.read_text(encoding="utf-8"))
    return {item["id"]: item for item in payload["labels"]}


async def triage_all(
    container: Container, tickets: list[Ticket], *, force: bool
) -> dict[str, TriageResult]:
    """Bounded concurrency comes from ResilientChatClient's semaphore.

    Bounded is FASTER than unbounded against one CPU-bound Ollama: the Day-0
    spike measured 1.8s/ticket at concurrency 1 versus 1.4s at concurrency 2.
    """
    semaphore = asyncio.Semaphore(max(container.settings.llm_concurrency, 1))

    async def one(ticket: Ticket) -> tuple[str, TriageResult]:
        async with semaphore:
            return ticket.id, await container.triage_service.triage(ticket, force=force)

    results: dict[str, TriageResult] = {}
    for done, coro in enumerate(asyncio.as_completed([one(t) for t in tickets]), start=1):
        ticket_id, result = await coro
        results[ticket_id] = result
        print(
            f"  [{done:2}/{len(tickets)}] {ticket_id} -> "
            f"{result.category.value}/{result.priority.value} "
            f"({result.telemetry.stage.value})",
            flush=True,
        )
    return results


async def _execute(
    container: Container, tickets: list[Ticket], *, force: bool, repeat: bool
) -> tuple[dict[str, TriageResult], float | None]:
    """Everything that touches the event loop, in one place."""
    try:
        results = await triage_all(container, tickets, force=force)

        self_consistency: float | None = None
        if repeat:
            print("\nSecond run for the determinism check...")
            second = await triage_all(container, tickets, force=True)
            agree = sum(
                1
                for tid in results
                if (results[tid].category, results[tid].priority)
                == (second[tid].category, second[tid].priority)
            )
            self_consistency = agree / len(results)
        return results, self_consistency
    finally:
        await container.aclose()


def build_health(results: dict[str, TriageResult]) -> PipelineHealth:
    health = PipelineHealth(total=len(results))
    for result in results.values():
        health.by_stage[result.telemetry.stage.value] += 1
        if result.telemetry.repairs:
            health.repaired += 1
        if result.signals.retried:
            health.retried += 1
        if result.escalate:
            health.escalated += 1
        if result.degraded:
            health.degraded += 1
        if result.injection_suspected:
            health.injection_detected += 1
        if result.spam_suspected:
            health.spam_detected += 1
        for repair in result.telemetry.repairs:
            health.repair_taxonomy[repair.value] += 1
        for failure in result.telemetry.failures:
            health.failure_taxonomy[failure.value] += 1
        if result.telemetry.latency_ms:
            health.latencies_ms.append(result.telemetry.latency_ms)
    return health


def prediction_record(ticket_id: str, result: TriageResult) -> dict[str, Any]:
    """EXACTLY the seven fields the brief specifies. Nothing extra.

    Diagnostics go in a sibling top-level key so a grader's script that reads
    only `metrics` and `predictions` works unmodified (PRD §11.1).
    """
    return {
        "id": ticket_id,
        "category": result.category.value,
        "priority": result.priority.value,
        "summary": result.summary,
        "suggested_reply": result.suggested_reply,
        "confidence": round(result.confidence, 3),
        "escalate": result.escalate,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the triage evaluation.")
    parser.add_argument(
        "--repeat", action="store_true", help="Run twice and report self-consistency (PRD 11.5)."
    )
    parser.add_argument(
        "--no-cache", action="store_true", help="Bypass the triage cache for a cold run."
    )
    parser.add_argument(
        "--heuristic-only",
        action="store_true",
        help="Score the regex fallback alone. No model, no network, ~instant.",
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging("WARNING")  # per-ticket progress is printed, not logged

    tickets = load_tickets(settings)
    labels = load_labels(settings)
    container = Container(settings)

    print(f"Model      : {settings.llm_model} @ {settings.llm_base_url}")
    print(f"Prompt     : {container.template.version}")
    print(
        f"Mode       : response_format={settings.llm_response_format.value}, "
        f"temp={settings.llm_temperature}, seed={settings.llm_seed}"
    )
    print(f"Tickets    : {len(tickets)}   Labelled: {len(labels)}")
    print()

    started = time.perf_counter()
    if args.heuristic_only:
        # The baseline on its own: no model, no network. Useful for checking
        # what the regex alone is worth before attributing anything to the LLM,
        # and it runs with Ollama stopped.
        baseline = HeuristicTriageStrategy(container.assembler)
        results = {t.id: baseline.triage_sync(t) for t in tickets}
        self_consistency = 1.0  # deterministic by construction
        asyncio.run(container.aclose())
    else:
        # ONE event loop for the whole run. The container owns an
        # httpx.AsyncClient bound to the loop it was created in; separate
        # asyncio.run() calls would close it from a different loop and raise
        # "Event loop is closed" at exit.
        results, self_consistency = asyncio.run(
            _execute(container, tickets, force=args.no_cache, repeat=args.repeat)
        )
    duration = time.perf_counter() - started

    # --- heuristic baseline: no model, same tickets, same scoring ------------
    heuristic = HeuristicTriageStrategy(container.assembler)
    heuristic_results = {t.id: heuristic.triage_sync(t) for t in tickets}

    # --- scoring, over the labelled subset only ------------------------------
    labelled = [t.id for t in tickets if t.id in labels]
    category_pairs = [(labels[i]["category"], results[i].category.value) for i in labelled]
    priority_pairs = [(labels[i]["priority"], results[i].priority.value) for i in labelled]
    heuristic_pairs = [
        (labels[i]["category"], heuristic_results[i].category.value) for i in labelled
    ]

    category_score = exact_agreement(category_pairs)
    priority_score = exact_agreement(priority_pairs)
    within_one = within_one_priority(priority_pairs)
    majority = majority_baseline(category_pairs)
    heuristic_score = exact_agreement(heuristic_pairs)

    confidences = [results[i].confidence for i in labelled]
    raw_confidences = [
        results[i].telemetry.raw_confidence
        if results[i].telemetry.raw_confidence is not None
        else results[i].confidence
        for i in labelled
    ]
    calibrated_cal = calibration(category_pairs, confidences)
    raw_cal = calibration(category_pairs, raw_confidences)

    disagreements: list[dict[str, Any]] = []
    ticket_index = {t.id: t for t in tickets}
    for ticket_id in labelled:
        result = results[ticket_id]
        for field, gold, predicted in (
            ("category", labels[ticket_id]["category"], result.category.value),
            ("priority", labels[ticket_id]["priority"], result.priority.value),
        ):
            if gold != predicted:
                disagreements.append(
                    {
                        "id": ticket_id,
                        "field": field,
                        "gold": gold,
                        "predicted": predicted,
                        "confidence": result.confidence,
                        "stage": result.telemetry.stage.value,
                        "subject": ticket_index[ticket_id].subject,
                    }
                )

    health = build_health(results)
    run_metadata = {
        "generated_at": datetime.now(UTC).isoformat(),
        "model": settings.llm_model,
        "base_url": settings.llm_base_url,
        "prompt_version": container.template.version,
        "response_format": settings.llm_response_format.value,
        "temperature": settings.llm_temperature,
        "seed": settings.llm_seed,
        "confidence_mode": settings.confidence_mode.value,
        "escalate_threshold": settings.escalate_confidence_threshold,
        "wall_clock_seconds": round(duration, 1),
        "python": platform.python_version(),
        "cache_bypassed": args.no_cache,
        "heuristic_only": args.heuristic_only,
    }

    # --- results.json --------------------------------------------------------
    # `metrics` and `predictions` match the brief's shape EXACTLY. Everything
    # extra is a sibling key, so the contract is met literally while the rigour
    # is still on the record (PRD 11.1).
    payload = {
        "metrics": {
            "category_accuracy": round(category_score.rate, 4),
            "priority_agreement": round(priority_score.rate, 4),
        },
        "predictions": [prediction_record(t.id, results[t.id]) for t in tickets],
        "run_metadata": run_metadata,
        "extended_metrics": {
            "labelled_subset_size": category_score.total,
            "category_accuracy_detail": category_score.as_dict(),
            "priority_agreement_detail": priority_score.as_dict(),
            "priority_within_one": within_one.as_dict(),
            "majority_class_baseline": majority.as_dict(),
            "heuristic_fallback_baseline": heuristic_score.as_dict(),
            "cohens_kappa_category": round(cohens_kappa(category_pairs, CATEGORY_LABELS), 4),
            "cohens_kappa_priority": round(cohens_kappa(priority_pairs, PRIORITY_LABELS), 4),
            "macro_f1_category": round(macro_f1(category_pairs, CATEGORY_LABELS), 4),
            "calibration_raw": raw_cal.as_dict(),
            "calibration_shipped": calibrated_cal.as_dict(),
            "self_consistency": self_consistency,
            "pipeline_health": health.as_dict(),
        },
        "diagnostics": {
            "disagreements": disagreements,
            "per_ticket_stage": {
                tid: r.telemetry.stage.value for tid, r in sorted(results.items())
            },
            "escalated": sorted(tid for tid, r in results.items() if r.escalate),
        },
    }
    # A diagnostic mode must never overwrite the graded deliverable. The
    # baseline run writes beside results.json, not over it.
    results_name = "results.heuristic.json" if args.heuristic_only else "results.json"
    report_name = "report.heuristic.md" if args.heuristic_only else "report.md"
    (EVAL_DIR / results_name).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    (EVAL_DIR / report_name).write_text(
        render_report(
            category_pairs=category_pairs,
            priority_pairs=priority_pairs,
            category_score=category_score,
            priority_score=priority_score,
            priority_within_one=within_one,
            majority=majority,
            heuristic_score=heuristic_score,
            calibration_raw=raw_cal,
            calibration_calibrated=calibrated_cal,
            disagreements=disagreements,
            health=health.as_dict(),
            run_metadata=run_metadata,
            self_consistency=self_consistency,
        ),
        encoding="utf-8",
    )

    _print_summary(
        args.heuristic_only,
        results_name,
        report_name,
        category_score,
        priority_score,
        within_one,
        majority,
        heuristic_score,
        health,
        disagreements,
        duration,
        self_consistency,
    )
    return 0


def _print_summary(
    heuristic_only: bool,
    results_name: str,
    report_name: str,
    category: Score,
    priority: Score,
    within_one: Score,
    majority: Score,
    heuristic: Score,
    health: PipelineHealth,
    disagreements: list[dict[str, Any]],
    duration: float,
    self_consistency: float | None,
) -> None:
    print()
    print("=" * 66)
    print(f"category_accuracy  {category}")
    print(f"priority_agreement {priority}")
    print(f"  within one level {within_one}")
    print("-" * 66)
    print(f"majority baseline  {majority}")
    print(f"heuristic baseline {heuristic}")
    if self_consistency is not None:
        print(f"self-consistency   {self_consistency:.1%}")
    print("-" * 66)
    print(f"stages             {dict(health.by_stage)}")
    print(f"repairs needed     {health.repaired}/{health.total}  {dict(health.repair_taxonomy)}")
    print(f"escalated          {health.escalated}/{health.total}")
    print(f"disagreements      {len(disagreements)}")
    print(f"wall clock         {duration:.1f}s")
    print("=" * 66)
    print(f"wrote eval/{results_name} and eval/{report_name}")
    if not heuristic_only:
        print("Now read the disagreements and update eval/ERROR_ANALYSIS.md by hand.")


if __name__ == "__main__":
    sys.exit(main())
