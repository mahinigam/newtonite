"""Aggregates every v1 router under a single mount point."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth, items, teams, users

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(items.router)
api_router.include_router(teams.router)
api_router.include_router(users.router)
