# LLM-match Endpoint Error Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the LLM-match endpoint's coarse `200 {suggestion, reason}`-on-everything contract with a differentiated `reason` taxonomy and honest HTTP status codes (200 for ran-but-no-result, 503 for retryable operational failures incl. LLM-provider errors, 500 for unexpected crashes).

**Architecture:** `_run_llm_match_for_title` returns a small `LLMMatchOutcome(suggestion, reason)` instead of bare `None`, so the endpoint can map each distinct precondition to a status. LLM-provider failures (today swallowed to `None` deep in `complete_json`) are surfaced via an **opt-in** `raise_on_error` flag threaded through `complete_json` + `match_episode_via_llm` and a new `AIProviderError(EngramError)`; existing callers (disc-ID, curator) keep `raise_on_error=False` and are unchanged.

**Tech Stack:** Python 3.11, FastAPI, SQLModel/async SQLite, pytest (`uv run pytest`), ruff. Frontend: TypeScript (comment-only change).

**Spec:** `docs/superpowers/specs/2026-06-06-llm-match-error-contract-design.md`

**Reason → status (the contract being built):**

| `reason` | HTTP | `suggestion` |
|---|---|---|
| `null` (success) / `cached` | 200 | object |
| `ai_disabled`, `not_configured`, `no_show`, `no_season`, `show_not_found`, `no_match` | 200 | `null` |
| `matcher_unavailable`, `transcription_failed`, `llm_error` | 503 | `null` |
| `internal_error` | 500 | `null` |

---

## File Structure

- `backend/app/core/errors.py` — **modify**: add `AIProviderError(EngramError)`.
- `backend/app/core/ai_client.py` — **modify**: add `raise_on_error: bool = False` to `complete_json`; raise `AIProviderError` on HTTP failure / re-raise on unexpected when the flag is set.
- `backend/app/matcher/llm_episode_matcher.py` — **modify**: add `raise_on_error: bool = False` to `match_episode_via_llm`; thread it into `complete_json`; update docstring.
- `backend/app/api/routes.py` — **modify**: add imports (`dataclass`, `JSONResponse`, `AIProviderError`); add `LLMMatchOutcome` + `_LLM_MATCH_RETRYABLE_REASONS`; refactor `_run_llm_match_for_title` (return outcomes, split guards, catch `AIProviderError`) and `llm_match_title` (status mapping).
- `backend/tests/unit/test_ai_client.py` — **modify**: add `raise_on_error` unit tests.
- `backend/tests/unit/test_llm_episode_matcher.py` — **modify**: add flag-threading unit tests.
- `backend/tests/integration/test_workflow.py` — **modify**: update one existing mocked test to the new return type; add real-helper reason-taxonomy tests.
- `frontend/src/api/client.ts` — **modify**: update `LLMMatchResult` / `runLLMMatch` doc-comment only.
- `CHANGELOG.md` — **modify**: `### Changed` bullet under `## [Unreleased]`.

---

## Task 1: `AIProviderError` + `complete_json(raise_on_error=...)`

**Files:**
- Modify: `backend/app/core/errors.py` (after the `DatabaseError` class, ~line 72)
- Modify: `backend/app/core/ai_client.py` (signature ~line 54-62; except blocks ~line 101-108; imports near top)
- Test: `backend/tests/unit/test_ai_client.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_ai_client.py` (the module already defines `_mock_httpx(response_json, status=200)` and imports `patch`):

```python
class TestCompleteJsonRaiseOnError:
    @pytest.mark.asyncio
    async def test_http_error_raises_aiprovidererror_when_flag_set(self):
        from app.core.ai_client import complete_json
        from app.core.errors import AIProviderError

        mock = _mock_httpx({}, status=500)
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            with pytest.raises(AIProviderError):
                await complete_json(
                    prompt="x",
                    schema=None,
                    provider="anthropic",
                    api_key="k",
                    raise_on_error=True,
                )

    @pytest.mark.asyncio
    async def test_http_error_returns_none_by_default(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx({}, status=500)
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(
                prompt="x", schema=None, provider="anthropic", api_key="k"
            )
        assert result is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_ai_client.py -k raise_on_error -q`
Expected: FAIL — `ImportError: cannot import name 'AIProviderError'` (and/or `TypeError: complete_json() got an unexpected keyword argument 'raise_on_error'`).

- [ ] **Step 3a: Add the exception class**

In `backend/app/core/errors.py`, after the `DatabaseError` class (~line 72), add:

