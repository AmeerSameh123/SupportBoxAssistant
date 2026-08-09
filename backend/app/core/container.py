"""The composition root. The one file that knows every concrete class.

Everything else in the codebase receives interfaces. Without this file,
`from app.llm.client import OpenAICompatibleChatClient` would appear in a dozen
modules and dependency inversion would be a diagram rather than a property
(PRD §6.1, Factory / Composition Root).

The object graph, outermost first:

    TriageService
      ├── QualityGate
      ├── FileTriageCache | NullCache          (Null Object)
      ├── LlmTriageStrategy                    (Strategy)
      │     └── ResilientChatClient            (Decorator: retry + breaker)
      │           └── OpenAICompatibleChatClient   (Adapter)
      └── HeuristicTriageStrategy              (Strategy: terminal fallback)
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import Settings
from app.domain.policy import (
    ConfidenceCalibrator,
    EscalationPolicy,
    TriageAssembler,
)
from app.llm.client import OpenAICompatibleChatClient
from app.llm.prompt import TriagePromptTemplate
from app.llm.resilience import CircuitBreaker, ResilientChatClient
from app.storage.cache import FileTriageCache, NullCache
from app.storage.review_repository import JsonReviewRepository
from app.storage.ticket_repository import JsonTicketRepository
from app.triage.heuristic_strategy import HeuristicTriageStrategy
from app.triage.llm_strategy import LlmTriageStrategy
from app.triage.quality_gate import QualityGate
from app.triage.safety import SafetyScanner
from app.triage.service import TriageService

logger = logging.getLogger(__name__)


class Container:
    """Builds the object graph once and owns the lifecycle of what it created."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

        self._http = httpx.AsyncClient(timeout=settings.llm_timeout_seconds)
        self.chat_client_raw = OpenAICompatibleChatClient(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
            max_response_bytes=settings.llm_max_response_bytes,
            http_client=self._http,
        )
        self.breaker = CircuitBreaker(
            threshold=settings.llm_breaker_threshold,
            cooldown_seconds=settings.llm_breaker_cooldown_seconds,
        )
        self.chat_client = ResilientChatClient(
            self.chat_client_raw,
            max_retries=settings.llm_max_retries,
            concurrency=settings.llm_concurrency,
            breaker=self.breaker,
        )

        self.assembler = TriageAssembler(
            EscalationPolicy(settings.escalate_confidence_threshold),
            ConfidenceCalibrator(settings.confidence_mode),
        )
        self.scanner = SafetyScanner()
        self.template = TriagePromptTemplate()

        self.ticket_repository = JsonTicketRepository(settings.tickets_path)
        self.review_repository = JsonReviewRepository(settings.reviews_path)
        self.cache = FileTriageCache(settings.cache_dir) if settings.cache_enabled else NullCache()

        self.triage_service = TriageService(
            primary=LlmTriageStrategy(
                self.chat_client,
                template=self.template,
                assembler=self.assembler,
                scanner=self.scanner,
                response_format=settings.llm_response_format,
                temperature=settings.llm_temperature,
                seed=settings.llm_seed,
                max_tokens=settings.llm_max_tokens,
                max_repair_attempts=settings.llm_max_retries,
                model_name=settings.llm_model,
            ),
            fallback=HeuristicTriageStrategy(self.assembler, self.scanner),
            gate=QualityGate(settings.min_signal_chars),
            assembler=self.assembler,
            cache=self.cache,
            scanner=self.scanner,
            prompt_version=self.template.version,
            model_name=settings.llm_model,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def llm_reachable(self) -> bool:
        """Used by /readyz. Cheap, and never raises into the handler."""
        try:
            response = await self._http.get(
                f"{self.settings.llm_base_url}/models",
                headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                timeout=3.0,
            )
        except httpx.HTTPError:
            return False
        return response.status_code < 500
