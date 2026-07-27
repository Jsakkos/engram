# AI Connection Test + Provider Error Detail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop Engram from reporting "No confident AI match found" when the AI matcher never ran, and give users a one-click way to verify AI provider credentials before spending a transcription run.

**Architecture:** Three layers, in dependency order. First a frontend-only honesty fix so each backend reason gets its own message (highest user value, zero backend risk). Then `ai_client.py` hardening: a shared `classify_provider_error` that reads the provider's response body, plus closing two silent-failure shapes (truncated responses, null content). Finally a `POST /api/validate/ai` endpoint that round-trips a tiny prompt through the same `complete_json` the matcher uses, surfaced as a Test Connection button.

**Tech Stack:** Python 3.11 / FastAPI / httpx / pytest on the backend; React 18 / TypeScript / vitest / React Testing Library on the frontend. Backend commands run from `backend/` with `uv run`; frontend commands from `frontend/`.

**Spec:** `docs/superpowers/specs/2026-07-26-ai-connection-test-design.md`

---

## File Structure

**Backend**

| File | Responsibility | Change |
|------|----------------|--------|
| `backend/app/core/errors.py` | Exception hierarchy | Modify: `AIProviderError` gains `code` / `detail` |
| `backend/app/core/ai_client.py` | Provider transport + JSON parsing | Modify: classifier, `retries`/`timeout` params, adapter hardening |
| `backend/app/api/validation.py` | Pre-flight validators | Modify: add `POST /api/validate/ai` |
| `backend/app/api/routes.py` | REST surface | Modify: `llm-match` 503 body carries the classified cause |
| `backend/tests/unit/test_ai_client.py` | ai_client tests | Modify: new cases |
| `backend/tests/unit/test_validation_ai.py` | Endpoint tests | Create |

**Frontend**

| File | Responsibility | Change |
|------|----------------|--------|
| `frontend/src/components/ReviewQueue/llmFeedback.ts` | Reason to message mapping | Modify: one message per reason |
| `frontend/src/components/ReviewQueue/llmFeedback.test.ts` | Mapping tests | Modify: one case per reason |
| `frontend/src/components/ReviewQueue.tsx` | Review page state | Modify: track key-present, pass gating prop |
| `frontend/src/components/ReviewQueue/Inspector.tsx` | Title inspector | Modify: disable button when unusable |
| `frontend/src/components/ReviewQueue/Inspector.test.tsx` | Inspector tests | Modify: gating cases |
| `frontend/src/api/client.ts` | API types | Modify: `LLMMatchResult` gains `detail` / `message` |
| `frontend/src/utils/aiValidation.ts` | Connection-test client | Create |
| `frontend/src/utils/aiValidation.test.ts` | Its tests | Create |
| `frontend/src/components/ConfigWizard.tsx` | Settings UI | Modify: Test Connection button |

Phase 1 (Tasks 1 to 4) is independently shippable and delivers the highest-value fix. Phase 2 (Tasks 5 to 10) hardens the client. Phase 3 (Tasks 11 to 14) adds the connection test.

---

## Phase 1: Honest reasons in the Inspector

### Task 1: One message per reason in `llmFeedback.ts`

**Files:**
- Modify: `frontend/src/components/ReviewQueue/llmFeedback.ts`
- Test: `frontend/src/components/ReviewQueue/llmFeedback.test.ts`

Context: today every reason except `internal_error` falls through to "No confident AI match found", so an unconfigured key reports that the AI ran and found nothing.

- [ ] **Step 1: Write the failing tests**

Replace the existing `'no_suggestion'` test (it asserts against a reason the backend no longer emits) and add one case per reason. In `llmFeedback.test.ts`, inside the existing `describe('llmResultToFeedback', ...)`:

```ts
    it.each([
        ['ai_disabled', 'AI episode matching is turned off. Enable it in Settings.'],
        ['not_configured', 'No AI API key is set. Add one in Settings, then use Test Connection.'],
        ['no_show', 'This job has no detected show title, so there is nothing to match against.'],
        ['no_season', 'This job has no detected season, so there is nothing to match against.'],
        ['show_not_found', 'The show could not be found on TMDB.'],
        ['no_match', 'No confident AI match found.'],
    ])('maps reason %s to its own message', (reason, text) => {
        expect(llmResultToFeedback({ suggestion: null, reason })).toEqual({
            tone: 'warn',
            text,
        });
    });

    it('keeps internal_error as an error tone', () => {
        expect(llmResultToFeedback({ suggestion: null, reason: 'internal_error' })).toEqual({
            tone: 'error',
            text: 'AI match failed. Check the server log.',
        });
    });

    it('falls back to a generic message for an unrecognised reason', () => {
        expect(llmResultToFeedback({ suggestion: null, reason: 'brand_new_reason' })).toEqual({
            tone: 'warn',
            text: 'AI match did not produce a suggestion (brand_new_reason).',
        });
    });
```

Delete the old test that asserts `reason: 'no_suggestion'` maps to "No confident AI match found."

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm run test:unit -- llmFeedback`
Expected: FAIL. The `it.each` rows for `ai_disabled` through `show_not_found` all receive "No confident AI match found.", and the unrecognised-reason case receives it too.

- [ ] **Step 3: Implement the mapping**

Replace the body of `llmResultToFeedback` in `llmFeedback.ts`, and replace the docstring paragraph that says unknown reasons "fall through to the generic no confident match message" (that behaviour is what this task removes):

```ts
/** Reason to user-facing message. Only `no_match` means the model actually ran
 *  and was not confident; every other reason is a guard that fired first, so
 *  reporting them all as "no confident match" tells the user the AI ran when it
 *  did not. */
const REASON_MESSAGES: Record<string, string> = {
    ai_disabled: 'AI episode matching is turned off. Enable it in Settings.',
    not_configured: 'No AI API key is set. Add one in Settings, then use Test Connection.',
    no_show: 'This job has no detected show title, so there is nothing to match against.',
    no_season: 'This job has no detected season, so there is nothing to match against.',
    show_not_found: 'The show could not be found on TMDB.',
    no_match: 'No confident AI match found.',
};