```python
class AIProviderError(EngramError):
    """LLM provider call failed.

    Raised when an AI provider request fails at the transport/HTTP layer
    (rate-limit exhausted, auth, out-of-credits, provider 5xx, network). Lets
    callers distinguish a provider failure from "the model ran and was not
    confident" (which stays a plain ``None``/no-match result).
    """

    pass
```

- [ ] **Step 3b: Thread `raise_on_error` through `complete_json`**

In `backend/app/core/ai_client.py`, add the import near the other `app.core` imports at the top of the file:

```python
from app.core.errors import AIProviderError
```

Add the parameter to the signature (after `max_tokens: int = 1024,`):

```python
async def complete_json(
    *,
    prompt: str,
    schema: dict | None,
    provider: str,
    api_key: str,
    model: str | None = None,
    max_tokens: int = 1024,
    raise_on_error: bool = False,
) -> dict | None:
```

Replace the final try/except (currently ~line 101-108):

```python
    try:
        return await _with_429_retry(factory)
    except httpx.HTTPError as e:
        logger.warning("AI provider %s HTTP error: %s", provider, e, exc_info=True)
        return None
    except Exception as e:
        logger.warning("AI provider %s unexpected error: %s", provider, e, exc_info=True)
        return None
```

with:

```python
    try:
        return await _with_429_retry(factory)
    except httpx.HTTPError as e:
        logger.warning("AI provider %s HTTP error: %s", provider, e, exc_info=True)
        if raise_on_error:
            raise AIProviderError(f"{provider} request failed: {e}") from e
        return None
    except Exception as e:
        logger.warning("AI provider %s unexpected error: %s", provider, e, exc_info=True)
        if raise_on_error:
            raise
        return None
```

> Note: the early `return None` paths (empty `api_key`, unknown `provider`) and the
> "valid response but empty/unparseable body" paths are intentionally left untouched —
> those are not transport failures. Re-raising the generic `Exception` (rather than
> wrapping it) keeps genuine code bugs classified as `internal_error`/500 downstream,
> not `llm_error`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_ai_client.py -q`
Expected: PASS (the new tests plus all pre-existing `test_ai_client.py` tests stay green — the default behavior is unchanged).

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/errors.py backend/app/core/ai_client.py backend/tests/unit/test_ai_client.py
git commit -m "feat(ai): opt-in raise_on_error surfaces provider failures as AIProviderError"
```

---

## Task 2: Thread `raise_on_error` through `match_episode_via_llm`

**Files:**
- Modify: `backend/app/matcher/llm_episode_matcher.py` (signature ~line 80-89; `complete_json` call ~line 123-129; docstring ~line 90)
- Test: `backend/tests/unit/test_llm_episode_matcher.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_llm_episode_matcher.py`:

```python
class TestMatchEpisodeViaLlmRaiseOnError:
    @pytest.mark.asyncio
    async def test_raise_on_error_propagates_aiprovidererror(self):
        from unittest.mock import AsyncMock, patch

        from app.core.errors import AIProviderError
        from app.matcher.llm_episode_matcher import match_episode_via_llm

        transcript = "the detective examined the case file carefully " * 20  # >500 chars
        with (
            patch(
                "app.matcher.llm_episode_matcher.fetch_season_episodes",
                return_value=[{"episode_number": 1, "name": "Pilot", "overview": "x"}],
            ),
            patch(
                "app.matcher.llm_episode_matcher.complete_json",
                AsyncMock(side_effect=AIProviderError("boom")),
            ) as mock_cj,
        ):
            with pytest.raises(AIProviderError):
                await match_episode_via_llm(
                    transcript=transcript,
                    show_name="X",
                    season=1,
                    tmdb_show_id="123",
                    ai_provider="gemini",
                    ai_api_key="k",
                    tmdb_api_key="t",
                    raise_on_error=True,
                )
        assert mock_cj.await_args.kwargs["raise_on_error"] is True

    @pytest.mark.asyncio
    async def test_default_threads_false_and_returns_none(self):
        from unittest.mock import AsyncMock, patch

        from app.matcher.llm_episode_matcher import match_episode_via_llm

        transcript = "the detective examined the case file carefully " * 20
        with (
            patch(
                "app.matcher.llm_episode_matcher.fetch_season_episodes",
                return_value=[{"episode_number": 1, "name": "Pilot", "overview": "x"}],
            ),
            patch(
                "app.matcher.llm_episode_matcher.complete_json",
                AsyncMock(return_value=None),
            ) as mock_cj,
        ):
            result = await match_episode_via_llm(
                transcript=transcript,
                show_name="X",
                season=1,
                tmdb_show_id="123",
                ai_provider="gemini",
                ai_api_key="k",
                tmdb_api_key="t",
            )
        assert result is None
        assert mock_cj.await_args.kwargs["raise_on_error"] is False
```

