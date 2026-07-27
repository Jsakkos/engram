# AI connection test + provider error detail

Date: 2026-07-26
Status: Approved, ready for implementation planning

## Problem

A user reported that LLM episode matching does not work for them, including with a
paid OpenAI account. The dashboard showed:

```
Request failed (503 Service Unavailable): {"suggestion":null,"reason":"llm_error"}
```

and the backend log showed:

```
app.core.errors.AIProviderError: gemini request failed: Client error '429 Too Many
Requests' for url 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent'
POST /api/jobs/182/titles/1436/llm-match HTTP/1.1" 503
```

### This is not a regression

`git log v0.24.0..HEAD` over the entire LLM-match call chain (`ai_client.py`,
`llm_episode_matcher.py`, `curator.py`, `tmdb_client.py`, `ripping_helpers.py`,
`app_config.py`, `config_service.py`, `ReviewQueue.tsx`, `Inspector.tsx`,
`client.ts`) shows the only touching commits are #533, #525, #508 and #501, none
of which affect AI matching. The two files that matter last changed in
`eeb36be5` on 2026-06-06, shipped in v0.16.3. The 24 unit tests in
`test_ai_client.py` and `test_llm_episode_matcher.py` pass.

The log line above is itself proof the pipeline works: transcription completed,
TMDB synopses were fetched, the prompt was built, and the HTTP request reached
the provider. The provider refused it.

### What is actually broken: the diagnosis is impossible

Provider failure information is destroyed at three layers.

1. **The response body is discarded.** `resp.raise_for_status()` raises
   `httpx.HTTPStatusError`, whose string carries status and URL but not the body.
   `ai_client.py:113` logs that bare exception. The body is the only thing that
   distinguishes OpenAI's `rate_limit_exceeded` (transient) from
   `insufficient_quota` (permanent, no credits).
2. **Every failure collapses to one reason.** `AIProviderError` becomes
   `LLMMatchOutcome.failed("llm_error")` at `routes.py:4339`. Bad key, no
   credits, throttling, model not enabled, DNS failure and timeout are
   indistinguishable.
3. **The UI renders the raw error.** `ReviewQueue.tsx:429` catches the 503 and
   shows `ApiError.message`, which is `Request failed (503 ...): {body}`
   (`client.ts:16`). The comment at `ReviewQueue.tsx:413` still claims the
   endpoint always returns 200, which stopped being true when #350 added the 503
   contract; the frontend was never updated to map those reasons.

Two secondary findings:

- `_with_429_retry` (`ai_client.py:38`) retries every 429 three times with
  backoff. OpenAI returns 429 for `insufficient_quota`, which never resolves, so
  a credit-less key costs four requests and roughly seven seconds of backoff
  before reporting the same opaque error.
- A credential problem is only discovered *after* 1 to 3 minutes of Whisper
  transcription, because `_run_llm_match_for_title` transcribes before it ever
  contacts the provider. There is no cheap pre-flight check anywhere in the app.

## Goals

1. Let a user verify AI provider credentials in one click, before spending a
   transcription run, and get a specific reason when they do not work.
2. Make every AI provider failure, including those during real matching, report
   a specific cause rather than `llm_error`.

## Non-goals

Filed as separate follow-up issues, deliberately out of this bundle:

- Un-nesting the AI provider dropdown, key field and "AI-Powered Episode
  Matching (TV)" toggle from `{config.aiIdentificationEnabled && ...}` at
  `ConfigWizard.tsx:1135`, which currently makes episode matching
  unconfigurable unless disc identification is also enabled.
- Clearing the "Key saved" badge when the user switches provider, so a stale
  key from a previous provider cannot be silently reused.

## Design

### 1. `classify_provider_error` in `ai_client.py`

A single function shared by both features:

```python
def classify_provider_error(provider: str, exc: Exception) -> tuple[str, str]:
    """Return (code, human_message) for an httpx failure."""
```

| Code | Meaning | Typical trigger |
|------|---------|-----------------|
| `bad_key` | Provider rejected the credential | 401, or Gemini 400 `INVALID_ARGUMENT` on the key |
| `no_credits` | Key is valid, account cannot pay | OpenAI 429 `insufficient_quota`, OpenRouter 402 |
| `rate_limited` | Transient throttling, retry helps | OpenAI 429 `rate_limit_exceeded`, Gemini 429 `RESOURCE_EXHAUSTED` |
| `model_unavailable` | Key lacks access to the default model | 404 `model_not_found`, 403 `PERMISSION_DENIED` |
| `bad_request` | Request shape rejected | 400 that is not a credential problem |
| `network` | Could not reach the provider | `httpx.ConnectError` |
| `timeout` | No response in time | `httpx.TimeoutException` |
| `unknown` | Anything else | 5xx, unparseable body |

The function reads `exc.response.text` where present, caps it (2 KB) and never
echoes it verbatim to the UI; the human message is composed by Engram. The
provider-specific identifiers above are the expected shapes and **must be
confirmed against captured live error bodies during implementation**, per the
project's fixture-fidelity rule. Note that OpenRouter signals exhausted credit
with HTTP 402 rather than OpenAI's 429, so the two cannot share a status-only
branch.

`AIProviderError` gains `code: str` and `detail: str` attributes so the
classification survives the trip up the stack. Existing constructor calls keep
working; both fields default.

### 2. `complete_json` gains `retries`

