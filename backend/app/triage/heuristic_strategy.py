"""The deterministic floor. Transparent regex rules, no model, never fails.

Two jobs, and the second is the interesting one:

  1. It converts "the AI layer failed" from an exception into a degraded but
     honest answer, which is what the brief means by fallbacks. It is why
     eval/results.json contains 30 predictions even with Ollama stopped.

  2. Its accuracy on the labelled subset is a PUBLISHED BASELINE in the eval. If
     the LLM cannot clearly beat regex, the LLM is not earning its latency — and
     that would be the single most useful finding in the report (PRD §11.3).

Because of (2) this module is held to a real standard rather than treated as dead
code. The keyword tables here are also the single source for multi-intent
detection, so the "is this ticket about two things" signal and the fallback
classifier can never disagree.
"""

from __future__ import annotations

import re

from app.domain.enums import Category, Priority, TriageStage
from app.domain.models import Ticket, TriageResult, TriageSignals, TriageTelemetry
from app.domain.policy import TriageAssembler
from app.triage.safety import (
    INJECTION_SAFE_REPLY,
    SPAM_SAFE_REPLY,
    SafetyScanner,
)

HEURISTIC_CONFIDENCE = 0.3

# One cluster per category. Order within a cluster is irrelevant; the classifier
# scores by match count, so a ticket mentioning three billing words beats one
# that mentions a single bug word.
_CLUSTERS: dict[Category, tuple[str, ...]] = {
    Category.BILLING: (
        "refund",
        "invoice",
        "charge",
        "charged",
        "billing",
        "billed",
        "payment",
        "card",
        "subscription",
        "plan",
        "price",
        "pricing",
        "vat",
        "receipt",
        "cancel my subscription",
        "annual",
        "monthly",
        "renew",
        "downgrade",
    ),
    Category.BUG: (
        "crash",
        "crashes",
        "freeze",
        "freezes",
        "error",
        "broken",
        "bug",
        "not working",
        "doesn't work",
        "doesnt work",
        "fails",
        "failing",
        "503",
        "500",
        "slow",
        "timeout",
        "stopped working",
        "outage",
        "down",
    ),
    Category.ACCOUNT: (
        "log in",
        "login",
        "log-in",
        "password",
        "locked out",
        "access",
        "sign in",
        "account",
        "invite",
        "teammate",
        "colleague",
        "workspace",
        "gdpr",
        "delete all",
        "personal data",
        "sso",
        "2fa",
    ),
    Category.SECURITY: (
        "vulnerability",
        "idor",
        "xss",
        "csrf",
        "exploit",
        "disclosure",
        "security researcher",
        "responsibly",
        "breach",
        "cve",
    ),
    Category.FEATURE_REQUEST: (
        "feature request",
        "could you add",
        "would be great",
        "please add",
        "any way to",
        "is there a way",
        "dark mode",
        "support for",
        "roadmap",
        "consider this a feature",
    ),
}

_URGENT_MARKERS = (
    "production down",
    "entire team",
    "sla",
    "urgent",
    "immediately",
    "every minute",
    "blocked",
    "blocking",
    "outage",
    "503",
    "critical",
)
_HIGH_MARKERS = (
    "third time",
    "still not",
    "nobody has replied",
    "twice",
    "cancelling",
    "cancelling",
    "locked out",
    "refund",
    "charged",
    "lost my",
)
_LOW_MARKERS = (
    "no rush",
    "tiny thing",
    "small typo",
    "just thought",
    "nvm",
    "never mind",
    "figured it out",
    "quick one",
    "no hurry",
)

# Stopword frequency beats a language-detection dependency for a five-language
# problem where exactly one ticket (T-010) is non-English.
_NON_ENGLISH_STOPWORDS = frozenset(
    {
        "la",
        "el",
        "los",
        "las",
        "de",
        "que",
        "en",
        "mi",
        "un",
        "una",
        "por",
        "con",
        "para",
        "es",
        "se",
        "su",
        "pero",
        "como",
        "mas",
        "no",
        "y",
        "je",
        "le",
        "les",
        "des",
        "est",
        "pas",
        "vous",
        "nous",
        "une",
        "der",
        "die",
        "das",
        "und",
        "ich",
        "nicht",
        "ist",
        "mit",
        "ein",
    }
)
_ENGLISH_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "is",
        "to",
        "of",
        "in",
        "for",
        "that",
        "it",
        "my",
        "on",
        "with",
        "this",
        "but",
        "have",
        "was",
        "are",
        "you",
        "your",
        "i",
        "we",
        "can",
        "not",
        "be",
        "at",
        "from",
        "just",
        "please",
    }
)

