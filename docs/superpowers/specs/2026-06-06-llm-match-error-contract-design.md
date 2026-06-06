# LLM-match endpoint error contract — design

**Date:** 2026-06-06
**Status:** Approved (taxonomy + status policy confirmed with user)
**Scope:** Backend only (+ frontend contract comment). The review-Inspector UI that
renders these reasons is a **separate** follow-up task and is out of scope here.

## Problem

`POST /api/jobs/{job_id}/titles/{title_id}/llm-match` (the review Inspector's
"Try LLM match" button) has a contract too coarse to act on:

1. **Genuine server errors masquerade as success.** The endpoint
   ([`llm_match_title`](../../../backend/app/api/routes.py)) wraps the helper in a
   blanket `try/except Exception` and on *any* exception returns **HTTP 200** with
   `{"suggestion": null, "reason": "internal_error"}`. A client cannot tell a
   transient failure (retry later) from "ran fine, no match" — the frontend
   `try/catch` never even fires because the response is 2xx.
2. **Eight distinct preconditions collapse into one reason.**
   [`_run_llm_match_for_title`](../../../backend/app/api/routes.py) returns bare
   `None` for AI-disabled, not-configured, missing show, missing season, matcher
   init failure, no TMDB show id, empty transcript, and LLM-no-confidence — all of
   which the endpoint flattens into a single `reason="no_suggestion"`. A user can't
   tell "AI matching is turned off" from "couldn't find a match".

## Goal

A differentiated, predictable contract:
- Distinct `reason` strings, one per failure mode.
- HTTP status that lets clients/monitoring react: 2xx for deterministic
  ran-but-no-result outcomes, 5xx for failures worth surfacing (transient
  operational failures + unexpected crashes).
- Preserve the existing cached-suggestion idempotency (no re-transcription on
  double-click) and the existing `logger.exception(...)` diagnostic trace.

## Reason taxonomy (full granular)

| `reason` | HTTP | `suggestion` | Branch / meaning |
|---|---|---|---|
| `null` | 200 | object | Success — LLM returned a confident match (persisted). |
| `cached` | 200 | object | Idempotent re-click; returned from `match_details.llm_suggestion` without re-transcribing. |
| `ai_disabled` | 200 | `null` | `ai_episode_matching_enabled` is false in config. |
| `not_configured` | 200 | `null` | Enabled but no `ai_api_key` set. |
| `no_show` | 200 | `null` | Job has no `detected_title`. |
| `no_season` | 200 | `null` | Job has no `detected_season`. |
| `show_not_found` | 200 | `null` | TMDB show-id lookup (`fetch_show_id`) returned nothing. |
| `no_match` | 200 | `null` | Matcher ran *successfully* but produced no confident episode — empty/short transcript content, no TMDB synopses, or the model returned zero-confidence. Strictly "ran fine, not confident." |
| `matcher_unavailable` | **503** | `null` | Episode matcher failed to initialize for the show. Operational / retryable. |
| `transcription_failed` | **503** | `null` | Whisper produced an empty transcript. Operational / retryable. |
| `llm_error` | **503** | `null` | The LLM provider call itself failed — 429-exhausted, auth, out-of-credits, provider 5xx, or network error. Operational / retryable (external upstream). |
| `internal_error` | **500** | `null` | Unexpected exception caught by the endpoint. `logger.exception(...)` records the trace. |

**Status policy** (confirmed): only *deterministic* config/data outcomes stay 200.
The operational failures (`matcher_unavailable`, `transcription_failed`, `llm_error`)
return **503** so they read as retryable. Unexpected exceptions return **500**. This
means on the frontend `runLLMMatch` *throws* `ApiError` only for 503/500; every 200
carries an actionable `reason`.

**`no_match` vs `llm_error`** (the gap this revision closes): previously a provider
failure (Gemini 429 / out-of-credits / auth / 5xx) was swallowed into `None` by
`complete_json` and would have read as `no_match` — indistinguishable from "the model
ran and wasn't confident." We now separate them: `no_match` means the provider
responded but there was no confident answer; `llm_error` means the provider call
failed and is worth a retry.

**Uniform body shape** (confirmed): the 503 and 500 responses use the *same*
`{"suggestion": null, "reason": "..."}` body (via `JSONResponse`), so monitoring and
the frontend can always read `reason` regardless of status. Because the status is
still non-2xx, the frontend `apiFetch` wrapper still throws `ApiError` (with the JSON
body captured in `ApiError.body`).

## Design

