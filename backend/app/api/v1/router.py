"""Assembles v1. One place to see every route the application exposes.

Explicit assembly rather than filesystem auto-discovery: OWASP API9 is
"improper inventory management", and a router you can read top to bottom is an
inventory. Magic route registration is how undocumented endpoints survive.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import health, reviews, tickets, triage

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(tickets.router)
api_router.include_router(triage.router)
api_router.include_router(reviews.router)