_WORD = re.compile(r"[a-z']+")


def count_clusters(text: str) -> dict[Category, int]:
    """How many category keyword clusters this text touches, and how strongly.

    Exposed because multi-intent detection (PRD §7.2) uses the same table. T-005
    is genuinely billing *and* account; a single label is lossy and the
    confidence should say so.
    """
    lowered = text.lower()
    return {
        category: sum(1 for kw in keywords if kw in lowered)
        for category, keywords in _CLUSTERS.items()
    }


def is_multi_intent(text: str) -> bool:
    """True when two or more category clusters are meaningfully present."""
    scores = count_clusters(text)
    return sum(1 for score in scores.values() if score >= 2) >= 2


def looks_non_english(text: str) -> bool:
    words = _WORD.findall(text.lower())
    if len(words) < 5:
        return False
    foreign = sum(1 for w in words if w in _NON_ENGLISH_STOPWORDS)
    english = sum(1 for w in words if w in _ENGLISH_STOPWORDS)
    return foreign > english and foreign >= 3


class HeuristicTriageStrategy:
    """Keyword classification. Total function: it cannot raise, by construction.

    Every branch below terminates in a value and there is no I/O, no parsing and
    no external call anywhere in the path. That is not an accident — the cascade
    has to end somewhere that cannot fail, and this is that place.
    """

    def __init__(
        self,
        assembler: TriageAssembler,
        scanner: SafetyScanner | None = None,
    ) -> None:
        self._assembler = assembler
        self._scanner = scanner or SafetyScanner()

    @property
    def name(self) -> str:
        return "heuristic"

    async def triage(self, ticket: Ticket) -> TriageResult:
        return self.triage_sync(ticket)

    def triage_sync(self, ticket: Ticket) -> TriageResult:
        """Synchronous core. Async wrapper exists only to satisfy the port."""
        text = ticket.text
        verdict = self._scanner.scan(text)

        category = self._classify(text, verdict.spam_suspected)
        priority = self._prioritize(text, category)
        reply = self._reply(verdict.injection_suspected, verdict.spam_suspected)

        signals = TriageSignals(
            body_length=len(ticket.body.strip()),
            multi_intent=is_multi_intent(text),
            non_english=looks_non_english(text),
            fallback_used=True,
            injection_suspected=verdict.injection_suspected,
            spam_suspected=verdict.spam_suspected,
        )
        return self._assembler.assemble(
            category=category,
            priority=priority,
            summary=self._summary(ticket),
            suggested_reply=reply,
            raw_confidence=HEURISTIC_CONFIDENCE,
            signals=signals,
            telemetry=TriageTelemetry(stage=TriageStage.FALLBACK, model="heuristic"),
        )

    # -----------------------------------------------------------------------

    def _classify(self, text: str, spam: bool) -> Category:
        if spam:
            return Category.OTHER
        scores = count_clusters(text)
        best = max(scores, key=lambda c: scores[c])
        # Security wins ties outright: a false negative there is far more
        # expensive than a false positive, and T-014 mentions "report" and
        # "endpoint" which also read as bug vocabulary.
        if scores[Category.SECURITY] > 0:
            return Category.SECURITY
        return best if scores[best] > 0 else Category.OTHER

    def _prioritize(self, text: str, category: Category) -> Priority:
        lowered = text.lower()
        if category is Category.SECURITY:
            return Priority.URGENT
        if any(marker in lowered for marker in _URGENT_MARKERS):
            return Priority.URGENT
        if any(marker in lowered for marker in _LOW_MARKERS):
            return Priority.LOW
        if any(marker in lowered for marker in _HIGH_MARKERS):
            return Priority.HIGH
        return Priority.MEDIUM

    def _reply(self, injection: bool, spam: bool) -> str:
        if injection:
            return INJECTION_SAFE_REPLY
        if spam:
            return SPAM_SAFE_REPLY
        # Neutral acknowledgement. It commits to nothing — no refund, no
        # deadline, no fix — because a degraded classification is exactly when a
        # confident-sounding promise is most dangerous.
        return (
            "Thanks for getting in touch. We've received your message and a "
            "member of the support team will review it and follow up shortly."
        )

    def _summary(self, ticket: Ticket) -> str:
        subject = ticket.subject.strip()
        if subject:
            return f"[auto] {subject}"[:200]
        body = " ".join(ticket.body.split())
        return (
            (f"[auto] {body}"[:197] + "...")
            if len(body) > 190
            else f"[auto] {body}"[:200] or "[auto] Empty message"
        )
