"""Triage cache, plus the Null Object that replaces it when caching is off.

Not optional, and not a performance nicety. Without it every frontend page load
re-runs 30 model calls and every eval iteration costs minutes. Keying on the
prompt version means editing the prompt correctly invalidates everything, which
is what makes a cached eval run trustworthy rather than stale (PRD §7.1 stage 2).

`NullCache` exists so that `CACHE_ENABLED=false` injects a different object
rather than scattering `if self._cache is not None:` through the service. That
removes a branch from the production path and a whole class of test case from the
suite (PRD §6.1, Null Object).
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from app.domain.models import TriageResult

logger = logging.getLogger(__name__)


class NullCache:
    """Implements TriageCache by doing nothing, correctly."""

    async def get(self, key: str) -> TriageResult | None:
        return None

    async def set(self, key: str, result: TriageResult) -> None:
        return None


class FileTriageCache:
    """One JSON file per key, under CACHE_DIR.

    A file per entry rather than one big file: entries are written concurrently
    during an eval run, and independent files mean no lock contention and no
    read-modify-write race. Inspectable with `cat`, which matters when you are
    debugging why a cached answer looks wrong.
    """

    def __init__(self, directory: Path) -> None:
        self._dir = directory
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> TriageResult | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return TriageResult.model_validate(payload)
        except (OSError, ValueError) as exc:
            # A corrupt or stale-schema entry must never break a run. Treat it
            # as a miss and move on; the worst case is one recomputation.
            logger.warning("cache.unreadable_entry", extra={"error_type": type(exc).__name__})
            return None

    async def set(self, key: str, result: TriageResult) -> None:
        path = self._path(key)
        try:
            async with self._lock:
                self._dir.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_text(result.model_dump_json(indent=2), encoding="utf-8")
                tmp.replace(path)
        except OSError as exc:
            # A cache that cannot write is a slow cache, not a broken app.
            logger.warning("cache.write_failed", extra={"error_type": type(exc).__name__})

    def _path(self, key: str) -> Path:
        # `key` is a hex sha256 produced by TriageService, never user input — but
        # it is still validated rather than trusted, because "it can't be
        # attacker-controlled" is a property that outlives nobody's refactor.
        if not key.isalnum():
            raise ValueError("cache key must be alphanumeric")
        return self._dir / f"{key}.json"