> If `test_llm_episode_matcher.py` does not already `import pytest`, add it at the top.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_llm_episode_matcher.py -k RaiseOnError -q`
Expected: FAIL — `TypeError: match_episode_via_llm() got an unexpected keyword argument 'raise_on_error'`.

- [ ] **Step 3: Add the parameter and thread it**

In `backend/app/matcher/llm_episode_matcher.py`, update the signature (add the last param):

```python
async def match_episode_via_llm(
    *,
    transcript: str,
    show_name: str,
    season: int,
    tmdb_show_id: str,
    ai_provider: str,
    ai_api_key: str,
    tmdb_api_key: str,
    raise_on_error: bool = False,
) -> LLMEpisodeMatch | None:
```

Update the docstring first line block to:

```python
    """Run LLM episode matching. Returns None on no-confident-match or empty response.

    When ``raise_on_error`` is True, a provider/transport failure raises
    ``AIProviderError`` (instead of being swallowed to None by ``complete_json``)
    so callers can distinguish a provider outage from "no confident match".
    """
```

Update the `complete_json` call (~line 123) to pass the flag:

```python
    raw = await complete_json(
        prompt=prompt,
        schema=RESPONSE_SCHEMA,
        provider=ai_provider,
        api_key=ai_api_key,
        max_tokens=512,
        raise_on_error=raise_on_error,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_llm_episode_matcher.py -q`
Expected: PASS (new tests + existing matcher tests stay green; the curator path uses the `False` default and is unaffected).

- [ ] **Step 5: Commit**

```bash
git add backend/app/matcher/llm_episode_matcher.py backend/tests/unit/test_llm_episode_matcher.py
git commit -m "feat(matcher): thread raise_on_error through match_episode_via_llm"
```

---

## Task 3: Endpoint contract — `LLMMatchOutcome`, status mapping, refactored helper

**Files:**
- Modify: `backend/app/api/routes.py` (imports lines 12-34; `_run_llm_match_for_title` ~line 3353; `llm_match_title` ~line 3405)
- Test: `backend/tests/integration/test_workflow.py` (`TestLLMMatchEndpoint`, ~line 856)

- [ ] **Step 1: Write the failing test + a shared seed helper, and update the existing mocked test**

In `backend/tests/integration/test_workflow.py`, add a module-level helper (place it just above `class TestLLMMatchEndpoint:`):

```python
import types


async def _seed_review_title(detected_title="The Expanse", detected_season=1):
    """Create a REVIEW_NEEDED TV job + one REVIEW title; return (job_id, title_id)."""
    from app.database import async_session
    from app.models.disc_job import ContentType, DiscJob, DiscTitle, JobState, TitleState

    async with async_session() as s:
        job = DiscJob(
            drive_id="TEST:",
            volume_label="X_S1D1",
            state=JobState.REVIEW_NEEDED,
            content_type=ContentType.TV,
            detected_title=detected_title,
            detected_season=detected_season,
        )
        s.add(job)
        await s.commit()
        await s.refresh(job)
        title = DiscTitle(
            job_id=job.id,
            title_index=0,
            state=TitleState.REVIEW,
            duration_seconds=1200,
            file_path="/tmp/x.mkv",
        )
        s.add(title)
        await s.commit()
        await s.refresh(title)
        return job.id, title.id


def _fake_config(**overrides):
    """A stand-in AppConfig for the LLM-match helper."""
    base = dict(
        ai_episode_matching_enabled=True,
        ai_api_key="key",
        ai_provider="gemini",
        tmdb_api_key="tmdb",
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)
```

Update the existing `test_returns_suggestion_and_persists` so its `fake_run` returns the new type (replace the existing `fake_run` body, ~line 887-894):

```python
        async def fake_run(**kwargs):
            from app.api.routes import LLMMatchOutcome

            return LLMMatchOutcome.ok(
                {
                    "episode": 4,
                    "confidence": 0.88,
                    "reasoning": "r",
                    "runner_up": None,
                    "model": "gemini-2.5-flash-lite",
                }
            )
```

Add a first new failing test to `TestLLMMatchEndpoint`:

```python
    @pytest.mark.asyncio
    async def test_ai_disabled_returns_200_reason(self, client, setup_db, monkeypatch):
        from unittest.mock import AsyncMock

        job_id, title_id = await _seed_review_title()
        monkeypatch.setattr(
            "app.services.config_service.get_config",
            AsyncMock(return_value=_fake_config(ai_episode_matching_enabled=False)),
        )

        r = await client.post(f"/api/jobs/{job_id}/titles/{title_id}/llm-match")
        assert r.status_code == 200
        assert r.json() == {"suggestion": None, "reason": "ai_disabled"}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/integration/test_workflow.py -k llm -q`
Expected: FAIL — `test_returns_suggestion_and_persists` errors on `ImportError: cannot import name 'LLMMatchOutcome'`, and `test_ai_disabled_returns_200_reason` fails because the endpoint currently returns `reason="no_suggestion"`.

- [ ] **Step 3a: Add imports to `routes.py`**

Add `from dataclasses import dataclass` after `from collections import Counter` (line 12).
Change line 20 to `from fastapi.responses import JSONResponse, StreamingResponse`.
Add `from app.core.errors import AIProviderError` in the `app.core` import block (after line 28).

- [ ] **Step 3b: Add `LLMMatchOutcome` + retryable set**

Immediately above `async def _run_llm_match_for_title` (~line 3353) insert:

```python
@dataclass(frozen=True)
class LLMMatchOutcome:
    """Result of an LLM-match attempt. ``reason is None`` means success."""

    suggestion: dict | None
    reason: str | None

    @classmethod
    def ok(cls, suggestion: dict) -> "LLMMatchOutcome":
        return cls(suggestion=suggestion, reason=None)

    @classmethod
    def failed(cls, reason: str) -> "LLMMatchOutcome":
        return cls(suggestion=None, reason=reason)


# Operational failures the caller may retry (HTTP 503). Every other reason is a
# deterministic config/data outcome returned as 200 with a differentiated reason.
_LLM_MATCH_RETRYABLE_REASONS = frozenset(
    {"matcher_unavailable", "transcription_failed", "llm_error"}
)
```

- [ ] **Step 3c: Refactor `_run_llm_match_for_title`**

Replace the entire body of `_run_llm_match_for_title` with:

```python
async def _run_llm_match_for_title(*, title: "DiscTitle", job: "DiscJob") -> LLMMatchOutcome:
    """Invoke the LLM episode matcher for a single title.

    Returns an :class:`LLMMatchOutcome` whose ``reason`` distinguishes each
    failure mode (``ai_disabled``, ``not_configured``, ``no_show``, ``no_season``,
    ``matcher_unavailable``, ``show_not_found``, ``transcription_failed``,
    ``llm_error``, ``no_match``) or is ``None`` on success.
    """
    from app.core.curator import curator as episode_curator
    from app.matcher.llm_episode_matcher import match_episode_via_llm
    from app.matcher.tmdb_client import fetch_show_id
    from app.services.config_service import get_config

    config = await get_config()
    if not config or not getattr(config, "ai_episode_matching_enabled", False):
        return LLMMatchOutcome.failed("ai_disabled")
    if not config.ai_api_key:
        return LLMMatchOutcome.failed("not_configured")
    if not job.detected_title:
        return LLMMatchOutcome.failed("no_show")
    if not job.detected_season:
        return LLMMatchOutcome.failed("no_season")

    # Make sure the matcher is initialized for the show (so transcribe_full works)
    episode_curator._ensure_initialized(job.detected_title)
    if not episode_curator._matcher:
        return LLMMatchOutcome.failed("matcher_unavailable")

    tmdb_show_id = await asyncio.to_thread(fetch_show_id, job.detected_title)
    if not tmdb_show_id:
        return LLMMatchOutcome.failed("show_not_found")

    transcript = await asyncio.to_thread(
        episode_curator._matcher.transcribe_full, Path(title.file_path)
    )
    if not transcript:
        return LLMMatchOutcome.failed("transcription_failed")

    try:
        suggestion = await match_episode_via_llm(
            transcript=transcript,
            show_name=job.detected_title,
            season=job.detected_season,
            tmdb_show_id=str(tmdb_show_id),
            ai_provider=config.ai_provider,
            ai_api_key=config.ai_api_key,
            tmdb_api_key=config.tmdb_api_key,
            raise_on_error=True,
        )
    except AIProviderError:
        logger.warning(
            "LLM match: provider error for title %s -> llm_error",
            sanitize_log_value(title.id),
        )
        return LLMMatchOutcome.failed("llm_error")

    if not suggestion:
        return LLMMatchOutcome.failed("no_match")
    return LLMMatchOutcome.ok(
        {
            "episode": suggestion.episode,
            "confidence": suggestion.confidence,
            "reasoning": suggestion.reasoning,
            "runner_up": (
                {
                    "episode": suggestion.runner_up.episode,
                    "confidence": suggestion.runner_up.confidence,
                }
                if suggestion.runner_up is not None
                else None
            ),
            "model": suggestion.model,
        }
    )
```

- [ ] **Step 3d: Refactor the endpoint**

Replace the `try/except`-to-`return` tail of `llm_match_title` (currently ~line 3428-3443, from `try:` onward) with:

```python
    try:
        outcome = await _run_llm_match_for_title(title=title, job=job)
    except Exception:
        logger.exception("LLM match endpoint failed for title %s", sanitize_log_value(title_id))
        return JSONResponse(
            status_code=500, content={"suggestion": None, "reason": "internal_error"}
        )

    if outcome.reason in _LLM_MATCH_RETRYABLE_REASONS:
        return JSONResponse(
            status_code=503, content={"suggestion": None, "reason": outcome.reason}
        )

    if outcome.suggestion is None:
        return {"suggestion": None, "reason": outcome.reason}

    # Persist into match_details for refresh durability
    existing["llm_suggestion"] = outcome.suggestion
    title.match_details = json.dumps(existing)
    session.add(title)
    await session.commit()

    return {"suggestion": outcome.suggestion, "reason": None}
```

> `existing` is already computed just above (the cache-hit dedup block) and stays in scope.

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && uv run pytest tests/integration/test_workflow.py -k llm -q`
Expected: PASS — `test_ai_disabled_returns_200_reason`, the updated `test_returns_suggestion_and_persists`, and `test_returns_cached_suggestion_without_re_transcribing` all green.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes.py backend/tests/integration/test_workflow.py
git commit -m "feat(api): differentiated reason taxonomy + 503/500 statuses for llm-match"
```

---

## Task 4: Full reason-taxonomy coverage (real helper, mocked deps)

**Files:**
- Test: `backend/tests/integration/test_workflow.py` (`TestLLMMatchEndpoint`)

- [ ] **Step 1: Add the remaining branch tests**

Append these methods to `TestLLMMatchEndpoint`. They patch the helper's *inner* imports (which re-resolve per call) plus the `episode_curator` singleton's attributes.

```python
    @pytest.mark.asyncio
    async def test_not_configured_returns_200_reason(self, client, setup_db, monkeypatch):
        from unittest.mock import AsyncMock

        job_id, title_id = await _seed_review_title()
        monkeypatch.setattr(
            "app.services.config_service.get_config",
            AsyncMock(return_value=_fake_config(ai_api_key="")),
        )
        r = await client.post(f"/api/jobs/{job_id}/titles/{title_id}/llm-match")
        assert r.status_code == 200
        assert r.json()["reason"] == "not_configured"

    @pytest.mark.asyncio
    async def test_no_show_returns_200_reason(self, client, setup_db, monkeypatch):
        from unittest.mock import AsyncMock

        job_id, title_id = await _seed_review_title(detected_title=None)
        monkeypatch.setattr(
            "app.services.config_service.get_config",
            AsyncMock(return_value=_fake_config()),
        )
        r = await client.post(f"/api/jobs/{job_id}/titles/{title_id}/llm-match")
        assert r.status_code == 200
        assert r.json()["reason"] == "no_show"

    @pytest.mark.asyncio
    async def test_no_season_returns_200_reason(self, client, setup_db, monkeypatch):
        from unittest.mock import AsyncMock

        job_id, title_id = await _seed_review_title(detected_season=None)
        monkeypatch.setattr(
            "app.services.config_service.get_config",
            AsyncMock(return_value=_fake_config()),
        )
        r = await client.post(f"/api/jobs/{job_id}/titles/{title_id}/llm-match")
        assert r.status_code == 200
        assert r.json()["reason"] == "no_season"

    @pytest.mark.asyncio
    async def test_matcher_unavailable_returns_503(self, client, setup_db, monkeypatch):
        from unittest.mock import AsyncMock

        from app.core.curator import curator as episode_curator

        job_id, title_id = await _seed_review_title()
        monkeypatch.setattr(
            "app.services.config_service.get_config",
            AsyncMock(return_value=_fake_config()),
        )
        monkeypatch.setattr(episode_curator, "_ensure_initialized", lambda *a, **k: False)
        monkeypatch.setattr(episode_curator, "_matcher", None)

        r = await client.post(f"/api/jobs/{job_id}/titles/{title_id}/llm-match")
        assert r.status_code == 503
        assert r.json() == {"suggestion": None, "reason": "matcher_unavailable"}

    @pytest.mark.asyncio
    async def test_show_not_found_returns_200_reason(self, client, setup_db, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        from app.core.curator import curator as episode_curator

        job_id, title_id = await _seed_review_title()
        monkeypatch.setattr(
            "app.services.config_service.get_config",
            AsyncMock(return_value=_fake_config()),
        )
        monkeypatch.setattr(episode_curator, "_ensure_initialized", lambda *a, **k: True)
        monkeypatch.setattr(episode_curator, "_matcher", MagicMock())
        monkeypatch.setattr("app.matcher.tmdb_client.fetch_show_id", lambda *a, **k: None)

        r = await client.post(f"/api/jobs/{job_id}/titles/{title_id}/llm-match")
        assert r.status_code == 200
        assert r.json()["reason"] == "show_not_found"

    @pytest.mark.asyncio
    async def test_transcription_failed_returns_503(self, client, setup_db, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        from app.core.curator import curator as episode_curator

        job_id, title_id = await _seed_review_title()
        monkeypatch.setattr(
            "app.services.config_service.get_config",
            AsyncMock(return_value=_fake_config()),
        )
        fake_matcher = MagicMock()
        fake_matcher.transcribe_full.return_value = ""
        monkeypatch.setattr(episode_curator, "_ensure_initialized", lambda *a, **k: True)
        monkeypatch.setattr(episode_curator, "_matcher", fake_matcher)
        monkeypatch.setattr("app.matcher.tmdb_client.fetch_show_id", lambda *a, **k: "123")

        r = await client.post(f"/api/jobs/{job_id}/titles/{title_id}/llm-match")
        assert r.status_code == 503
        assert r.json() == {"suggestion": None, "reason": "transcription_failed"}

    @pytest.mark.asyncio
    async def test_no_match_returns_200_reason(self, client, setup_db, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        from app.core.curator import curator as episode_curator

        job_id, title_id = await _seed_review_title()
        monkeypatch.setattr(
            "app.services.config_service.get_config",
            AsyncMock(return_value=_fake_config()),
        )
        fake_matcher = MagicMock()
        fake_matcher.transcribe_full.return_value = "a long transcript " * 50
        monkeypatch.setattr(episode_curator, "_ensure_initialized", lambda *a, **k: True)
        monkeypatch.setattr(episode_curator, "_matcher", fake_matcher)
        monkeypatch.setattr("app.matcher.tmdb_client.fetch_show_id", lambda *a, **k: "123")
        monkeypatch.setattr(
            "app.matcher.llm_episode_matcher.match_episode_via_llm",
            AsyncMock(return_value=None),
        )

        r = await client.post(f"/api/jobs/{job_id}/titles/{title_id}/llm-match")
        assert r.status_code == 200
        assert r.json()["reason"] == "no_match"

    @pytest.mark.asyncio
    async def test_llm_error_returns_503(self, client, setup_db, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        from app.core.curator import curator as episode_curator
        from app.core.errors import AIProviderError

        job_id, title_id = await _seed_review_title()
        monkeypatch.setattr(
            "app.services.config_service.get_config",
            AsyncMock(return_value=_fake_config()),
        )
        fake_matcher = MagicMock()
        fake_matcher.transcribe_full.return_value = "a long transcript " * 50
        monkeypatch.setattr(episode_curator, "_ensure_initialized", lambda *a, **k: True)
        monkeypatch.setattr(episode_curator, "_matcher", fake_matcher)
        monkeypatch.setattr("app.matcher.tmdb_client.fetch_show_id", lambda *a, **k: "123")
        monkeypatch.setattr(
            "app.matcher.llm_episode_matcher.match_episode_via_llm",
            AsyncMock(side_effect=AIProviderError("boom")),
        )

        r = await client.post(f"/api/jobs/{job_id}/titles/{title_id}/llm-match")
        assert r.status_code == 503
        assert r.json() == {"suggestion": None, "reason": "llm_error"}

    @pytest.mark.asyncio
    async def test_internal_error_returns_500(self, client, setup_db, monkeypatch):
        from unittest.mock import AsyncMock

        job_id, title_id = await _seed_review_title()
        monkeypatch.setattr(
            "app.services.config_service.get_config",
            AsyncMock(side_effect=RuntimeError("boom")),
        )
        r = await client.post(f"/api/jobs/{job_id}/titles/{title_id}/llm-match")
        assert r.status_code == 500
        assert r.json() == {"suggestion": None, "reason": "internal_error"}

    @pytest.mark.asyncio
    async def test_success_via_real_helper_persists(self, client, setup_db, monkeypatch):
        import json
        import types as _types
        from unittest.mock import AsyncMock, MagicMock

        from app.core.curator import curator as episode_curator
        from app.database import async_session
        from app.models.disc_job import DiscTitle

        job_id, title_id = await _seed_review_title()
        monkeypatch.setattr(
            "app.services.config_service.get_config",
            AsyncMock(return_value=_fake_config()),
        )
        fake_matcher = MagicMock()
        fake_matcher.transcribe_full.return_value = "a long transcript " * 50
        monkeypatch.setattr(episode_curator, "_ensure_initialized", lambda *a, **k: True)
        monkeypatch.setattr(episode_curator, "_matcher", fake_matcher)
        monkeypatch.setattr("app.matcher.tmdb_client.fetch_show_id", lambda *a, **k: "123")
        fake_match = _types.SimpleNamespace(
            episode=4, confidence=0.9, reasoning="r", runner_up=None, model="gemini"
        )
        monkeypatch.setattr(
            "app.matcher.llm_episode_matcher.match_episode_via_llm",
            AsyncMock(return_value=fake_match),
        )

        r = await client.post(f"/api/jobs/{job_id}/titles/{title_id}/llm-match")
        assert r.status_code == 200
        body = r.json()
        assert body["reason"] is None
        assert body["suggestion"]["episode"] == 4

        async with async_session() as s:
            refreshed = await s.get(DiscTitle, title_id)
            details = json.loads(refreshed.match_details or "{}")
            assert details["llm_suggestion"]["episode"] == 4
```

- [ ] **Step 2: Run to verify pass**

Run: `cd backend && uv run pytest tests/integration/test_workflow.py -k llm -q`
Expected: PASS — all `TestLLMMatchEndpoint` tests green. (If a branch fails, fix the corresponding `LLMMatchOutcome.failed(...)` in `_run_llm_match_for_title` or the status mapping; do not weaken the assertions.)

- [ ] **Step 3: Commit**

```bash
git add backend/tests/integration/test_workflow.py
git commit -m "test(api): cover full llm-match reason taxonomy via real helper"
```

---

## Task 5: Update the frontend contract comment

**Files:**
- Modify: `frontend/src/api/client.ts` (the `LLMMatchResult` interface + `runLLMMatch` doc-comment, ~lines 68-90)

- [ ] **Step 1: Replace the doc-comment above `LLMMatchResult`**

Replace the single line `/** Shape returned by POST /api/jobs/{job_id}/titles/{title_id}/llm-match */`
(line 68) with:

```typescript
/**
 * Shape returned by `POST /api/jobs/{job_id}/titles/{title_id}/llm-match`.
 *
 * `reason` discriminates the outcome. By HTTP status:
 * - **200** — `runLLMMatch` resolves with this shape. `reason` is one of:
 *   - `null` — success; `suggestion` is populated and persisted server-side.
 *   - `"cached"` — idempotent re-click; cached `suggestion` returned without re-transcribing.
 *   - `"ai_disabled"` — AI episode matching is turned off in config.
 *   - `"not_configured"` — enabled but no AI API key is set.
 *   - `"no_show"` — the job has no detected show title.
 *   - `"no_season"` — the job has no detected season.
 *   - `"show_not_found"` — the show could not be resolved on TMDB.
 *   - `"no_match"` — the model ran but produced no confident episode.
 * - **503** — `runLLMMatch` THROWS `ApiError`; retryable operational failures.
 *   `ApiError.body` carries the same `{ suggestion: null, reason }` JSON, where
 *   `reason` is `"matcher_unavailable"`, `"transcription_failed"`, or `"llm_error"`
 *   (the LLM provider call itself failed — rate-limit/credits/auth/5xx/network).
 * - **500** — `runLLMMatch` THROWS `ApiError`; unexpected server error,
 *   `reason: "internal_error"` (also in `ApiError.body`).
 */
```

> Comment only — do **not** narrow the `reason: string | null` type or add UI logic.
> Rendering these reasons in the Inspector is the sibling frontend follow-up task.

- [ ] **Step 2: Verify nothing else changed**

Run: `git diff --stat frontend/src/api/client.ts`
Expected: one file changed, only the comment region. (A comment-only edit cannot affect
TypeScript compilation; no build needed. If `frontend/node_modules` is present you may
optionally run `cd frontend && npx tsc --noEmit`, but it is not required.)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/client.ts
git commit -m "docs(client): document llm-match reason taxonomy + status mapping"
```

---

## Task 6: Changelog entry

**Files:**
- Modify: `CHANGELOG.md` (under `## [Unreleased]`, line 5)

- [ ] **Step 1: Add a `### Changed` block**

Insert directly after line 5 (`## [Unreleased]`) so the file reads:

```markdown
## [Unreleased]

### Changed

- The review Inspector's "Try LLM match" endpoint now returns a differentiated `reason` (e.g. `ai_disabled`, `not_configured`, `no_season`, `show_not_found`, `transcription_failed`, `no_match`, `llm_error`) and uses HTTP 503 for retryable operational failures (matcher/transcription/LLM-provider errors) and 500 for unexpected errors, instead of reporting every failure as a 200. This lets the UI tell "AI matching is off / couldn't find a match" apart from "the LLM provider failed, retry". (#347 follow-up)

## [0.15.1] - 2026-06-03
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): note llm-match error-contract change"
```

---

## Task 7: Final verification + PR

- [ ] **Step 1: Run the full affected test surface**

Run: `cd backend && uv run pytest tests/unit/test_ai_client.py tests/unit/test_llm_episode_matcher.py tests/integration/test_workflow.py -k "llm or raise_on_error or RaiseOnError" -q`
Expected: PASS, no failures.

- [ ] **Step 2: Lint + format check the changed backend files**

Run: `cd backend && uv run ruff check app/api/routes.py app/core/ai_client.py app/core/errors.py app/matcher/llm_episode_matcher.py tests/integration/test_workflow.py tests/unit/test_ai_client.py tests/unit/test_llm_episode_matcher.py`
Then: `cd backend && uv run ruff format --check app/api/routes.py app/core/ai_client.py app/core/errors.py app/matcher/llm_episode_matcher.py`
Expected: "All checks passed!" / no files would be reformatted. (If format check fails, run the same `ruff format` without `--check`, then re-commit.)

- [ ] **Step 3: Open the PR**

Push the branch and open a PR to `main`. Use the `superpowers:finishing-a-development-branch` skill for the wrap-up. Suggested PR body covers: the new reason taxonomy + status mapping (table), the opt-in `raise_on_error`/`AIProviderError` mechanism (and that disc-ID/curator paths are unchanged), the comment-only frontend contract update, and that the Inspector UI is a separate follow-up. Reference it as a follow-up to [#347].

```bash
git push -u origin claude/quirky-dhawan-299400
gh pr create --base main --title "fix(api): differentiate llm-match endpoint error contract" --body "<as above>"
```

---

## Self-Review

**Spec coverage:**
- Reason taxonomy (8 + cached + null + internal_error) → Tasks 3-4 (each reason has a test). ✓
- Status policy (200 deterministic / 503 retryable incl. `llm_error` / 500 crash) → Task 3 (`_LLM_MATCH_RETRYABLE_REASONS`, endpoint mapping) + Task 4 tests. ✓
- Uniform `{suggestion, reason}` body on 503/500 via `JSONResponse` → Task 3d + asserted in Task 4 (`== {"suggestion": None, "reason": ...}`). ✓
- Cached idempotency preserved → untouched cache block in Task 3d; existing cached test stays green (Task 3 Step 4). ✓
- `logger.exception(...)` preserved → Task 3d keeps it. ✓
- `LLMMatchOutcome` helper return type → Task 3b/3c. ✓
- Opt-in `raise_on_error` + `AIProviderError`, existing callers unchanged → Tasks 1-2 (defaults False; unit tests assert default returns None / threads False). ✓
- Frontend contract comment only → Task 5. ✓
- Changelog `### Changed` → Task 6. ✓
- Verification commands from spec → Task 7. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code; every run step has an expected result. ✓

**Type consistency:** `LLMMatchOutcome.ok()/.failed()` used consistently across Task 3b/3c and the updated `fake_run` (Task 3 Step 1); `raise_on_error` keyword spelled identically in Tasks 1, 2, 3c and the unit-test assertions; `_LLM_MATCH_RETRYABLE_REASONS` contents match the 503 rows in the contract table. ✓
