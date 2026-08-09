"""Read-only access to the inbound corpus.

The domain says "give me T-005"; it does not know a JSON file exists. Swapping
this for Postgres touches one file and no caller (PRD §6.1, Repository).

There is no write method. That is interface segregation doing real work: the
queue's read path is physically incapable of mutating the corpus, so "could a bug
here corrupt tickets.json?" has a structural answer rather than a careful one.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.core.errors import ConfigurationError
from app.domain.models import Ticket

# Ticket IDs are looked up in a dict, never interpolated into a path. This
# pattern is belt-and-braces for the API layer (OWASP API1): even a malformed id
# cannot become a filesystem traversal because no filesystem operation takes one.
TICKET_ID_PATTERN = re.compile(r"^T-\d{3}$")


class JsonTicketRepository:
    """Loads tickets.json once at construction and serves them from memory."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._tickets: dict[str, Ticket] = {}
        self._order: tuple[str, ...] = ()
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConfigurationError(
                f"tickets.json not found at {self._path}. "
                "Set DATA_DIR or run from the repository root."
            ) from exc
        except ValueError as exc:
            raise ConfigurationError(f"tickets.json is not valid JSON: {exc}") from exc

        if not isinstance(raw, list):
            raise ConfigurationError("tickets.json must contain a JSON array")

        tickets = [Ticket.model_validate(item) for item in raw]
        self._tickets = {t.id: t for t in tickets}
        self._order = tuple(t.id for t in tickets)

    def list_all(self) -> tuple[Ticket, ...]:
        """Corpus order is preserved — it is chronological, and a queue that
        reorders itself between loads is a queue nobody trusts."""
        return tuple(self._tickets[tid] for tid in self._order)

    def get(self, ticket_id: str) -> Ticket | None:
        return self._tickets.get(ticket_id)

    def __len__(self) -> int:
        return len(self._tickets)
