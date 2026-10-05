"""Notification delivery against a real isolated SQLite database."""

from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import get_session
from app.main import app
from app.models import Base, Job, JobState


@pytest_asyncio.fixture
async def env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/jobs.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def dependency():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = dependency
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, factory
    finally:
        app.dependency_overrides.pop(get_session, None)
        await engine.dispose()


async def add(factory, states):
    async with factory() as session:
        rows = [Job(kind="scan_library", state=JobState(s)) for s in states]
        session.add_all(rows)
        await session.commit()
        return [j.id for j in rows]


async def poll(client, cursor=None):
    response = await client.post("/api/jobs/notifications", json=cursor or {})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_bootstrap_silent_keeps_old_active(env):
    client, factory = env
    ids = await add(factory, ["running"] + ["done"] * 550 + ["error", "queued"])
    result = await poll(client)
    assert result["items"] == []
    assert result["cursor"] == ids[-1]
    assert result["pending"] == [ids[0], ids[-1]]


@pytest.mark.asyncio
async def test_old_active_completion_and_no_duplicate(env):
    client, factory = env
    ids = await add(factory, ["running"] + ["done"] * 110)
    cursor = await poll(client)
    async with factory() as session:
        await session.execute(
            update(Job)
            .where(Job.id == ids[0])
            .values(state=JobState.done, finished_at=datetime.now(UTC), result={"created": 3})
        )
        await session.commit()
    result = await poll(client, cursor)
    assert [j["id"] for j in result["items"]] == [ids[0]]
    assert result["items"][0]["result"] == {"created": 3}
    assert result["pending"] == []
    assert (await poll(client, result))["items"] == []


@pytest.mark.asyncio
async def test_fast_jobs_and_queue_error_without_finished_at(env):
    client, factory = env
    cursor = await poll(client)
    ids = await add(factory, ["done", "error", "canceled", "queued", "running"])
    result = await poll(client, cursor)
    assert [j["id"] for j in result["items"]] == ids[:2]
    assert result["pending"] == ids[3:]
    assert (await poll(client, result))["items"] == []


@pytest.mark.asyncio
async def test_burst_pagination_has_no_loss_or_duplicates(env):
    client, factory = env
    cursor = await poll(client)
    ids = await add(factory, ["done"] * 425)
    seen = []
    for expected_more in [True, True, False]:
        cursor = await poll(client, cursor)
        assert cursor["has_more"] is expected_more
        seen.extend(j["id"] for j in cursor["items"])
    assert seen == ids
    assert (await poll(client, cursor))["items"] == []


@pytest.mark.asyncio
async def test_deleted_canceled_and_duplicate_watches_are_pruned(env):
    client, factory = env
    ids = await add(factory, ["running"] * 3 + ["done"])
    cursor = await poll(client)
    cursor["pending"] += [ids[0], 999999]
    async with factory() as session:
        await session.execute(delete(Job).where(Job.id == ids[0]))
        await session.execute(update(Job).where(Job.id == ids[1]).values(state=JobState.canceled))
        await session.execute(update(Job).where(Job.id == ids[2]).values(state=JobState.error))
        await session.commit()
    result = await poll(client, cursor)
    assert result["pending"] == []
    assert [j["id"] for j in result["items"]] == [ids[2]]


@pytest.mark.asyncio
async def test_pending_job_stays_watched_across_new_batches(env):
    client, factory = env
    ids = await add(factory, ["running"])
    cursor = await poll(client)
    await add(factory, ["done"] * 202)
    cursor = await poll(client, cursor)
    assert cursor["pending"] == ids and cursor["has_more"]
    cursor = await poll(client, cursor)
    assert cursor["pending"] == ids and not cursor["has_more"]


@pytest.mark.asyncio
async def test_database_reset_reinitializes_without_old_toasts(env):
    client, factory = env
    ids = await add(factory, ["running", "done"])
    result = await poll(client, {"cursor": 100, "pending": [90]})
    assert result["cursor"] == ids[-1]
    assert result["pending"] == [ids[0]] and result["items"] == []


@pytest.mark.asyncio
async def test_invalid_cursor_rejected(env):
    client, _ = env
    for body in [{"cursor": -1}, {"pending": [0]}, {"pending": [-4]}]:
        assert (await client.post("/api/jobs/notifications", json=body)).status_code == 422