### Helper returns an outcome, not bare `None`

Introduce a small immutable result object so the helper communicates the distinct
reason up to the endpoint instead of erasing it:

```python
@dataclass(frozen=True)
class LLMMatchOutcome:
    suggestion: dict | None
    reason: str | None  # None == success

    @classmethod
    def ok(cls, suggestion: dict) -> "LLMMatchOutcome":
        return cls(suggestion=suggestion, reason=None)

    @classmethod
    def failed(cls, reason: str) -> "LLMMatchOutcome":
        return cls(suggestion=None, reason=reason)
```

`_run_llm_match_for_title` returns `LLMMatchOutcome` at every branch (e.g.
`return LLMMatchOutcome.failed("ai_disabled")`, … , `return LLMMatchOutcome.ok({...})`).
The combined `not config.ai_api_key or not job.detected_title or not job.detected_season`
guard is **split** into three separate checks so `not_configured` / `no_show` /
`no_season` are distinguishable (in that order, matching the table). The helper still
raises on truly unexpected errors — those are caught by the endpoint.

### Surfacing LLM-provider errors (`llm_error`)

`complete_json` ([`ai_client.py`](../../../backend/app/core/ai_client.py)) currently
swallows *every* provider failure into `return None` (the `except httpx.HTTPError` /
`except Exception` blocks), so the error never reaches the endpoint. `match_episode_via_llm`
has **two** callers — our endpoint helper *and* the main matching pipeline
(`curator.py`), which relies on `None` → silent fall-through. So the fix must be
**opt-in**, leaving existing callers' behavior untouched:

1. Add `AIProviderError(EngramError)` to [`app/core/errors.py`](../../../backend/app/core/errors.py).
2. `complete_json(..., raise_on_error: bool = False)`: when `True`, the
   `except httpx.HTTPError` branch raises `AIProviderError(...) from e` instead of
   returning `None`; the generic `except Exception` branch **re-raises** (so genuine
   bugs still bubble up as `internal_error`/500, not `llm_error`). The early
   `None` returns (empty api_key, unknown provider) and the "valid response but empty/
   unparseable body" path are unchanged — those are not transport failures and remain
   `no_match`/config outcomes.
3. `match_episode_via_llm(..., raise_on_error: bool = False)`: threads the flag into
   `complete_json` and lets `AIProviderError` propagate. Default `False` keeps the
   curator path returning `None` exactly as today.
4. `_run_llm_match_for_title` calls it with `raise_on_error=True` and wraps just that
   call: `except AIProviderError → LLMMatchOutcome.failed("llm_error")`. Any other
   exception propagates to the endpoint's catch-all → `internal_error`.

### Endpoint maps reason → status

```python
# Operational failures the caller may retry; everything else (deterministic
# config/data outcomes) is a normal 200 with a differentiated reason.
_LLM_MATCH_RETRYABLE_REASONS = frozenset(
    {"matcher_unavailable", "transcription_failed", "llm_error"}
)
```

`llm_match_title` flow (cache check unchanged, runs *before* the helper):

```python
try:
    outcome = await _run_llm_match_for_title(title=title, job=job)
except Exception:
    logger.exception("LLM match endpoint failed for title %s", sanitize_log_value(title_id))
    return JSONResponse(status_code=500, content={"suggestion": None, "reason": "internal_error"})

if outcome.reason in _LLM_MATCH_RETRYABLE_REASONS:
    return JSONResponse(status_code=503, content={"suggestion": None, "reason": outcome.reason})

if outcome.suggestion is None:
    return {"suggestion": None, "reason": outcome.reason}  # 200 + differentiated reason

# success → persist into match_details for refresh durability (unchanged)
existing["llm_suggestion"] = outcome.suggestion
title.match_details = json.dumps(existing)
session.add(title)
await session.commit()
return {"suggestion": outcome.suggestion, "reason": None}
```

Add `JSONResponse` to the existing `from fastapi.responses import ...` line.

### Why this shape

- **One return type from the helper** keeps the interface honest: the endpoint never
  re-infers a reason it threw away. The helper is independently testable — given a
  config and mocked deps, it returns a known `LLMMatchOutcome`.
- **The retryable set is the single source of truth** for the 200-vs-503 split, so
  there's one place to change the policy.
- **Uniform error body** means the frontend's existing throw-on-non-2xx behavior
  (`apiFetch` → `ApiError`) keeps working, and the reason is still machine-readable
  in `ApiError.body` for the sibling UI task.

## Frontend contract (comment only)

