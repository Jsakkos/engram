"""Request coalescing, persistence and explicit retries for AI episode matching."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Update

from app.api import routes
from app.database import get_session
from app.main import app
from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState, TitleState
from tests.unit.conftest import _unit_session_factory

SUGGESTION = {
    "episode": 3,
    "confidence": 0.9,
    "reasoning": "Cargo dialogue.",
    "runner_up": None,
    "model": "current-local-model",
}
PREVIOUS = {**SUGGESTION, "episode": 1, "model": "previous-model"}


@pytest.fixture
async def review_title():
    async with _unit_session_factory() as session:
        job = DiscJob(
            drive_id="TEST:",
            volume_label="TEST_S1D1",
            content_type=ContentType.TV,
            state=JobState.REVIEW_NEEDED,
            detected_title="Example",
            detected_season=1,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        title = DiscTitle(
            job_id=job.id,
            title_index=0,
            duration_seconds=1200,
            state=TitleState.REVIEW,
            match_details=json.dumps({"llm_suggestion": PREVIOUS, "score": 0.7}),
        )
        session.add(title)
        await session.commit()
        await session.refresh(title)
        return job, title


@pytest.fixture
async def client():
    async def session_override():
        async with _unit_session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_session, None)


def request(job, title, *, force=True, joined=None):
    async def get_title(*args):
        if joined is not None:
            joined.set()
        return title

    return routes.llm_match_title(
        title.id,
        force=force,
        job=job,
        session=AsyncMock(get=get_title),
    )


async def saved_details(title_id):
    async with _unit_session_factory() as session:
        title = await session.get(DiscTitle, title_id)
        return json.loads(title.match_details)


@pytest.mark.asyncio
@pytest.mark.parametrize("known_id", [195241, None])
async def test_on_demand_matching_uses_the_confirmed_show_identity(
    review_title, monkeypatch, known_id
):
    from app.core.curator import curator
    from app.matcher import llm_episode_matcher, tmdb_client
    from app.services import config_service, ripping_helpers

    job, title = review_title
    job.detected_title = "Frasier"
    job.tmdb_id = known_id
    monkeypatch.setattr(
        config_service,
        "get_config",
        AsyncMock(
            return_value=SimpleNamespace(
                ai_episode_matching_enabled=True,
                ai_provider="lmstudio",
                ai_api_key="",
                ai_model="test-model",
                ai_local_base_url="http://localhost:1234/v1",
                tmdb_api_key="test",
            )
        ),
    )
    initialize = Mock(return_value=True)
    monkeypatch.setattr(curator, "_ensure_initialized", initialize)
    monkeypatch.setattr(
        curator, "_matcher", SimpleNamespace(transcribe_full=lambda path: "cargo dialogue " * 100)
    )
    resolve = Mock(return_value=987)
    monkeypatch.setattr(tmdb_client, "fetch_show_id", resolve)
    monkeypatch.setattr(ripping_helpers, "find_staging_file", lambda *args: Path("test.mkv"))
    match = AsyncMock(return_value=SimpleNamespace(**SUGGESTION))
    monkeypatch.setattr(llm_episode_matcher, "match_episode_via_llm", match)

    outcome = await routes._run_llm_match_for_title(title=title, job=job)
    assert outcome.suggestion == SUGGESTION
    assert match.await_args.kwargs["tmdb_show_id"] == str(known_id or 987)
    initialize.assert_called_once_with("Frasier", tmdb_id=known_id)
    if known_id:
        resolve.assert_not_called()
    else:
        resolve.assert_called_once_with("Frasier")


@pytest.mark.asyncio
async def test_normal_request_returns_saved_suggestion(client, review_title, monkeypatch):
    job, title = review_title
    run = AsyncMock()
    monkeypatch.setattr(routes, "_run_llm_match_for_title", run)
    response = await client.post(f"/api/jobs/{job.id}/titles/{title.id}/llm-match")
    assert response.status_code == 200
    assert response.json() == {"suggestion": PREVIOUS, "reason": "cached"}
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_force_refresh_replaces_suggestion_and_preserves_other_details(
    client,
    review_title,
    monkeypatch,
):
    job, title = review_title

    async def run(**kwargs):
        # Simulate another operation updating match details during inference.
        async with _unit_session_factory() as session:
            current = await session.get(DiscTitle, title.id)
            details = json.loads(current.match_details)
            details["review_note"] = "keep this"
            current.match_details = json.dumps(details)
            await session.commit()
        return routes.LLMMatchOutcome.ok(SUGGESTION)

    monkeypatch.setattr(routes, "_run_llm_match_for_title", run)
    response = await client.post(f"/api/jobs/{job.id}/titles/{title.id}/llm-match?force=true")
    assert response.status_code == 200
    assert response.json() == {"suggestion": SUGGESTION, "reason": None}
    assert await saved_details(title.id) == {
        "llm_suggestion": SUGGESTION,
        "score": 0.7,
        "review_note": "keep this",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["no_match", "llm_error", "internal_error"])
async def test_failed_refresh_preserves_previous_suggestion(
    client,
    review_title,
    monkeypatch,
    reason,
):
    job, title = review_title
    run = AsyncMock(
        side_effect=RuntimeError("test failure") if reason == "internal_error" else None,
        return_value=routes.LLMMatchOutcome.failed(reason),
    )
    monkeypatch.setattr(routes, "_run_llm_match_for_title", run)
    response = await client.post(f"/api/jobs/{job.id}/titles/{title.id}/llm-match?force=true")
    assert (
        response.status_code == {"no_match": 200, "llm_error": 503, "internal_error": 500}[reason]
    )
    assert response.json()["reason"] == reason
    assert (await saved_details(title.id))["llm_suggestion"] == PREVIOUS


@pytest.mark.asyncio
@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("reason", [None, "no_match", "llm_error", "internal_error"])
async def test_overlapping_requests_share_successes_and_failures(
    review_title,
    monkeypatch,
    force,
    reason,
):
    job, title = review_title
    if not force:
        async with _unit_session_factory() as session:
            current = await session.get(DiscTitle, title.id)
            current.match_details = "{}"
            await session.commit()
    started, release, joined = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def infer(**kwargs):
        started.set()
        await release.wait()
        if reason == "internal_error":
            raise RuntimeError("test failure")
        return (
            routes.LLMMatchOutcome.failed(reason)
            if reason
            else routes.LLMMatchOutcome.ok(SUGGESTION)
        )

    run = AsyncMock(side_effect=infer)
    monkeypatch.setattr(routes, "_run_llm_match_for_title", run)
    tasks = [asyncio.create_task(request(job, title, force=force))]
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        tasks.append(asyncio.create_task(request(job, title, force=force, joined=joined)))
        await asyncio.wait_for(joined.wait(), timeout=5)
        run.assert_awaited_once()
    finally:
        release.set()
        responses = await asyncio.gather(*tasks)

    bodies = [
        json.loads(result.body) if isinstance(result, routes.JSONResponse) else result
        for result in responses
    ]
    assert bodies[0] == bodies[1]
    assert bodies[0]["reason"] == reason
    assert (job.id, title.id) not in routes._llm_match_tasks
    if reason:
        # Errors must not poison the in-flight registry or prevent a later retry.
        run.side_effect = None
        run.return_value = routes.LLMMatchOutcome.ok(SUGGESTION)
        assert await request(job, title, force=force) == {"suggestion": SUGGESTION, "reason": None}
        assert run.await_count == 2


@pytest.mark.asyncio
async def test_forced_retry_does_not_reuse_a_concurrent_cache_lookup(review_title, monkeypatch):
    job, title = review_title
    started, release, joined = asyncio.Event(), asyncio.Event(), asyncio.Event()
    match_and_persist = routes._match_and_persist_llm_suggestion

    async def delayed_lookup(job_id, title_id, *, force):
        if not force:
            started.set()
            await release.wait()
        return await match_and_persist(job_id, title_id, force=force)

    run = AsyncMock(return_value=routes.LLMMatchOutcome.ok(SUGGESTION))
    monkeypatch.setattr(routes, "_match_and_persist_llm_suggestion", delayed_lookup)
    monkeypatch.setattr(routes, "_run_llm_match_for_title", run)
    tasks = [asyncio.create_task(request(job, title, force=False))]
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        tasks.append(asyncio.create_task(request(job, title, force=True, joined=joined)))
        await asyncio.wait_for(joined.wait(), timeout=5)
    finally:
        release.set()
        normal, forced = await asyncio.gather(*tasks)
    assert normal == {"suggestion": PREVIOUS, "reason": "cached"}
    assert forced == {"suggestion": SUGGESTION, "reason": None}
    run.assert_awaited_once()
    assert (job.id, title.id) not in routes._llm_match_tasks


@pytest.mark.asyncio
async def test_different_titles_can_run_independently(review_title, monkeypatch):
    job, first_title = review_title
    async with _unit_session_factory() as session:
        second_title = DiscTitle(job_id=job.id, title_index=1, duration_seconds=1200)
        session.add(second_title)
        await session.commit()
        await session.refresh(second_title)
    both_started, release = asyncio.Event(), asyncio.Event()
    started_ids = set()

    async def infer(*, title, job):
        started_ids.add(title.id)
        if len(started_ids) == 2:
            both_started.set()
        await release.wait()
        return routes.LLMMatchOutcome.ok(SUGGESTION)

    monkeypatch.setattr(routes, "_run_llm_match_for_title", infer)
    tasks = [asyncio.create_task(request(job, title)) for title in (first_title, second_title)]
    try:
        await asyncio.wait_for(both_started.wait(), timeout=5)
    finally:
        release.set()
        results = await asyncio.gather(*tasks)
    assert all(result["suggestion"] == SUGGESTION for result in results)
    assert (job.id, first_title.id) not in routes._llm_match_tasks
    assert (job.id, second_title.id) not in routes._llm_match_tasks


@pytest.mark.asyncio
async def test_cancelled_caller_does_not_cancel_shared_inference(review_title, monkeypatch):
    job, title = review_title
    started, release, joined = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def infer(**kwargs):
        started.set()
        await release.wait()
        return routes.LLMMatchOutcome.ok(SUGGESTION)

    run = AsyncMock(side_effect=infer)
    monkeypatch.setattr(routes, "_run_llm_match_for_title", run)
    first = asyncio.create_task(request(job, title))
    tasks = [first]
    second = None
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        second = asyncio.create_task(request(job, title, joined=joined))
        tasks.append(second)
        await asyncio.wait_for(joined.wait(), timeout=5)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(first, timeout=5)
    finally:
        release.set()
        # Drain every caller even if setup fails before the second one is started.
        await asyncio.gather(*tasks, return_exceptions=True)
    assert second is not None
    result = await second
    assert result == {"suggestion": SUGGESTION, "reason": None}
    run.assert_awaited_once()
    assert (await saved_details(title.id))["llm_suggestion"] == SUGGESTION
    assert (job.id, title.id) not in routes._llm_match_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("detected_title", "Different show"),
        ("detected_season", 2),
        ("tmdb_id", 999),
        ("episode_namespace", "tvdb"),
        ("match_language", "de"),
    ],
)
async def test_does_not_persist_a_suggestion_after_job_identity_changes(
    client,
    review_title,
    monkeypatch,
    field,
    value,
):
    job, title = review_title

    async def infer(**kwargs):
        async with _unit_session_factory() as session:
            current_job = await session.get(DiscJob, job.id)
            setattr(current_job, field, value)
            current_title = await session.get(DiscTitle, title.id)
            current_title.match_details = json.dumps({"identity_updated": True})
            await session.commit()
        return routes.LLMMatchOutcome.ok(SUGGESTION)

    monkeypatch.setattr(routes, "_run_llm_match_for_title", infer)
    response = await client.post(f"/api/jobs/{job.id}/titles/{title.id}/llm-match?force=true")
    assert response.status_code == 200
    assert response.json() == {"suggestion": None, "reason": "context_changed"}
    assert await saved_details(title.id) == {"identity_updated": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("output_filename", "/new-rip.mkv"),
        ("matched_episode", "S01E02"),
        ("state", TitleState.COMPLETED),
    ],
)
async def test_does_not_persist_after_the_file_or_review_decision_changes(
    client,
    review_title,
    monkeypatch,
    field,
    value,
):
    job, title = review_title

    async def infer(**kwargs):
        async with _unit_session_factory() as session:
            current = await session.get(DiscTitle, title.id)
            setattr(current, field, value)
            await session.commit()
        return routes.LLMMatchOutcome.ok(SUGGESTION)

    monkeypatch.setattr(routes, "_run_llm_match_for_title", infer)
    response = await client.post(f"/api/jobs/{job.id}/titles/{title.id}/llm-match?force=true")
    assert response.json() == {"suggestion": None, "reason": "context_changed"}
    assert (await saved_details(title.id))["llm_suggestion"] == PREVIOUS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "saved"),
    [
        pytest.param(("metadata",), True, id="merge-unrelated-metadata"),
        pytest.param(("identity",), False, id="keep-original-job-context"),
        pytest.param(("metadata", "metadata"), False, id="stop-after-two-collisions"),
        pytest.param(("metadata", "identity"), False, id="identity-changes-during-retry"),
        pytest.param(("metadata", "decision"), False, id="decision-changes-during-retry"),
        pytest.param(("metadata", "file"), False, id="file-changes-during-retry"),
    ],
)
async def test_a_write_boundary_collision_preserves_newer_edits(
    client,
    review_title,
    monkeypatch,
    changes,
    saved,
):
    job, title = review_title
    infer = AsyncMock(return_value=routes.LLMMatchOutcome.ok(SUGGESTION))
    monkeypatch.setattr(routes, "_run_llm_match_for_title", infer)
    execute = AsyncSession.execute
    writes = 0

    async def execute_after_change(session, statement, *args, **kwargs):
        nonlocal writes
        if isinstance(statement, Update) and statement.table.name == "disc_titles":
            writes += 1
        if (
            isinstance(statement, Update)
            and statement.table.name == "disc_titles"
            and writes <= len(changes)
        ):
            change = changes[writes - 1]
            # Inject immediately before the guarded write, including on its retry.
            async with _unit_session_factory() as writer:
                if change == "metadata":
                    current = await writer.get(DiscTitle, title.id)
                    details = json.loads(current.match_details)
                    details["review_note"] = f"newer edit {writes}"
                    current.match_details = json.dumps(details)
                elif change == "identity":
                    current = await writer.get(DiscJob, job.id)
                    current.detected_season = 2
                elif change == "decision":
                    current = await writer.get(DiscTitle, title.id)
                    current.matched_episode = "S01E02"
                else:
                    current = await writer.get(DiscTitle, title.id)
                    current.output_filename = "/new-rip.mkv"
                await writer.commit()
        return await execute(session, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", execute_after_change)
    response = await client.post(f"/api/jobs/{job.id}/titles/{title.id}/llm-match?force=true")
    assert response.status_code == 200
    assert response.json() == {
        "suggestion": SUGGESTION if saved else None,
        "reason": None if saved else "context_changed",
    }
    assert writes == 2
    infer.assert_awaited_once()
    details = await saved_details(title.id)
    assert details["llm_suggestion"] == (SUGGESTION if saved else PREVIOUS)
    assert details["score"] == 0.7
    if "metadata" in changes:
        last_edit = max(index + 1 for index, change in enumerate(changes) if change == "metadata")
        assert details["review_note"] == f"newer edit {last_edit}"


@pytest.mark.asyncio
async def test_shared_inference_finishes_when_its_only_caller_disconnects(
    review_title, monkeypatch
):
    job, title = review_title
    started, release = asyncio.Event(), asyncio.Event()

    async def infer(**kwargs):
        started.set()
        await release.wait()
        return routes.LLMMatchOutcome.ok(SUGGESTION)

    monkeypatch.setattr(routes, "_run_llm_match_for_title", infer)
    caller = asyncio.create_task(request(job, title))
    worker = None
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        worker = routes._llm_match_tasks[(job.id, title.id)]
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(caller, timeout=5)
    finally:
        release.set()
        await asyncio.gather(caller, return_exceptions=True)
    assert worker is not None
    result = await asyncio.wait_for(asyncio.shield(worker), timeout=5)
    assert result["suggestion"] == SUGGESTION
    assert (await saved_details(title.id))["llm_suggestion"] == SUGGESTION
    assert (job.id, title.id) not in routes._llm_match_tasks


@pytest.mark.asyncio
async def test_a_hundred_overlapping_callers_share_work_even_when_some_cancel(
    review_title, monkeypatch
):
    job, title = review_title
    joined, release = asyncio.Event(), asyncio.Event()
    callers_started = 0

    class RequestSession:
        async def get(self, *args):
            nonlocal callers_started
            callers_started += 1
            if callers_started == 100:
                joined.set()
            return title

    async def infer(**kwargs):
        await release.wait()
        return routes.LLMMatchOutcome.ok(SUGGESTION)

    run = AsyncMock(side_effect=infer)
    monkeypatch.setattr(routes, "_run_llm_match_for_title", run)
    callers = [
        asyncio.create_task(
            routes.llm_match_title(title.id, force=True, job=job, session=RequestSession())
        )
        for _ in range(100)
    ]
    try:
        await asyncio.wait_for(joined.wait(), timeout=5)
        for caller in callers[::3]:
            caller.cancel()
        await asyncio.gather(*callers[::3], return_exceptions=True)
    finally:
        release.set()
        results = await asyncio.gather(*callers, return_exceptions=True)
    assert sum(isinstance(value, asyncio.CancelledError) for value in results) == 34
    assert (
        sum(isinstance(value, dict) and value["suggestion"] == SUGGESTION for value in results)
        == 66
    )
    run.assert_awaited_once()
    assert not routes._llm_match_tasks


@pytest.mark.asyncio
async def test_deleted_title_is_not_recreated_after_inference(client, review_title, monkeypatch):
    job, title = review_title

    async def infer(**kwargs):
        async with _unit_session_factory() as session:
            current = await session.get(DiscTitle, title.id)
            await session.delete(current)
            await session.commit()
        return routes.LLMMatchOutcome.ok(SUGGESTION)

    monkeypatch.setattr(routes, "_run_llm_match_for_title", infer)
    response = await client.post(f"/api/jobs/{job.id}/titles/{title.id}/llm-match?force=true")
    assert response.status_code == 404
    async with _unit_session_factory() as session:
        assert await session.get(DiscTitle, title.id) is None