export function llmResultToFeedback(result: LLMMatchResult): LLMFeedback | null {
    if (result.suggestion) return null;
    if (!result.reason || result.reason === 'cached') return null;
    if (result.reason === 'internal_error') {
        return { tone: 'error', text: 'AI match failed. Check the server log.' };
    }
    const known = REASON_MESSAGES[result.reason];
    return {
        tone: 'warn',
        text: known ?? `AI match did not produce a suggestion (${result.reason}).`,
    };
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `npm run test:unit -- llmFeedback`
Expected: PASS, all cases.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ReviewQueue/llmFeedback.ts frontend/src/components/ReviewQueue/llmFeedback.test.ts
git commit -m "fix(review): give each AI-match reason its own message

Every reason except internal_error collapsed into 'No confident AI match
found', so an unconfigured key reported that the AI ran and found nothing.
The differentiated reasons added in #350 were never consumed by the frontend.
An unrecognised reason now names itself instead of being disguised."
```

---

### Task 2: Track whether an AI key is configured

**Files:**
- Modify: `frontend/src/components/ReviewQueue.tsx:181` (state) and `:252-262` (config fetch)

Context: `GET /api/config` already returns `ai_api_key` as `"***"` when set and `""` when not, which is how `ConfigWizard.tsx:296` derives its own saved-key flag. No backend change is needed.

- [ ] **Step 1: Add the state**

Next to the existing `aiEpisodeMatchingEnabled` state at `ReviewQueue.tsx:181`:

```tsx
    const [aiEpisodeMatchingEnabled, setAiEpisodeMatchingEnabled] = useState(false);
    const [aiKeyConfigured, setAiKeyConfigured] = useState(false);
```

- [ ] **Step 2: Populate it from the existing config fetch**

Replace the `useEffect` at `ReviewQueue.tsx:252-262`:

```tsx
    // Fetch config once on mount to know whether AI matching is usable. Both
    // flags gate the "Try AI match" button: enabling the feature without a key
    // makes every click burn a full transcription before failing a guard.
    useEffect(() => {
        fetch('/api/config')
            .then((r) => r.ok ? r.json() : null)
            .then((data) => {
                if (data?.ai_episode_matching_enabled) {
                    setAiEpisodeMatchingEnabled(true);
                }
                // "***" is the redacted stand-in for a stored key; "" means unset.
                if (data?.ai_api_key === '***') {
                    setAiKeyConfigured(true);
                }
            })
            .catch(() => {/* non-critical */});
    }, []);
```

- [ ] **Step 3: Pass it to the Inspector**

At `ReviewQueue.tsx:1221`, beside the existing `aiEpisodeMatchingEnabled` prop:

```tsx
                                aiEpisodeMatchingEnabled={aiEpisodeMatchingEnabled}
                                aiKeyConfigured={aiKeyConfigured}
```

- [ ] **Step 4: Verify the app still typechecks**

Run: `npm run build`
Expected: FAIL with a TypeScript error that `aiKeyConfigured` does not exist on the Inspector's props. That is the expected hand-off into Task 3; do not fix it here.

- [ ] **Step 5: No commit yet**

This task is committed together with Task 3, because the tree does not typecheck in between.

---

### Task 3: Disable the button when AI matching cannot succeed

**Files:**
- Modify: `frontend/src/components/ReviewQueue/Inspector.tsx:72-77` (props) and `:372-388` (button)
- Test: `frontend/src/components/ReviewQueue/Inspector.test.tsx`

- [ ] **Step 1: Write the failing tests**

Add to `Inspector.test.tsx`, and extend `renderInspector`'s props object with `aiKeyConfigured?: boolean` passing `aiKeyConfigured={props.aiKeyConfigured ?? true}` to `<Inspector>`:

```tsx
    it('disables the button when no AI key is configured', () => {
        renderInspector({ aiKeyConfigured: false });
        const btn = screen.getByRole('button', { name: /try ai match/i });
        expect(btn).toBeDisabled();
        expect(btn).toHaveAttribute('title', expect.stringMatching(/no ai api key/i));
    });

    it('enables the button when matching is on and a key is set', () => {
        renderInspector({ aiKeyConfigured: true, isLlmMatching: false });
        expect(screen.getByRole('button', { name: /try ai match/i })).toBeEnabled();
    });
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm run test:unit -- Inspector`
Expected: FAIL. The first case fails because the button is enabled and its title is "Run AI episode matching".

- [ ] **Step 3: Implement**

Add to the props type at `Inspector.tsx:72-77`:

```tsx
    aiKeyConfigured: boolean;
```

and add it to the destructured parameter list alongside `aiEpisodeMatchingEnabled`. Then replace the button block at `Inspector.tsx:372-388`:

```tsx
                        {aiEpisodeMatchingEnabled && (
                            <SvActionButton
                                tone="cyan"
                                size="sm"
                                onClick={() => onTryLLMMatch(title.id)}
                                disabled={isLlmMatching || !aiKeyConfigured}
                                title={
                                    aiKeyConfigured
                                        ? 'Run AI episode matching'
                                        : 'No AI API key is set. Add one in Settings, then use Test Connection.'
                                }
                            >
                                {isLlmMatching ? (
                                    <>
                                        <IcoRetry size={11} className="animate-spin" /> Matching…
                                    </>
                                ) : (
                                    'Try AI match'
                                )}
                            </SvActionButton>
                        )}
```

- [ ] **Step 4: Run the tests and the build**

Run: `npm run test:unit -- Inspector`
Expected: PASS.

Run: `npm run build`
Expected: PASS. The Task 2 type error is now resolved.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ReviewQueue.tsx frontend/src/components/ReviewQueue/Inspector.tsx frontend/src/components/ReviewQueue/Inspector.test.tsx
git commit -m "fix(review): disable Try AI match when no API key is configured

Clicking it without a key spent a full Whisper transcription before the
not_configured guard fired. The key-present flag comes from the redacted
ai_api_key that GET /api/config already returns, so no new config field."
```

---

### Task 4: Correct the stale 200-only contract comment

**Files:**
- Modify: `frontend/src/components/ReviewQueue.tsx:412-416`

Context: the comment predates #350, which added the 503 branch. It is the reason the 503 path was never given a message, and it will mislead the next reader.

- [ ] **Step 1: Replace the comment**

```tsx
    // Run the LLM matcher for a single title, then refresh so the persisted
    // llm_suggestion surfaces in the Inspector. Outcomes arrive two ways: a 200
    // with a discriminating `reason` (mapped by llmResultToFeedback), or a
    // thrown ApiError for the 503 retryable failures and 500. Pending state and
    // feedback are keyed by title id so they follow the selected title.
```

- [ ] **Step 2: Verify nothing broke**

Run: `npm run build`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ReviewQueue.tsx
git commit -m "docs(review): correct the stale 'always returns 200' comment

The llm-match endpoint has returned 503 for retryable failures since #350."
```

---

## Phase 2: Harden the AI client

### Task 5: `AIProviderError` carries a code and detail

**Files:**
- Modify: `backend/app/core/errors.py:75-84`
- Test: `backend/tests/unit/test_ai_client.py`

- [ ] **Step 1: Write the failing test**

Add to `test_ai_client.py`:

```python
class TestAIProviderErrorFields:
    def test_defaults_keep_existing_call_sites_working(self):
        from app.core.errors import AIProviderError

        err = AIProviderError("boom")
        assert str(err) == "boom"
        assert err.code == "unknown"
        assert err.detail == ""

    def test_code_and_detail_are_carried(self):
        from app.core.errors import AIProviderError

        err = AIProviderError("boom", code="no_credits", detail="quota exhausted")
        assert err.code == "no_credits"
        assert err.detail == "quota exhausted"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_ai_client.py::TestAIProviderErrorFields -v`
Expected: FAIL with `AttributeError: 'AIProviderError' object has no attribute 'code'`.

- [ ] **Step 3: Implement**

Replace the `pass` body of `AIProviderError` in `errors.py`, keeping the existing docstring above it:

```python
    def __init__(self, message: str, *, code: str = "unknown", detail: str = "") -> None:
        """``code`` is a stable machine-readable cause (see
        ``ai_client.classify_provider_error``); ``detail`` is the provider's own
        truncated explanation, kept for logs. Both default so existing
        single-argument call sites are unaffected."""
        super().__init__(message)
        self.code = code
        self.detail = detail
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_ai_client.py -v`
Expected: PASS, including all pre-existing cases.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/errors.py backend/tests/unit/test_ai_client.py
git commit -m "feat(errors): AIProviderError carries a machine-readable code and detail"
```

---

### Task 6: `classify_provider_error`

**Files:**
- Modify: `backend/app/core/ai_client.py`
- Test: `backend/tests/unit/test_ai_client.py`

Context: `httpx.HTTPStatusError`'s string carries status and URL but never the response body, so today's logs cannot tell an exhausted quota from a rate limit.

- [ ] **Step 1: Write the failing tests**

The bodies below are the documented shapes for each provider. Before finalising, capture one real 4xx body per provider and correct these fixtures if they differ, per the project's rule that parsers are validated against verbatim provider output rather than synthetic fixtures.

```python
class TestClassifyProviderError:
    def _status_error(self, status: int, body: str):
        from httpx import HTTPStatusError, Request, Response

        req = Request("POST", "http://x")
        return HTTPStatusError(
            "err", request=req, response=Response(status, request=req, text=body)
        )

    @pytest.mark.parametrize(
        "provider,status,body,expected",
        [
            ("openai", 429, '{"error":{"code":"insufficient_quota"}}', "no_credits"),
            ("openai", 429, '{"error":{"code":"rate_limit_exceeded"}}', "rate_limited"),
            ("openai", 401, '{"error":{"code":"invalid_api_key"}}', "bad_key"),
            ("openai", 404, '{"error":{"code":"model_not_found"}}', "model_unavailable"),
            ("openrouter", 402, '{"error":{"message":"Insufficient credits"}}', "no_credits"),
            ("anthropic", 401, '{"error":{"type":"authentication_error"}}', "bad_key"),
            ("anthropic", 429, '{"error":{"type":"rate_limit_error"}}', "rate_limited"),
            ("gemini", 429, '{"error":{"status":"RESOURCE_EXHAUSTED"}}', "rate_limited"),
            ("gemini", 400, '{"error":{"status":"INVALID_ARGUMENT"}}', "bad_key"),
            ("gemini", 403, '{"error":{"status":"PERMISSION_DENIED"}}', "model_unavailable"),
            ("openai", 500, '{"error":{"message":"oops"}}', "unknown"),
        ],
    )
    def test_http_statuses(self, provider, status, body, expected):
        from app.core.ai_client import classify_provider_error

        code, message = classify_provider_error(provider, self._status_error(status, body))
        assert code == expected
        assert message  # never empty; the UI renders it verbatim

    def test_connect_error_is_network(self):
        import httpx

        from app.core.ai_client import classify_provider_error

        code, _ = classify_provider_error("openai", httpx.ConnectError("no route"))
        assert code == "network"

    def test_timeout_is_timeout(self):
        import httpx

        from app.core.ai_client import classify_provider_error

        code, _ = classify_provider_error("openai", httpx.ReadTimeout("slow"))
        assert code == "timeout"

    def test_body_is_capped_and_not_echoed_whole(self):
        from app.core.ai_client import classify_provider_error

        huge = '{"error":{"message":"' + ("x" * 9000) + '"}}'
        code, message = classify_provider_error("openai", self._status_error(429, huge))
        assert len(message) < 500
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_ai_client.py::TestClassifyProviderError -v`
Expected: FAIL with `ImportError: cannot import name 'classify_provider_error'`.

- [ ] **Step 3: Implement**

Add to `ai_client.py` below `MAX_RETRIES`:

```python
_MAX_DETAIL_CHARS = 2048

# Human sentences per cause. Rendered verbatim in the UI, so they must be
# actionable and must never contain the provider's raw body.
_CAUSE_MESSAGES = {
    "bad_key": "The API key was rejected. Check it was copied in full and belongs to {provider}.",
    "no_credits": (
        "{provider} accepted the key but the account has no API credits. "
        "Note that a paid chat subscription does not include API usage."
    ),
    "rate_limited": "{provider} is rate limiting this key. Wait a moment and try again.",
    "model_unavailable": "This key does not have access to the model Engram uses for {provider}.",
    "bad_request": "{provider} rejected the request.",
    "network": "Could not reach {provider}. Check the internet connection.",
    "timeout": "{provider} did not respond in time.",
    "unknown": "{provider} returned an unexpected error.",
}


def classify_provider_error(provider: str, exc: Exception) -> tuple[str, str]:
    """Map a provider failure to a stable ``(code, human_message)`` pair.

    The provider's own body is the only thing that separates a permanently
    exhausted quota from transient throttling (both are HTTP 429 on OpenAI), so
    this reads it. The body is never returned to the caller; it is summarised
    into a fixed sentence and left to the logs.
    """
    if isinstance(exc, httpx.TimeoutException):
        code = "timeout"
    elif isinstance(exc, httpx.HTTPStatusError):
        code = _classify_status(provider, exc)
    elif isinstance(exc, httpx.HTTPError):
        code = "network"
    else:
        code = "unknown"
    return code, _CAUSE_MESSAGES[code].format(provider=provider)


def _classify_status(provider: str, exc: "httpx.HTTPStatusError") -> str:
    status = exc.response.status_code
    try:
        body = (exc.response.text or "")[:_MAX_DETAIL_CHARS].lower()
    except Exception:  # noqa: BLE001 — a body we cannot read must not mask the status
        body = ""

    # OpenRouter signals exhausted credit with 402, unlike OpenAI's 429.
    if status == 402:
        return "no_credits"
    if status in (401, 403):
        # Gemini uses 403 PERMISSION_DENIED for model access, not for a bad key.
        if status == 403 and "permission_denied" in body:
            return "model_unavailable"
        return "bad_key"
    if status == 404:
        return "model_unavailable"
    if status == 429:
        if "insufficient_quota" in body or "exceeded your current quota" in body:
            return "no_credits"
        return "rate_limited"
    if status == 400:
        # Gemini reports a malformed/invalid key as 400 INVALID_ARGUMENT.
        if "invalid_argument" in body or "api key" in body:
            return "bad_key"
        return "bad_request"
    return "unknown"


def detail_from(exc: Exception) -> str:
    """The provider's own body, capped, for logs and diagnostics only."""
    response = getattr(exc, "response", None)
    if response is None:
        return str(exc)[:_MAX_DETAIL_CHARS]
    try:
        return (response.text or "")[:_MAX_DETAIL_CHARS]
    except Exception:  # noqa: BLE001
        return ""
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_ai_client.py::TestClassifyProviderError -v`
Expected: PASS, all 14 cases.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/ai_client.py backend/tests/unit/test_ai_client.py
git commit -m "feat(ai): classify provider failures from the response body

httpx.HTTPStatusError carries only status and URL, so an exhausted quota and a
rate limit were indistinguishable in the log. Reads the body to separate
no_credits from rate_limited, bad_key, model_unavailable and network."
```

---

### Task 7: Attach the classification to raised errors

**Files:**
- Modify: `backend/app/core/ai_client.py:110-123`
- Test: `backend/tests/unit/test_ai_client.py`

- [ ] **Step 1: Write the failing test**

```python
class TestCompleteJsonErrorCodes:
    @pytest.mark.asyncio
    async def test_429_quota_raises_no_credits(self):
        from app.core.ai_client import complete_json
        from app.core.errors import AIProviderError

        mock = _mock_httpx({}, status=429)
        mock.post.return_value.raise_for_status.side_effect = _status_error_with_body(
            429, '{"error":{"code":"insufficient_quota"}}'
        )
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            with pytest.raises(AIProviderError) as exc_info:
                await complete_json(
                    prompt="x", schema=None, provider="openai", api_key="k",
                    raise_on_error=True, retries=0,
                )
        assert exc_info.value.code == "no_credits"
        assert "credits" in str(exc_info.value).lower()
```

Add this module-level helper beside `_mock_httpx`:

```python
def _status_error_with_body(status: int, body: str):
    from httpx import HTTPStatusError, Request, Response

    req = Request("POST", "http://x")
    return HTTPStatusError(
        "err", request=req, response=Response(status, request=req, text=body)
    )
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_ai_client.py::TestCompleteJsonErrorCodes -v`
Expected: FAIL. `complete_json` does not accept `retries`, so `TypeError: complete_json() got an unexpected keyword argument 'retries'`.

- [ ] **Step 3: Implement**

Change the `complete_json` signature, adding both parameters the connection test will need:

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
    retries: int = MAX_RETRIES,
    timeout: float = _TIMEOUT_SECONDS,
) -> dict | None:
```

Thread `timeout` through to the adapters. `_with_429_retry` requires a no-argument
callable, so the factory closures capture it rather than taking it as a parameter.
Each `_call_*` gains a trailing `timeout: float` parameter used as
`httpx.AsyncClient(timeout=timeout)`, and each closure passes it, for example:

```python
    elif provider == "gemini":

        def factory():
            return _call_gemini(prompt, api_key, model, max_tokens, schema, timeout)
```

Then replace the error handling at the end:

```python
    try:
        return await _with_429_retry(factory, provider=provider, retries=retries)
    except httpx.HTTPError as e:
        code, message = classify_provider_error(provider, e)
        logger.warning(
            "AI provider %s failed (%s): %s | body=%s",
            provider, code, e, detail_from(e), exc_info=True,
        )
        if raise_on_error:
            raise AIProviderError(message, code=code, detail=detail_from(e)) from e
        return None
    except Exception as e:
        logger.warning("AI provider %s unexpected error: %s", provider, e, exc_info=True)
        if raise_on_error:
            # Deliberate: let unexpected (non-transport) errors propagate unwrapped so
            # callers classify them as internal errors, not retryable provider errors.
            raise
        return None
```

And update `_with_429_retry` so it honours `retries` and stops retrying a permanent quota failure:

```python
async def _with_429_retry(coro_factory, *, provider: str, retries: int = MAX_RETRIES):
    """Call coro_factory() up to retries+1 times, backing off on 429.

    A 429 that classifies as ``no_credits`` is permanent within any retry window,
    so it is re-raised immediately rather than paying the backoff ladder for a
    guaranteed failure.
    """
    delay = _BACKOFF_BASE
    for attempt in range(retries + 1):
        try:
            return await coro_factory()
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 429 or attempt == retries:
                raise
            if classify_provider_error(provider, e)[0] == "no_credits":
                raise
            jitter = random.uniform(0, 0.25 * delay)
            await asyncio.sleep(delay + jitter)
            delay *= 2
    return None
```

Note the log line changes the `AIProviderError` message from the old
`f"{provider} request failed: {e}"` to the human sentence. That message is now
what reaches the UI.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_ai_client.py -v`
Expected: PASS. If any pre-existing test asserts on the old `"... request failed: ..."` message text, update it to assert on `.code` instead, which is the stable contract.

- [ ] **Step 5: Add the no-retry test and re-run**

```python
    @pytest.mark.asyncio
    async def test_no_credits_is_not_retried(self):
        from app.core.ai_client import complete_json
        from app.core.errors import AIProviderError

        mock = _mock_httpx({}, status=429)
        mock.post.return_value.raise_for_status.side_effect = _status_error_with_body(
            429, '{"error":{"code":"insufficient_quota"}}'
        )
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            with pytest.raises(AIProviderError):
                await complete_json(
                    prompt="x", schema=None, provider="openai", api_key="k",
                    raise_on_error=True,
                )
        assert mock.post.await_count == 1
```

Run: `uv run pytest tests/unit/test_ai_client.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/ai_client.py backend/tests/unit/test_ai_client.py
git commit -m "feat(ai): carry the classified cause on AIProviderError

Adds retries and timeout parameters so callers can run a fast pre-flight check,
and stops retrying a 429 that means 'no credits', which never recovers."
```

---

### Task 8: Stop null content from becoming a 500

**Files:**
- Modify: `backend/app/core/ai_client.py` (all three `_call_*` adapters)
- Test: `backend/tests/unit/test_ai_client.py`

Context: OpenAI sends `"content": null` on a refusal. `.get("content", "")` returns `None` (the key exists), `_parse_json_text(None)` raises `AttributeError`, which is not an `AIProviderError`, so it escapes the handler in `_run_llm_match_for_title` and surfaces as HTTP 500.

- [ ] **Step 1: Write the failing tests**

```python
class TestNullContent:
    @pytest.mark.asyncio
    async def test_openai_null_content_returns_none(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx({"choices": [{"finish_reason": "stop", "message": {"content": None}}]})
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(
                prompt="x", schema=None, provider="openai", api_key="k"
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_anthropic_null_text_returns_none(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx({"content": [{"text": None}]})
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(
                prompt="x", schema=None, provider="anthropic", api_key="k"
            )
        assert result is None

    @pytest.mark.asyncio
    async def test_gemini_null_text_returns_none(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx(
            {"candidates": [{"content": {"parts": [{"text": None}]}}]}
        )
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(
                prompt="x", schema=None, provider="gemini", api_key="k"
            )
        assert result is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_ai_client.py::TestNullContent -v`
Expected: FAIL. All three raise `AttributeError: 'NoneType' object has no attribute 'strip'` (swallowed to `None` only because `raise_on_error` defaults to False, so assert on the raise path is what fails first; if they pass as `None` here, re-run with `raise_on_error=True` to see the `AttributeError`).

- [ ] **Step 3: Implement**

Three one-line changes:

```python
# _call_anthropic
        text = content[0].get("text") or ""

# _call_openai_compatible
        text = choices[0].get("message", {}).get("content") or ""

# _call_gemini
        text = parts[0].get("text") or ""
```

And make `_parse_json_text` defensive at its entry, since it is also called directly from `ai_identifier.py:82`:

```python
def _parse_json_text(text: str | None) -> dict | None:
    """Parse JSON, tolerating ```json fences and surrounding whitespace."""
    if not text:
        return None
    text = text.strip()
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_ai_client.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/ai_client.py backend/tests/unit/test_ai_client.py
git commit -m "fix(ai): a null content field no longer raises AttributeError

OpenAI sends content: null on a refusal. .get(key, '') returns None when the key
exists, so _parse_json_text raised AttributeError, which escaped raise_on_error
unwrapped and surfaced as HTTP 500 instead of a clean empty result."
```

---

### Task 9: Detect truncated and malformed responses

**Files:**
- Modify: `backend/app/core/ai_client.py`
- Test: `backend/tests/unit/test_ai_client.py`

Context: a truncated body fails `json.loads`, `_parse_json_text` returns `None`, and `complete_json` returns `None` even under `raise_on_error=True`. The matcher cannot tell that apart from a legitimate no-match, so it reports "No confident AI match found".

- [ ] **Step 1: Write the failing tests**

```python
class TestTruncatedAndMalformed:
    @pytest.mark.asyncio
    async def test_finish_reason_length_raises_truncated(self):
        from app.core.ai_client import complete_json
        from app.core.errors import AIProviderError

        mock = _mock_httpx(
            {"choices": [{"finish_reason": "length",
                          "message": {"content": '{"episode": 3, "confid'}}]}
        )
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            with pytest.raises(AIProviderError) as exc_info:
                await complete_json(
                    prompt="x", schema=None, provider="openai", api_key="k",
                    raise_on_error=True,
                )
        assert exc_info.value.code == "response_truncated"

    @pytest.mark.asyncio
    async def test_malformed_raises_when_raise_on_error(self):
        from app.core.ai_client import complete_json
        from app.core.errors import AIProviderError

        mock = _mock_httpx({"choices": [{"finish_reason": "stop",
                                         "message": {"content": "I don't know"}}]})
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            with pytest.raises(AIProviderError) as exc_info:
                await complete_json(
                    prompt="x", schema=None, provider="openai", api_key="k",
                    raise_on_error=True,
                )
        assert exc_info.value.code == "malformed_response"

    @pytest.mark.asyncio
    async def test_malformed_still_returns_none_by_default(self):
        """curator._maybe_add_llm_suggestion relies on the None contract."""
        from app.core.ai_client import complete_json

        mock = _mock_httpx({"choices": [{"finish_reason": "stop",
                                         "message": {"content": "I don't know"}}]})
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(
                prompt="x", schema=None, provider="openai", api_key="k"
            )
        assert result is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_ai_client.py::TestTruncatedAndMalformed -v`
Expected: FAIL on the first two (`DID NOT RAISE AIProviderError`); the third passes already and guards the default contract.

- [ ] **Step 3: Implement**

Add the two new causes to `_CAUSE_MESSAGES`:

```python
    "response_truncated": (
        "{provider} cut the response off before it was complete. "
        "This usually means the reply exceeded the token budget."
    ),
    "malformed_response": "{provider} returned a reply that was not valid JSON.",
```

Add a truncation sentinel raised by the adapters and translated by `complete_json`:

```python
class _TruncatedResponse(Exception):
    """Internal: provider stopped generating because it hit the token budget."""
```

In `_call_openai_compatible`, after `choices` is resolved:

```python
        if choices[0].get("finish_reason") == "length":
            # OpenAI's own guidance: check finish_reason before parsing, because
            # partial JSON parses as garbage or not at all.
            raise _TruncatedResponse
```

In `_call_anthropic`, the equivalent field is `stop_reason` on the top-level body:

```python
        if data.get("stop_reason") == "max_tokens":
            raise _TruncatedResponse
```

In `_call_gemini`, it is `finishReason` on the candidate:

```python
        if candidates[0].get("finishReason") == "MAX_TOKENS":
            raise _TruncatedResponse
```

Then in `complete_json`, insert two handlers before the generic `except Exception`, and distinguish a parse failure from a genuine `None`:

```python
    try:
        result = await _with_429_retry(factory, provider=provider, retries=retries)
    except _TruncatedResponse:
        logger.warning("AI provider %s truncated its response (token budget)", provider)
        if raise_on_error:
            raise AIProviderError(
                _CAUSE_MESSAGES["response_truncated"].format(provider=provider),
                code="response_truncated",
            ) from None
        return None
    except httpx.HTTPError as e:
        ...  # unchanged from Task 7
    except Exception as e:
        ...  # unchanged from Task 7

    if result is None and raise_on_error:
        # The transport succeeded but the body was empty or unparseable. Callers
        # with raise_on_error set need this separated from "the model ran and had
        # no confident answer", which is also a None from the layer above.
        raise AIProviderError(
            _CAUSE_MESSAGES["malformed_response"].format(provider=provider),
            code="malformed_response",
        )
    return result
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_ai_client.py -v`
Expected: PASS. One pre-existing test, `test_raise_on_error_propagates_aiprovidererror` in `test_llm_episode_matcher.py`, may now also see `malformed_response`; run the matcher suite too.

Run: `uv run pytest tests/unit/test_llm_episode_matcher.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/ai_client.py backend/tests/unit/test_ai_client.py
git commit -m "fix(ai): surface truncated and unparseable provider responses

Both previously returned None even under raise_on_error, so the matcher reported
'no confident match' when the provider had actually failed. finish_reason was
never checked, which OpenAI's docs explicitly say to do before parsing."
```

---

### Task 10: Run the full backend suite

- [ ] **Step 1: Run every backend test**

Run: `uv run pytest -q`
Expected: PASS. Investigate any failure before continuing; Tasks 7 to 9 changed the shared client that `curator.py` and `ai_identifier.py` also use.

- [ ] **Step 2: Lint**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: clean.

- [ ] **Step 3: Commit any fixes**

```bash
git add -A backend
git commit -m "test: fix fallout from the ai_client hardening"
```

Skip this commit if nothing changed.

---

## Phase 3: The connection test

### Task 11: `POST /api/validate/ai`

**Files:**
- Modify: `backend/app/api/validation.py`
- Test: Create `backend/tests/unit/test_validation_ai.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the AI provider connection-test endpoint."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.routes import require_localhost_or_lan
from app.core.errors import AIProviderError
from app.main import app


@pytest.fixture
async def client():
    app.dependency_overrides[require_localhost_or_lan] = lambda: None
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(require_localhost_or_lan, None)


class TestValidateAI:
    @pytest.mark.asyncio
    async def test_success_reports_the_model(self, client):
        with patch(
            "app.core.ai_client.complete_json", new=AsyncMock(return_value={"ok": True})
        ):
            resp = await client.post(
                "/api/validate/ai", json={"provider": "openai", "api_key": "sk-x"}
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["version"] == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_no_credits_is_reported_verbatim(self, client):
        err = AIProviderError("no credits on this account", code="no_credits")
        with patch("app.core.ai_client.complete_json", new=AsyncMock(side_effect=err)):
            resp = await client.post(
                "/api/validate/ai", json={"provider": "openai", "api_key": "sk-x"}
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        assert body["error"] == "no credits on this account"

    @pytest.mark.asyncio
    async def test_unknown_provider_is_rejected(self, client):
        resp = await client.post(
            "/api/validate/ai", json={"provider": "hal9000", "api_key": "k"}
        )
        assert resp.json()["valid"] is False
        assert "provider" in resp.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_blank_key_falls_back_to_stored_config(self, client):
        from app.services.config_service import update_config

        await update_config(ai_provider="openai", ai_api_key="sk-stored")
        spy = AsyncMock(return_value={"ok": True})
        with patch("app.core.ai_client.complete_json", new=spy):
            resp = await client.post("/api/validate/ai", json={"provider": "openai"})
        assert resp.json()["valid"] is True
        assert spy.await_args.kwargs["api_key"] == "sk-stored"

    @pytest.mark.asyncio
    async def test_no_key_anywhere_is_invalid(self, client):
        from app.services.config_service import update_config

        await update_config(ai_provider="openai")
        with patch(
            "app.services.config_service.get_config",
            new=AsyncMock(return_value=type("C", (), {"ai_api_key": ""})()),
        ):
            resp = await client.post("/api/validate/ai", json={"provider": "openai"})
        assert resp.json()["valid"] is False
        assert "no api key" in resp.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_retries_disabled_and_short_timeout(self, client):
        spy = AsyncMock(return_value={"ok": True})
        with patch("app.core.ai_client.complete_json", new=spy):
            await client.post(
                "/api/validate/ai", json={"provider": "openai", "api_key": "sk-x"}
            )
        assert spy.await_args.kwargs["retries"] == 0
        assert spy.await_args.kwargs["timeout"] == 10.0
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_validation_ai.py -v`
Expected: FAIL with 404 responses; the endpoint does not exist.

- [ ] **Step 3: Implement**

Add the request model beside the others in `validation.py`:

```python
class AiValidationRequest(BaseModel):
    """Request model for AI provider connection testing.

    ``api_key`` is optional: when omitted or blank the stored key for the
    provider is used, so a user can verify a key they saved earlier without
    having it to hand. The stored key is never returned.
    """

    provider: str
    api_key: str | None = None
```

And the endpoint at the end of the file:

```python
@router.post("/validate/ai", response_model=ValidationResponse)
async def validate_ai(
    request: AiValidationRequest,
    _: None = Depends(require_localhost_or_lan),
) -> ValidationResponse:
    """Verify an AI provider credential with one real, tiny completion.

    Deliberately routed through the same ``complete_json`` the episode matcher
    uses, rather than a bespoke auth ping: a ping would exercise a different
    endpoint, without the structured-output convention, and could therefore pass
    while real matching fails. Gated to the host (or an opted-in LAN) because,
    unlike the other validators, this one spends the user's money.
    """
    from app.core.ai_client import DEFAULT_MODELS, complete_json
    from app.core.errors import AIProviderError

    provider = (request.provider or "").strip()
    if provider not in DEFAULT_MODELS:
        return ValidationResponse(
            valid=False, error=f"Unknown AI provider: {provider or '(empty)'}"
        )

    api_key = (request.api_key or "").strip()
    if not api_key:
        from app.services.config_service import get_config

        config = await get_config()
        api_key = (getattr(config, "ai_api_key", "") or "").strip()
    if not api_key:
        return ValidationResponse(
            valid=False, error="No API key provided and none is saved for this provider"
        )

    try:
        result = await complete_json(
            prompt='Reply with {"ok": true} and nothing else. Respond as JSON.',
            schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
            provider=provider,
            api_key=api_key,
            max_tokens=16,
            raise_on_error=True,
            retries=0,
            timeout=10.0,
        )
    except AIProviderError as e:
        logger.warning("AI validation failed for %s: %s", provider, e.code)
        return ValidationResponse(valid=False, error=str(e))
    except Exception as e:  # noqa: BLE001 — a validator must never 500
        logger.warning("AI validation raised unexpectedly for %s", provider, exc_info=True)
        return ValidationResponse(valid=False, error=f"Validation failed: {e}")

    if not result:
        return ValidationResponse(valid=False, error="Provider returned an empty response")
    return ValidationResponse(valid=True, version=DEFAULT_MODELS[provider])
```

Change the existing FastAPI import at the top of `validation.py` and add the gate:

```python
from fastapi import APIRouter, Depends

from app.api.routes import require_localhost_or_lan
```

This is not circular: `routes.py` imports from `validation.py` only inside
function bodies (`routes.py:2474` and `:3204`), never at module level.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_validation_ai.py -v`
Expected: PASS, all six.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/validation.py backend/tests/unit/test_validation_ai.py
git commit -m "feat(api): add POST /api/validate/ai connection test

Round-trips a 16-token completion through the same complete_json the matcher
uses, with retries off and a 10s timeout, so a credential problem is found in
one click instead of after a 1-3 minute transcription."
```

---

### Task 12: `aiValidation.ts`

**Files:**
- Create: `frontend/src/utils/aiValidation.ts`
- Test: Create `frontend/src/utils/aiValidation.test.ts`

- [ ] **Step 1: Write the failing tests**

```ts
import { describe, expect, it, vi, afterEach } from 'vitest';
import { requestAiValidation } from './aiValidation';

afterEach(() => { vi.unstubAllGlobals(); });

function stubFetch(impl: unknown) {
    vi.stubGlobal('fetch', impl);
}

describe('requestAiValidation', () => {
    it('returns valid when the provider accepts the key', async () => {
        stubFetch(vi.fn().mockResolvedValue({
            ok: true, json: async () => ({ valid: true, version: 'gpt-4o-mini' }),
        }));
        expect(await requestAiValidation('openai', 'sk-x')).toEqual({
            status: 'valid', model: 'gpt-4o-mini',
        });
    });

    it('returns invalid with the backend message when rejected', async () => {
        stubFetch(vi.fn().mockResolvedValue({
            ok: true, json: async () => ({ valid: false, error: 'no credits' }),
        }));
        expect(await requestAiValidation('openai', 'sk-x')).toEqual({
            status: 'invalid', error: 'no credits',
        });
    });

    it('omits api_key entirely when the field is blank', async () => {
        const spy = vi.fn().mockResolvedValue({
            ok: true, json: async () => ({ valid: true, version: 'gpt-4o-mini' }),
        });
        stubFetch(spy);
        await requestAiValidation('openai', '   ');
        expect(JSON.parse(spy.mock.calls[0][1].body)).toEqual({ provider: 'openai' });
    });

    it('reports a network failure as error, not invalid', async () => {
        stubFetch(vi.fn().mockRejectedValue(new Error('offline')));
        const result = await requestAiValidation('openai', 'sk-x');
        expect(result.status).toBe('error');
    });

    it('reports a non-OK response as error', async () => {
        stubFetch(vi.fn().mockResolvedValue({
            ok: false, status: 403, text: async () => 'forbidden',
        }));
        expect((await requestAiValidation('openai', 'sk-x')).status).toBe('error');
    });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `npm run test:unit -- aiValidation`
Expected: FAIL, cannot resolve `./aiValidation`.

- [ ] **Step 3: Implement**

```ts
/**
 * AI provider credential validation against POST /api/validate/ai.
 *
 * Three outcomes, deliberately distinct (same split as tmdbValidation.ts):
 *  - 'valid'   — the provider answered a real completion
 *  - 'invalid' — the check ran and the provider REJECTED the credential
 *  - 'error'   — the check itself could not run; nothing was learned about the
 *                key. Conflating this with 'invalid' sends users hunting for a
 *                key problem that may not exist.
 */
export type AiValidationResult =
  | { status: 'valid'; model?: string }
  | { status: 'invalid'; error: string }
  | { status: 'error'; error: string };

export async function requestAiValidation(
  provider: string,
  apiKey: string,
): Promise<AiValidationResult> {
  // Omit the key entirely when blank so the backend falls back to the stored
  // one. Posting "" would be rejected as an empty key, which is what makes the
  // TMDB button useless for a saved token.
  const trimmed = apiKey.trim();
  const payload: Record<string, string> = { provider };
  if (trimmed) payload.api_key = trimmed;

  let response: Response;
  try {
    response = await fetch('/api/validate/ai', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  } catch (err) {
    console.error('AI validation request failed (network):', err);
    return {
      status: 'error',
      error: "Couldn't reach the validation endpoint — is the backend running?",
    };
  }

  if (!response.ok) {
    const detail = await response.text().catch(() => '');
    console.error(`AI validation endpoint returned HTTP ${response.status}:`, detail);
    return {
      status: 'error',
      error: `Couldn't check the key — validation endpoint returned HTTP ${response.status}`,
    };
  }

  try {
    const result = await response.json();
    if (result.valid) {
      return { status: 'valid', model: result.version };
    }
    return { status: 'invalid', error: result.error || 'Provider rejected the key' };
  } catch (err) {
    console.error('AI validation returned an unparseable response:', err);
    return { status: 'error', error: "Couldn't check the key — unexpected response" };
  }
}
```

- [ ] **Step 4: Run the tests**

Run: `npm run test:unit -- aiValidation`
Expected: PASS, all five.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/utils/aiValidation.ts frontend/src/utils/aiValidation.test.ts
git commit -m "feat(config): add the AI connection-test client"
```

---

### Task 13: The Test Connection button

**Files:**
- Modify: `frontend/src/components/ConfigWizard.tsx`

- [ ] **Step 1: Add the state and handler**

Beside the existing `tmdbValidation` state at `ConfigWizard.tsx:246`:

```tsx
    const [aiValidation, setAiValidation] = useState<{status: 'idle' | 'testing' | 'valid' | 'invalid' | 'error', error?: string, model?: string}>({status: 'idle'});
```

Add the import at the top of the file:

```tsx
import { requestAiValidation } from '../utils/aiValidation';
```

And the handler beside `handleTestTmdb` (which starts at `ConfigWizard.tsx:549`):

```tsx
    const handleTestAi = async () => {
        const key = config.aiApiKey.trim();
        if (!key && !savedKeys.ai) {
            setAiValidation({status: 'invalid', error: 'Please enter a key first'});
            return;
        }
        // Prefix hint only, never a gate: provider key formats change, and a
        // wrong-provider key is the common mistake after switching providers.
        const expectedPrefix = AI_KEY_PLACEHOLDERS[config.aiProvider]?.replace('...', '');
        if (key && expectedPrefix && !key.startsWith(expectedPrefix)) {
            setAiValidation({
                status: 'invalid',
                error: `That does not look like a ${AI_PROVIDER_LABELS[config.aiProvider] || config.aiProvider} key (expected it to start with "${expectedPrefix}")`,
            });
            return;
        }
        setAiValidation({status: 'testing'});
        const result = await requestAiValidation(config.aiProvider, config.aiApiKey);
        if (result.status === 'valid') {
            setAiValidation({status: 'valid', model: result.model});
        } else {
            setAiValidation({status: result.status, error: result.error});
        }
    };
```

- [ ] **Step 2: Reset the status when the key or provider changes**

In the `onChange` of the AI key input at `ConfigWizard.tsx:1162`:

```tsx
                                        onChange={(e) => {
                                            handleInputChange('aiApiKey', e.target.value);
                                            setAiValidation({status: 'idle'});
                                        }}
```

And in the provider select's `onValueChange` at `ConfigWizard.tsx:1143`:

```tsx
                                        onValueChange={(v) => {
                                            handleInputChange('aiProvider', v);
                                            setAiValidation({status: 'idle'});
                                        }}
```

- [ ] **Step 3: Add the button**

Immediately after the AI key `<input>` (which closes at `ConfigWizard.tsx:1163`) and before its `<span className="form-hint">`:

```tsx
                                    <div style={{display: 'flex', alignItems: 'center', gap: '0.5rem', marginTop: '0.5rem'}}>
                                        <button
                                            type="button"
                                            onClick={handleTestAi}
                                            disabled={aiValidation.status === 'testing' || (!config.aiApiKey && !savedKeys.ai)}
                                            className="btn-secondary"
                                            style={{padding: '0.25rem 0.75rem', fontSize: '0.85rem'}}
                                        >
                                            {aiValidation.status === 'testing' ? 'Testing...' : 'Test Connection'}
                                        </button>
                                        {aiValidation.status === 'valid' && (
                                            <span style={{color: '#22c55e', fontSize: '0.85rem'}}>
                                                ✓ Connected{aiValidation.model ? ` (${aiValidation.model})` : ''}
                                            </span>
                                        )}
                                        {aiValidation.status === 'invalid' && (
                                            <span style={{color: '#ef4444', fontSize: '0.85rem'}}>✗ {aiValidation.error}</span>
                                        )}
                                        {/* Amber, not red: the key wasn't rejected, the check
                                            itself failed. Same distinction as the TMDB test. */}
                                        {aiValidation.status === 'error' && (
                                            <span style={{color: '#f59e0b', fontSize: '0.85rem'}}>⚠ {aiValidation.error}</span>
                                        )}
                                    </div>
```

- [ ] **Step 4: Verify**

Run: `npm run build`
Expected: PASS.

Run: `npm run lint`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ConfigWizard.tsx
git commit -m "feat(config): add a Test Connection button for the AI provider

Sends one tiny completion through the real matcher path and reports the specific
cause on failure, so a bad key or an empty account is found before a rip."
```

---

### Task 14: Carry the classified cause into the llm-match 503

**Files:**
- Modify: `backend/app/api/routes.py:4339-4345` and `:4398-4399`
- Modify: `frontend/src/api/client.ts:68-97`
- Modify: `frontend/src/components/ReviewQueue/llmFeedback.ts`
- Test: `backend/tests/unit/test_api_routes.py`, `frontend/src/components/ReviewQueue/llmFeedback.test.ts`

- [ ] **Step 1: Write the failing backend test**

Add to `backend/tests/unit/test_api_routes.py`:

```python
class TestLLMMatch503Detail:
    @pytest.mark.asyncio
    async def test_provider_error_detail_reaches_the_body(self, client):
        from app.api.routes import LLMMatchOutcome

        outcome = LLMMatchOutcome(
            suggestion=None, reason="llm_error",
            detail="no_credits", message="This account has no API credits.",
        )
        with patch("app.api.routes._run_llm_match_for_title",
                   new=AsyncMock(return_value=outcome)):
            resp = await client.post("/api/jobs/1/titles/1/llm-match")
        assert resp.status_code == 503
        body = resp.json()
        assert body["detail"] == "no_credits"
        assert body["message"] == "This account has no API credits."
```

This test needs a job and title fixture; follow the setup already used by the
other `test_api_routes.py` cases that post to a job endpoint.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_api_routes.py::TestLLMMatch503Detail -v`
Expected: FAIL. `LLMMatchOutcome` has no `detail` or `message` field.

- [ ] **Step 3: Implement the backend half**

Extend the dataclass at `routes.py:4254`:

```python
@dataclass(frozen=True)
class LLMMatchOutcome:
    """Result of an LLM-match attempt. ``reason is None`` means success."""

    suggestion: dict | None
    reason: str | None
    detail: str = ""   # machine-readable provider cause, e.g. "no_credits"
    message: str = ""  # human sentence, rendered verbatim in the Inspector

    @classmethod
    def ok(cls, suggestion: dict) -> "LLMMatchOutcome":
        return cls(suggestion=suggestion, reason=None)

    @classmethod
    def failed(cls, reason: str, *, detail: str = "", message: str = "") -> "LLMMatchOutcome":
        return cls(suggestion=None, reason=reason, detail=detail, message=message)
```

Populate it in the handler at `routes.py:4339`:

```python
    except AIProviderError as e:
        logger.warning(
            "LLM match: provider error for title %s -> llm_error (%s)",
            sanitize_log_value(title.id), e.code, exc_info=True,
        )
        return LLMMatchOutcome.failed("llm_error", detail=e.code, message=str(e))
```

And include them in the 503 body at `routes.py:4398`:

```python
    if outcome.reason in _LLM_MATCH_RETRYABLE_REASONS:
        return JSONResponse(
            status_code=503,
            content={
                "suggestion": None,
                "reason": outcome.reason,
                "detail": outcome.detail,
                "message": outcome.message,
            },
        )
```

- [ ] **Step 4: Run the backend test**

Run: `uv run pytest tests/unit/test_api_routes.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing frontend test**

Add to `llmFeedback.test.ts`:

```ts
describe('llmErrorToFeedback', () => {
    it('renders the provider message from a 503 body', () => {
        const err = new ApiError(503, 'Service Unavailable', JSON.stringify({
            suggestion: null, reason: 'llm_error',
            detail: 'no_credits', message: 'This account has no API credits.',
        }));
        expect(llmErrorToFeedback(err)).toEqual({
            tone: 'error', text: 'This account has no API credits.',
        });
    });

    it('falls back to a readable sentence when the body has no message', () => {
        const err = new ApiError(503, 'Service Unavailable', JSON.stringify({
            suggestion: null, reason: 'transcription_failed',
        }));
        expect(llmErrorToFeedback(err)).toEqual({
            tone: 'error',
            text: 'Could not transcribe this file, so there was nothing to match.',
        });
    });

    it('handles a non-ApiError without exposing raw JSON', () => {
        expect(llmErrorToFeedback(new Error('boom'))).toEqual({
            tone: 'error', text: 'AI match failed. Check the server log.',
        });
    });
});
```

Import `ApiError` and `llmErrorToFeedback` at the top of that test file.

- [ ] **Step 6: Run to verify it fails**

Run: `npm run test:unit -- llmFeedback`
Expected: FAIL, `llmErrorToFeedback` is not exported.

- [ ] **Step 7: Implement the frontend half**

Add to `llmFeedback.ts`:

```ts
import { ApiError } from '../../api/client';

/** Retryable 503 reasons, for when the body carries no provider message. */
const RETRYABLE_MESSAGES: Record<string, string> = {
    matcher_unavailable: 'The episode matcher is not ready for this show yet. Try again shortly.',
    transcription_failed: 'Could not transcribe this file, so there was nothing to match.',
    llm_error: 'The AI provider call failed. Check the provider key in Settings.',
};

/**
 * Map a thrown `runLLMMatch` error (503 or 500) to Inspector feedback.
 *
 * Prefers the backend's `message`, which the provider-error classifier composed
 * from the provider's own response body. Falls back to a per-reason sentence so
 * the raw JSON body of ApiError.message never reaches the user.
 */
export function llmErrorToFeedback(err: unknown): LLMFeedback {
    if (err instanceof ApiError) {
        try {
            const body = JSON.parse(err.body) as {
                reason?: string;
                message?: string;
            };
            if (body.message) return { tone: 'error', text: body.message };
            const known = body.reason ? RETRYABLE_MESSAGES[body.reason] : undefined;
            if (known) return { tone: 'error', text: known };
        } catch {
            // Unparseable body falls through to the generic message below.
        }
    }
    return { tone: 'error', text: 'AI match failed. Check the server log.' };
}
```

Then use it in `ReviewQueue.tsx`, replacing the catch block at `:429-437`:

```tsx
        } catch (err) {
            console.error('LLM match failed', err);
            setLlmFeedback((prev) => ({ ...prev, [titleId]: llmErrorToFeedback(err) }));
        }
```

and add `llmErrorToFeedback` to the existing import of `llmResultToFeedback`.

Finally update the `LLMMatchResult` doc block in `client.ts:81-87` so the 503
bullet documents the two new fields:

```ts
 * - **503** — `runLLMMatch` THROWS `ApiError`; retryable operational failures.
 *   `ApiError.body` carries `{ suggestion: null, reason, detail, message }`, where
 *   `reason` is `"matcher_unavailable"`, `"transcription_failed"`, or `"llm_error"`.
 *   For `"llm_error"`, `detail` is the classified provider cause (`"no_credits"`,
 *   `"bad_key"`, `"rate_limited"`, `"model_unavailable"`, `"response_truncated"`,
 *   `"malformed_response"`, `"network"`, `"timeout"`, `"unknown"`) and `message`
 *   is a ready-to-render sentence. Use `llmErrorToFeedback` rather than
 *   `ApiError.message`, which is the raw body.
```

- [ ] **Step 8: Run everything**

Run: `npm run test:unit`
Expected: PASS.

Run: `npm run build && npm run lint`
Expected: PASS, clean.

- [ ] **Step 9: Commit**

```bash
git add backend/app/api/routes.py backend/tests/unit/test_api_routes.py frontend/src/api/client.ts frontend/src/components/ReviewQueue.tsx frontend/src/components/ReviewQueue/llmFeedback.ts frontend/src/components/ReviewQueue/llmFeedback.test.ts
git commit -m "feat(review): show the real reason an AI match failed

The 503 body now carries the classified provider cause and a ready-to-render
sentence, so the Inspector stops printing 'Request failed (503 ...): {json}'."
```

---

### Task 15: Full verification and changelog

- [ ] **Step 1: Run the full backend suite**

Run from `backend/`: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 2: Run the full frontend suite**

Run from `frontend/`: `npm run test:unit && npm run build && npm run lint`
Expected: PASS, clean.

- [ ] **Step 3: Manual smoke test**

Start the backend and dashboard on this session's ports (see CLAUDE.md, "Parallel sessions"). In Settings, with AI identification enabled:

1. Enter a deliberately wrong key and click Test Connection. Expect a red ✗ naming the cause, not a generic failure.
2. Clear the field with a key already saved and click Test Connection. Expect it to test the stored key rather than reporting an empty key.
3. Turn off AI episode matching, open a review job, and confirm the Try AI match button is hidden. Turn it on with no key saved and confirm the button is disabled with the explanatory tooltip.

- [ ] **Step 4: Add the changelog entry**

Under `## [Unreleased]` in `CHANGELOG.md`:

```markdown
### Added
- **AI connection test.** Settings now has a Test Connection button for the AI provider that sends one tiny request through the real matcher path and reports the specific cause on failure (bad key, no credits, rate limited, model unavailable). It can test a key already saved without re-entering it.

### Fixed
- **AI episode matching now says why it failed.** Every outcome except "the model ran and was not confident" previously reported "No confident AI match found", so an unconfigured key looked like a failed match after a full transcription. Each reason now has its own message, and the Try AI match button is disabled when no key is set.
- **Provider failures are no longer opaque.** The provider's own error is read and classified, so an exhausted quota is distinguishable from throttling instead of both surfacing as a raw `503 ... {"reason":"llm_error"}`.
- **Truncated and refused provider responses are surfaced.** A response cut off at the token limit, or a refusal sending a null content field, previously became a silent "no match" or an HTTP 500.
```

- [ ] **Step 5: Commit and stop the servers**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog for the AI connection test and error detail"
```

Then stop the backend and Vite processes this session started, scoped by port, per CLAUDE.md.

---

## Self-Review Notes

Spec coverage check, section by section:

| Spec section | Task |
|---|---|
| 1. `classify_provider_error` | 6 |
| 2. `retries` (and `timeout`) | 7 |
| 3. `POST /api/validate/ai` | 11 |
| 4. Classified cause into the matcher | 14 |
| 5. Frontend `aiValidation.ts` + button | 12, 13 |
| 6. Honest reasons in the Inspector | 1, 2, 3, 4 |
| 7. Adapter hardening | 8, 9 |
| 8. Testing | folded into each task, plus 10 and 15 |

Spec goal 3 (never claim no-match when the matcher did not run) is deliberately
sequenced first, in Tasks 1 to 4, so the highest-value fix can ship without
waiting on the backend work.

Two spec details resolved during planning:

- The spec's proposed `ai_api_key_set` config field is not needed;
  `GET /api/config` already returns `ai_api_key` as `"***"` when set. Task 2 uses
  that, avoiding a new field to keep in three-way sync.
- The spec named `finish_reason` generically. Only OpenAI and OpenRouter use that
  key; Anthropic uses top-level `stop_reason: "max_tokens"` and Gemini uses
  `finishReason: "MAX_TOKENS"` on the candidate. Task 9 handles all three.
