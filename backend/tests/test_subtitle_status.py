"""Batch status correctness for pending work, existing tracks and deleted results."""

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import get_session
from app.main import app
from app.models import Base, Job, JobState, SubtitleTrack, Video

CUES = [{"start_ms": 0, "end_ms": 1000, "text": "fixture subtitle"}]


@pytest_asyncio.fixture
async def status_env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/status.db")

    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all(Video(id=i, title=f"Fixture {i}") for i in range(1, 41))
        await session.commit()

    async def dependency():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = dependency
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            yield client, factory, engine
    finally:
        app.dependency_overrides.pop(get_session, None)
        await engine.dispose()


async def track(
    session, video_id=1, *, imported=False, translated=False, source_id=None, lang="en"
):
    row = SubtitleTrack(
        video_id=video_id,
        label="fixture",
        engine="import" if imported else "local",
        language=lang,
        cues=CUES,
        source_track_id=source_id,
        source_cues=CUES if translated else None,
    )
    session.add(row)
    await session.flush()
    return row.id


async def statuses(client, ids=(1,)):
    response = await client.get("/api/subtitle-status", params=[("video_ids", i) for i in ids])
    assert response.status_code == 200, response.text
    return response.json()


async def test_idle_imported_generated_and_translation_only(status_env):
    client, factory, _ = status_env
    async with factory() as session:
        await track(session, 2, imported=True)
        await track(session, 3)
        await track(session, 4, translated=True, lang="zh-CN")
        await session.commit()
    rows = (await statuses(client, [1, 2, 3, 4]))["items"]
    assert rows[0]["generation"]["state"] == "idle"
    assert rows[0]["translation"]["state"] == "idle"
    assert rows[1]["generation"]["state"] == "imported"
    assert rows[1]["imported_count"] == 1 and rows[1]["translated_count"] == 0
    assert rows[2]["generation"]["state"] == "done"
    assert rows[2]["generated_count"] == 1
    assert rows[3]["generation"]["state"] == "idle"
    assert rows[3]["translation"]["state"] == "done"
    assert rows[3]["translated_languages"] == ["zh-CN"]


async def test_translation_survives_deleting_original(status_env):
    client, factory, _ = status_env
    async with factory() as session:
        original = await track(session)
        translated = await track(session, translated=True, source_id=original, lang="zh-CN")
        await track(session, translated=True, source_id=original, lang="ja")
        session.add(
            Job(
                kind="subtitle_translate",
                video_id=1,
                state=JobState.done,
                result={"track_id": translated},
                progress=1,
            )
        )
        await session.commit()
        await session.execute(delete(SubtitleTrack).where(SubtitleTrack.id == original))
        await session.commit()
    row = (await statuses(client))["items"][0]
    assert row["generated_count"] == 0
    assert row["translated_count"] == 2
    assert row["translated_languages"] == ["ja", "zh-CN"]
    assert row["translation"]["state"] == "done"


async def test_running_priority_is_not_hidden_by_new_jobs(status_env):
    client, factory, _ = status_env
    async with factory() as session:
        running = Job(kind="subtitle_generate", video_id=1, state=JobState.running, progress=0.42)
        session.add(running)
        await session.flush()
        running_id = running.id
        session.add_all(
            Job(kind="subtitle_generate", video_id=1, state=JobState.error) for _ in range(510)
        )
        session.add(Job(kind="subtitle_generate", video_id=1, state=JobState.queued))
        session.add(
            Job(
                kind="subtitle_translate",
                video_id=1,
                state=JobState.queued,
                payload={"target_language": "zh-CN"},
            )
        )
        session.add(Job(kind="subtitle_model_download", video_id=2, state=JobState.running))
        await session.commit()
    row, two = (await statuses(client, [1, 2]))["items"]
    assert row["generation"]["job_id"] == running_id
    assert row["generation"]["state"] == "running"
    assert row["generation"]["progress"] == 0.42
    assert row["generation"]["active_count"] == 2
    assert row["translation"]["state"] == "queued"
    assert row["translation"]["target_language"] == "zh-CN"
    assert two["generation"]["state"] == "idle"


@pytest.mark.parametrize("state", ["queued", "running", "error", "canceled", "done"])
async def test_current_job_state_and_existing_track_counts(status_env, state):
    client, factory, _ = status_env
    async with factory() as session:
        tid = await track(session)
        session.add(
            Job(
                kind="subtitle_generate",
                video_id=1,
                state=JobState(state),
                progress=1.5,
                result={"track_id": tid},
                error="provider failed" if state == "error" else None,
            )
        )
        await session.commit()
    row = (await statuses(client))["items"][0]
    assert row["generation"]["state"] == state
    assert row["generation"]["progress"] == 1
    assert row["generation"]["active_count"] == (1 if state in ["queued", "running"] else 0)
    assert row["generated_count"] == 1
    if state == "error":
        assert row["generation"]["error"] == "provider failed"


async def test_deleted_latest_result_does_not_claim_success(status_env):
    client, factory, _ = status_env
    async with factory() as session:
        await track(session)  # An older result remains usable.
        deleted = await track(session)
        session.add(
            Job(
                kind="subtitle_generate",
                video_id=1,
                state=JobState.done,
                progress=1,
                result={"track_id": deleted},
            )
        )
        await session.commit()
        await session.execute(delete(SubtitleTrack).where(SubtitleTrack.id == deleted))
        await session.commit()
    row = (await statuses(client))["items"][0]
    assert row["generation"]["state"] == "missing"
    assert row["generated_count"] == 1


async def test_latest_terminal_failure_beats_older_success(status_env):
    client, factory, _ = status_env
    async with factory() as session:
        tid = await track(session)
        session.add(
            Job(kind="subtitle_generate", video_id=1, state=JobState.done, result={"track_id": tid})
        )
        await session.flush()
        session.add(
            Job(
                kind="subtitle_generate",
                video_id=1,
                state=JobState.error,
                error="latest attempt failed",
            )
        )
        await session.commit()
    row = (await statuses(client))["items"][0]
    assert row["generation"]["state"] == "error"
    assert row["generated_count"] == 1


async def test_batch_is_bounded_and_does_not_fetch_subtitle_text(status_env):
    client, factory, engine = status_env
    async with factory() as session:
        for vid in range(1, 41):
            await track(session, vid)
        await session.commit()
    statements = []

    def record(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    response = await statuses(client, list(range(1, 41)) + [1, 9999])
    event.remove(engine.sync_engine, "before_cursor_execute", record)
    assert len(response["items"]) == 40 and response["missing_ids"] == [9999]
    assert len(statements) == 3
    assert "fixture subtitle" not in str(response)
    assert "payload" not in str(response)


@pytest.mark.parametrize(
    "query",
    [
        "",
        "?video_ids=0",
        "?video_ids=-1",
        "?video_ids=abc",
        "?" + "&".join("video_ids=1" for _ in range(101)),
    ],
)
async def test_invalid_batch_rejected(status_env, query):
    client, _, _ = status_env
    assert (await client.get("/api/subtitle-status" + query)).status_code == 422