Add `retries: int = MAX_RETRIES` and thread it into `_with_429_retry`. The test
endpoint passes `retries=0` so a dead key fails in one request instead of
absorbing the backoff ladder.

Independently, `_with_429_retry` stops retrying when `classify_provider_error`
returns `no_credits`: a permanently exhausted quota does not recover within the
retry window, and the current behaviour only adds latency to a guaranteed
failure.

### 3. `POST /api/validate/ai`

Lives in `backend/app/api/validation.py` beside the existing validators, reuses
the existing `ValidationResponse` model, and is gated by
`require_localhost_or_lan`. The gate matters here in a way it does not for the
other validators: this endpoint spends the user's money, so it must not be
reachable from a LAN peer unless the user has explicitly opted in via
`allow_lan_access`.

Request:

```json
{ "provider": "openai", "api_key": "sk-..." }
```

`api_key` is optional. When omitted or blank, the endpoint falls back to the key
stored in config. This is the primary support case: the user already saved a key,
it does not work, and they do not have it available to re-paste. The stored key
is never returned in the response.

The check calls the same entry point the matcher uses:

```python
await complete_json(
    prompt='Reply with {"ok": true} and nothing else.',
    schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
    provider=provider,
    api_key=api_key,
    max_tokens=16,
    retries=0,
    raise_on_error=True,
)
```

Routing through `complete_json` rather than a bespoke HTTP call is the point of
the design. A separate auth ping would validate a different request shape than
the matcher sends (different endpoint, no `response_format` or `responseSchema`,
possibly a different model) and could therefore pass while real matching fails.
Reusing the entry point means the test can only drift if the matcher drifts with
it. Cost is a fraction of a cent per click.

Timeout for the test is 10 seconds rather than the module's 30, since a healthy
provider answers a 16-token completion well inside that and a user waiting on a
button should not sit for half a minute. This requires a second new parameter on
`complete_json`, `timeout: float = _TIMEOUT_SECONDS`, threaded through to each
`_call_*` adapter's `httpx.AsyncClient(timeout=...)`. `_TIMEOUT_SECONDS` stays
the default, so no existing call site changes behaviour.

Responses:

- Success: `{"valid": true, "version": "gpt-4o-mini"}`. Surfacing the model that
  answered tells the user which model Engram defaults to for their provider.
- Failure: `{"valid": false, "error": "<human message from the classifier>"}`,
  for example: *"OpenAI accepted the key but the account has no API credits. A
  ChatGPT Plus subscription does not include API usage."*

### 4. The real matcher reports the same causes

`_run_llm_match_for_title` (`routes.py:4339`) catches `AIProviderError` and
includes its `code` in the 503 body alongside the existing
`reason: "llm_error"`, keeping the current contract additive:

```json
{"suggestion": null, "reason": "llm_error", "detail": "no_credits",
 "message": "OpenAI accepted the key but the account has no API credits."}
```

`llmResultToFeedback` in `ReviewQueue.tsx` grows a 503 branch that renders
`message` in the Inspector instead of the raw `ApiError.message`. The stale
"endpoint always returns 200" comment at `ReviewQueue.tsx:413` is corrected. The
`LLMMatchResult` doc block in `client.ts` is updated to document the new fields.

This half is what makes the bundle worth shipping together: without it, the test
button explains a failure at configuration time while mid-rip and post-rip
failures stay unreadable, and the two paths would describe the same underlying
problem in two different vocabularies.

### 5. Frontend

`frontend/src/utils/aiValidation.ts` mirrors `tmdbValidation.ts`, including its
deliberate three-way split between `valid` (provider accepted), `invalid` (the
check ran and the provider rejected) and `error` (the check could not run, so
nothing was learned about the key). It fixes one wart in the TMDB original: when
the field is empty and a key is saved, it omits `api_key` so the backend tests
the stored key, rather than posting an empty string that always fails.

A **Test Connection** button sits beside the AI key field in `ConfigWizard.tsx`,
matching the TMDB "Test Token" button's `idle | testing | valid | invalid |
error` states and its enable rule (`!testing && (keyEntered || savedKeys.ai)`).

Client-side pre-flight: `AI_KEY_PLACEHOLDERS` already encodes each provider's key
prefix (`sk-ant-`, `sk-`, `sk-or-`, `AIzaSy`). When the entered key does not match
the selected provider's prefix, the button reports the mismatch without spending
a request. This is a hint, not a gate: an unrecognised prefix warns but still
allows the test, since provider key formats change.

### 6. Testing

Backend unit:

- `classify_provider_error` against captured verbatim 4xx bodies from all four
  providers, one case per code in the table.
- `complete_json(retries=0)` issues exactly one request on a 429.
- `_with_429_retry` does not retry when the classifier returns `no_credits`.

Backend route:

- `/api/validate/ai` with `complete_json` mocked: success, each failure code,
  stored-key fallback when `api_key` is omitted, empty-provider rejection, and
  LAN rejection when `allow_lan_access` is off.
- `llm-match` 503 body carries `detail` and `message`.

Frontend:

- vitest for `aiValidation.ts` covering valid, invalid, network failure,
  non-JSON response and the omitted-key path.
- vitest for the button's state transitions and the prefix-mismatch hint.
- vitest for `llmResultToFeedback`'s 503 branch.

## Open questions

None. Provider error-body shapes are stated as expected values above and are to
be verified against live captures during implementation, which is a build step
rather than a design decision.
