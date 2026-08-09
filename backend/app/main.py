"""The application factory.

A factory rather than a module-level `app = FastAPI()` so tests can build an
instance with different settings and overridden dependencies without touching
the environment or import order.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.container import Container
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.security import (
    BodySizeLimitMiddleware,
    RateLimitMiddleware,
    RequestContextMiddleware,
    TokenBucketRateLimiter,
)

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = Container(settings)
        app.state.container = container
        logger.info(
            "app.startup",
            extra={
                "env": settings.app_env,
                "model": settings.llm_model,
                "response_format": settings.llm_response_format.value,
                "tickets_loaded": len(container.ticket_repository),
                "cache_enabled": settings.cache_enabled,
                # Logged loudly on purpose: nobody should assume protection that
                # is not configured.
                "auth_enabled": settings.auth_enabled,
            },
        )
        try:
            yield
        finally:
            await container.aclose()
            logger.info("app.shutdown")

    app = FastAPI(
        title="Support Inbox Assistant",
        version="0.1.0",
        summary="First-pass triage for a support inbox, with a human in the loop.",
        lifespan=lifespan,
        # Disabled in production: interactive docs are a development affordance
        # and a production information leak (OWASP API8).
        docs_url=settings.docs_url,
        redoc_url=None,
        openapi_url=settings.openapi_url,
    )

    # Middleware executes in reverse registration order, so the outermost layer
    # is registered last. Ordering matters here: the body cap must run before
    # anything reads a body, and the request context must wrap everything so
    # even a rejected request carries a correlation ID.
    app.add_middleware(
        RateLimitMiddleware,
        limiter=TokenBucketRateLimiter(settings.rate_limit_per_minute),
    )
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(
        CORSMiddleware,
        # Never "*", and never "*" with credentials. The dev frontend is proxied
        # through Vite anyway, so the demo path needs no CORS at all.
        allow_origins=[settings.frontend_origin],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app, debug=not settings.is_production)
    app.include_router(api_router)
    return app


app = create_app()
