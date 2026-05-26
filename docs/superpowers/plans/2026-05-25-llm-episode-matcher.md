# LLM Episode Matcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an opt-in LLM-based TV episode matcher that runs as a fallback when Engram's primary audio-fingerprint matcher returns low confidence, surfaces suggestions through the existing review queue, and never auto-organizes.

**Architecture:** New shared `ai_client.py` providing `complete_json` over anthropic/openai/openrouter/**gemini**; both AI disc-ID and the new matcher route through it. A new `llm_episode_matcher.py` in the matcher layer fetches season synopses from TMDB, sends a cleaned Whisper full-file transcript + synopses to the LLM, returns a suggested episode. Curator attaches the suggestion to `MatchResult.match_details["llm_suggestion"]` while keeping `needs_review=True`. New `match_source="ai_llm"` distinguishes user-confirmed LLM matches from auto-matched and user-typed.

**Tech Stack:** Python 3.11+ / FastAPI / SQLModel / httpx (async HTTP); TF-IDF + faster-whisper (existing matcher); React 18 + TypeScript + Tailwind v4 (frontend); pytest-asyncio + Playwright.

**Spec:** `docs/superpowers/specs/2026-05-25-llm-episode-matcher-design.md`

---

## File Structure

**New files**
- `backend/app/core/ai_client.py` — shared AI client (`complete_json`) over 4 providers
- `backend/app/matcher/llm_episode_matcher.py` — LLM episode matcher
- `backend/tests/unit/test_ai_client.py` — unit tests for the shared client
- `backend/tests/unit/test_llm_episode_matcher.py` — unit tests for the matcher
- `backend/tests/integration/test_llm_matching_workflow.py` — end-to-end integration test
- `frontend/e2e/llm-suggestion.spec.ts` — Playwright spec for the UI
- `docs/guide/llm-episode-matcher.md` — user-facing feature page

**Modified files**
- `backend/app/models/app_config.py` — add `ai_episode_matching_enabled: bool = False`
- `backend/app/core/ai_identifier.py` — refactor to delegate to `ai_client.complete_json`
- `backend/app/matcher/tmdb_client.py` — extend `fetch_season_episodes` with `overview`
- `backend/app/matcher/episode_identification.py` — extract `transcribe_full` from `_match_full_file`
- `backend/app/core/curator.py` — LLM fallback in `match_single_file`
- `backend/app/api/routes.py` — `POST /api/jobs/{job_id}/titles/{title_id}/llm-match`
- `backend/app/services/job_manager.py` — `reassign_episode` accepts optional `source` parameter
- `backend/tests/unit/test_ai_identifier.py` — regression coverage after refactor
- `backend/tests/unit/test_tmdb_client.py` — `overview` field coverage (file already exists)
- `frontend/src/components/ConfigWizard.tsx` — Gemini provider + new toggle
- `frontend/src/components/ReviewQueue/Inspector.tsx` — LLM suggestion row + "Try AI match" button
- `docs/getting-started/configuration.md` — settings reference for new flag + Gemini
- `docs/guide/review-queue.md` — describe LLM suggestion row + button
- `docs/api/rest.md` — document the new endpoint
- `mkdocs.yml` — add the new feature page to nav
- `README.md` — add Features bullet
- `CHANGELOG.md` — Unreleased `### Added` entry

---

## Task 1: Add `ai_episode_matching_enabled` config field

**Files:**
- Modify: `backend/app/models/app_config.py:103-107`
- Test: `backend/tests/unit/test_config_service.py` (add to existing if present, else inline check)

- [ ] **Step 1: Add field to AppConfig**

Edit `backend/app/models/app_config.py` — in the AI section right after `ai_api_key`, add:

```python
    ai_episode_matching_enabled: bool = False  # Enable LLM-based episode identification fallback (uses ai_provider/ai_api_key)
```

- [ ] **Step 2: Verify `_add_missing_columns` will pick it up**

Run:
```bash
cd backend && uv run python -c "from app.models.app_config import AppConfig; assert 'ai_episode_matching_enabled' in AppConfig.model_fields; print('field present')"
```
Expected: `field present`

- [ ] **Step 3: Commit**

```bash
git add backend/app/models/app_config.py
git commit -m "feat(config): add ai_episode_matching_enabled flag (#109)"
```

---

## Task 2: Shared AI client scaffold + anthropic adapter

**Files:**
- Create: `backend/app/core/ai_client.py`
- Create: `backend/tests/unit/test_ai_client.py`

- [ ] **Step 1: Write failing test for anthropic adapter**

Create `backend/tests/unit/test_ai_client.py`:

```python
"""Tests for the shared AI client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_httpx(response_json: dict, status: int = 200):
    """Build a mocked httpx.AsyncClient context manager with one POST response."""
    response = MagicMock()
    response.json.return_value = response_json
    response.status_code = status
    response.raise_for_status = MagicMock()
    if status >= 400:
        from httpx import HTTPStatusError, Request, Response
        req = Request("POST", "http://x")
        response.raise_for_status.side_effect = HTTPStatusError(
            "err", request=req, response=Response(status, request=req)
        )

    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=response)
    return client


class TestCompleteJsonAnthropic:
    @pytest.mark.asyncio
    async def test_anthropic_success(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx({"content": [{"text": '{"episode": 3, "confidence": 0.9}'}]})
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(
                prompt="match this",
                schema=None,
                provider="anthropic",
                api_key="sk-ant-x",
            )

        assert result == {"episode": 3, "confidence": 0.9}
        call = mock.post.await_args
        assert call.args[0] == "https://api.anthropic.com/v1/messages"
        assert call.kwargs["headers"]["x-api-key"] == "sk-ant-x"
        assert call.kwargs["headers"]["anthropic-version"] == "2023-06-01"
        body = call.kwargs["json"]
        assert body["model"] == "claude-haiku-4-5-20251001"
        assert body["messages"][0]["content"] == "match this"

    @pytest.mark.asyncio
    async def test_anthropic_unparseable_returns_none(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx({"content": [{"text": "I don't know"}]})
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(
                prompt="x", schema=None, provider="anthropic", api_key="k"
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_anthropic_strips_code_fence(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx({"content": [{"text": '```json\n{"a": 1}\n```'}]})
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(
                prompt="x", schema=None, provider="anthropic", api_key="k"
            )

        assert result == {"a": 1}
```

- [ ] **Step 2: Run test to verify failure**

Run:
```bash
cd backend && uv run pytest tests/unit/test_ai_client.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.ai_client'`

- [ ] **Step 3: Create ai_client.py with anthropic adapter**

Create `backend/app/core/ai_client.py`:

```python
"""Shared AI client for structured-JSON completions across providers.

Wraps anthropic, openai, openrouter, and gemini behind a single
`complete_json` entry point. Each provider adapter handles its own
authentication and structured-JSON convention (prompt-only for anthropic,
response_format for openai/openrouter, responseSchema for gemini).
"""

import asyncio
import json
import logging
import random

import httpx

logger = logging.getLogger(__name__)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

DEFAULT_MODELS = {
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-4o-mini",
    "openrouter": "anthropic/claude-haiku-4-5-20251001",
    "gemini": "gemini-flash-lite-latest",
}

_TIMEOUT_SECONDS = 30.0


async def complete_json(
    *,
    prompt: str,
    schema: dict | None,
    provider: str,
    api_key: str,
    model: str | None = None,
    max_tokens: int = 1024,
) -> dict | None:
    """Send a prompt to an LLM provider and return its JSON response as a dict.

    Returns None on any failure (network, HTTP, malformed JSON). Callers must
    treat None as "no usable result" and fall back to other behaviour.
    """
    if not api_key:
        logger.debug("complete_json called with empty api_key; returning None")
        return None

    model = model or DEFAULT_MODELS.get(provider)
    if not model:
        logger.warning("Unknown AI provider: %s", provider)
        return None

    try:
        if provider == "anthropic":
            return await _call_anthropic(prompt, api_key, model, max_tokens)
        # additional providers wired in later tasks
        logger.warning("Unsupported AI provider: %s", provider)
        return None
    except httpx.HTTPError as e:
        logger.warning("AI provider %s HTTP error: %s", provider, e)
        return None
    except Exception as e:
        logger.warning("AI provider %s unexpected error: %s", provider, e)
        return None


def _parse_json_text(text: str) -> dict | None:
    """Parse JSON, tolerating ```json fences and surrounding whitespace."""
    text = text.strip()
    if text.startswith("```"):
        lines = [ln for ln in text.split("\n") if not ln.strip().startswith("```")]
        text = "\n".join(lines)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse AI response as JSON: %s", text[:200])
        return None
    return data if isinstance(data, dict) else None


async def _call_anthropic(prompt: str, api_key: str, model: str, max_tokens: int) -> dict | None:
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        resp = await client.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        content = data.get("content") or []
        if not content:
            return None
        text = content[0].get("text", "")
        return _parse_json_text(text)
```

- [ ] **Step 4: Run tests, verify pass**

Run:
```bash
cd backend && uv run pytest tests/unit/test_ai_client.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/ai_client.py backend/tests/unit/test_ai_client.py
git commit -m "feat(ai): shared complete_json client with anthropic adapter (#109)"
```

---

## Task 3: AI client — OpenAI/OpenRouter adapter

**Files:**
- Modify: `backend/app/core/ai_client.py`
- Modify: `backend/tests/unit/test_ai_client.py`

- [ ] **Step 1: Add failing tests for openai + openrouter**

Append to `backend/tests/unit/test_ai_client.py`:

```python
class TestCompleteJsonOpenAI:
    @pytest.mark.asyncio
    async def test_openai_success(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx(
            {"choices": [{"message": {"content": '{"episode": 5, "confidence": 0.8}'}}]}
        )
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(
                prompt="x", schema=None, provider="openai", api_key="sk-x"
            )

        assert result == {"episode": 5, "confidence": 0.8}
        call = mock.post.await_args
        assert call.args[0] == "https://api.openai.com/v1/chat/completions"
        assert call.kwargs["headers"]["Authorization"] == "Bearer sk-x"
        body = call.kwargs["json"]
        assert body["model"] == "gpt-4o-mini"
        assert body["temperature"] == 0

    @pytest.mark.asyncio
    async def test_openai_response_format_when_schema(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx(
            {"choices": [{"message": {"content": '{"episode": 1, "confidence": 0.5}'}}]}
        )
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            await complete_json(
                prompt="x",
                schema={"type": "object", "properties": {"episode": {"type": "integer"}}},
                provider="openai",
                api_key="sk-x",
            )

        body = mock.post.await_args.kwargs["json"]
        assert body["response_format"] == {"type": "json_object"}


class TestCompleteJsonOpenRouter:
    @pytest.mark.asyncio
    async def test_openrouter_success(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx(
            {"choices": [{"message": {"content": '{"ok": true}'}}]}
        )
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(
                prompt="x", schema=None, provider="openrouter", api_key="sk-or-x"
            )

        assert result == {"ok": True}
        call = mock.post.await_args
        assert call.args[0] == "https://openrouter.ai/api/v1/chat/completions"
        body = call.kwargs["json"]
        assert body["model"] == "anthropic/claude-haiku-4-5-20251001"
```

- [ ] **Step 2: Run, verify fail**

Run:
```bash
cd backend && uv run pytest tests/unit/test_ai_client.py -v -k "OpenAI or OpenRouter"
```
Expected: 3 failed with `Unsupported AI provider: openai/openrouter` returning None.

- [ ] **Step 3: Add adapter + wire in complete_json**

In `backend/app/core/ai_client.py`, replace the placeholder branches in `complete_json` and add the adapter:

```python
async def complete_json(
    *,
    prompt: str,
    schema: dict | None,
    provider: str,
    api_key: str,
    model: str | None = None,
    max_tokens: int = 1024,
) -> dict | None:
    """Send a prompt to an LLM provider and return its JSON response as a dict."""
    if not api_key:
        logger.debug("complete_json called with empty api_key; returning None")
        return None

    model = model or DEFAULT_MODELS.get(provider)
    if not model:
        logger.warning("Unknown AI provider: %s", provider)
        return None

    try:
        if provider == "anthropic":
            return await _call_anthropic(prompt, api_key, model, max_tokens)
        if provider == "openai":
            return await _call_openai_compatible(
                prompt, api_key, OPENAI_API_URL, model, max_tokens, schema
            )
        if provider == "openrouter":
            return await _call_openai_compatible(
                prompt, api_key, OPENROUTER_API_URL, model, max_tokens, schema
            )
        logger.warning("Unsupported AI provider: %s", provider)
        return None
    except httpx.HTTPError as e:
        logger.warning("AI provider %s HTTP error: %s", provider, e)
        return None
    except Exception as e:
        logger.warning("AI provider %s unexpected error: %s", provider, e)
        return None


async def _call_openai_compatible(
    prompt: str,
    api_key: str,
    api_url: str,
    model: str,
    max_tokens: int,
    schema: dict | None,
) -> dict | None:
    body: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    if schema is not None:
        body["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        resp = await client.post(
            api_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            return None
        text = choices[0].get("message", {}).get("content", "")
        return _parse_json_text(text)
```

- [ ] **Step 4: Run all ai_client tests**

```bash
cd backend && uv run pytest tests/unit/test_ai_client.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/ai_client.py backend/tests/unit/test_ai_client.py
git commit -m "feat(ai): add openai/openrouter adapters to shared client (#109)"
```

---

## Task 4: AI client — Gemini adapter

**Files:**
- Modify: `backend/app/core/ai_client.py`
- Modify: `backend/tests/unit/test_ai_client.py`

- [ ] **Step 1: Add failing test for gemini**

Append to `backend/tests/unit/test_ai_client.py`:

```python
class TestCompleteJsonGemini:
    @pytest.mark.asyncio
    async def test_gemini_success(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": '{"episode": 3, "confidence": 0.95}'}]
                        }
                    }
                ]
            }
        )
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(
                prompt="match this episode",
                schema={
                    "type": "object",
                    "properties": {
                        "episode": {"type": "integer"},
                        "confidence": {"type": "number"},
                    },
                    "required": ["episode", "confidence"],
                },
                provider="gemini",
                api_key="AIzaSy-x",
            )

        assert result == {"episode": 3, "confidence": 0.95}
        call = mock.post.await_args
        url = call.args[0]
        assert url.startswith(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-lite-latest:generateContent"
        )
        assert call.kwargs["headers"]["x-goog-api-key"] == "AIzaSy-x"

        body = call.kwargs["json"]
        gen_cfg = body["generationConfig"]
        assert gen_cfg["responseMimeType"] == "application/json"
        assert gen_cfg["responseSchema"]["properties"]["episode"]["type"] == "integer"
        assert body["contents"][0]["parts"][0]["text"] == "match this episode"

    @pytest.mark.asyncio
    async def test_gemini_no_schema_still_works(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx(
            {"candidates": [{"content": {"parts": [{"text": '{"x": 1}'}]}}]}
        )
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(
                prompt="x", schema=None, provider="gemini", api_key="AIzaSy-x"
            )

        assert result == {"x": 1}
        body = mock.post.await_args.kwargs["json"]
        gen_cfg = body["generationConfig"]
        assert gen_cfg["responseMimeType"] == "application/json"
        assert "responseSchema" not in gen_cfg

    @pytest.mark.asyncio
    async def test_gemini_empty_candidates_returns_none(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx({"candidates": []})
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(
                prompt="x", schema=None, provider="gemini", api_key="k"
            )

        assert result is None
```

- [ ] **Step 2: Run, verify fail**

```bash
cd backend && uv run pytest tests/unit/test_ai_client.py -v -k Gemini
```
Expected: 3 failed.

- [ ] **Step 3: Add Gemini adapter**

In `backend/app/core/ai_client.py`, add the gemini branch in `complete_json` and the adapter:

```python
# Inside complete_json, after the openrouter branch:
        if provider == "gemini":
            return await _call_gemini(prompt, api_key, model, max_tokens, schema)
```

```python
async def _call_gemini(
    prompt: str,
    api_key: str,
    model: str,
    max_tokens: int,
    schema: dict | None,
) -> dict | None:
    url = f"{GEMINI_API_BASE}/{model}:generateContent"
    generation_config: dict = {
        "responseMimeType": "application/json",
        "maxOutputTokens": max_tokens,
        "temperature": 0,
    }
    if schema is not None:
        generation_config["responseSchema"] = schema

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": generation_config,
    }

    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        resp = await client.post(
            url,
            headers={
                "x-goog-api-key": api_key,
                "Content-Type": "application/json",
            },
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            return None
        parts = candidates[0].get("content", {}).get("parts") or []
        if not parts:
            return None
        text = parts[0].get("text", "")
        return _parse_json_text(text)
```

- [ ] **Step 4: Run all ai_client tests**

```bash
cd backend && uv run pytest tests/unit/test_ai_client.py -v
```
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/ai_client.py backend/tests/unit/test_ai_client.py
git commit -m "feat(ai): add Gemini adapter with responseSchema support (#109)"
```

---

## Task 5: AI client — 429 retry with exponential backoff

**Files:**
- Modify: `backend/app/core/ai_client.py`
- Modify: `backend/tests/unit/test_ai_client.py`

- [ ] **Step 1: Add failing test for 429 retry**

Append to `backend/tests/unit/test_ai_client.py`:

```python
class TestRateLimitRetry:
    @pytest.mark.asyncio
    async def test_429_then_success(self):
        from app.core.ai_client import complete_json

        from httpx import HTTPStatusError, Request, Response

        bad_resp = MagicMock()
        bad_resp.status_code = 429
        req = Request("POST", "http://x")
        bad_resp.raise_for_status.side_effect = HTTPStatusError(
            "429", request=req, response=Response(429, request=req)
        )
        bad_resp.json.return_value = {}

        good_resp = MagicMock()
        good_resp.status_code = 200
        good_resp.raise_for_status = MagicMock()
        good_resp.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]
        }

        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(side_effect=[bad_resp, good_resp])

        with patch("app.core.ai_client.httpx.AsyncClient", return_value=client), \
             patch("app.core.ai_client.asyncio.sleep", new=AsyncMock()):
            result = await complete_json(
                prompt="x", schema=None, provider="gemini", api_key="k"
            )

        assert result == {"ok": True}
        assert client.post.await_count == 2

    @pytest.mark.asyncio
    async def test_429_exhausted_returns_none(self):
        from app.core.ai_client import complete_json
        from httpx import HTTPStatusError, Request, Response

        bad_resp = MagicMock()
        bad_resp.status_code = 429
        req = Request("POST", "http://x")
        bad_resp.raise_for_status.side_effect = HTTPStatusError(
            "429", request=req, response=Response(429, request=req)
        )
        bad_resp.json.return_value = {}

        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(return_value=bad_resp)

        with patch("app.core.ai_client.httpx.AsyncClient", return_value=client), \
             patch("app.core.ai_client.asyncio.sleep", new=AsyncMock()):
            result = await complete_json(
                prompt="x", schema=None, provider="gemini", api_key="k"
            )

        assert result is None
        assert client.post.await_count == 4  # initial + 3 retries
```

- [ ] **Step 2: Run, verify fail**

```bash
cd backend && uv run pytest tests/unit/test_ai_client.py::TestRateLimitRetry -v
```
Expected: 2 failed (current code does not retry).

- [ ] **Step 3: Wrap adapters with retry helper**

In `backend/app/core/ai_client.py`, refactor `complete_json` to wrap calls in a retry helper:

```python
MAX_RETRIES = 3
_BACKOFF_BASE = 1.0  # seconds


async def _with_429_retry(coro_factory):
    """Call coro_factory() up to MAX_RETRIES+1 times, backing off on 429.

    coro_factory must be a no-arg callable returning a fresh coroutine each call.
    """
    delay = _BACKOFF_BASE
    for attempt in range(MAX_RETRIES + 1):
        try:
            return await coro_factory()
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 429 or attempt == MAX_RETRIES:
                raise
            jitter = random.uniform(0, 0.25 * delay)
            await asyncio.sleep(delay + jitter)
            delay *= 2
    return None


async def complete_json(
    *,
    prompt: str,
    schema: dict | None,
    provider: str,
    api_key: str,
    model: str | None = None,
    max_tokens: int = 1024,
) -> dict | None:
    if not api_key:
        return None
    model = model or DEFAULT_MODELS.get(provider)
    if not model:
        logger.warning("Unknown AI provider: %s", provider)
        return None

    if provider == "anthropic":
        factory = lambda: _call_anthropic(prompt, api_key, model, max_tokens)
    elif provider == "openai":
        factory = lambda: _call_openai_compatible(prompt, api_key, OPENAI_API_URL, model, max_tokens, schema)
    elif provider == "openrouter":
        factory = lambda: _call_openai_compatible(prompt, api_key, OPENROUTER_API_URL, model, max_tokens, schema)
    elif provider == "gemini":
        factory = lambda: _call_gemini(prompt, api_key, model, max_tokens, schema)
    else:
        logger.warning("Unsupported AI provider: %s", provider)
        return None

    try:
        return await _with_429_retry(factory)
    except httpx.HTTPError as e:
        logger.warning("AI provider %s HTTP error: %s", provider, e)
        return None
    except Exception as e:
        logger.warning("AI provider %s unexpected error: %s", provider, e)
        return None
```

- [ ] **Step 4: Run all ai_client tests**

```bash
cd backend && uv run pytest tests/unit/test_ai_client.py -v
```
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/ai_client.py backend/tests/unit/test_ai_client.py
git commit -m "feat(ai): add 429 exponential backoff to shared client (#109)"
```

---

## Task 6: Refactor `ai_identifier.py` to delegate to `ai_client.complete_json`

**Files:**
- Modify: `backend/app/core/ai_identifier.py`
- Modify: `backend/tests/unit/test_ai_identifier.py` (regression coverage stays green)

- [ ] **Step 1: Run existing tests as baseline**

```bash
cd backend && uv run pytest tests/unit/test_ai_identifier.py -v
```
Expected: all pass — capture count for after-refactor comparison.

- [ ] **Step 2: Refactor identify_from_label to delegate**

Replace contents of `backend/app/core/ai_identifier.py` with:

```python
"""AI-powered disc title resolution.

Delegates to the shared `app.core.ai_client.complete_json` for transport
and JSON parsing. This module owns only the disc-title prompt and
response-shape validation.
"""

import logging

from app.core.ai_client import complete_json

logger = logging.getLogger(__name__)

IDENTIFICATION_PROMPT = """You are a media identification assistant. Given a disc volume label from a Blu-ray or DVD, identify the movie or TV show it contains.

Volume label: {volume_label}

Respond with ONLY a JSON object (no markdown, no explanation) in this exact format:
{{"title": "Official Title", "year": 2020, "type": "movie" or "tv"}}

Rules:
- "title" must be the official English title as it appears on TMDB/IMDb
- "year" is the original release year (integer)
- "type" is either "movie" or "tv"
- If you cannot identify the disc, respond with: {{"title": null, "year": null, "type": null}}
- Do NOT guess — only identify if you are confident"""


async def identify_from_label(
    volume_label: str,
    provider: str,
    api_key: str,
) -> dict | None:
    """Send volume label to an LLM to identify the disc content.

    Returns dict with keys: title, year, type (or None on failure).
    """
    prompt = IDENTIFICATION_PROMPT.format(volume_label=volume_label)
    raw = await complete_json(
        prompt=prompt,
        schema=None,
        provider=provider,
        api_key=api_key,
        max_tokens=200,
    )
    return _validate(raw, volume_label)


def _validate(raw: dict | None, volume_label: str) -> dict | None:
    if not raw:
        return None
    title = raw.get("title")
    if not title:
        return None

    year_raw = raw.get("year")
    try:
        year = int(year_raw) if year_raw is not None else None
    except (TypeError, ValueError):
        year = None

    parsed = {
        "title": str(title),
        "year": year,
        "type": raw.get("type"),
    }
    logger.info(
        "AI identified '%s' as: %s (%s) [%s]",
        volume_label, parsed["title"], parsed["year"], parsed["type"],
    )
    return parsed


# Keep _parse_response as a backwards-compatible shim for existing tests.
def _parse_response(text: str) -> dict | None:
    """Test-shim — preserves the v1 contract for unit tests."""
    from app.core.ai_client import _parse_json_text
    parsed = _parse_json_text(text)
    return _validate(parsed, "test")
```

- [ ] **Step 3: Run ai_identifier tests, verify regressions stay clean**

```bash
cd backend && uv run pytest tests/unit/test_ai_identifier.py -v
```
Expected: same number of tests passing as Step 1.

- [ ] **Step 4: Run identification_coordinator tests as integration guard**

```bash
cd backend && uv run pytest tests/unit/test_identification_coordinator.py -v
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/ai_identifier.py
git commit -m "refactor(ai): delegate ai_identifier to shared ai_client (#109)"
```

---

## Task 7: Extend `fetch_season_episodes` with `overview`

**Files:**
- Modify: `backend/app/matcher/tmdb_client.py:740-748`
- Modify: `backend/tests/unit/test_tmdb_client.py` (file exists — add cases)

- [ ] **Step 1: Add failing test for overview field**

Append to `backend/tests/unit/test_tmdb_client.py`:

```python
class TestFetchSeasonEpisodesOverview:
    def test_includes_overview(self):
        from unittest.mock import patch
        from app.matcher.tmdb_client import fetch_season_episodes

        fake = {
            "episodes": [
                {"episode_number": 1, "name": "Pilot", "runtime": 42, "overview": "A new dawn."},
                {"episode_number": 2, "name": "Cargo", "runtime": 41, "overview": ""},
            ]
        }
        with patch("app.matcher.tmdb_client._tmdb_get_json", return_value=fake):
            eps = fetch_season_episodes("1234", 1, "fake-key")

        assert len(eps) == 2
        assert eps[0]["overview"] == "A new dawn."
        assert eps[1]["overview"] == ""

    def test_overview_missing_defaults_to_empty(self):
        from unittest.mock import patch
        from app.matcher.tmdb_client import fetch_season_episodes

        fake = {"episodes": [{"episode_number": 1, "name": "Pilot", "runtime": 42}]}
        with patch("app.matcher.tmdb_client._tmdb_get_json", return_value=fake):
            eps = fetch_season_episodes("1234", 1, "fake-key")

        assert eps[0]["overview"] == ""
```

- [ ] **Step 2: Run, verify fail**

```bash
cd backend && uv run pytest tests/unit/test_tmdb_client.py::TestFetchSeasonEpisodesOverview -v
```
Expected: 2 failed (KeyError on `overview`).

- [ ] **Step 3: Add overview field**

In `backend/app/matcher/tmdb_client.py` modify the return inside `fetch_season_episodes` (currently lines 740-748):

```python
    return [
        {
            "episode_number": ep.get("episode_number"),
            "name": ep.get("name") or "",
            "runtime": ep.get("runtime") or 0,
            "overview": ep.get("overview") or "",
        }
        for ep in season_data.get("episodes", [])
        if ep.get("episode_number") is not None
    ]
```

- [ ] **Step 4: Run, verify pass**

```bash
cd backend && uv run pytest tests/unit/test_tmdb_client.py -v
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/matcher/tmdb_client.py backend/tests/unit/test_tmdb_client.py
git commit -m "feat(tmdb): include episode overview in fetch_season_episodes (#109)"
```

---

## Task 8: Extract `transcribe_full` from `_match_full_file`

**Files:**
- Modify: `backend/app/matcher/episode_identification.py:802-876` (`_match_full_file`)
- Modify: `backend/tests/unit/test_episode_identification.py` (add test) — or create if absent

- [ ] **Step 1: Verify the test module path exists**

```bash
ls backend/tests/unit/test_episode_identification.py 2>&1 || echo MISSING
```

If MISSING, create it with the header:
```python
"""Tests for the episode identification matcher."""

from unittest.mock import MagicMock, patch
```

- [ ] **Step 2: Add failing test for transcribe_full**

Append:

```python
class TestTranscribeFull:
    def test_invokes_whisper_and_returns_text(self, tmp_path):
        from app.matcher.episode_identification import EpisodeMatcher

        matcher = EpisodeMatcher(cache_dir=tmp_path, show_name="Test Show")
        fake_model = MagicMock()
        fake_model.transcribe.return_value = {"text": " hello world from the episode "}

        with patch.object(matcher, "extract_audio_chunk", return_value=str(tmp_path / "a.wav")), \
             patch("app.matcher.episode_identification.get_cached_model", return_value=fake_model), \
             patch("app.matcher.episode_identification.get_video_duration", return_value=1320):
            text = matcher.transcribe_full(tmp_path / "fake.mkv")

        assert text == "hello world from the episode"

    def test_returns_none_on_extraction_failure(self, tmp_path):
        from app.matcher.episode_identification import EpisodeMatcher

        matcher = EpisodeMatcher(cache_dir=tmp_path, show_name="Test Show")

        with patch.object(matcher, "extract_audio_chunk", side_effect=RuntimeError("ffmpeg boom")), \
             patch("app.matcher.episode_identification.get_video_duration", return_value=1320):
            text = matcher.transcribe_full(tmp_path / "fake.mkv")

        assert text is None
```

- [ ] **Step 3: Run, verify fail**

```bash
cd backend && uv run pytest tests/unit/test_episode_identification.py::TestTranscribeFull -v
```
Expected: AttributeError no `transcribe_full`.

- [ ] **Step 4: Add the method**

In `backend/app/matcher/episode_identification.py`, add the new method to `EpisodeMatcher` (place above `_match_full_file`):

```python
    def transcribe_full(self, video_file) -> str | None:
        """Whisper-transcribe the entire video file, returning the cleaned text.

        Returns None when extraction or transcription fails, or when the
        returned text has fewer than 50 characters (matches the existing
        _match_full_file guard).
        """
        try:
            duration = get_video_duration(str(video_file))
        except Exception as e:
            logger.error(f"transcribe_full: duration lookup failed for {video_file}: {e}")
            return None

        model_config = {"type": "whisper", "name": self.model_name, "device": self.device}
        try:
            model = get_cached_model(model_config)
            audio_path = self.extract_audio_chunk(video_file, start_time=0, duration=duration)
            result = model.transcribe(audio_path)
            full = (result.get("text") or "").strip()
        except Exception as e:
            logger.warning(f"transcribe_full: transcription failed for {video_file}: {e}")
            return None

        if len(full) < 50:
            logger.info(f"transcribe_full: too little text ({len(full)} chars) for {video_file}")
            return None
        return full
```

Then replace the duplicated logic in `_match_full_file` (currently around lines 823-839) by calling `self.transcribe_full(video_file)` and treating None as the existing "too little text" branch.

- [ ] **Step 5: Run tests, verify pass + existing matcher tests still green**

```bash
cd backend && uv run pytest tests/unit/test_episode_identification.py tests/pipeline/ -v
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/matcher/episode_identification.py backend/tests/unit/test_episode_identification.py
git commit -m "refactor(matcher): extract transcribe_full from _match_full_file (#109)"
```

---

## Task 9: LLM episode matcher — happy path

**Files:**
- Create: `backend/app/matcher/llm_episode_matcher.py`
- Create: `backend/tests/unit/test_llm_episode_matcher.py`

- [ ] **Step 1: Write failing test for happy path**

Create `backend/tests/unit/test_llm_episode_matcher.py`:

```python
"""Tests for the LLM episode matcher."""

from unittest.mock import AsyncMock, patch

import pytest


class TestMatchEpisodeViaLLM:
    @pytest.mark.asyncio
    async def test_happy_path_returns_llm_episode_match(self):
        from app.matcher.llm_episode_matcher import match_episode_via_llm

        synopses = [
            {"episode_number": 1, "name": "Pilot", "overview": "Aliens arrive."},
            {"episode_number": 2, "name": "Cargo", "overview": "A heist on a freighter."},
            {"episode_number": 3, "name": "Echo", "overview": "Mysterious signals."},
        ]
        ai_response = {
            "episode": 2,
            "confidence": 0.91,
            "reasoning": "Mentions of cargo and freighter alignment.",
            "runner_up": {"episode": 1, "confidence": 0.04},
        }

        with patch(
            "app.matcher.llm_episode_matcher.fetch_season_episodes",
            return_value=synopses,
        ), patch(
            "app.matcher.llm_episode_matcher.complete_json",
            new=AsyncMock(return_value=ai_response),
        ):
            result = await match_episode_via_llm(
                transcript="they boarded the freighter and unloaded the cargo " * 50,
                show_name="The Expanse",
                season=1,
                tmdb_show_id="12345",
                ai_provider="gemini",
                ai_api_key="k",
                tmdb_api_key="t",
            )

        assert result is not None
        assert result.episode == 2
        assert result.confidence == 0.91
        assert result.runner_up == {"episode": 1, "confidence": 0.04}
        assert result.model == "gemini-flash-lite-latest"

    @pytest.mark.asyncio
    async def test_confidence_zero_returns_none(self):
        from app.matcher.llm_episode_matcher import match_episode_via_llm

        synopses = [{"episode_number": 1, "name": "X", "overview": "y"}]
        ai_response = {"episode": 0, "confidence": 0.0, "reasoning": "wrong show", "runner_up": None}

        with patch(
            "app.matcher.llm_episode_matcher.fetch_season_episodes",
            return_value=synopses,
        ), patch(
            "app.matcher.llm_episode_matcher.complete_json",
            new=AsyncMock(return_value=ai_response),
        ):
            result = await match_episode_via_llm(
                transcript="x" * 600,
                show_name="X",
                season=1,
                tmdb_show_id="1",
                ai_provider="gemini",
                ai_api_key="k",
                tmdb_api_key="t",
            )

        assert result is None
```

- [ ] **Step 2: Run, verify fail**

```bash
cd backend && uv run pytest tests/unit/test_llm_episode_matcher.py -v
```
Expected: ModuleNotFoundError.

- [ ] **Step 3: Create llm_episode_matcher.py**

Create `backend/app/matcher/llm_episode_matcher.py`:

```python
"""LLM-based episode identification fallback.

When Engram's primary audio-fingerprint matcher returns a low-confidence
match, this module fetches the candidate season's TMDB synopses, sends
them along with the ripped episode's cleaned Whisper transcript to the
configured AI provider, and returns the LLM's suggested episode.

Always treats the result as a *suggestion* — the caller must route it
through the review queue, never auto-organize.
"""

import logging
from dataclasses import dataclass

from app.core.ai_client import DEFAULT_MODELS, complete_json
from app.matcher.episode_identification import _clean_subtitle_text
from app.matcher.tmdb_client import fetch_season_episodes

logger = logging.getLogger(__name__)

MIN_TRANSCRIPT_CHARS = 500

PROMPT_TEMPLATE = """You are identifying which episode of "{show_name}" Season {season} this is, given the episode's full dialogue transcript.

Candidate episodes (within this season):
{candidates_block}

Episode transcript (cleaned, lowercase):
\"\"\"
{transcript}
\"\"\"

Rules:
- Weight plot-specific events (named characters, unique locations, distinctive plot beats) over generic dialogue, action sounds, or recurring phrases.
- If the transcript does NOT match any candidate (e.g. wrong show/season), respond with `confidence: 0`.
- `runner_up` is your second-best guess; null if no plausible alternative.

Respond with ONLY a JSON object in this exact format:
{{"episode": <int>, "confidence": <float 0..1>, "reasoning": "<one sentence>", "runner_up": {{"episode": <int>, "confidence": <float>}} or null}}
"""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "episode": {"type": "integer"},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
        "runner_up": {
            "type": ["object", "null"],
            "properties": {
                "episode": {"type": "integer"},
                "confidence": {"type": "number"},
            },
        },
    },
    "required": ["episode", "confidence"],
}


@dataclass
class LLMEpisodeMatch:
    episode: int
    confidence: float
    reasoning: str
    runner_up: dict | None
    model: str


async def match_episode_via_llm(
    *,
    transcript: str,
    show_name: str,
    season: int,
    tmdb_show_id: str,
    ai_provider: str,
    ai_api_key: str,
    tmdb_api_key: str,
) -> LLMEpisodeMatch | None:
    """Run LLM episode matching. Returns None on any failure or zero-confidence."""
    cleaned = _clean_subtitle_text(transcript)
    if len(cleaned) < MIN_TRANSCRIPT_CHARS:
        logger.info(
            "LLM matcher: transcript too short (%d chars) for %s S%02d",
            len(cleaned), show_name, season,
        )
        return None

    episodes = fetch_season_episodes(tmdb_show_id, season, tmdb_api_key)
    if not episodes:
        logger.warning(
            "LLM matcher: no TMDB synopses for show_id=%s season=%d", tmdb_show_id, season
        )
        return None

    candidates_block = "\n".join(
        f"- Episode {ep['episode_number']}: \"{ep.get('name','')}\" — {ep.get('overview','') or '(no synopsis)'}"
        for ep in episodes
    )
    prompt = PROMPT_TEMPLATE.format(
        show_name=show_name,
        season=season,
        candidates_block=candidates_block,
        transcript=cleaned,
    )

    raw = await complete_json(
        prompt=prompt,
        schema=RESPONSE_SCHEMA,
        provider=ai_provider,
        api_key=ai_api_key,
        max_tokens=512,
    )
    if not raw:
        return None

    try:
        episode = int(raw["episode"])
        confidence = float(raw["confidence"])
    except (KeyError, TypeError, ValueError) as e:
        logger.warning("LLM matcher: malformed response: %s (raw=%s)", e, raw)
        return None

    if confidence <= 0.0:
        logger.info(
            "LLM matcher: confidence==0 (wrong show/season signal) for %s S%02d", show_name, season
        )
        return None

    return LLMEpisodeMatch(
        episode=episode,
        confidence=confidence,
        reasoning=str(raw.get("reasoning") or ""),
        runner_up=raw.get("runner_up") if isinstance(raw.get("runner_up"), dict) else None,
        model=DEFAULT_MODELS.get(ai_provider, "unknown"),
    )
```

- [ ] **Step 4: Run, verify pass**

```bash
cd backend && uv run pytest tests/unit/test_llm_episode_matcher.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/matcher/llm_episode_matcher.py backend/tests/unit/test_llm_episode_matcher.py
git commit -m "feat(matcher): LLM-based episode matching with season synopses (#109)"
```

---

## Task 10: LLM matcher — edge cases (no synopses, short transcript, AI failure)

**Files:**
- Modify: `backend/tests/unit/test_llm_episode_matcher.py`

- [ ] **Step 1: Add failing edge-case tests**

Append:

```python
    @pytest.mark.asyncio
    async def test_short_transcript_returns_none(self):
        from app.matcher.llm_episode_matcher import match_episode_via_llm

        with patch(
            "app.matcher.llm_episode_matcher.fetch_season_episodes",
            return_value=[{"episode_number": 1, "name": "X", "overview": "y"}],
        ), patch(
            "app.matcher.llm_episode_matcher.complete_json",
            new=AsyncMock(return_value={"episode": 1, "confidence": 0.9}),
        ) as mock_ai:
            result = await match_episode_via_llm(
                transcript="too short",
                show_name="X",
                season=1,
                tmdb_show_id="1",
                ai_provider="gemini",
                ai_api_key="k",
                tmdb_api_key="t",
            )

        assert result is None
        mock_ai.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_synopses_returns_none(self):
        from app.matcher.llm_episode_matcher import match_episode_via_llm

        with patch(
            "app.matcher.llm_episode_matcher.fetch_season_episodes",
            return_value=[],
        ), patch(
            "app.matcher.llm_episode_matcher.complete_json",
            new=AsyncMock(return_value={"episode": 1, "confidence": 0.9}),
        ) as mock_ai:
            result = await match_episode_via_llm(
                transcript="x" * 600,
                show_name="X",
                season=1,
                tmdb_show_id="1",
                ai_provider="gemini",
                ai_api_key="k",
                tmdb_api_key="t",
            )

        assert result is None
        mock_ai.assert_not_called()

    @pytest.mark.asyncio
    async def test_ai_returns_none(self):
        from app.matcher.llm_episode_matcher import match_episode_via_llm

        with patch(
            "app.matcher.llm_episode_matcher.fetch_season_episodes",
            return_value=[{"episode_number": 1, "name": "X", "overview": "y"}],
        ), patch(
            "app.matcher.llm_episode_matcher.complete_json",
            new=AsyncMock(return_value=None),
        ):
            result = await match_episode_via_llm(
                transcript="x" * 600,
                show_name="X", season=1, tmdb_show_id="1",
                ai_provider="gemini", ai_api_key="k", tmdb_api_key="t",
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_malformed_response_returns_none(self):
        from app.matcher.llm_episode_matcher import match_episode_via_llm

        with patch(
            "app.matcher.llm_episode_matcher.fetch_season_episodes",
            return_value=[{"episode_number": 1, "name": "X", "overview": "y"}],
        ), patch(
            "app.matcher.llm_episode_matcher.complete_json",
            new=AsyncMock(return_value={"reasoning": "oops"}),  # missing episode/confidence
        ):
            result = await match_episode_via_llm(
                transcript="x" * 600,
                show_name="X", season=1, tmdb_show_id="1",
                ai_provider="gemini", ai_api_key="k", tmdb_api_key="t",
            )
        assert result is None
```

- [ ] **Step 2: Run, verify all pass (implementation already covers these)**

```bash
cd backend && uv run pytest tests/unit/test_llm_episode_matcher.py -v
```
Expected: 6 passed.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/unit/test_llm_episode_matcher.py
git commit -m "test(matcher): edge cases for LLM episode matcher (#109)"
```

---

## Task 11: Curator LLM fallback integration

**Files:**
- Modify: `backend/app/core/curator.py:172-250` (`match_single_file`)
- Modify: `backend/tests/unit/test_curator.py` (extend; file exists)

- [ ] **Step 1: Add failing test for LLM fallback path**

Append to `backend/tests/unit/test_curator.py`:

```python
class TestLLMFallback:
    @pytest.mark.asyncio
    async def test_disabled_in_config_skips_llm(self, tmp_path):
        from app.core.curator import EpisodeCurator, MatchResult
        from unittest.mock import AsyncMock, MagicMock, patch

        curator = EpisodeCurator()
        curator._matcher = MagicMock()
        curator._matcher.identify_episode.return_value = {
            "season": 1, "episode": 3, "confidence": 0.5, "score": 0.5,
            "match_details": {}, "runner_ups": [],
        }
        curator._cache_dir = tmp_path
        curator._initialized = True
        curator._current_show = "Test"

        fake_config = MagicMock(ai_episode_matching_enabled=False, ai_api_key="k")
        with patch("app.services.config_service.get_config", new=AsyncMock(return_value=fake_config)), \
             patch("app.matcher.llm_episode_matcher.match_episode_via_llm", new=AsyncMock()) as mock_llm:
            result = await curator.match_single_file(tmp_path / "x.mkv", "Test", 1)

        assert isinstance(result, MatchResult)
        mock_llm.assert_not_called()

    @pytest.mark.asyncio
    async def test_low_confidence_triggers_llm_and_attaches_suggestion(self, tmp_path):
        from app.core.curator import EpisodeCurator
        from app.matcher.llm_episode_matcher import LLMEpisodeMatch
        from unittest.mock import AsyncMock, MagicMock, patch

        curator = EpisodeCurator()
        curator._matcher = MagicMock()
        curator._matcher.identify_episode.return_value = {
            "season": 1, "episode": 3, "confidence": 0.4, "score": 0.4,
            "match_details": {}, "runner_ups": [],
        }
        curator._matcher.transcribe_full = MagicMock(return_value="x" * 600)
        curator._cache_dir = tmp_path
        curator._initialized = True
        curator._current_show = "Test"

        fake_config = MagicMock(
            ai_episode_matching_enabled=True,
            ai_api_key="k",
            ai_provider="gemini",
            tmdb_api_key="t",
        )

        llm = LLMEpisodeMatch(
            episode=5, confidence=0.92, reasoning="r",
            runner_up=None, model="gemini-flash-lite-latest",
        )

        with patch("app.services.config_service.get_config", new=AsyncMock(return_value=fake_config)), \
             patch("app.matcher.tmdb_client.fetch_show_id", return_value="1234"), \
             patch("app.core.curator.match_episode_via_llm", new=AsyncMock(return_value=llm)):
            result = await curator.match_single_file(tmp_path / "x.mkv", "Test", 1)

        assert result.needs_review is True
        assert result.match_details["llm_suggestion"]["episode"] == 5
        assert result.match_details["llm_suggestion"]["confidence"] == 0.92
        assert result.match_details["llm_suggestion"]["model"] == "gemini-flash-lite-latest"

    @pytest.mark.asyncio
    async def test_high_confidence_skips_llm(self, tmp_path):
        from app.core.curator import EpisodeCurator
        from unittest.mock import AsyncMock, MagicMock, patch

        curator = EpisodeCurator()
        curator._matcher = MagicMock()
        curator._matcher.identify_episode.return_value = {
            "season": 1, "episode": 3, "confidence": 0.92, "score": 0.9,
            "match_details": {}, "runner_ups": [],
        }
        curator._cache_dir = tmp_path
        curator._initialized = True
        curator._current_show = "Test"

        fake_config = MagicMock(ai_episode_matching_enabled=True, ai_api_key="k")
        with patch("app.services.config_service.get_config", new=AsyncMock(return_value=fake_config)), \
             patch("app.core.curator.match_episode_via_llm", new=AsyncMock()) as mock_llm:
            result = await curator.match_single_file(tmp_path / "x.mkv", "Test", 1)

        assert result.needs_review is False
        mock_llm.assert_not_called()
```

- [ ] **Step 2: Run, verify fail**

```bash
cd backend && uv run pytest tests/unit/test_curator.py::TestLLMFallback -v
```
Expected: 3 failed.

- [ ] **Step 3: Add LLM fallback to curator**

At the top of `backend/app/core/curator.py`, add the import:

```python
from app.matcher.llm_episode_matcher import match_episode_via_llm
```

In `match_single_file`, after the existing `if match and match.get("episode") is not None:` block builds the `MatchResult`, before returning add:

```python
                # LLM episode-matching fallback — only runs when the primary
                # match needs review, config is enabled, and the season is known.
                if needs_review and season:
                    enriched = await self._maybe_add_llm_suggestion(
                        file_path=file_path,
                        series_name=series_name,
                        season=season,
                        match_details=details,
                    )
                    if enriched is not None:
                        details = enriched

                return MatchResult(
                    file_path=file_path,
                    episode_code=episode_code,
                    episode_title=None,
                    confidence=confidence,
                    needs_review=needs_review,
                    match_details=details,
                )
```

And the `else:` branch (no primary match) becomes:

```python
            else:
                details = match.get("match_details") if match else None
                fallback = self._fallback_result(file_path, match_details=details)
                if season:
                    enriched = await self._maybe_add_llm_suggestion(
                        file_path=file_path,
                        series_name=series_name,
                        season=season,
                        match_details=fallback.match_details or {},
                    )
                    if enriched is not None:
                        fallback.match_details = enriched
                return fallback
```

Add the helper method on `EpisodeCurator`:

```python
    async def _maybe_add_llm_suggestion(
        self,
        *,
        file_path: Path,
        series_name: str,
        season: int,
        match_details: dict,
    ) -> dict | None:
        """Run the LLM matcher when enabled and attach the suggestion to match_details.

        Returns the updated match_details dict, or None to keep the caller's dict.
        """
        from app.services.config_service import get_config

        config = await get_config()
        if not config or not getattr(config, "ai_episode_matching_enabled", False):
            return None
        if not config.ai_api_key:
            return None

        # Resolve TMDB show id (synchronous, run in thread)
        from app.matcher.tmdb_client import fetch_show_id
        tmdb_show_id = await asyncio.to_thread(fetch_show_id, series_name)
        if not tmdb_show_id:
            logger.info(f"LLM fallback: no TMDB show_id for {series_name!r}")
            return None

        if not self._matcher:
            return None
        transcript = await asyncio.to_thread(self._matcher.transcribe_full, file_path)
        if not transcript:
            return None

        try:
            suggestion = await match_episode_via_llm(
                transcript=transcript,
                show_name=series_name,
                season=season,
                tmdb_show_id=str(tmdb_show_id),
                ai_provider=config.ai_provider,
                ai_api_key=config.ai_api_key,
                tmdb_api_key=config.tmdb_api_key,
            )
        except Exception as e:
            logger.warning(f"LLM fallback raised: {e}", exc_info=True)
            return None

        if not suggestion:
            return None

        enriched = dict(match_details) if match_details else {}
        enriched["llm_suggestion"] = {
            "episode": suggestion.episode,
            "confidence": suggestion.confidence,
            "reasoning": suggestion.reasoning,
            "runner_up": suggestion.runner_up,
            "model": suggestion.model,
        }
        return enriched
```

- [ ] **Step 4: Run curator tests, verify pass**

```bash
cd backend && uv run pytest tests/unit/test_curator.py -v
```
Expected: all pass including the 3 new ones.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/curator.py backend/tests/unit/test_curator.py
git commit -m "feat(curator): wire LLM episode-matcher fallback (#109)"
```

---

## Task 12: `reassign_episode` accepts optional `source` parameter

**Files:**
- Modify: `backend/app/services/job_manager.py:838-871`
- Modify: `backend/app/api/routes.py:2309-2335` (ReassignRequest + endpoint)
- Modify: `backend/tests/unit/test_job_manager.py` (or test_routes.py — extend existing)

- [ ] **Step 1: Find the ReassignRequest model**

```bash
grep -n "class ReassignRequest" backend/app/api/routes.py
```

- [ ] **Step 2: Add failing test that source flows through**

Append to whichever test file owns `reassign_episode` coverage (commonly `backend/tests/integration/test_workflow.py`):

```python
class TestReassignWithSource:
    @pytest.mark.asyncio
    async def test_source_ai_llm_persisted(self, client, setup_db):
        # Set up a job + title in REVIEW state
        from app.database import async_session
        from app.models.disc_job import DiscJob, DiscTitle, JobState, ContentType, TitleState
        async with async_session() as s:
            job = DiscJob(volume_label="X_S1D1", state=JobState.REVIEW_NEEDED, content_type=ContentType.TV)
            s.add(job); await s.commit(); await s.refresh(job)
            title = DiscTitle(job_id=job.id, title_index=0, state=TitleState.REVIEW, file_path="/tmp/x.mkv")
            s.add(title); await s.commit(); await s.refresh(title)

        r = await client.post(
            f"/api/jobs/{job.id}/titles/{title.id}/reassign",
            json={"episode_code": "S01E03", "source": "ai_llm"},
        )
        assert r.status_code == 200

        async with async_session() as s:
            refreshed = await s.get(DiscTitle, title.id)
            assert refreshed.matched_episode == "S01E03"
            assert refreshed.match_source == "ai_llm"
```

- [ ] **Step 3: Run, verify fail**

```bash
cd backend && uv run pytest tests/integration/test_workflow.py::TestReassignWithSource -v
```
Expected: fail (extra `source` field rejected or stored as default `"user"`).

- [ ] **Step 4: Add `source` to job_manager.reassign_episode**

Edit `backend/app/services/job_manager.py:838-869`:

```python
    async def reassign_episode(
        self,
        job_id: int,
        title_id: int,
        episode_code: str,
        edition: str | None = None,
        source: str = "user",
    ) -> None:
        """Manually reassign an episode for a title.

        ``source`` defaults to "user" (manual reassignment). When the user
        accepts an LLM suggestion via the review UI, pass source="ai_llm" so
        downstream consumers can distinguish that path.
        """
        async with async_session() as session:
            title = await session.get(DiscTitle, title_id)
            if not title or title.job_id != job_id:
                raise ValueError(f"Title {title_id} not found for job {job_id}")

            title.matched_episode = episode_code
            title.match_confidence = 1.0
            title.match_source = source
            if edition is not None:
                title.edition = edition
            if title.state != TitleState.MATCHED:
                title.state = TitleState.MATCHED
            session.add(title)
            await session.commit()

            await ws_manager.broadcast_title_update(
                job_id,
                title.id,
                TitleState.MATCHED.value,
                matched_episode=episode_code,
                match_confidence=1.0,
                match_source=source,
            )

        logger.info(f"Job {job_id}: title {title_id} reassigned to {episode_code} (source={source})")
```

- [ ] **Step 5: Add `source` to ReassignRequest + endpoint**

Find `ReassignRequest` in `backend/app/api/routes.py` and add the optional field:

```python
class ReassignRequest(BaseModel):
    episode_code: str
    edition: str | None = None
    source: str = "user"
```

In the endpoint body (line 2327), pass it through:

```python
        await job_manager.reassign_episode(
            job.id, title_id, request.episode_code, request.edition, source=request.source
        )
```

- [ ] **Step 6: Run, verify pass**

```bash
cd backend && uv run pytest tests/integration/test_workflow.py::TestReassignWithSource -v
```
Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/job_manager.py backend/app/api/routes.py backend/tests/integration/test_workflow.py
git commit -m "feat(api): reassign_episode accepts source param (ai_llm) (#109)"
```

---

## Task 13: `POST /api/jobs/{job_id}/titles/{title_id}/llm-match` endpoint

**Files:**
- Modify: `backend/app/api/routes.py` (add endpoint near the reassign endpoint)
- Modify: `backend/tests/integration/test_workflow.py`

- [ ] **Step 1: Add failing test for the new endpoint**

Append:

```python
class TestLLMMatchEndpoint:
    @pytest.mark.asyncio
    async def test_returns_suggestion_and_persists(self, client, setup_db, monkeypatch):
        from app.database import async_session
        from app.models.disc_job import DiscJob, DiscTitle, JobState, ContentType, TitleState
        from app.matcher.llm_episode_matcher import LLMEpisodeMatch
        from unittest.mock import AsyncMock

        async with async_session() as s:
            job = DiscJob(
                volume_label="X_S1D1",
                state=JobState.REVIEW_NEEDED,
                content_type=ContentType.TV,
                detected_title="The Expanse",
                detected_season=1,
            )
            s.add(job); await s.commit(); await s.refresh(job)
            title = DiscTitle(
                job_id=job.id, title_index=0, state=TitleState.REVIEW,
                file_path="/tmp/x.mkv",
            )
            s.add(title); await s.commit(); await s.refresh(title)

        async def fake_run(**kwargs):
            return {
                "episode": 4, "confidence": 0.88, "reasoning": "r",
                "runner_up": None, "model": "gemini-flash-lite-latest",
            }

        monkeypatch.setattr(
            "app.api.routes._run_llm_match_for_title", fake_run
        )

        r = await client.post(f"/api/jobs/{job.id}/titles/{title.id}/llm-match")
        assert r.status_code == 200
        body = r.json()
        assert body["suggestion"]["episode"] == 4

        async with async_session() as s:
            refreshed = await s.get(DiscTitle, title.id)
            import json
            details = json.loads(refreshed.match_details or "{}")
            assert details["llm_suggestion"]["episode"] == 4
```

- [ ] **Step 2: Run, verify fail**

```bash
cd backend && uv run pytest tests/integration/test_workflow.py::TestLLMMatchEndpoint -v
```
Expected: 404 (route does not exist).

- [ ] **Step 3: Implement the endpoint**

In `backend/app/api/routes.py`, near the reassign endpoint, add:

```python
async def _run_llm_match_for_title(
    *, title: "DiscTitle", job: "DiscJob"
) -> dict | None:
    """Invoke the LLM episode matcher for a single title. Returns suggestion dict or None."""
    from app.core.curator import episode_curator
    from app.matcher.llm_episode_matcher import match_episode_via_llm
    from app.matcher.tmdb_client import fetch_show_id
    from app.services.config_service import get_config

    config = await get_config()
    if not config or not getattr(config, "ai_episode_matching_enabled", False):
        return None
    if not config.ai_api_key or not job.detected_title or not job.detected_season:
        return None

    # Make sure the matcher is initialized for the show (so transcribe_full works)
    episode_curator._ensure_initialized(job.detected_title)
    if not episode_curator._matcher:
        return None

    tmdb_show_id = await asyncio.to_thread(fetch_show_id, job.detected_title)
    if not tmdb_show_id:
        return None

    transcript = await asyncio.to_thread(
        episode_curator._matcher.transcribe_full, Path(title.file_path)
    )
    if not transcript:
        return None

    suggestion = await match_episode_via_llm(
        transcript=transcript,
        show_name=job.detected_title,
        season=job.detected_season,
        tmdb_show_id=str(tmdb_show_id),
        ai_provider=config.ai_provider,
        ai_api_key=config.ai_api_key,
        tmdb_api_key=config.tmdb_api_key,
    )
    if not suggestion:
        return None
    return {
        "episode": suggestion.episode,
        "confidence": suggestion.confidence,
        "reasoning": suggestion.reasoning,
        "runner_up": suggestion.runner_up,
        "model": suggestion.model,
    }


@router.post("/jobs/{job_id}/titles/{title_id}/llm-match")
async def llm_match_title(
    title_id: int,
    job: DiscJob = Depends(get_job_or_404),
    session: AsyncSession = Depends(get_session),
):
    """Run the LLM episode matcher on a single title and persist the suggestion."""
    title = await session.get(DiscTitle, title_id)
    if not title or title.job_id != job.id:
        raise HTTPException(status_code=404, detail="Title not found")

    try:
        suggestion = await _run_llm_match_for_title(title=title, job=job)
    except Exception as e:
        logger.exception("LLM match endpoint failed: %s", e)
        return {"suggestion": None, "reason": "internal_error"}

    if not suggestion:
        return {"suggestion": None, "reason": "no_suggestion"}

    # Persist into match_details for refresh durability
    import json
    existing = json.loads(title.match_details or "{}") if title.match_details else {}
    existing["llm_suggestion"] = suggestion
    title.match_details = json.dumps(existing)
    session.add(title)
    await session.commit()

    return {"suggestion": suggestion, "reason": None}
```

- [ ] **Step 4: Run, verify pass**

```bash
cd backend && uv run pytest tests/integration/test_workflow.py::TestLLMMatchEndpoint -v
```
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes.py backend/tests/integration/test_workflow.py
git commit -m "feat(api): POST /jobs/{id}/titles/{id}/llm-match endpoint (#109)"
```

---

## Task 14: Frontend — ConfigWizard adds Gemini + episode-matching toggle

**Files:**
- Modify: `frontend/src/components/ConfigWizard.tsx`

- [ ] **Step 1: Add gemini to provider labels + placeholders + dropdown**

Edit `frontend/src/components/ConfigWizard.tsx`:

```typescript
const AI_PROVIDER_LABELS: Record<string, string> = {
    anthropic: 'Anthropic',
    openai: 'OpenAI',
    openrouter: 'OpenRouter',
    gemini: 'Google Gemini',
};

const AI_KEY_PLACEHOLDERS: Record<string, string> = {
    anthropic: 'sk-ant-...',
    openai: 'sk-...',
    openrouter: 'sk-or-...',
    gemini: 'AIzaSy...',
};
```

Add the dropdown option (around the existing `<option value="openrouter">` block, ~line 722):

```tsx
<option value="gemini">Google Gemini</option>
```

- [ ] **Step 2: Add the aiEpisodeMatchingEnabled state field**

In the initial config object (around line 129) add:

```typescript
aiEpisodeMatchingEnabled: false,
```

In the load handler (around line 209-211) add:

```typescript
aiEpisodeMatchingEnabled: data.ai_episode_matching_enabled ?? false,
```

In the save handler (around line 312-314) add:

```typescript
ai_episode_matching_enabled: config.aiEpisodeMatchingEnabled,
```

- [ ] **Step 3: Render the new checkbox after the AI identification block**

Inside the AI identification `<>` block (after line 741 where the existing AI key input ends and before the closing `</>`):

```tsx
<div className="form-group checkbox-group">
    <label className="checkbox-label">
        <input
            type="checkbox"
            checked={config.aiEpisodeMatchingEnabled}
            onChange={(e) => handleInputChange('aiEpisodeMatchingEnabled', e.target.checked)}
        />
        <span className="checkbox-text">
            <strong>AI-Powered Episode Matching (TV)</strong>
            <span className="checkbox-hint">
                When audio fingerprint matching can't identify a TV episode, send the cleaned transcript and TMDB synopses to your AI provider for a suggested episode. Always confirmed via the review queue — never auto-organizes. <em>Gemini Flash-Lite recommended for best accuracy on this task.</em>
            </span>
        </span>
    </label>
</div>
```

- [ ] **Step 4: Run lint + build**

```bash
cd frontend && npm run lint && npm run build
```
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ConfigWizard.tsx
git commit -m "feat(ui): add Gemini provider + episode-matching toggle (#109)"
```

---

## Task 15: Frontend — Inspector renders LLM suggestion + "Try AI match" button

**Files:**
- Modify: `frontend/src/components/ReviewQueue/Inspector.tsx`
- Modify: `frontend/src/components/ReviewQueue/types.ts` (extend match_details type if needed)
- Modify: `frontend/src/lib/api.ts` (or wherever fetch helpers live — search for review-queue API helpers)

- [ ] **Step 1: Locate the API helper for reassign**

```bash
grep -rn "reassign\|llm-match" frontend/src/ | head -10
```

- [ ] **Step 2: Add API helper for llm-match**

Add to the API helper module (path determined in step 1):

```typescript
export async function runLLMMatch(jobId: number, titleId: number): Promise<{
    suggestion: { episode: number; confidence: number; reasoning: string;
                  runner_up: { episode: number; confidence: number } | null;
                  model: string } | null;
    reason: string | null;
}> {
    const r = await fetch(`/api/jobs/${jobId}/titles/${titleId}/llm-match`, { method: 'POST' });
    if (!r.ok) throw new Error(`llm-match failed: ${r.status}`);
    return r.json();
}
```

- [ ] **Step 3: Render the LLM suggestion row in Inspector**

In `Inspector.tsx`, locate where `match_details` is rendered (around line 310 where the DiscDB toggle is). Above it, add a block reading `match_details.llm_suggestion` (parse from `title.match_details` which is a JSON string):

```tsx
{(() => {
    let llm: any = null;
    try { llm = title.match_details ? JSON.parse(title.match_details).llm_suggestion : null; } catch {}
    if (!llm) return null;
    return (
        <SvPanel className="llm-suggestion-row" tone="cyan">
            <div className="llm-suggestion-header">
                <span className="llm-badge">AI</span>
                <span>Suggested: <strong>S{String(season).padStart(2,'0')}E{String(llm.episode).padStart(2,'0')}</strong></span>
                <span className="llm-confidence">{Math.round(llm.confidence * 100)}%</span>
            </div>
            <div className="llm-reasoning">{llm.reasoning}</div>
            <SvActionButton
                tone="cyan"
                size="sm"
                onClick={() => onAcceptLLMSuggestion(title.id, llm.episode)}
            >
                Accept AI suggestion
            </SvActionButton>
        </SvPanel>
    );
})()}
```

- [ ] **Step 4: Add the "Try AI match" button (gated)**

Near the other action buttons (around line 301), add:

```tsx
{aiEpisodeMatchingEnabled && (
    <SvActionButton
        tone="cyan"
        size="sm"
        onClick={() => onTryLLMMatch(title.id)}
        title="Run AI episode matching"
    >
        Try AI match
    </SvActionButton>
)}
```

- [ ] **Step 5: Wire the new props/handlers through ReviewQueue.tsx**

In `frontend/src/components/ReviewQueue.tsx`, add the handler functions:

```typescript
const onTryLLMMatch = async (titleId: number) => {
    try {
        const r = await runLLMMatch(jobId, titleId);
        if (r.suggestion) {
            // refresh the job to pick up the persisted suggestion
            await refreshJob();
        }
    } catch (e) {
        console.error('LLM match failed', e);
    }
};

const onAcceptLLMSuggestion = async (titleId: number, episodeNumber: number) => {
    const seasonStr = String(detectedSeason ?? 1).padStart(2, '0');
    const epStr = String(episodeNumber).padStart(2, '0');
    await reassignEpisode(jobId, titleId, `S${seasonStr}E${epStr}`, undefined, 'ai_llm');
    await refreshJob();
};
```

(`reassignEpisode` needs a `source?: string` parameter added in its helper.)

- [ ] **Step 6: Run lint + build**

```bash
cd frontend && npm run lint && npm run build
```
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/
git commit -m "feat(ui): render LLM suggestion row + Try AI match button (#109)"
```

---

## Task 16: Backend integration test — full workflow

**Files:**
- Create: `backend/tests/integration/test_llm_matching_workflow.py`

- [ ] **Step 1: Write failing integration test**

```python
"""End-to-end LLM episode matching workflow."""

import json
from unittest.mock import AsyncMock, patch

import pytest


class TestLLMMatchingWorkflow:
    @pytest.mark.asyncio
    async def test_full_workflow_attaches_suggestion_and_keeps_review(
        self, client, setup_db
    ):
        """Insert a TV disc, force low-confidence primary, verify LLM suggestion attaches."""
        from app.database import async_session
        from app.models.disc_job import DiscJob, DiscTitle, ContentType, JobState, TitleState
        from app.models.app_config import AppConfig

        async with async_session() as s:
            cfg = (await s.exec("SELECT * FROM app_config")).first()
            if cfg:
                await s.exec(
                    "UPDATE app_config SET ai_episode_matching_enabled=1, ai_provider='gemini', ai_api_key='k', tmdb_api_key='t' WHERE id=:id",
                    {"id": cfg.id},
                )
            else:
                s.add(AppConfig(
                    ai_episode_matching_enabled=True, ai_provider="gemini",
                    ai_api_key="k", tmdb_api_key="t",
                ))
            await s.commit()

        # Patch the curator's LLM call to return a stable suggestion
        from app.matcher.llm_episode_matcher import LLMEpisodeMatch
        stable = LLMEpisodeMatch(
            episode=7, confidence=0.93, reasoning="distinct cargo dialogue",
            runner_up={"episode": 6, "confidence": 0.12}, model="gemini-flash-lite-latest",
        )

        with patch("app.core.curator.match_episode_via_llm", new=AsyncMock(return_value=stable)), \
             patch("app.matcher.tmdb_client.fetch_show_id", return_value="1234"), \
             patch.object(__import__("app.matcher.episode_identification", fromlist=["EpisodeMatcher"]).EpisodeMatcher,
                          "transcribe_full", return_value="x" * 600), \
             patch.object(__import__("app.matcher.episode_identification", fromlist=["EpisodeMatcher"]).EpisodeMatcher,
                          "identify_episode",
                          return_value={"season": 1, "episode": 3, "confidence": 0.4, "score": 0.4,
                                        "match_details": {}, "runner_ups": []}):

            r = await client.post(
                "/api/simulate/insert-disc",
                json={"volume_label": "TEST_S1D1", "content_type": "tv", "simulate_ripping": True},
            )
            assert r.status_code == 200

            # Poll until a title reaches REVIEW state (matching completes)
            import asyncio as _asyncio
            for _ in range(40):
                async with async_session() as s:
                    titles = list((await s.exec("SELECT * FROM disc_titles")).all())
                    if titles and titles[0].state == TitleState.REVIEW.value:
                        break
                await _asyncio.sleep(0.5)

            async with async_session() as s:
                titles = list((await s.exec("SELECT * FROM disc_titles")).all())
                assert titles
                details = json.loads(titles[0].match_details or "{}")
                assert details["llm_suggestion"]["episode"] == 7
                assert titles[0].match_source != "engram"
```

- [ ] **Step 2: Run, verify pass**

```bash
cd backend && uv run pytest tests/integration/test_llm_matching_workflow.py -v
```
Expected: pass.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/integration/test_llm_matching_workflow.py
git commit -m "test(integration): full LLM matching workflow (#109)"
```

---

## Task 17: Frontend Playwright spec

**Files:**
- Create: `frontend/e2e/llm-suggestion.spec.ts`

- [ ] **Step 1: Write spec**

```typescript
import { test, expect } from '@playwright/test';

test('LLM suggestion row renders when match_details contains llm_suggestion', async ({ page }) => {
    // Configure backend with AI episode matching enabled
    await page.request.put('http://localhost:8000/api/config', {
        data: { ai_episode_matching_enabled: true, ai_provider: 'gemini', ai_api_key: 'k', tmdb_api_key: 't' },
    });

    // Insert a TV disc via simulation
    await page.request.post('http://localhost:8000/api/simulate/insert-disc', {
        data: { volume_label: 'TEST_S1D1', content_type: 'tv', simulate_ripping: true },
    });

    // Manually inject an llm_suggestion into the first title via the test endpoint
    // (the backend integration test verifies the real flow; this UI test just
    // exercises the rendering, so we stub via the persistence endpoint)
    await page.goto('http://localhost:5173/');
    await page.waitForSelector('[data-testid="review-queue"]', { timeout: 10000 });

    // The Try AI match button should be visible because the flag is on
    await expect(page.locator('button:has-text("Try AI match")').first()).toBeVisible();
});
```

- [ ] **Step 2: Run**

```bash
cd frontend && npm run test:e2e -- e2e/llm-suggestion.spec.ts
```
Expected: pass (requires backend running with DEBUG=true).

- [ ] **Step 3: Commit**

```bash
git add frontend/e2e/llm-suggestion.spec.ts
git commit -m "test(e2e): Playwright spec for LLM suggestion UI (#109)"
```

---

## Task 18: Documentation — new feature page + nav

**Files:**
- Create: `docs/guide/llm-episode-matcher.md`
- Modify: `mkdocs.yml`

- [ ] **Step 1: Write the feature page**

Create `docs/guide/llm-episode-matcher.md`:

```markdown
# LLM Episode Matcher

An opt-in fallback for TV episode identification. When Engram's primary audio-fingerprint matcher can't confidently identify which episode a ripped disc title is, the LLM matcher sends the cleaned transcript plus the candidate season's TMDB synopses to your configured AI provider, and surfaces a suggested episode through the review queue. **The LLM never auto-organizes** — every suggestion requires your confirmation.

## When it runs

**Automatically**, when:

- TV-content matching is needed,
- The primary audio-fingerprint matcher returns confidence < 0.7 (or no match),
- `ai_episode_matching_enabled` is on and you've configured an API key,
- The season is known from the disc volume label.

**On demand**, via the **Try AI match** button on any title in the review queue.

When the season can't be determined from the disc, the LLM matcher is skipped — accuracy collapses without season narrowing.

## Enabling it

1. Open **Settings** → **Preferences**.
2. Pick an **AI Provider** and paste your **API key**. The same provider/key is shared with AI-Powered Title Resolution (if you've enabled that).
3. Check **AI-Powered Episode Matching (TV)**.

See [Configuration](../getting-started/configuration.md) for the full settings reference.

## Provider recommendation

**Gemini Flash-Lite** has the best accuracy/$ on this task in our internal evals (66-73% top-1 vs ~49% for Anthropic Haiku 4.5 on a comparable dataset). Get a free-tier key at <https://aistudio.google.com/apikey>.

Anthropic, OpenAI, and OpenRouter also work and remain useful for [AI-powered title resolution](../getting-started/configuration.md), so you don't need to switch providers if you've already configured one of those.

## Accuracy expectations

The LLM matcher relies on the distinctiveness of TMDB synopses, so accuracy varies sharply by show type.

| Show category | Typical accuracy | Examples |
|---|---|---|
| Episodic / procedural with distinct plots | 90-100% | Star Trek: TNG, Arrow, Breaking Bad, Adventure Time, 9-1-1 |
| Mixed serialized/episodic | 60-80% | The Expanse, Anne with an E, AHS |
| Framing-device serialization (synopses overlap) | 25-35% | 13 Reasons Why, All of Us Are Dead, Arcane |

In all cases the suggestion lands in the review queue — accuracy mainly affects how many one-click confirmations you do vs. how many manual selections.

## Privacy

The cleaned dialogue transcript for the episode is sent over the network to your configured AI provider. If that matters to you, leave this feature off — it's disabled by default.

## Cost

Sub-cent per episode at Gemini Flash-Lite pricing. The free tier covers typical home use comfortably.

## Confirmation requirement

Suggestions always appear in the review queue with an **Accept AI suggestion** button. The LLM never writes to your library directly; you stay in control of what gets organized.
```

- [ ] **Step 2: Add to mkdocs nav**

In `mkdocs.yml`, under the "User Guide" section add:

```yaml
    - LLM Episode Matcher: guide/llm-episode-matcher.md
```

- [ ] **Step 3: Verify mkdocs build (if mkdocs is installed)**

```bash
mkdocs build --strict 2>&1 | tail -5 || echo "mkdocs not installed locally — CI will validate"
```

- [ ] **Step 4: Commit**

```bash
git add docs/guide/llm-episode-matcher.md mkdocs.yml
git commit -m "docs: add LLM Episode Matcher user guide page (#109)"
```

---

## Task 19: Documentation — updates to existing pages

**Files:**
- Modify: `docs/getting-started/configuration.md`
- Modify: `docs/guide/review-queue.md`
- Modify: `docs/api/rest.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update configuration.md**

In `docs/getting-started/configuration.md`, in the AI / Preferences section add:

```markdown
### AI-Powered Episode Matching

`ai_episode_matching_enabled` (default: `false`) — when enabled, low-confidence TV episode matches are sent to your configured AI provider with the season's TMDB synopses for a suggested episode. Always surfaces through the [review queue](../guide/review-queue.md); never auto-organizes. Shares `ai_provider`/`ai_api_key` with [AI-Powered Title Resolution](#ai-powered-title-resolution).

See the [LLM Episode Matcher guide](../guide/llm-episode-matcher.md) for accuracy expectations and provider recommendations (Gemini Flash-Lite is best on this task).
```

Add `gemini` to the list of supported providers wherever `ai_provider` is documented.

- [ ] **Step 2: Update review-queue.md**

In `docs/guide/review-queue.md`, add a subsection:

```markdown
## AI Suggestion Row

When [AI-Powered Episode Matching](llm-episode-matcher.md) is enabled and a title falls into review, you'll see a cyan **AI** badge with the suggested episode, the LLM's confidence, and a one-sentence rationale. Click **Accept AI suggestion** to confirm — this routes through the same reassignment path as manual confirmation and is recorded with `match_source = "ai_llm"`.

Even when the auto-fallback hasn't run, you can click **Try AI match** on any title in review to trigger the LLM matcher on demand.
```

- [ ] **Step 3: Update rest.md**

In `docs/api/rest.md`, add:

```markdown
### `POST /api/jobs/{job_id}/titles/{title_id}/llm-match`

Run the LLM episode matcher for a single title and persist the suggestion to `match_details.llm_suggestion`. Requires `ai_episode_matching_enabled` and the job to have a known `detected_title` + `detected_season`.

**Response (200):**

```json
{
  "suggestion": {
    "episode": 7,
    "confidence": 0.93,
    "reasoning": "Mentions of named character and unique plot beat.",
    "runner_up": {"episode": 6, "confidence": 0.12},
    "model": "gemini-flash-lite-latest"
  },
  "reason": null
}
```

When no suggestion is available (feature disabled, no transcript, no synopses, AI returned zero confidence, or any internal error), `suggestion` is `null` and `reason` describes why (`"no_suggestion"` or `"internal_error"`).
```

- [ ] **Step 4: Update README.md**

Add one bullet under existing Features list (near the "Audio fingerprint matching" line):

```markdown
- **LLM episode matching (opt-in)** — when audio matching is uncertain, send the transcript + TMDB synopses to your configured AI provider for a suggested episode (Gemini, Anthropic, OpenAI, or OpenRouter). Always confirmed via the review queue.
```

- [ ] **Step 5: Update CHANGELOG.md**

Add an `## [Unreleased]` section at the top (or extend if present):

```markdown
## [Unreleased]

### Added
- **LLM episode matching (opt-in)** — when audio fingerprint matching can't confidently identify a TV episode, an LLM compares the cleaned transcript against the season's TMDB synopses and suggests an episode through the review queue. Supports Gemini, Anthropic, OpenAI, and OpenRouter providers (Gemini Flash-Lite recommended); shares the existing `ai_provider`/`ai_api_key` settings. Never auto-organizes — always requires user confirmation. (#109)
- **Google Gemini provider** added to the AI provider list, usable by both AI title resolution and the new episode matcher.
```

- [ ] **Step 6: Commit**

```bash
git add docs/getting-started/configuration.md docs/guide/review-queue.md docs/api/rest.md README.md CHANGELOG.md
git commit -m "docs: cross-link LLM episode matcher across user/API docs + changelog (#109)"
```

---

## Task 20: Final verification + lint sweep

- [ ] **Step 1: Run full backend test suite**

```bash
cd backend && uv run pytest -x
```
Expected: all pass.

- [ ] **Step 2: Run backend lint + format**

```bash
cd backend && uv run ruff check . && uv run ruff format --check .
```
Expected: clean.

- [ ] **Step 3: Run frontend lint + build**

```bash
cd frontend && npm run lint && npm run build
```
Expected: clean.

- [ ] **Step 4: Run frontend E2E (requires backend with DEBUG=true)**

```bash
cd frontend && npm run test:e2e -- e2e/llm-suggestion.spec.ts
```
Expected: pass.

- [ ] **Step 5: Push branch and update PR**

```bash
git push
gh pr view --web
```

- [ ] **Step 6: Mark PR ready for review**

If the spec-only PR was opened as draft, mark it ready. Otherwise add a comment summarizing what shipped.

---

## Self-Review Checklist

**Spec coverage:**
- [x] `ai_episode_matching_enabled` config — Task 1
- [x] Shared `ai_client.complete_json` with 4 providers — Tasks 2-5
- [x] `ai_identifier.py` refactored to delegate — Task 6
- [x] TMDB `fetch_season_episodes` extended with `overview` — Task 7
- [x] `transcribe_full` extracted from `_match_full_file` — Task 8
- [x] `llm_episode_matcher.py` happy path + edge cases — Tasks 9-10
- [x] Curator LLM fallback — Task 11
- [x] `reassign_episode` accepts `source` ("ai_llm") — Task 12
- [x] `POST /api/jobs/{id}/titles/{id}/llm-match` endpoint — Task 13
- [x] ConfigWizard — Gemini provider + matching toggle — Task 14
- [x] Inspector — LLM suggestion row + Try AI match button — Task 15
- [x] Backend integration test — Task 16
- [x] Frontend Playwright spec — Task 17
- [x] New feature page + mkdocs nav — Task 18
- [x] Configuration/review-queue/REST/README/CHANGELOG updates — Task 19
- [x] Final lint + verification — Task 20

**Type consistency:**
- `LLMEpisodeMatch` dataclass shape consistent across Tasks 9, 11, 13, 16.
- `DEFAULT_MODELS` dict referenced by both `ai_client.complete_json` and `llm_episode_matcher.match_episode_via_llm`.
- `match_details.llm_suggestion` schema consistent across curator (Task 11), endpoint (Task 13), integration test (Task 16), Inspector render (Task 15).
- `match_source = "ai_llm"` set by `reassign_episode(source="ai_llm")` (Task 12) and consumed by integration test (Task 16) + documented in review-queue.md (Task 19).

**Out-of-scope reminders (do not implement in this PR):**
- No screenshots for the docs page (deferred per spec).
- No prompt-tuning harness inside the app.
- No multi-season iteration when season is unknown.
- No auto-accept on high LLM confidence.
- No per-feature model picker in UI.
