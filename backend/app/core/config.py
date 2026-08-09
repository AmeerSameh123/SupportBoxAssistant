"""Configuration. The only module in the codebase that reads the environment.

Everything else receives a `Settings` instance. That is not stylistic: a second
`os.environ` read somewhere in the triage layer would be untestable, invisible in
`.env.example`, and would defeat the startup validation below (PRD §13).

Startup validation matters more than it looks. A typo in LLM_BASE_URL should fail
at boot with a readable message, not at ticket 17 with a connection error.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.enums import ConfidenceMode, ResponseFormatMode

# backend/app/core/config.py -> backend/app/core -> backend/app -> backend -> repo
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Every knob, with the default that makes `make dev` work out of the box."""

    model_config = SettingsConfigDict(
        # Both locations work: running from backend/ (uvicorn) and from the repo
        # root (eval). A quickstart that depends on the reader's cwd is broken.
        env_file=(REPO_ROOT / ".env", Path(".env")),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- LLM ---------------------------------------------------------------
    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = "llama3.2:3b"
    # Ollama requires the field and ignores the value. A real provider needs a
    # real key, which is why this is read from the environment and never logged.
    llm_api_key: str = "ollama"
    # DEFAULT CHANGED ON EVIDENCE, not on documentation. The Day-0 spike found
    # that Ollama 0.32.6 *does* honour OpenAI's json_schema variant: it fixed
    # 4/4 of the reproducible SCHEMA_ECHO failures and ran ~2x faster on them.
    # The upstream issue that says otherwise (ollama#10001) was filed against
    # v0.6.2 in March 2025 and no longer describes current behaviour.
    # The repair layer remains the guarantee — see ADR-0002.
    llm_response_format: ResponseFormatMode = ResponseFormatMode.JSON_SCHEMA
    llm_timeout_seconds: float = Field(default=45.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_concurrency: int = Field(default=2, ge=1, le=32)
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    llm_seed: int | None = 42
    llm_max_tokens: int = Field(default=700, ge=64, le=4096)
    llm_max_response_bytes: int = Field(default=65_536, ge=1024)

    # Circuit breaker (PRD §7.1). Without it, a stopped Ollama turns a 30-ticket
    # eval into 30 x 3 timeouts.
    llm_breaker_threshold: int = Field(default=3, ge=1)
    llm_breaker_cooldown_seconds: float = Field(default=30.0, gt=0)

    # --- Triage policy -----------------------------------------------------
    escalate_confidence_threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    min_signal_chars: int = Field(default=15, ge=0)
    confidence_mode: ConfidenceMode = ConfidenceMode.SELF_REPORTED

    # --- Storage -----------------------------------------------------------
    data_dir: Path = REPO_ROOT / "data"
    cache_enabled: bool = True
    cache_dir: Path = REPO_ROOT / ".cache" / "triage"

    # --- API ---------------------------------------------------------------
    app_env: str = "development"
    api_token: str = ""
    frontend_origin: str = "http://localhost:5173"
    max_request_bytes: int = Field(default=32_768, ge=1024)
    rate_limit_per_minute: int = Field(default=60, ge=1)
    log_level: str = "INFO"

    # -----------------------------------------------------------------------
    # Validation
    # -----------------------------------------------------------------------

    @field_validator("llm_base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        """SSRF guard at the only place a URL enters this system (OWASP API7).

        LLM_BASE_URL is operator configuration, never request input — no endpoint
        anywhere accepts a URL. This validator exists so a malformed value is a
        startup failure rather than an outbound request to somewhere unintended.
        """
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError(f"LLM_BASE_URL must be http or https, got {parsed.scheme!r}")
        if not parsed.netloc:
            raise ValueError("LLM_BASE_URL must include a host")
        return value.rstrip("/")

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(allowed)}")
        return upper

    @model_validator(mode="after")
    def _validate_production(self) -> Settings:
        """Refuse to start an unauthenticated production deployment.

        A default that is safe in development and unsafe in production is how
        misconfiguration happens. Making it a startup error removes the choice.
        """
        if self.is_production and not self.api_token:
            raise ValueError(
                "API_TOKEN is required when APP_ENV=production. "
                "Set it, or run with APP_ENV=development."
            )
        return self

    # -----------------------------------------------------------------------
    # Derived
    # -----------------------------------------------------------------------

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def auth_enabled(self) -> bool:
        """A token that is not set is auth disabled. Explicit, and logged at
        startup so nobody assumes protection that is not there."""
        return bool(self.api_token)

    @property
    def docs_url(self) -> str | None:
        """OpenAPI docs are a development affordance and a production
        information leak (PRD §10, API8)."""
        return None if self.is_production else "/docs"

    @property
    def openapi_url(self) -> str | None:
        return None if self.is_production else "/openapi.json"

    @property
    def tickets_path(self) -> Path:
        return self.data_dir / "tickets.json"

    @property
    def labels_path(self) -> Path:
        return self.data_dir / "labels.json"

    @property
    def reviews_path(self) -> Path:
        return self.data_dir / "reviews.json"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached so the environment is read once per process.

    Tests clear the cache rather than mutating the environment, which keeps them
    order-independent.
    """
    return Settings()
