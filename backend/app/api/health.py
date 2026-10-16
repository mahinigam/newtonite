"""Unversioned operational endpoints used by operators and CI."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session

router = APIRouter(tags=["health"])


@router.get("/healthz", summary="Liveness probe")
async def healthz() -> dict[str, str]:
    """Reports that the process is up. Deliberately does not touch the database."""
    return {"status": "ok"}


@router.get("/readyz", summary="Readiness probe")
async def readyz(session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, str]:
    """Reports readiness by proving a database round-trip works."""
    await session.execute(text("SELECT 1"))
    return {"status": "ready", "database": "ok"}
