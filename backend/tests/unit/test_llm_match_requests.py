"""Request coalescing, persistence and explicit retries for AI episode matching."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

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
            await first
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
