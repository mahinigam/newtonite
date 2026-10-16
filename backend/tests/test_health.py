"""Liveness and readiness endpoints."""

from __future__ import annotations

from httpx import AsyncClient


async def test_healthz_reports_process_up(client: AsyncClient) -> None:
    response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readyz_proves_a_database_round_trip(client: AsyncClient) -> None:
    response = await client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok"}
