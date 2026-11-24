import os
from pathlib import Path
from uuid import uuid4

import asyncpg
import httpx
import pytest

from external_stubs.app.main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def stubs(tmp_path, monkeypatch):
    monkeypatch.setenv("STUBS_DB_PATH", str(tmp_path / "ledger.sqlite3"))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://stubs") as client:
            yield client


@pytest.fixture
async def db():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run the PostgreSQL tests")

    schema = f"worker_test_{uuid4().hex}"
    admin = await asyncpg.connect(url)
    try:
        await admin.execute(f"CREATE SCHEMA {schema}")
        async with asyncpg.create_pool(url, min_size=1, max_size=8,
                                       server_settings={"search_path": schema}) as pool:
            migration = Path(__file__).resolve().parents[1] / "migrations" / "001_init.sql"
            await pool.execute(migration.read_text())
            yield pool
    finally:
        await admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await admin.close()
