"""The pre-LLM screen. Refusing to guess is a product decision, not a shortcut.

Sending an empty string to a language model and asking for a category invites
confident nonsense — and the model obliges: in the Day-0 spike, T-030 (empty
body) produced a malformed schema echo rather than an answer.

The gate is deliberately CONSERVATIVE. A false negative costs one wasted LLM call
worth about 1.5 seconds. A false positive silently drops a real customer's ticket
into "insufficient information". Those are not symmetric, so the rules below only
fire when there is no plausible signal at all (PRD §7.1 stage 1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.models import Ticket

# Tokens that carry no information about a support issue. Kept deliberately
# small: "hello" and "hi" are NOT here, because "hi, I can't log in" is a real
# ticket and a greeting is not evidence of junk.
_JUNK_TOKENS = frozenset(
    {
        "test",
        "testing",
        "tests",
        "ignore",
        "ignored",
        "asdf",
        "asdfgh",
        "qwerty",
        "foo",
        "bar",
        "baz",
        "lorem",
        "ipsum",
        "dummy",
        "sample",
        "blah",
        "xxx",
        "aaa",
    }
)

_TOKEN = re.compile(r"[a-z0-9']+")
_CONSONANT_RUN = re.compile(r"[bcdfghjklmnpqrstvwxz]{4,}")

MIN_KEYBOARD_MASH_LENGTH = 7


@dataclass(frozen=True, slots=True)
class GateVerdict:
    """Why the gate fired. `reason` is surfaced to the reviewer, not swallowed."""

    reason: str
    summary: str


INSUFFICIENT_REPLY = "Insufficient information - human review required."


class QualityGate:
    def __init__(self, min_signal_chars: int = 15) -> None:
        self._min_chars = min_signal_chars

    def assess(self, ticket: Ticket) -> GateVerdict | None:
        """Return a verdict when the ticket should skip the LLM, else None."""
        subject = ticket.subject.strip()
        body = ticket.body.strip()

        if not subject and not body:
            return GateVerdict(
                reason="empty_message",
                summary="Message has no subject and no body.",
            )

        combined = f"{subject} {body}".strip()
        if len(combined) < self._min_chars:
            return GateVerdict(
                reason="below_min_signal_chars",
                summary=f"Message is only {len(combined)} characters long.",
            )

        if self._is_keyboard_mash(body):
            return GateVerdict(
                reason="no_lexical_content",
                summary="Message appears to be a test or keyboard mash.",
            )

        return None

    def _is_keyboard_mash(self, body: str) -> bool:
        """True only when every token is either junk or non-lexical.

        Two conditions, both required, because either alone over-fires: a real
        ticket can contain the word "test" ("the test environment is down"), and
        a real ticket can contain an unusual product name. Requiring at least one
        mashed token AND no informative tokens is what keeps T-018
        ("doesnt work anymore. please fix asap") and T-023 ("nvm figured it out")
        out of the gate — both are low-information but genuinely real.
        """
        tokens = _TOKEN.findall(body.lower())
        if not tokens:
            return False

        mashed = 0
        for token in tokens:
            if token in _JUNK_TOKENS:
                continue
            if self._looks_mashed(token):
                mashed += 1
                continue
            return False  # an informative token: not junk

        return mashed >= 1

    @staticmethod
    def _looks_mashed(token: str) -> bool:
        """Heuristic for keyboard mash: long, alphabetic, with a consonant run.

        "asdkjhasd" contains the run "sdkjh". English words essentially never
        carry four consecutive consonants outside compounds, so this is a cheap
        proxy for "not a word" that needs no dictionary shipped in the repo.
        """
        return (
            len(token) >= MIN_KEYBOARD_MASH_LENGTH
            and token.isalpha()
            and bool(_CONSONANT_RUN.search(token))
        )