`frontend/src/api/client.ts`: update the doc-comment on `LLMMatchResult` /
`runLLMMatch` to enumerate the full reason set and note the status mapping:
- 200 with `reason` ∈ {`null`, `cached`, `ai_disabled`, `not_configured`, `no_show`,
  `no_season`, `show_not_found`, `no_match`} — normal `LLMMatchResult`.
- 503 (`matcher_unavailable`, `transcription_failed`, `llm_error`) and 500
  (`internal_error`): `runLLMMatch` **throws `ApiError`**; the same
  `{suggestion: null, reason}` body is carried in `ApiError.body`.

The `reason: string | null` type is **not** narrowed and no UI logic is added here —
that's the sibling frontend follow-up. Comment only.

## Testing

Existing `TestLLMMatchEndpoint` tests mock the whole helper. Two changes / additions:

1. **Update** `test_returns_suggestion_and_persists`: its `fake_run` now returns
   `LLMMatchOutcome.ok({...})` instead of a bare dict (the endpoint now consumes an
   outcome). The cached test is unaffected (it returns before the helper runs).
2. **Add reason-taxonomy tests that exercise the *real* helper** with mocked
   dependencies, covering every branch. The helper's `from ... import` statements are
   *inside* the function body, so each call re-reads the module attribute — monkeypatching
   `app.services.config_service.get_config`, `app.matcher.tmdb_client.fetch_show_id`,
   `app.matcher.llm_episode_matcher.match_episode_via_llm`, and
   `app.core.curator.episode_curator` (`_ensure_initialized` + `_matcher`) is sufficient,
   no real Whisper/TMDB/AI calls.

   | Test | Mock setup | Assert |
   |---|---|---|
   | `ai_disabled` | config `ai_episode_matching_enabled=False` | 200, reason `ai_disabled` |
   | `not_configured` | enabled, `ai_api_key=""` | 200, reason `not_configured` |
   | `no_show` | enabled+key, job `detected_title=None` | 200, reason `no_show` |
   | `no_season` | enabled+key, job `detected_season=None` | 200, reason `no_season` |
   | `matcher_unavailable` | `_ensure_initialized` no-op, `_matcher=None` | **503**, reason `matcher_unavailable` |
   | `show_not_found` | matcher present, `fetch_show_id`→`None` | 200, reason `show_not_found` |
   | `transcription_failed` | matcher present, show id ok, `transcribe_full`→`""` | **503**, reason `transcription_failed` |
   | `no_match` | all ok, `match_episode_via_llm`→`None` | 200, reason `no_match` |
   | `llm_error` | all ok, `match_episode_via_llm` raises `AIProviderError` | **503**, reason `llm_error` |
   | `internal_error` | `get_config` raises (or a non-provider exception) | **500**, reason `internal_error` |
   | success (real helper) | all ok, `match_episode_via_llm`→fake match | 200, reason `null`, persisted in `match_details` |

   Add a focused `complete_json` unit test too: with `raise_on_error=True` and a mocked
   provider call raising `httpx.HTTPStatusError`, assert it raises `AIProviderError`;
   with `raise_on_error=False` (default), assert it still returns `None` (so the
   curator/disc-ID paths are provably unchanged).

   Use a `SimpleNamespace`/`Mock` for the fake config and the fake `LLMEpisodeMatch`
   (`episode`, `confidence`, `reasoning`, `runner_up=None`, `model`).

## Verification

- `cd backend && uv run pytest tests/integration/test_workflow.py -k llm -q`
- `uv run ruff check app/api/routes.py`

## Changelog

`### Changed` bullet under `## [Unreleased]` in `CHANGELOG.md`: the LLM-match endpoint
now returns differentiated `reason` strings (incl. distinguishing an LLM-provider
failure from "no confident match") and uses 503/500 for operational/unexpected
failures (previously every failure was a 200 `internal_error`/`no_suggestion`).

## Out of scope

- Review-Inspector UI rendering of the new reasons (sibling frontend task).
- Splitting `no_match` further into its remaining sub-reasons — transcript-too-short
  vs no-TMDB-synopses vs zero-confidence (provider failure *is* now split out as
  `llm_error`; the rest would need `match_episode_via_llm` to return a richer result).
- Distinguishing *retryable* provider errors (429/5xx/network) from *permanent* ones
  (auth/out-of-credits) — `complete_json` collapses the HTTP status, so all map to the
  single retryable `llm_error` for v1.
- A "force re-run / re-transcribe" path (cached idempotency stays as-is).
