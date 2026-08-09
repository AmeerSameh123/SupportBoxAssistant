"""Shared fixtures. Everything here is offline, deterministic, and temp-scoped."""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.core.config import Settings
from app.domain.models import Ticket
from app.domain.policy import ConfidenceCalibrator, EscalationPolicy, TriageAssembler
from app.llm.prompt import TriagePromptTemplate
from app.main import create_app
from app.triage.heuristic_strategy import HeuristicTriageStrategy
from app.triage.llm_strategy import LlmTriageStrategy
from app.triage.quality_gate import QualityGate
from app.triage.service import TriageService
from tests.fakes import FakeChatClient, InMemoryCache, InMemoryReviewRepository

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
FIXTURES = Path(__file__).parent / "data"


@pytest.fixture(scope="session")
def malformed_cases() -> list[dict[str, object]]:
    """The Day-0 capture set. `source` distinguishes observed from constructed."""
    payload = json.loads((FIXTURES / "malformed_outputs.json").read_text(encoding="utf-8"))
    return list(payload["cases"])


@pytest.fixture(scope="session")
def real_tickets() -> list[Ticket]:
    raw = json.loads((DATA_DIR / "tickets.json").read_text(encoding="utf-8"))
    return [Ticket.model_validate(item) for item in raw]


@pytest.fixture
def ticket_by_id(real_tickets: list[Ticket]) -> dict[str, Ticket]:
    return {t.id: t for t in real_tickets}


@pytest.fixture
def assembler() -> TriageAssembler:
    return TriageAssembler(EscalationPolicy(0.55), ConfidenceCalibrator())


@pytest.fixture
def heuristic(assembler: TriageAssembler) -> HeuristicTriageStrategy:
    return HeuristicTriageStrategy(assembler)


@pytest.fixture
def fake_client() -> FakeChatClient:
    return FakeChatClient()


@pytest.fixture
def cache() -> InMemoryCache:
    return InMemoryCache()


@pytest.fixture
def service(
    fake_client: FakeChatClient,
    assembler: TriageAssembler,
    heuristic: HeuristicTriageStrategy,
    cache: InMemoryCache,
) -> TriageService:
    """The real TriageService with a fake at the LLM boundary.

    This is the object graph the contract test drives. Everything above the
    ChatClient port is production code; only the network is replaced.
    """
    template = TriagePromptTemplate(nonce_factory=lambda: "testnonce")
    return TriageService(
        primary=LlmTriageStrategy(
            fake_client,
            template=template,
            assembler=assembler,
            max_repair_attempts=2,
            model_name="fake-model",
        ),
        fallback=heuristic,
        gate=QualityGate(min_signal_chars=15),
        assembler=assembler,
        cache=cache,
        prompt_version=template.version,
        model_name="fake-model",
    )


# ---------------------------------------------------------------------------
# API fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    """Real corpus, throwaway review store. Never writes to the repo's data/."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    shutil.copy(DATA_DIR / "tickets.json", data_dir / "tickets.json")
    shutil.copy(DATA_DIR / "labels.json", data_dir / "labels.json")
    return Settings(
        data_dir=data_dir,
        cache_enabled=False,
        app_env="development",
        api_token="",
        rate_limit_per_minute=1000,
    )


@pytest.fixture
def reviews() -> InMemoryReviewRepository:
    return InMemoryReviewRepository()


@pytest.fixture
def client(
    test_settings: Settings,
    service: TriageService,
    reviews: InMemoryReviewRepository,
) -> Iterator[TestClient]:
    """A TestClient whose triage and review layers are fakes.

    Dependency overrides rather than monkeypatching: this is the mechanism the
    composition root was built for, and it means the API tests exercise the real
    routing, validation, middleware and error handling with nothing real behind
    them (PRD §5, D).
    """
    app = create_app(test_settings)
    app.dependency_overrides[deps.get_triage_service] = lambda: service
    app.dependency_overrides[deps.get_review_repository] = lambda: reviews
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def authed_client(
    test_settings: Settings,
    service: TriageService,
    reviews: InMemoryReviewRepository,
) -> Iterator[TestClient]:
    """Same app, with API_TOKEN set, for the authentication tests."""
    settings = test_settings.model_copy(update={"api_token": "test-token-abc"})
    app = create_app(settings)
    app.dependency_overrides[deps.get_triage_service] = lambda: service
    app.dependency_overrides[deps.get_review_repository] = lambda: reviews
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


VALID_DRAFT = json.dumps(
    {
        "category": "billing",
        "priority": "high",
        "summary": "Duplicate charge for June",
        "suggested_reply": "We are looking into the duplicate charge.",
        "confidence": 0.9,
    }
)
