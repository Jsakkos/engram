# Local AI Provider (Ollama + LM Studio) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Engram's AI identification and episode matching run against a local Ollama or LM Studio server instead of a hosted paid provider.

**Architecture:** Both local servers expose the same OpenAI-compatible `/v1/chat/completions` contract that `_call_openai_compatible()` in `backend/app/core/ai_client.py` already speaks, so this adds two provider slugs (`ollama`, `lmstudio`) that reuse that adapter with a configurable base URL. The bulk of the work is relaxing invariants that silently assume a remote paid API: an API key must no longer be required, loopback URLs must pass a dedicated SSRF guard, and timeouts must widen to survive cold model loads.

**Tech Stack:** Python 3.11, FastAPI, SQLModel, httpx, pytest (`unittest.mock.patch`, no respx). Frontend: React 18, TypeScript, Vitest + React Testing Library.

**Spec:** `docs/superpowers/specs/2026-08-29-local-ai-provider-design.md`
**Issue:** https://github.com/Jsakkos/engram/issues/606

---

## Orientation for the implementer

Read this before Task 1. It is the context that is not obvious from the file names.

**What `complete_json` is.** `backend/app/core/ai_client.py` is the single outbound LLM
call site for the whole application. Every AI feature funnels through `complete_json()`,
which dispatches on a provider slug string, calls a per-provider adapter, and returns a
parsed `dict` or `None`. `None` means "no usable result"; callers must always tolerate it.

**Why an API key is load-bearing today.** Four places treat "the user has stored an API
key" as equivalent to "AI is configured". Local servers need no key, so unless all four
change, the feature will appear to be configured and will silently do nothing. This is the
single most likely way to ship a broken version of this feature.

**Why the SSRF guard matters.** `app/core/security.py` deliberately rejects loopback and
private addresses in `is_safe_remote_url()`. Local AI servers live at exactly those
addresses, so a separate, deliberately weaker guard is required. Do not relax
`is_safe_remote_url` itself; other call sites depend on it.

**Testing convention.** `backend/tests/unit/test_ai_client.py` mocks transport with
`unittest.mock.patch("app.core.ai_client.httpx.AsyncClient", return_value=mock)` and a
hand-rolled `_mock_httpx()` helper at the top of the file. Reuse that helper. Do not add
`respx` or `pytest-httpx`; they are not dependencies of this project.

**Run backend commands from `backend/`** and use `uv`, never bare `python`/`pytest`.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `backend/app/core/ai_client.py` | Provider vocabulary, dispatch, timeouts, error text, JSON parsing | Modify |
| `backend/app/core/security.py` | `is_safe_local_ai_url` guard | Modify |
| `backend/app/models/app_config.py` | `ai_local_base_url` persisted field | Modify |
| `backend/app/api/routes.py` | `ConfigResponse` / `ConfigUpdate` / `PUT` validation | Modify |
| `backend/app/api/validation.py` | `validate_ai` fixes, new `GET /ai/local-models` | Modify |
| `backend/app/core/curator.py` | Gate uses `ai_is_configured` | Modify |
| `backend/app/services/matching_coordinator.py` | Gate uses `ai_is_configured` | Modify |
| `backend/app/services/identification_coordinator.py` | Gate uses `ai_is_configured` | Modify |
| `backend/tests/unit/test_ai_client.py` | Adapter, dispatch, timeout, error tests | Modify |
| `backend/tests/unit/test_security_local_ai_url.py` | SSRF guard tests | Create |
| `backend/tests/unit/test_validation_ai.py` | Local-provider validator tests | Modify |
| `backend/tests/unit/test_local_models_endpoint.py` | Model-list endpoint tests | Create |
| `backend/tests/unit/test_ai_configured_gates.py` | The four-gate invariant | Create |
| `frontend/src/utils/localModels.ts` | Fetch helper for the model dropdown | Create |
| `frontend/src/utils/localModels.test.ts` | Helper tests | Create |
| `frontend/src/components/ConfigWizard.tsx` | Slugs, base URL field, model dropdown, hint | Modify |
| `CHANGELOG.md` | `[Unreleased]` entry | Modify |
| `docs/` | User-facing setup docs | Modify |

---

## Task 1: Provider vocabulary and the `ai_is_configured` invariant

Pure additions to `ai_client.py`. Nothing calls them yet, which keeps this task safe to
land on its own.

**Files:**
- Modify: `backend/app/core/ai_client.py`
- Test: `backend/tests/unit/test_ai_client.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_ai_client.py`:

```python
class TestLocalProviderVocabulary:
    def test_local_providers_are_ollama_and_lmstudio(self):
        from app.core.ai_client import LOCAL_PROVIDERS

        assert LOCAL_PROVIDERS == {"ollama", "lmstudio"}

    def test_default_base_urls_match_upstream_conventions(self):
        from app.core.ai_client import LOCAL_DEFAULT_BASE_URLS

        assert LOCAL_DEFAULT_BASE_URLS["ollama"] == "http://localhost:11434/v1"
        assert LOCAL_DEFAULT_BASE_URLS["lmstudio"] == "http://localhost:1234/v1"

    def test_blank_configured_url_falls_back_to_default(self):
        from app.core.ai_client import resolve_local_base_url

        assert resolve_local_base_url("ollama", "") == "http://localhost:11434/v1"
        assert resolve_local_base_url("lmstudio", "   ") == "http://localhost:1234/v1"

    def test_configured_url_wins_and_trailing_slash_is_stripped(self):
        from app.core.ai_client import resolve_local_base_url

        assert resolve_local_base_url("ollama", "http://box:8080/v1/") == "http://box:8080/v1"

    def test_resolve_returns_blank_for_a_remote_provider(self):
        from app.core.ai_client import resolve_local_base_url

        assert resolve_local_base_url("openai", "") == ""

    def test_known_providers_covers_remote_and_local(self):
        from app.core.ai_client import KNOWN_PROVIDERS

        assert {"anthropic", "openai", "openrouter", "gemini"} <= KNOWN_PROVIDERS
        assert {"ollama", "lmstudio"} <= KNOWN_PROVIDERS


class TestAiIsConfigured:
    def test_remote_provider_requires_a_key(self):
        from app.core.ai_client import ai_is_configured

        assert ai_is_configured("openai", "sk-x") is True
        assert ai_is_configured("openai", "") is False

    def test_local_provider_needs_no_key(self):
        from app.core.ai_client import ai_is_configured

        assert ai_is_configured("ollama", "") is True
        assert ai_is_configured("lmstudio", "") is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run from `backend/`:

```bash
uv run pytest tests/unit/test_ai_client.py -k "LocalProviderVocabulary or AiIsConfigured" -v
```

Expected: FAIL with `ImportError: cannot import name 'LOCAL_PROVIDERS' from 'app.core.ai_client'`.

- [ ] **Step 3: Write the implementation**

In `backend/app/core/ai_client.py`, immediately after the `DEFAULT_MODELS` dict, insert:

```python
# Ollama and LM Studio both expose the OpenAI-compatible /v1/chat/completions
# contract, so they reuse _call_openai_compatible with only the base URL
# differing. They are grouped here because several invariants elsewhere
# (credential presence, timeout, error wording) key off "is this local?" and
# must not each hardcode the slug list.
LOCAL_PROVIDERS = frozenset({"ollama", "lmstudio"})

LOCAL_DEFAULT_BASE_URLS = {
    "ollama": "http://localhost:11434/v1",
    "lmstudio": "http://localhost:1234/v1",
}

# DEFAULT_MODELS deliberately has no entry for the local slugs: there is no
# correct default, because the answer depends on which weights the user pulled.
# That means DEFAULT_MODELS can no longer double as the set of providers that
# exist, which is what this is for.
KNOWN_PROVIDERS = frozenset(DEFAULT_MODELS) | LOCAL_PROVIDERS


def resolve_local_base_url(provider: str, configured: str) -> str:
    """Base URL for a local provider: the configured value, else the default.

    Returns "" for a remote provider so a caller can use a truthy check as
    "is there a local endpoint here?" without a second membership test.
    A trailing slash is stripped so callers can append "/chat/completions"
    unconditionally without producing a double slash.
    """
    if provider not in LOCAL_PROVIDERS:
        return ""
    url = (configured or "").strip() or LOCAL_DEFAULT_BASE_URLS[provider]
    return url.rstrip("/")


def ai_is_configured(provider: str, api_key: str) -> bool:
    """True if this provider has what it needs to make a call.

    Local providers authenticate to nothing, so requiring a key would silently
    disable them at every call site that guards on one. Four such gates exist
    (this module, curator, matching_coordinator, identification_coordinator);
    they all call this rather than testing the key directly.
    """
    return provider in LOCAL_PROVIDERS or bool(api_key)
```

Also extend `_PROVIDER_LABELS` in the same file so local failures read as product names:

```python
_PROVIDER_LABELS = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
    "gemini": "Gemini",
    "ollama": "Ollama",
    "lmstudio": "LM Studio",
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/unit/test_ai_client.py -k "LocalProviderVocabulary or AiIsConfigured" -v
```

Expected: PASS, 8 passed.

- [ ] **Step 5: Run the full ai_client suite for regressions**

```bash
uv run pytest tests/unit/test_ai_client.py -v
```

Expected: PASS, no failures.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check app/core/ai_client.py && uv run ruff format app/core/ai_client.py
git add backend/app/core/ai_client.py backend/tests/unit/test_ai_client.py
git commit -m "feat(ai): add local provider vocabulary and ai_is_configured (#606)"
```

---

## Task 2: `is_safe_local_ai_url` SSRF guard

**Files:**
- Modify: `backend/app/core/security.py`
- Test: `backend/tests/unit/test_security_local_ai_url.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_security_local_ai_url.py`:

```python
"""Tests for the local-AI base-URL guard.

This guard is deliberately weaker than is_safe_remote_url: it must ACCEPT
loopback and private addresses, because that is where a local inference server
lives. What it still rejects is what makes it a guard at all.
"""

import pytest


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434/v1",
        "http://127.0.0.1:1234/v1",
        "http://192.168.1.50:11434/v1",
        "https://ai.lan:1234/v1",
        "http://[::1]:11434/v1",
    ],
)
def test_accepts_local_and_private_endpoints(url):
    from app.core.security import is_safe_local_ai_url

    assert is_safe_local_ai_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "",
        "file:///etc/passwd",
        "ftp://localhost/v1",
        "javascript:alert(1)",
        "http://",
        "//localhost:11434/v1",
        "http://user:pass@localhost:11434/v1",
        "http://localhost:11434/v1?x=1",
        "http://localhost:11434/v1#frag",
    ],
)
def test_rejects_unsafe_shapes(url):
    from app.core.security import is_safe_local_ai_url

    assert is_safe_local_ai_url(url) is False


def test_is_not_an_alias_for_the_remote_guard():
    """Regression guard: the two must stay distinct.

    If someone ever points is_safe_local_ai_url at is_safe_remote_url, every
    local provider breaks with a confusing validation error rather than an
    obvious one, so pin the difference.
    """
    from app.core.security import is_safe_local_ai_url, is_safe_remote_url

    assert is_safe_remote_url("http://localhost:11434/v1") is False
    assert is_safe_local_ai_url("http://localhost:11434/v1") is True
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/unit/test_security_local_ai_url.py -v
```

Expected: FAIL with `ImportError: cannot import name 'is_safe_local_ai_url'`.

- [ ] **Step 3: Write the implementation**

In `backend/app/core/security.py`, insert directly after `is_safe_dashboard_url`:

```python
def is_safe_local_ai_url(url: str) -> bool:
    """Return True if ``url`` is usable as a local AI server's base URL.

    Deliberately NOT is_safe_remote_url. That guard rejects loopback, private
    and link-local hosts to prevent SSRF, and those are precisely the addresses
    an Ollama or LM Studio server listens on, so this is an intentional
    exemption rather than an oversight.

    Unlike is_safe_dashboard_url (whose value is only rendered as a link), the
    server DOES issue POST requests to this URL, so this is a blind-SSRF
    primitive against the host's own loopback interface. It is accepted because
    the config surface is already fully trusted (it holds API keys, filesystem
    paths and library roots), because the endpoints that write it are gated by
    require_localhost_or_lan, and because the response body is parsed as JSON
    and discarded unless it matches the requested schema, making the primitive
    blind rather than an exfiltration channel. See
    docs/superpowers/specs/2026-08-29-local-ai-provider-design.md.

    What is still rejected: non-http(s) schemes (file:, javascript:, ftp:), a
    missing host, embedded credentials, and any query or fragment. The last two
    matter because callers append "/chat/completions" to this value, so a query
    string would silently move the path into the query and a credential would be
    logged with the request URL.
    """
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False

    if parsed.scheme not in ("http", "https"):
        return False
    if not host:
        return False
    if parsed.username or parsed.password:
        return False
    if parsed.query or parsed.fragment:
        return False
    return True
```

Then add a bullet to the module docstring's list, after the
`is_within_configured_roots` bullet:

```
- ``is_safe_local_ai_url`` — a deliberately permissive guard for the
  user-configured local AI server base URL, which must accept loopback.
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/unit/test_security_local_ai_url.py -v
```

Expected: PASS, 15 passed.

- [ ] **Step 5: Verify the existing security suite still passes**

```bash
uv run pytest tests/unit/ -k security -v
```

Expected: PASS, no failures.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check app/core/security.py && uv run ruff format app/core/security.py
git add backend/app/core/security.py backend/tests/unit/test_security_local_ai_url.py
git commit -m "feat(security): add is_safe_local_ai_url loopback-permitting guard (#606)"
```

---

## Task 3: Local error messages

Do this before wiring dispatch so that Task 4 can assert on real text.

**Files:**
- Modify: `backend/app/core/ai_client.py`
- Test: `backend/tests/unit/test_ai_client.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_ai_client.py`:

```python
class TestLocalErrorMessages:
    def test_network_failure_names_the_product_and_url(self):
        import httpx

        from app.core.ai_client import classify_provider_error

        code, message = classify_provider_error(
            "ollama",
            httpx.ConnectError("refused"),
            base_url="http://localhost:11434/v1",
        )
        assert code == "network"
        assert "Ollama" in message
        assert "http://localhost:11434/v1" in message
        assert "ollama serve" in message

    def test_lmstudio_network_failure_does_not_mention_ollama(self):
        import httpx

        from app.core.ai_client import classify_provider_error

        _, message = classify_provider_error(
            "lmstudio",
            httpx.ConnectError("refused"),
            base_url="http://localhost:1234/v1",
        )
        assert "LM Studio" in message
        assert "ollama" not in message.lower()

    def test_404_tells_the_user_to_pull_the_model(self):
        from app.core.ai_client import classify_provider_error

        code, message = classify_provider_error(
            "ollama",
            _status_error_with_body(404, "model not found"),
            model="llama3.1:8b",
        )
        assert code == "model_unavailable"
        assert "ollama pull llama3.1:8b" in message

    def test_remote_providers_keep_their_wording(self):
        """Regression guard: the local table must not leak into remote text."""
        import httpx

        from app.core.ai_client import classify_provider_error

        _, message = classify_provider_error("openai", httpx.ConnectError("refused"))
        assert message == "Could not reach OpenAI. Check the internet connection."

    def test_no_credits_is_never_reported_for_a_local_provider(self):
        """A local server has no billing, so the remote wording would be nonsense."""
        from app.core.ai_client import classify_provider_error

        _, message = classify_provider_error("ollama", _status_error_with_body(402, ""))
        assert "credits" not in message.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/unit/test_ai_client.py -k TestLocalErrorMessages -v
```

Expected: FAIL with `TypeError: classify_provider_error() got an unexpected keyword argument 'base_url'`.

- [ ] **Step 3: Write the implementation**

In `backend/app/core/ai_client.py`, add after `_CAUSE_MESSAGES`:

```python
# Local servers have no account, no billing and no key, so the remote wording
# ("the account has no API credits") is actively misleading. These override
# _CAUSE_MESSAGES for the local slugs only. {provider} is the display label,
# {url} the resolved base URL, {model} the requested model id, and {hint} a
# per-product instruction for starting the server.
_LOCAL_CAUSE_MESSAGES: dict[ProviderErrorCode, str] = {
    "network": "Could not reach {provider} at {url}. Is the server running? ({hint})",
    "timeout": (
        "{provider} at {url} did not respond in time. A model loading for the first "
        "time can be slow; try again once it is loaded."
    ),
    "model_unavailable": "Model '{model}' is not available in {provider}. {pull}",
    "bad_key": "{provider} rejected the request at {url}.",
    "no_credits": "{provider} rejected the request at {url}.",
    "rate_limited": "{provider} at {url} is busy. Wait a moment and try again.",
    "bad_request": "{provider} at {url} rejected the request.",
    "unknown": "{provider} at {url} returned an unexpected error.",
}

_LOCAL_START_HINTS = {
    "ollama": "run: ollama serve",
    "lmstudio": "start the server from LM Studio's Developer tab",
}

_LOCAL_PULL_HINTS = {
    "ollama": "Pull it first: ollama pull {model}",
    "lmstudio": "Download it in LM Studio, then load it.",
}

# Shown when a local provider has no model set. Unlike the remote providers
# there is no default to fall back to, so this is a hard stop rather than a
# silent None.
_LOCAL_BLANK_MODEL_MESSAGE = (
    "Select a model for {provider}. Local providers have no default, because the "
    "answer depends on which models you have installed."
)
```

Replace `classify_provider_error` with:

```python
def classify_provider_error(
    provider: str,
    exc: Exception,
    *,
    base_url: str = "",
    model: str = "",
) -> tuple[ProviderErrorCode, str]:
    """Map a provider failure to a stable ``(code, human_message)`` pair.

    The provider's own body is the only thing that separates a permanently
    exhausted quota from transient throttling (both are HTTP 429 on OpenAI), so
    this reads it. The body is never returned to the caller; it is summarised
    into a fixed sentence and left to the logs.

    ``base_url`` and ``model`` are used only for the local providers, whose
    messages name the endpoint and the model because "connection refused" is
    useless to a user who has three inference servers on different ports.
    """
    code: ProviderErrorCode
    if isinstance(exc, httpx.TimeoutException):
        code = "timeout"
    elif isinstance(exc, httpx.HTTPStatusError):
        code = _classify_status(provider, exc)
    elif isinstance(exc, httpx.HTTPError):
        code = "network"
    else:
        code = "unknown"

    label = _PROVIDER_LABELS.get(provider, provider)
    if provider in LOCAL_PROVIDERS:
        template = _LOCAL_CAUSE_MESSAGES.get(code, _LOCAL_CAUSE_MESSAGES["unknown"])
        pull = _LOCAL_PULL_HINTS.get(provider, "").format(model=model or "the model")
        return code, template.format(
            provider=label,
            url=base_url or LOCAL_DEFAULT_BASE_URLS.get(provider, ""),
            model=model or "(none set)",
            hint=_LOCAL_START_HINTS.get(provider, ""),
            pull=pull,
        )
    return code, _CAUSE_MESSAGES[code].format(provider=label)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/unit/test_ai_client.py -k TestLocalErrorMessages -v
```

Expected: PASS, 5 passed.

- [ ] **Step 5: Run the full ai_client suite**

```bash
uv run pytest tests/unit/test_ai_client.py -v
```

Expected: PASS. If an existing test asserts on an exact remote message, that
message must be unchanged; investigate rather than editing the assertion.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check app/core/ai_client.py && uv run ruff format app/core/ai_client.py
git add backend/app/core/ai_client.py backend/tests/unit/test_ai_client.py
git commit -m "feat(ai): local-provider error messages naming endpoint and model (#606)"
```

---

## Task 4: Wire the local slugs into `complete_json`

**Files:**
- Modify: `backend/app/core/ai_client.py`
- Test: `backend/tests/unit/test_ai_client.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_ai_client.py`:

```python
def _openai_shaped(text: str):
    """A minimal OpenAI-compatible chat-completions body."""
    return {"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}


class TestLocalDispatch:
    @pytest.mark.asyncio
    async def test_ollama_posts_to_the_default_endpoint_without_a_key(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx(_openai_shaped('{"ok": true}'))
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(
                prompt="hi",
                schema=None,
                provider="ollama",
                api_key="",
                model="llama3.1:8b",
            )

        assert result == {"ok": True}
        call = mock.post.await_args
        assert call.args[0] == "http://localhost:11434/v1/chat/completions"
        assert call.kwargs["json"]["model"] == "llama3.1:8b"

    @pytest.mark.asyncio
    async def test_lmstudio_uses_its_own_default_port(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx(_openai_shaped('{"ok": true}'))
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            await complete_json(
                prompt="hi",
                schema=None,
                provider="lmstudio",
                api_key="",
                model="qwen2.5-7b-instruct",
            )

        assert mock.post.await_args.args[0] == "http://localhost:1234/v1/chat/completions"

    @pytest.mark.asyncio
    async def test_configured_base_url_overrides_the_default(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx(_openai_shaped('{"ok": true}'))
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            await complete_json(
                prompt="hi",
                schema=None,
                provider="ollama",
                api_key="",
                model="llama3.1:8b",
                base_url="http://gpubox:11434/v1",
            )

        assert mock.post.await_args.args[0] == "http://gpubox:11434/v1/chat/completions"

    @pytest.mark.asyncio
    async def test_local_default_timeout_is_generous(self):
        """A cold LM Studio JIT-loads weights; 30s would look like a broken feature."""
        from app.core.ai_client import LOCAL_TIMEOUT_SECONDS, complete_json

        mock = _mock_httpx(_openai_shaped('{"ok": true}'))
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock) as ctor:
            await complete_json(
                prompt="hi", schema=None, provider="ollama", api_key="", model="m"
            )

        assert ctor.call_args.kwargs["timeout"] == LOCAL_TIMEOUT_SECONDS
        assert LOCAL_TIMEOUT_SECONDS >= 300.0

    @pytest.mark.asyncio
    async def test_remote_default_timeout_is_unchanged(self):
        from app.core.ai_client import _TIMEOUT_SECONDS, complete_json

        mock = _mock_httpx({"content": [{"text": '{"ok": true}'}]})
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock) as ctor:
            await complete_json(
                prompt="hi", schema=None, provider="anthropic", api_key="sk-ant-x"
            )

        assert ctor.call_args.kwargs["timeout"] == _TIMEOUT_SECONDS

    @pytest.mark.asyncio
    async def test_explicit_timeout_still_wins_for_a_local_provider(self):
        from app.core.ai_client import complete_json

        mock = _mock_httpx(_openai_shaped('{"ok": true}'))
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock) as ctor:
            await complete_json(
                prompt="hi",
                schema=None,
                provider="ollama",
                api_key="",
                model="m",
                timeout=5.0,
            )

        assert ctor.call_args.kwargs["timeout"] == 5.0

    @pytest.mark.asyncio
    async def test_blank_model_raises_a_pointed_error(self):
        from app.core.ai_client import complete_json
        from app.core.errors import AIProviderError

        with pytest.raises(AIProviderError) as exc:
            await complete_json(
                prompt="hi",
                schema=None,
                provider="ollama",
                api_key="",
                model=None,
                raise_on_error=True,
            )

        assert "Select a model" in str(exc.value)
        assert exc.value.code == "bad_request"

    @pytest.mark.asyncio
    async def test_blank_model_returns_none_when_not_raising(self):
        from app.core.ai_client import complete_json

        result = await complete_json(
            prompt="hi", schema=None, provider="ollama", api_key="", model=None
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_unsafe_base_url_is_refused_before_any_request(self):
        from app.core.ai_client import complete_json

        with patch("app.core.ai_client.httpx.AsyncClient") as ctor:
            result = await complete_json(
                prompt="hi",
                schema=None,
                provider="ollama",
                api_key="",
                model="m",
                base_url="file:///etc/passwd",
            )

        assert result is None
        ctor.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "model_id",
        ["llama3.1:8b", "qwen2.5:7b-instruct-q4_K_M", "lmstudio-community/Model-GGUF"],
    )
    async def test_real_local_model_ids_are_accepted(self, model_id):
        """A rejection here surfaces as a misleading 'clear the AI model field'."""
        from app.core.ai_client import complete_json

        mock = _mock_httpx(_openai_shaped('{"ok": true}'))
        with patch("app.core.ai_client.httpx.AsyncClient", return_value=mock):
            result = await complete_json(
                prompt="hi", schema=None, provider="ollama", api_key="", model=model_id
            )

        assert result == {"ok": True}
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/unit/test_ai_client.py -k TestLocalDispatch -v
```

Expected: FAIL. The first failures are `TypeError: complete_json() got an unexpected keyword argument 'base_url'`.

- [ ] **Step 3: Add the timeout constants**

In `backend/app/core/ai_client.py`, below `_TIMEOUT_SECONDS = 30.0`:

```python
# Local inference is not comparable to a hosted API. LM Studio JIT-loads model
# weights on the FIRST request, so a cold 7B model can spend 30-60s before
# emitting a token, and CPU-only generation runs into the minutes. The remote
# 30s value would make local support look broken on first use.
LOCAL_TIMEOUT_SECONDS = 300.0
# The Test Connection button: still generous enough to survive a cold load, but
# bounded so a user is not left staring at a spinner for five minutes.
LOCAL_VALIDATE_TIMEOUT_SECONDS = 120.0
```

- [ ] **Step 4: Change the `complete_json` signature and guards**

Change the signature so `timeout` resolves per provider. Replace:

```python
    timeout: float = _TIMEOUT_SECONDS,
) -> dict | None:
```

with:

```python
    base_url: str = "",
    timeout: float | None = None,
) -> dict | None:
```

Add to the docstring, just above the closing `"""`:

```
    ``base_url`` applies only to the local providers (ollama, lmstudio); blank
    means the slug's conventional default port. ``timeout`` of None means "pick
    the right default for this provider", which is much longer for local ones.
```

Replace the guard block that currently begins `if not api_key:` and ends just
before the `if provider == "anthropic":` dispatch with:

```python
    if not ai_is_configured(provider, api_key):
        logger.debug("complete_json called without a usable credential; returning None")
        return None

    if provider not in KNOWN_PROVIDERS:
        logger.warning("Unknown AI provider: %s", sanitize_log_value(provider))
        return None

    is_local = provider in LOCAL_PROVIDERS
    local_url = resolve_local_base_url(provider, base_url)

    if is_local and not is_safe_local_ai_url(local_url):
        # Refused before any request is issued, so a malformed value can never
        # become an outbound fetch.
        logger.warning("Rejecting unsafe local AI base URL: %s", sanitize_log_value(local_url))
        if raise_on_error:
            raise AIProviderError(
                f"'{local_url}' is not a valid server address. Use a plain http(s) "
                "URL such as http://localhost:11434/v1.",
                code="bad_request",
            )
        return None

    model = model or DEFAULT_MODELS.get(provider)
    if not model:
        # Only reachable for a local provider now, since KNOWN_PROVIDERS was
        # checked above and every remote slug has a default.
        logger.warning("No model set for local provider %s", sanitize_log_value(provider))
        if raise_on_error:
            raise AIProviderError(
                _LOCAL_BLANK_MODEL_MESSAGE.format(
                    provider=_PROVIDER_LABELS.get(provider, provider)
                ),
                code="bad_request",
            )
        return None

    if not _is_safe_model_name(model):
        logger.warning("Rejecting unsafe AI model name: %s", sanitize_log_value(model))
        if raise_on_error:
            raise AIProviderError(
                _INVALID_MODEL_MESSAGE.format(provider=_PROVIDER_LABELS.get(provider, provider)),
                code="bad_request",
            )
        return None

    if timeout is None:
        timeout = LOCAL_TIMEOUT_SECONDS if is_local else _TIMEOUT_SECONDS
```

Add the import at the top of the file, alongside the existing `security` import:

```python
from app.core.security import is_safe_local_ai_url, sanitize_log_value
```

- [ ] **Step 5: Add the dispatch branch**

In the `if provider == "anthropic": ... elif ...` chain, insert before the final
`else:`:

```python
    elif is_local:
        # Both local servers speak the OpenAI chat-completions contract, so this
        # is the same adapter the openai/openrouter slugs use. Ollama requires an
        # Authorization header but ignores its value, and LM Studio ignores the
        # header entirely, so an empty api_key is correct for both.
        def factory():
            return _call_openai_compatible(
                prompt,
                api_key or "local",
                f"{local_url}/chat/completions",
                model,
                max_tokens,
                schema,
                timeout,
            )
```

- [ ] **Step 6: Pass the local context into error classification**

In the `except httpx.HTTPError as e:` block, replace

```python
        code, message = classify_provider_error(provider, e)
```

with

```python
        code, message = classify_provider_error(provider, e, base_url=local_url, model=model)
```

- [ ] **Step 7: Run the tests to verify they pass**

```bash
uv run pytest tests/unit/test_ai_client.py -k TestLocalDispatch -v
```

Expected: PASS, 11 passed.

- [ ] **Step 8: Run the whole unit tier for regressions**

```bash
uv run pytest tests/unit/ -q
```

Expected: PASS. Pay attention to `test_ai_identifier.py`, `test_llm_episode_matcher.py`
and `test_config_ai_model_field.py`, which all exercise `complete_json`.

- [ ] **Step 9: Lint and commit**

```bash
uv run ruff check app/core/ai_client.py && uv run ruff format app/core/ai_client.py
git add backend/app/core/ai_client.py backend/tests/unit/test_ai_client.py
git commit -m "feat(ai): route ollama and lmstudio through the OpenAI-compatible adapter (#606)"
```

---

## Task 5: Preamble-tolerant JSON parsing

Small local models routinely emit reasoning text before the JSON object. This only
runs where parsing already failed, so it cannot regress the remote providers.

**Files:**
- Modify: `backend/app/core/ai_client.py`
- Test: `backend/tests/unit/test_ai_client.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_ai_client.py`:

```python
class TestPreambleTolerantParsing:
    def test_extracts_json_after_a_reasoning_preamble(self):
        from app.core.ai_client import _parse_json_text

        text = 'Let me think about this. The answer is:\n{"episode": 3, "confidence": 0.8}'
        assert _parse_json_text(text) == {"episode": 3, "confidence": 0.8}

    def test_ignores_trailing_commentary(self):
        from app.core.ai_client import _parse_json_text

        assert _parse_json_text('{"ok": true}\nHope that helps!') == {"ok": True}

    def test_handles_nested_braces(self):
        from app.core.ai_client import _parse_json_text

        text = 'Result: {"a": {"b": 1}, "c": 2} done'
        assert _parse_json_text(text) == {"a": {"b": 1}, "c": 2}

    def test_ignores_braces_inside_strings(self):
        from app.core.ai_client import _parse_json_text

        assert _parse_json_text('x {"title": "a } b"} y') == {"title": "a } b"}

    def test_plain_json_is_unaffected(self):
        from app.core.ai_client import _parse_json_text

        assert _parse_json_text('{"ok": true}') == {"ok": True}

    def test_fenced_json_is_still_handled(self):
        from app.core.ai_client import _parse_json_text

        assert _parse_json_text('```json\n{"ok": true}\n```') == {"ok": True}

    def test_genuinely_unparseable_text_still_returns_none(self):
        from app.core.ai_client import _parse_json_text

        assert _parse_json_text("I could not determine the episode.") is None

    def test_unbalanced_braces_return_none(self):
        from app.core.ai_client import _parse_json_text

        assert _parse_json_text('here you go {"ok": true') is None

    def test_a_bare_json_array_still_returns_none(self):
        """complete_json's contract is a dict; an array is not a usable result."""
        from app.core.ai_client import _parse_json_text

        assert _parse_json_text("[1, 2, 3]") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/unit/test_ai_client.py -k TestPreambleTolerantParsing -v
```

Expected: FAIL on `test_extracts_json_after_a_reasoning_preamble`,
`test_ignores_trailing_commentary`, `test_handles_nested_braces` and
`test_ignores_braces_inside_strings`. The other five should already pass.

- [ ] **Step 3: Write the implementation**

In `backend/app/core/ai_client.py`, add above `_parse_json_text`:

```python
def _extract_first_json_object(text: str) -> str | None:
    """Return the first balanced {...} span in ``text``, or None.

    Small local models frequently wrap their answer in prose ("Let me think...
    the answer is: {...}"), which no amount of prompting reliably suppresses.
    Brace counting is string-aware so that a '}' inside a value (an episode
    title, say) does not truncate the span, and escape-aware so a quote escaped
    inside a string does not flip the in-string state.
    """
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None
```

Then replace the `except json.JSONDecodeError:` block inside `_parse_json_text` with:

```python
    except json.JSONDecodeError:
        # Last resort, and only on a path that has already failed: pull the first
        # balanced object out of surrounding prose. Reached in practice only for
        # local models, so it cannot regress the hosted providers.
        candidate = _extract_first_json_object(text)
        if candidate is not None and candidate != text:
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError:
                logger.warning("Failed to parse AI response as JSON: %s", text[:200])
                return None
            return data if isinstance(data, dict) else None
        logger.warning("Failed to parse AI response as JSON: %s", text[:200])
        return None
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/unit/test_ai_client.py -k TestPreambleTolerantParsing -v
```

Expected: PASS, 9 passed.

- [ ] **Step 5: Run the full ai_client suite**

```bash
uv run pytest tests/unit/test_ai_client.py -q
```

Expected: PASS.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check app/core/ai_client.py && uv run ruff format app/core/ai_client.py
git add backend/app/core/ai_client.py backend/tests/unit/test_ai_client.py
git commit -m "fix(ai): tolerate reasoning preamble around JSON replies (#606)"
```

---

## Task 6: The `ai_local_base_url` config field

This project has a documented hazard: a new `AppConfig` field that is not also added to
`ConfigUpdate` and `ConfigResponse` is silently dropped by Pydantic, with no error.
The round-trip test in Step 1 is the regression guard for exactly that.

**Files:**
- Modify: `backend/app/models/app_config.py`
- Modify: `backend/app/api/routes.py`
- Test: `backend/tests/unit/test_config_ai_model_field.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_config_ai_model_field.py`:

This file is deliberately model-level with no database, and validates writes by calling
`update_config` directly (it raises before touching persistence). Match that style
rather than introducing an HTTP client fixture.

```python
BASE_URL_FIELD = "ai_local_base_url"


class TestAiLocalBaseUrlField:
    """Three-way sync for the local AI base URL.

    Same hazard as ai_model above: a field missing from ConfigUpdate or
    ConfigResponse is dropped by Pydantic with no error anywhere, so the wizard
    appears to save and the value never arrives.
    """

    def test_appconfig_defaults_to_blank(self):
        """Blank means "use the slug's conventional port", not "no endpoint"."""
        assert getattr(AppConfig(), BASE_URL_FIELD) == ""

    def test_config_update_accepts_and_carries_the_field(self):
        update = ConfigUpdate(**{BASE_URL_FIELD: "http://box:11434/v1"})
        assert update.model_dump()[BASE_URL_FIELD] == "http://box:11434/v1"
        # Unset stays None so PUT doesn't clobber it when omitted.
        assert ConfigUpdate().model_dump()[BASE_URL_FIELD] is None

    def test_config_response_exposes_the_field(self):
        assert BASE_URL_FIELD in ConfigResponse.model_fields


class TestLocalBaseUrlIsValidatedOnWrite:
    """The value is interpolated into an outbound request URL, so PUT must
    reject a malformed one rather than storing it for a later request to
    execute. update_config raises before it touches the database."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad",
        [
            "file:///etc/passwd",
            "javascript:alert(1)",
            "http://user:pass@localhost:11434/v1",
            "http://localhost:11434/v1?x=1",
            "//localhost:11434/v1",
        ],
    )
    async def test_unsafe_base_url_is_rejected(self, bad):
        with pytest.raises(HTTPException) as exc:
            await update_config(ConfigUpdate(ai_local_base_url=bad))
        assert exc.value.status_code == 422
        assert BASE_URL_FIELD in str(exc.value.detail)

    @pytest.mark.asyncio
    async def test_blank_clears_back_to_the_default(self):
        """Unlike a key, this is not a secret: clearing it is how a user reverts
        to the default port, so "" must pass the guard and reach persistence.
        A regression here would add it to config_service's sensitive_fields set,
        where a blank write is deliberately ignored."""
        assert ConfigUpdate(ai_local_base_url="").model_dump()[BASE_URL_FIELD] == ""

    @pytest.mark.asyncio
    async def test_a_loopback_url_is_accepted(self):
        """The whole point: is_safe_remote_url would reject this."""
        from app.core.security import is_safe_local_ai_url

        assert is_safe_local_ai_url("http://localhost:11434/v1") is True
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/unit/test_config_ai_model_field.py -k "LocalBaseUrl" -v
```

Expected: FAIL. `test_appconfig_defaults_to_blank` fails with
`AttributeError: 'AppConfig' object has no attribute 'ai_local_base_url'`, and the
`ConfigUpdate(...)` cases fail because Pydantic ignores the unknown keyword and
`model_dump()` raises `KeyError: 'ai_local_base_url'`. That KeyError is precisely the
silent-drop failure mode this class exists to catch.

- [ ] **Step 3: Add the persisted field**

In `backend/app/models/app_config.py`, directly after the `ai_model` field:

```python
    # Base URL for a local AI server (ollama / lmstudio only). "" means "use the
    # slug's conventional default port", which is what makes the common case
    # zero-config. Not a secret: it must stay out of the sensitive_fields set in
    # config_service, because clearing it back to the default is a legitimate
    # action and that set exists to prevent blanking.
    ai_local_base_url: str = ""
```

Also update the `ai_provider` comment on the line above to list the new slugs:

```python
    # "anthropic" | "openai" | "openrouter" | "gemini" | "ollama" | "lmstudio"
    ai_provider: str = "anthropic"
```

- [ ] **Step 4: Add the field to both API models**

In `backend/app/api/routes.py`, in `ConfigResponse` after `ai_model: str`:

```python
    ai_local_base_url: str
```

In `ConfigUpdate` after `ai_model: str | None = None`:

```python
    ai_local_base_url: str | None = None
```

In the `ConfigResponse(...)` construction in the GET handler, after the `ai_model=` line:

```python
        # Not a secret; the wizard must round-trip it to show the endpoint in use.
        ai_local_base_url=config.ai_local_base_url or "",
```

- [ ] **Step 5: Validate the URL on write**

In `backend/app/api/routes.py`, immediately after the existing `ai_model` validation
block in the `PUT /api/config` handler:

```python
    # Validated on write for the same reason ai_model is: this value is
    # interpolated into an outbound request URL, so an unchecked value stored
    # here becomes a request-forgery primitive executed on the next match. A
    # blank value is the documented way to revert to the provider default and is
    # deliberately allowed through.
    if update_data.get("ai_local_base_url"):
        from app.core.security import is_safe_local_ai_url

        if not is_safe_local_ai_url(update_data["ai_local_base_url"]):
            raise HTTPException(
                status_code=422,
                detail=(
                    "ai_local_base_url must be a plain http(s) URL with no credentials "
                    "or query string, such as 'http://localhost:11434/v1'"
                ),
            )
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
uv run pytest tests/unit/test_config_ai_model_field.py -v
```

Expected: PASS.

- [ ] **Step 7: Verify the schema reconciler picks up the new column**

The project adds columns through `database.py` reconcilers, not only Alembic, because
frozen builds skip Alembic entirely. Confirm a fresh boot adds the column:

```bash
uv run python -c "import asyncio; from app.database import init_db; asyncio.run(init_db())"
uv run python -c "
import sqlite3
cols = [r[1] for r in sqlite3.connect('engram.db').execute('PRAGMA table_info(app_config)')]
assert 'ai_local_base_url' in cols, cols
print('column present')
"
```

Expected: `column present`.

- [ ] **Step 8: Run the config API tests**

```bash
uv run pytest tests/unit/test_api_routes.py -q
```

Expected: PASS.

- [ ] **Step 9: Lint and commit**

```bash
uv run ruff check app/ && uv run ruff format app/models/app_config.py app/api/routes.py
git add backend/app/models/app_config.py backend/app/api/routes.py backend/tests/unit/test_config_ai_model_field.py
git commit -m "feat(config): add ai_local_base_url with write-time URL validation (#606)"
```

---

## Task 7: Replace the three API-key gates

The highest-risk task in the plan. Miss one of these and the feature ships looking
configured while doing nothing.

**Files:**
- Modify: `backend/app/core/curator.py:608`
- Modify: `backend/app/services/matching_coordinator.py:727`
- Modify: `backend/app/services/identification_coordinator.py:1819`
- Test: `backend/tests/unit/test_ai_configured_gates.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_ai_configured_gates.py`:

```python
"""The 'is AI configured?' invariant.

Four call sites once used `if not config.ai_api_key` as a proxy for 'is AI
usable?'. Local providers have no key, so any site still testing the key
directly silently disables local AI while appearing correctly configured. This
is a source-level guard because the alternative is three separate integration
tests that each need a full job pipeline.
"""

import inspect

import pytest

_GATED_MODULES = [
    "app.core.curator",
    "app.services.matching_coordinator",
    "app.services.identification_coordinator",
]


@pytest.mark.parametrize("module_path", _GATED_MODULES)
def test_no_module_gates_on_the_api_key_directly(module_path):
    import importlib

    source = inspect.getsource(importlib.import_module(module_path))
    assert "not config.ai_api_key" not in source, (
        f"{module_path} still gates on the API key directly. Local providers have "
        "no key, so this silently disables them. Use ai_is_configured(...)."
    )


@pytest.mark.parametrize("module_path", _GATED_MODULES)
def test_each_gated_module_uses_the_shared_helper(module_path):
    import importlib

    source = inspect.getsource(importlib.import_module(module_path))
    assert "ai_is_configured" in source, f"{module_path} does not use ai_is_configured"


def test_complete_json_returns_none_for_a_keyless_remote_provider():
    """The relaxation must not accidentally let remote providers through."""
    import asyncio

    from app.core.ai_client import complete_json

    result = asyncio.run(
        complete_json(prompt="hi", schema=None, provider="openai", api_key="")
    )
    assert result is None
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/unit/test_ai_configured_gates.py -v
```

Expected: FAIL, 6 failures (three modules times two assertions).

- [ ] **Step 3: Update `curator.py`**

In `backend/app/core/curator.py` around line 608, replace:

```python
        if not config.ai_api_key:
            return None
```

with:

```python
        from app.core.ai_client import ai_is_configured

        if not ai_is_configured(config.ai_provider, config.ai_api_key):
            return None
```

Then, in the `match_episode_via_llm(...)` call in the same method, add the base URL
after the `ai_model=` argument:

```python
                ai_local_base_url=getattr(config, "ai_local_base_url", "") or "",
```

- [ ] **Step 4: Update `matching_coordinator.py`**

In `backend/app/services/matching_coordinator.py` around line 727, replace:

```python
            if not config.ai_api_key:
                return None
```

with:

```python
            from app.core.ai_client import ai_is_configured

            if not ai_is_configured(config.ai_provider, config.ai_api_key):
                return None
```

- [ ] **Step 5: Update `identification_coordinator.py`**

In `backend/app/services/identification_coordinator.py` around line 1819, replace the
condition line:

```python
            and config.ai_api_key
```

with:

```python
            and ai_is_configured(config.ai_provider, config.ai_api_key)
```

Add the import at the top of that same `if` block's enclosing function, next to the
existing local imports:

```python
        from app.core.ai_client import ai_is_configured
```

Then extend the `identify_from_label(...)` call with the base URL as a fourth
positional-compatible keyword:

```python
                ai_result = await identify_from_label(
                    job.volume_label,
                    config.ai_provider,
                    config.ai_api_key,
                    getattr(config, "ai_model", "") or None,
                    base_url=getattr(config, "ai_local_base_url", "") or "",
                )
```

- [ ] **Step 6: Thread `base_url` through the two intermediate callers**

In `backend/app/core/ai_identifier.py`, add a `base_url: str = ""` keyword to
`identify_from_label`'s signature and pass it straight through to `complete_json`:

```python
        base_url=base_url,
```

In `backend/app/matcher/llm_episode_matcher.py`, add `ai_local_base_url: str = ""` to
`match_episode_via_llm`'s signature and pass it to `complete_json` as:

```python
        base_url=ai_local_base_url,
```

- [ ] **Step 7: Run the tests to verify they pass**

```bash
uv run pytest tests/unit/test_ai_configured_gates.py -v
```

Expected: PASS, 7 passed.

- [ ] **Step 8: Run the AI-adjacent suites for regressions**

```bash
uv run pytest tests/unit/test_ai_identifier.py tests/unit/test_llm_episode_matcher.py tests/unit/test_ai_client.py -q
```

Expected: PASS.

- [ ] **Step 9: Lint and commit**

```bash
uv run ruff check app/ && uv run ruff format app/core/curator.py app/core/ai_identifier.py app/services/matching_coordinator.py app/services/identification_coordinator.py app/matcher/llm_episode_matcher.py
git add backend/app backend/tests/unit/test_ai_configured_gates.py
git commit -m "fix(ai): gate on ai_is_configured so keyless local providers run (#606)"
```

---

## Task 8: Fix `validate_ai` for local providers

`DEFAULT_MODELS` is currently doing double duty as the known-provider allowlist. Left
alone, the Test Connection button rejects the local slugs and then raises `KeyError`.

**Files:**
- Modify: `backend/app/api/validation.py`
- Test: `backend/tests/unit/test_validation_ai.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_validation_ai.py`:

```python
class TestValidateAiLocalProviders:
    @pytest.mark.asyncio
    async def test_local_provider_is_accepted_without_a_key(self, client):
        from unittest.mock import AsyncMock, patch

        with patch(
            "app.core.ai_client.complete_json", new=AsyncMock(return_value={"ok": True})
        ):
            resp = await client.post(
                "/api/validate/ai",
                json={"provider": "ollama", "model": "llama3.1:8b"},
            )

        assert resp.status_code == 200
        assert resp.json()["valid"] is True

    @pytest.mark.asyncio
    async def test_local_provider_with_no_model_is_rejected_clearly(self, client):
        resp = await client.post("/api/validate/ai", json={"provider": "ollama"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        assert "model" in body["error"].lower()

    @pytest.mark.asyncio
    async def test_unknown_provider_is_still_rejected(self, client):
        resp = await client.post("/api/validate/ai", json={"provider": "notreal"})

        assert resp.status_code == 200
        assert resp.json()["valid"] is False
        assert "Unknown AI provider" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_local_validation_uses_the_longer_timeout(self, client):
        from unittest.mock import AsyncMock, patch

        from app.core.ai_client import LOCAL_VALIDATE_TIMEOUT_SECONDS

        spy = AsyncMock(return_value={"ok": True})
        with patch("app.core.ai_client.complete_json", new=spy):
            await client.post(
                "/api/validate/ai",
                json={"provider": "lmstudio", "model": "qwen2.5-7b-instruct"},
            )

        assert spy.await_args.kwargs["timeout"] == LOCAL_VALIDATE_TIMEOUT_SECONDS
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/unit/test_validation_ai.py -k TestValidateAiLocalProviders -v
```

Expected: FAIL. The first is `assert 'Unknown AI provider: ollama' ...` because
`ollama` is not in `DEFAULT_MODELS`.

- [ ] **Step 3: Rewrite the guards in `validate_ai`**

In `backend/app/api/validation.py`, change the import line inside `validate_ai`:

```python
    from app.core.ai_client import (
        DEFAULT_MODELS,
        KNOWN_PROVIDERS,
        LOCAL_PROVIDERS,
        LOCAL_VALIDATE_TIMEOUT_SECONDS,
        ai_is_configured,
        complete_json,
    )
```

Replace the provider membership check:

```python
    if provider not in KNOWN_PROVIDERS:
        return ValidationResponse(
            valid=False, error=f"Unknown AI provider: {provider or '(empty)'}"
        )
```

Replace the `if not api_key:` fatal check near the end of the config-loading block with
one that respects local providers, and add the blank-model check:

```python
    base_url = (request.base_url or "").strip()
    if config is not None and not base_url:
        base_url = (getattr(config, "ai_local_base_url", "") or "").strip()

    if not ai_is_configured(provider, api_key):
        return ValidationResponse(
            valid=False, error="No API key provided and none is saved for this provider"
        )
    if provider in LOCAL_PROVIDERS and not model:
        return ValidationResponse(
            valid=False,
            error=(
                "Select a model first. Local providers have no default, because the "
                "answer depends on which models you have installed."
            ),
        )
```

Update the `complete_json(...)` call to pass the base URL and the right timeout:

```python
            base_url=base_url,
            retries=0,
            timeout=(
                LOCAL_VALIDATE_TIMEOUT_SECONDS if provider in LOCAL_PROVIDERS else 10.0
            ),
```

Fix the success line so it cannot `KeyError` on a local slug:

```python
    return ValidationResponse(valid=True, version=model or DEFAULT_MODELS.get(provider, ""))
```

- [ ] **Step 4: Add `base_url` to the request model**

In `backend/app/api/validation.py`, in `AiValidationRequest`:

```python
    base_url: str | None = None
```

And extend its docstring with:

```
    ``base_url`` applies only to the local providers and, like the others, is
    sent as typed rather than as saved, so the button answers "will this
    endpoint work" before the user commits to it.
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest tests/unit/test_validation_ai.py -v
```

Expected: PASS, including the pre-existing remote-provider tests.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check app/api/validation.py && uv run ruff format app/api/validation.py
git add backend/app/api/validation.py backend/tests/unit/test_validation_ai.py
git commit -m "fix(ai): make the connection test work for local providers (#606)"
```

---

## Task 9: `GET /api/ai/local-models` endpoint

Lives in `validation.py` rather than `routes.py` because it shares that module's two
conventions: gated by `require_localhost_or_lan`, and never returns 5xx. The router is
mounted at `/api`, so the decorator path is `/ai/local-models`.

**Files:**
- Modify: `backend/app/api/validation.py`
- Test: `backend/tests/unit/test_local_models_endpoint.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_local_models_endpoint.py`:

```python
"""Tests for the local AI model-list endpoint."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _mock_get(payload: dict, status: int = 200):
    response = MagicMock()
    response.json.return_value = payload
    response.status_code = status
    response.raise_for_status = MagicMock()
    c = AsyncMock()
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    c.get = AsyncMock(return_value=response)
    return c


class TestLocalModelsEndpoint:
    @pytest.mark.asyncio
    async def test_returns_model_ids_from_the_server(self, client):
        payload = {"data": [{"id": "llama3.1:8b"}, {"id": "qwen2.5:7b"}]}
        with patch("app.api.validation.httpx.AsyncClient", return_value=_mock_get(payload)):
            resp = await client.get("/api/ai/local-models?provider=ollama")

        assert resp.status_code == 200
        assert resp.json()["models"] == ["llama3.1:8b", "qwen2.5:7b"]
        assert resp.json()["error"] is None

    @pytest.mark.asyncio
    async def test_queries_the_default_port_for_the_slug(self, client):
        mock = _mock_get({"data": []})
        with patch("app.api.validation.httpx.AsyncClient", return_value=mock):
            await client.get("/api/ai/local-models?provider=lmstudio")

        assert mock.get.await_args.args[0] == "http://localhost:1234/v1/models"

    @pytest.mark.asyncio
    async def test_unreachable_server_returns_200_with_an_error_string(self, client):
        import httpx

        c = AsyncMock()
        c.__aenter__ = AsyncMock(return_value=c)
        c.__aexit__ = AsyncMock(return_value=False)
        c.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        with patch("app.api.validation.httpx.AsyncClient", return_value=c):
            resp = await client.get("/api/ai/local-models?provider=ollama")

        assert resp.status_code == 200
        assert resp.json()["models"] == []
        assert "Ollama" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_remote_provider_is_rejected(self, client):
        resp = await client.get("/api/ai/local-models?provider=openai")

        assert resp.status_code == 200
        assert resp.json()["models"] == []
        assert "local" in resp.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_unsafe_base_url_is_refused_without_a_request(self, client):
        with patch("app.api.validation.httpx.AsyncClient") as ctor:
            resp = await client.get(
                "/api/ai/local-models?provider=ollama&base_url=file:///etc/passwd"
            )

        assert resp.status_code == 200
        assert resp.json()["models"] == []
        ctor.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_garbage_body_does_not_500(self, client):
        with patch(
            "app.api.validation.httpx.AsyncClient", return_value=_mock_get({"nope": 1})
        ):
            resp = await client.get("/api/ai/local-models?provider=ollama")

        assert resp.status_code == 200
        assert resp.json()["models"] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/unit/test_local_models_endpoint.py -v
```

Expected: FAIL with 404s, since the route does not exist.

- [ ] **Step 3: Write the implementation**

In `backend/app/api/validation.py`, add `import httpx` to the imports at the top, then
append this endpoint:

```python
class LocalModelsResponse(BaseModel):
    """Model ids available on a local AI server.

    Never signals failure with a status code: an unreachable server is the
    NORMAL case while a user is still setting things up, and a 5xx would make
    the wizard render an error banner instead of quietly falling back to a
    free-text model field.
    """

    models: list[str] = []
    error: str | None = None


@router.get("/ai/local-models", response_model=LocalModelsResponse)
async def list_local_models(
    provider: str,
    base_url: str = "",
    _: None = Depends(require_localhost_or_lan),
) -> LocalModelsResponse:
    """List the models a local AI server currently offers.

    Both Ollama and LM Studio expose the OpenAI-compatible ``GET /v1/models``,
    so one implementation serves both. Gated to the host (or an opted-in LAN)
    for the same reason as validate_ai: it triggers an outbound request the
    caller can provoke without owning the destination.

    Doubles as a reachability check, which is why the wizard does not need a
    separate Test button for the local case.
    """
    from app.core.ai_client import (
        LOCAL_PROVIDERS,
        classify_provider_error,
        resolve_local_base_url,
    )
    from app.core.security import is_safe_local_ai_url

    if provider not in LOCAL_PROVIDERS:
        return LocalModelsResponse(
            error=f"'{sanitize_log_value(provider)}' is not a local AI provider"
        )

    url_base = resolve_local_base_url(provider, base_url)
    if not is_safe_local_ai_url(url_base):
        return LocalModelsResponse(
            error=(
                "That is not a valid server address. Use a plain http(s) URL such "
                "as http://localhost:11434/v1."
            )
        )

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{url_base}/models")
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        _, message = classify_provider_error(provider, e, base_url=url_base)
        logger.info(
            "Local model list unavailable for %s: %s",
            sanitize_log_value(provider),
            sanitize_log_value(str(e)),
        )
        return LocalModelsResponse(error=message)
    except Exception:  # noqa: BLE001 — this endpoint must never 500
        logger.warning("Local model list raised unexpectedly", exc_info=True)
        return LocalModelsResponse(error="Could not read the model list")

    models: list[str] = []
    for entry in (data or {}).get("data") or []:
        if isinstance(entry, dict) and entry.get("id"):
            models.append(str(entry["id"]))
    return LocalModelsResponse(models=models)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/unit/test_local_models_endpoint.py -v
```

Expected: PASS, 6 passed.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check app/api/validation.py && uv run ruff format app/api/validation.py
git add backend/app/api/validation.py backend/tests/unit/test_local_models_endpoint.py
git commit -m "feat(ai): add GET /api/ai/local-models for the model picker (#606)"
```

---

## Task 10: Frontend model-list helper

**Files:**
- Create: `frontend/src/utils/localModels.ts`
- Create: `frontend/src/utils/localModels.test.ts`

Run all frontend commands from `frontend/`. If `node_modules` is absent in this
worktree, run `npm install` first and then `git checkout package-lock.json` before
committing, because the install rewrites the lockfile.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/utils/localModels.test.ts`:

```typescript
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fetchLocalModels, LOCAL_PROVIDERS, localProviderDefaultUrl } from './localModels';

describe('localProviderDefaultUrl', () => {
    it('knows both default ports', () => {
        expect(localProviderDefaultUrl('ollama')).toBe('http://localhost:11434/v1');
        expect(localProviderDefaultUrl('lmstudio')).toBe('http://localhost:1234/v1');
    });

    it('returns an empty string for a remote provider', () => {
        expect(localProviderDefaultUrl('openai')).toBe('');
    });
});

describe('LOCAL_PROVIDERS', () => {
    it('matches the backend slugs', () => {
        expect(LOCAL_PROVIDERS).toEqual(['ollama', 'lmstudio']);
    });
});

describe('fetchLocalModels', () => {
    beforeEach(() => {
        vi.restoreAllMocks();
    });

    it('returns the model list on success', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
            ok: true,
            json: async () => ({ models: ['llama3.1:8b'], error: null }),
        }));

        expect(await fetchLocalModels('ollama', '')).toEqual({
            models: ['llama3.1:8b'],
            error: null,
        });
    });

    it('passes the base URL through as a query parameter', async () => {
        const spy = vi.fn().mockResolvedValue({
            ok: true,
            json: async () => ({ models: [], error: null }),
        });
        vi.stubGlobal('fetch', spy);

        await fetchLocalModels('ollama', 'http://box:11434/v1');

        expect(spy.mock.calls[0][0]).toContain('base_url=http%3A%2F%2Fbox%3A11434%2Fv1');
    });

    it('surfaces the backend error string rather than throwing', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
            ok: true,
            json: async () => ({ models: [], error: 'Could not reach Ollama' }),
        }));

        expect(await fetchLocalModels('ollama', '')).toEqual({
            models: [],
            error: 'Could not reach Ollama',
        });
    });

    it('reports a network failure without throwing', async () => {
        vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));

        const result = await fetchLocalModels('ollama', '');
        expect(result.models).toEqual([]);
        expect(result.error).toContain('backend');
    });

    it('reports a non-ok response without throwing', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
            ok: false,
            status: 403,
            json: async () => ({}),
        }));

        const result = await fetchLocalModels('ollama', '');
        expect(result.models).toEqual([]);
        expect(result.error).toContain('403');
    });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
npm run test:unit -- src/utils/localModels.test.ts
```

Expected: FAIL, cannot resolve `./localModels`.

- [ ] **Step 3: Write the implementation**

Create `frontend/src/utils/localModels.ts`:

```typescript
/**
 * Model discovery for local AI providers (Ollama, LM Studio).
 *
 * Both servers expose the OpenAI-compatible GET /v1/models, which the backend
 * proxies at /api/ai/local-models. That endpoint never returns 5xx: an
 * unreachable server is the normal state while a user is still setting up, so
 * failure arrives as a populated `error` alongside an empty list and the caller
 * degrades to a free-text model field rather than showing a banner.
 */

export const LOCAL_PROVIDERS = ['ollama', 'lmstudio'] as const;

export type LocalProvider = (typeof LOCAL_PROVIDERS)[number];

/** Mirrors LOCAL_DEFAULT_BASE_URLS in backend/app/core/ai_client.py. The backend
 *  is authoritative; these are placeholders and pre-fills only. */
const DEFAULT_URLS: Record<string, string> = {
    ollama: 'http://localhost:11434/v1',
    lmstudio: 'http://localhost:1234/v1',
};

export function isLocalProvider(provider: string): boolean {
    return (LOCAL_PROVIDERS as readonly string[]).includes(provider);
}

export function localProviderDefaultUrl(provider: string): string {
    return DEFAULT_URLS[provider] ?? '';
}

export interface LocalModelsResult {
    models: string[];
    error: string | null;
}

export async function fetchLocalModels(
    provider: string,
    baseUrl: string,
): Promise<LocalModelsResult> {
    const params = new URLSearchParams({ provider });
    const trimmed = baseUrl.trim();
    if (trimmed) params.set('base_url', trimmed);

    let response: Response;
    try {
        response = await fetch(`/api/ai/local-models?${params.toString()}`);
    } catch (err) {
        console.error('Local model list request failed (network):', err);
        return {
            models: [],
            error: "Couldn't reach the backend to list models.",
        };
    }

    if (!response.ok) {
        console.error(`Local model list returned HTTP ${response.status}`);
        return {
            models: [],
            error: `Couldn't list models (HTTP ${response.status}).`,
        };
    }

    try {
        const body = await response.json();
        return {
            models: Array.isArray(body.models) ? body.models : [],
            error: body.error ?? null,
        };
    } catch (err) {
        console.error('Local model list returned an unparseable response:', err);
        return { models: [], error: "Couldn't read the model list." };
    }
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
npm run test:unit -- src/utils/localModels.test.ts
```

Expected: PASS, 8 passed.

- [ ] **Step 5: Lint and commit**

```bash
npm run lint
git add frontend/src/utils/localModels.ts frontend/src/utils/localModels.test.ts
git commit -m "feat(ui): add local AI model discovery helper (#606)"
```

---

## Task 11: ConfigWizard integration

**Files:**
- Modify: `frontend/src/components/ConfigWizard.tsx`
- Modify: `frontend/src/utils/aiValidation.ts`

- [ ] **Step 1: Extend the validation helper to carry the base URL**

In `frontend/src/utils/aiValidation.ts`, change the signature and payload:

```typescript
export async function requestAiValidation(
  provider: string,
  apiKey: string,
  model?: string,
  baseUrl?: string,
): Promise<AiValidationResult> {
```

and, after the existing `if (trimmedModel) payload.model = trimmedModel;`:

```typescript
  const trimmedBaseUrl = (baseUrl ?? '').trim();
  if (trimmedBaseUrl) payload.base_url = trimmedBaseUrl;
```

- [ ] **Step 2: Add the new slugs to the label maps**

In `frontend/src/components/ConfigWizard.tsx`, extend the three maps near line 74:

```typescript
const AI_PROVIDER_LABELS: Record<string, string> = {
    anthropic: 'Anthropic',
    openai: 'OpenAI',
    openrouter: 'OpenRouter',
    gemini: 'Google Gemini',
    ollama: 'Ollama',
    lmstudio: 'LM Studio',
};

const AI_KEY_PLACEHOLDERS: Record<string, string> = {
    anthropic: 'sk-ant-...',
    openai: 'sk-...',
    openrouter: 'sk-or-...',
    gemini: 'AIzaSy...',
};

// Shown as the model-field placeholder so the user can see what they are
// overriding. Mirrors DEFAULT_MODELS in backend/app/core/ai_client.py — the
// backend is authoritative, this is a label only. The local providers are
// deliberately absent: they have no default, and the model field is a required
// dropdown for them rather than an optional override.
const AI_DEFAULT_MODELS: Record<string, string> = {
    anthropic: 'claude-haiku-4-5-20251001',
    openai: 'gpt-4o-mini',
    openrouter: 'anthropic/claude-haiku-4-5-20251001',
    gemini: 'gemini-2.5-flash-lite',
};
```

Add the import near the other util imports at the top of the file:

```typescript
import { fetchLocalModels, isLocalProvider, localProviderDefaultUrl } from '../utils/localModels';
```

- [ ] **Step 3: Add the config field to state, load and save**

Add to the `ConfigData` interface next to `aiModel: string;`:

```typescript
    aiLocalBaseUrl: string;
```

Add to the initial-state object next to the other AI defaults:

```typescript
        aiLocalBaseUrl: '',
```

Add to the load mapping next to `aiModel: data.ai_model || '',`:

```typescript
                    aiLocalBaseUrl: data.ai_local_base_url || '',
```

Add to the save payload next to `ai_model: config.aiModel.trim(),`:

```typescript
                    ai_local_base_url: config.aiLocalBaseUrl.trim(),
```

- [ ] **Step 4: Add model-list state and a fetch effect**

Add alongside the other `useState` declarations in the component:

```typescript
    const [localModels, setLocalModels] = useState<{models: string[]; error: string | null}>({
        models: [],
        error: null,
    });
```

Add this effect after the existing effects:

```typescript
    // Populate the model dropdown whenever a local provider or its endpoint
    // changes. The fetch doubles as a reachability check, which is why the local
    // path does not need a separate Test button to tell the user their server is
    // down. A failure leaves `models` empty, and the field below falls back to
    // free text so a user with an unreachable server can still type an id.
    useEffect(() => {
        if (!config.aiIdentificationEnabled || !isLocalProvider(config.aiProvider)) {
            setLocalModels({models: [], error: null});
            return;
        }
        let cancelled = false;
        void (async () => {
            const result = await fetchLocalModels(config.aiProvider, config.aiLocalBaseUrl);
            if (!cancelled) setLocalModels(result);
        })();
        return () => {
            cancelled = true;
        };
    }, [config.aiIdentificationEnabled, config.aiProvider, config.aiLocalBaseUrl]);
```

- [ ] **Step 5: Reset the base URL when the provider changes**

The base URL field is shared by both local slugs, so a user who customised an Ollama
port must not silently inherit it for LM Studio. Replace the `onValueChange` handler of
the `aiProvider` select with:

```typescript
                                        onValueChange={(v) => {
                                            handleInputChange('aiProvider', v);
                                            // The base URL is shared by both local
                                            // slugs, so carrying a customised Ollama
                                            // port over to LM Studio would silently
                                            // point at the wrong server. Reset to the
                                            // new slug's default (blank for remote).
                                            handleInputChange('aiLocalBaseUrl', '');
                                            handleInputChange('aiModel', '');
                                            setAiValidation({status: 'idle'});
                                        }}
```

- [ ] **Step 6: Add the two new options to the select**

```typescript
                                        options={[
                                            { value: 'anthropic', label: 'Anthropic (Claude)' },
                                            { value: 'openai', label: 'OpenAI' },
                                            { value: 'openrouter', label: 'OpenRouter' },
                                            { value: 'gemini', label: 'Google Gemini' },
                                            { value: 'ollama', label: 'Ollama (local)' },
                                            { value: 'lmstudio', label: 'LM Studio (local)' },
                                        ]}
```

- [ ] **Step 7: Hide the key field and add the base URL field**

Wrap the existing API-key `<div className="form-group">` (the one containing
`aiApiKey`) in a conditional so it renders only for remote providers:

```typescript
                                {!isLocalProvider(config.aiProvider) && (
                                <div className="form-group">
                                    ... existing API key form-group contents, unchanged ...
                                </div>
                                )}
```

Immediately after it, add the base URL field:

```typescript
                                {isLocalProvider(config.aiProvider) && (
                                <div className="form-group">
                                    <label htmlFor="aiLocalBaseUrl">Server Address</label>
                                    <input
                                        id="aiLocalBaseUrl"
                                        type="text"
                                        placeholder={localProviderDefaultUrl(config.aiProvider)}
                                        value={config.aiLocalBaseUrl}
                                        onChange={(e) => {
                                            handleInputChange('aiLocalBaseUrl', e.target.value);
                                            setAiValidation({status: 'idle'});
                                        }}
                                    />
                                    <span className="form-hint">
                                        Leave blank to use {providerLabel}'s default address
                                        ({localProviderDefaultUrl(config.aiProvider)}). No API
                                        key is needed for a local server.
                                    </span>
                                    {localModels.error && (
                                        <span style={{color: '#f59e0b', fontSize: '0.85rem'}}>
                                            ⚠ {localModels.error}
                                        </span>
                                    )}
                                </div>
                                )}
```

- [ ] **Step 8: Turn the model field into a dropdown for local providers**

Replace the existing model `form-group` with:

```typescript
                                <div className="form-group">
                                    <label htmlFor="aiModel">
                                        {isLocalProvider(config.aiProvider) ? 'Model' : 'Model (optional)'}
                                    </label>
                                    {isLocalProvider(config.aiProvider) && localModels.models.length > 0 ? (
                                        <EngramSelect
                                            id="aiModel"
                                            value={config.aiModel}
                                            onValueChange={(v) => {
                                                handleInputChange('aiModel', v);
                                                setAiValidation({status: 'idle'});
                                            }}
                                            options={localModels.models.map((m) => ({value: m, label: m}))}
                                        />
                                    ) : (
                                        <input
                                            id="aiModel"
                                            type="text"
                                            placeholder={
                                                isLocalProvider(config.aiProvider)
                                                    ? 'e.g. llama3.1:8b'
                                                    : (AI_DEFAULT_MODELS[config.aiProvider] || '')
                                            }
                                            value={config.aiModel}
                                            onChange={(e) => {
                                                handleInputChange('aiModel', e.target.value);
                                                setAiValidation({status: 'idle'});
                                            }}
                                        />
                                    )}
                                    <span className="form-hint">
                                        {isLocalProvider(config.aiProvider)
                                            ? "Required. Local providers have no default, because it depends on which models you have installed."
                                            : "Leave blank to use Engram's default. Set this if your key cannot reach the default model."}
                                    </span>
                                </div>
```

- [ ] **Step 9: Add the concurrency hint**

Immediately after the model `form-group`, inside the same
`config.aiIdentificationEnabled && (...)` block:

```typescript
                                {/* Advisory only, never an override: a local server is
                                    one GPU or CPU, so parallel requests queue rather
                                    than overlap, and several large contexts at once can
                                    exhaust VRAM. Silently clamping a setting the user
                                    configured would be worse than telling them. */}
                                {isLocalProvider(config.aiProvider) && config.maxConcurrentMatches > 1 && (
                                    <div className="form-group">
                                        <span className="form-hint" style={{color: '#f59e0b'}}>
                                            ⚠ Max Concurrent Matches is set to {config.maxConcurrentMatches}.
                                            A local server processes one request at a time, so parallel
                                            matches queue rather than run faster, and several at once can
                                            exhaust VRAM. Consider setting it to 1 in Matching settings.
                                        </span>
                                    </div>
                                )}
```

- [ ] **Step 10: Pass the base URL to the Test button**

In `handleTestAi`, skip the key-prefix hint for local providers, drop the
"enter a key first" gate, and forward the base URL:

```typescript
    const handleTestAi = async () => {
        const local = isLocalProvider(config.aiProvider);
        const key = config.aiApiKey.trim();
        if (!local && !key && !savedKeys.ai) {
            setAiValidation({status: 'invalid', error: 'Please enter a key first'});
            return;
        }
        if (local && !config.aiModel.trim()) {
            setAiValidation({status: 'invalid', error: 'Select a model first'});
            return;
        }
        // Prefix hint only, never a gate: provider key formats change, and a
        // wrong-provider key is the common mistake after switching providers.
        // Local providers have no key at all, so the hint does not apply.
        const expectedPrefix = local ? '' : AI_KEY_PLACEHOLDERS[config.aiProvider]?.replace('...', '');
        if (key && expectedPrefix && !key.startsWith(expectedPrefix)) {
            setAiValidation({
                status: 'invalid',
                error: `That does not look like a ${AI_PROVIDER_LABELS[config.aiProvider] || config.aiProvider} key (expected it to start with "${expectedPrefix}")`,
            });
            return;
        }
        setAiValidation({status: 'testing'});
        const result = await requestAiValidation(
            config.aiProvider,
            config.aiApiKey,
            config.aiModel,
            config.aiLocalBaseUrl,
        );
        if (result.status === 'valid') {
            setAiValidation({status: 'valid', model: result.model});
        } else {
            setAiValidation({status: result.status, error: result.error});
        }
    };
```

- [ ] **Step 11: Move the Test button out of the remote-only block**

Step 7 wrapped the API-key `form-group` in `{!isLocalProvider(...) && (...)}`, and the
Test button currently lives inside it, so local providers would lose the button
entirely. Cut the whole `<div style={{display: 'flex', alignItems: 'center', gap:
'0.5rem', marginTop: '0.5rem'}}>...</div>` block (the button plus its three status
spans) out of the API-key group and paste it into its own `form-group` placed directly
after the model field, with the `disabled` clause relaxed for local providers:

```typescript
                                <div className="form-group">
                                    <div style={{display: 'flex', alignItems: 'center', gap: '0.5rem'}}>
                                        <button
                                            type="button"
                                            onClick={handleTestAi}
                                            disabled={aiValidation.status === 'testing' || (!isLocalProvider(config.aiProvider) && !config.aiApiKey && !savedKeys.ai)}
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
                                </div>
```

Verify by eye that the API-key `form-group` now ends with its `<span className="form-hint">`
and no longer contains a button.

- [ ] **Step 12: Typecheck, lint and test**

```bash
npm run build && npm run lint && npm run test:unit
```

Expected: all pass. `npm run build` runs the TypeScript check, which catches a missed
`aiLocalBaseUrl` in the `ConfigData` interface.

- [ ] **Step 13: Commit**

```bash
git add frontend/src/components/ConfigWizard.tsx frontend/src/utils/aiValidation.ts
git commit -m "feat(ui): local AI provider settings with model picker and concurrency hint (#606)"
```

---

## Task 12: Manual verification against your LM Studio install

The only step in this plan that exercises real inference. Do not skip it: every test
above mocks transport, so none of them prove the two servers actually behave as
documented.

- [ ] **Step 1: Start LM Studio's server**

In LM Studio, open the Developer tab, load a small instruct model (a 7B or 8B is
plenty), and start the server. Confirm from the shell:

```bash
curl -s http://localhost:1234/v1/models
```

Expected: a JSON body with a `data` array containing at least one `id`.

- [ ] **Step 2: Start Engram's backend on this worktree's own port**

From `backend/`:

```bash
DEBUG=true uv run uvicorn app.main:app --port 8100
```

Never use `--reload`: it spawns a second drive sentinel and creates duplicate disc events.

- [ ] **Step 3: Start the frontend pointed at that backend**

From `frontend/`:

```bash
VITE_PORT=5273 VITE_BACKEND_PORT=8100 npm run dev
```

- [ ] **Step 4: Verify the model list**

```bash
curl -s "http://localhost:8100/api/ai/local-models?provider=lmstudio"
```

Expected: `{"models":["<your model id>"],"error":null}`.

- [ ] **Step 5: Verify the wizard end to end**

Open http://localhost:5273, open Settings, enable AI identification, choose
**LM Studio (local)**. Confirm each of:

- The API key field is gone.
- The Server Address field shows `http://localhost:1234/v1` as its placeholder.
- The Model field is a dropdown listing your loaded model.
- Test Connection reports success, naming the model.
- Stop the LM Studio server and reload: the model field falls back to free text and an
  amber warning names LM Studio.

- [ ] **Step 6: Verify a real identification**

Restart the LM Studio server, save the config, and run:

```bash
curl -X POST localhost:8100/api/simulate/insert-disc \
  -H "Content-Type: application/json" \
  -d '{"volume_label":"THE_MATRIX_1999","content_type":"movie","simulate_ripping":true}'
```

Then confirm the log shows the AI path being taken rather than skipped:

```bash
grep -i "AI identif" ~/.engram/engram.log | tail -5
```

Expected: a line showing AI identification ran. A silent absence means a gate from
Task 7 was missed.

- [ ] **Step 7: Stop this session's servers**

Required before opening the PR. Scoped by port so a sibling session survives:

```powershell
Get-NetTCPConnection -LocalPort 8100,5273 -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ -Force }
```

- [ ] **Step 8: Note the Ollama gap in the PR**

You have LM Studio, not Ollama. Both go through the identical code path and adapter, so
the risk is low, but say plainly in the PR description that the Ollama path is covered
by unit tests and not by a live run, so a reviewer with Ollama can confirm it.

---

## Task 13: Documentation and changelog

**Files:**
- Modify: `CHANGELOG.md`
- Modify: the AI configuration page under `docs/`

- [ ] **Step 1: Find the AI docs page**

```bash
grep -rln "ai_provider\|AI Provider\|OpenRouter" docs/ --include=*.md
```

- [ ] **Step 2: Document local setup**

Append this section to the page found in Step 1, matching that page's existing heading
level:

```markdown
## Local AI (Ollama and LM Studio)

Engram can use a model running on your own machine instead of a paid hosted API.
Two servers are supported, and both work the same way because they expose the
same OpenAI-compatible interface.

**No API key is required.** Leave the key field alone; it is hidden when a local
provider is selected.

### Setup

1. Start your server:
   - **Ollama:** run `ollama serve`, then pull a model with `ollama pull llama3.1:8b`.
   - **LM Studio:** open the Developer tab, load a model, and start the server.
2. In Engram, open **Settings > AI**, enable AI identification, and choose
   **Ollama (local)** or **LM Studio (local)**.
3. Leave **Server Address** blank to use the default:

   | Provider | Default address |
   |---|---|
   | Ollama | `http://localhost:11434/v1` |
   | LM Studio | `http://localhost:1234/v1` |

   Set it only if your server runs on another port or another machine on your LAN.
4. Pick a model from the **Model** dropdown. The list is read from your server, so
   it only populates once the server is running. Unlike the hosted providers there
   is no default model, because the answer depends on which models you installed.
5. Click **Test Connection** to confirm Engram can reach it.

### Choosing a model

Any instruction-tuned model in the 7B to 14B range works well for disc
identification and episode matching. Both tasks ask for a small JSON reply, so
reasoning-heavy or very large models buy little and cost a lot of time.

### Performance

Set **Max Concurrent Matches** to 1 in Matching settings. A local server has one
GPU or CPU, so parallel requests queue rather than overlap, and several large
contexts at once can exhaust VRAM.

The first request after loading a model is slow: LM Studio loads the weights on
demand, which can take 30 to 60 seconds before any text appears. Engram allows up
to five minutes for a local response, so this is expected rather than a failure.

### Troubleshooting

| Message | Cause |
|---|---|
| Could not reach Ollama at ... | The server is not running, or the address is wrong. |
| Model '...' is not available | The model is not pulled (Ollama) or not loaded (LM Studio). |
| Select a model | Local providers have no default; pick one from the dropdown. |
```

- [ ] **Step 3: Add the changelog entry**

Under `## [Unreleased]` in `CHANGELOG.md`, in an `### Added` subsection:

```markdown
- Local AI providers: Engram can now use a local Ollama or LM Studio server for disc
  identification and episode matching instead of a paid hosted API. No API key is
  required; pick the model from a dropdown of what the server has installed. (#606)
```

- [ ] **Step 4: Verify the docs build**

From the repository root:

```bash
uv run --with mkdocs-material --with "mkdocstrings[python]" mkdocs build
```

Expected: builds. Roughly 18 pre-existing warnings are normal; do not add `--strict`.

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md docs/
git commit -m "docs: document local AI provider setup (#606)"
```

---

## Task 14: Final verification and PR

- [ ] **Step 1: Run the full backend unit tier**

From `backend/`:

```bash
uv run pytest tests/unit/ -q
```

Expected: PASS. This tier can exceed five minutes; do not run it inside a subagent with
a shorter timeout.

- [ ] **Step 2: Run the pipeline tier**

```bash
uv run pytest tests/pipeline/ -q
```

Expected: PASS. If tests fail with "no such table", this worktree's `engram.db` is an
empty stub; run `uv run python -c "import asyncio; from app.database import init_db; asyncio.run(init_db())"` first.

- [ ] **Step 3: Run the frontend checks**

From `frontend/`:

```bash
npm run build && npm run lint && npm run test:unit
```

Expected: all pass.

- [ ] **Step 4: Run the security review**

The spec commits to this, because the change deliberately weakens an SSRF guard.

```
/security-review
```

Address anything it raises about `is_safe_local_ai_url` or the two endpoints that
accept a `base_url`.

- [ ] **Step 5: Confirm no lockfile churn**

```bash
git status --short
```

If `frontend/package-lock.json` is modified only because of a local `npm install`,
restore it:

```bash
git checkout frontend/package-lock.json
```

- [ ] **Step 6: Open the PR**

```bash
git push -u origin claude/local-ai-support-plan-561005
gh pr create --title "feat(ai): support local AI providers (Ollama, LM Studio)" --body "$(cat <<'BODY'
Closes #606.

Adds `ollama` and `lmstudio` as AI providers. Both expose the same
OpenAI-compatible `/v1/chat/completions` contract that `_call_openai_compatible`
already speaks, so no new adapter was needed: the work was relaxing invariants
that assumed a remote paid API.

## What changed

- Two new provider slugs sharing the existing OpenAI-compatible adapter, with a
  configurable base URL (`ai_local_base_url`, blank means the conventional port).
- `ai_is_configured()` replaces four separate `if not config.ai_api_key` gates.
  Local servers need no key, so every one of those gates would otherwise have
  silently disabled the feature while it appeared configured.
- `is_safe_local_ai_url()`: a deliberately loopback-permitting SSRF guard, kept
  separate from `is_safe_remote_url` so no other call site is weakened.
- Longer timeouts for local slugs (300s matching, 120s validation), because a
  cold LM Studio JIT-loads weights before emitting a token.
- Local-specific error text naming the endpoint and the model, replacing remote
  wording like "the account has no API credits".
- `GET /api/ai/local-models` powering a model dropdown in the wizard.
- Preamble-tolerant JSON extraction, since small local models wrap replies in prose.

## Testing

Unit tests cover both slugs, URL resolution, the timeout selection, the SSRF
guard, the four-gate invariant, and the model-list endpoint's failure paths.
Verified end to end against a live LM Studio server. The Ollama path is covered
by unit tests but was not exercised against a live server; it shares the adapter
and dispatch with LM Studio, so the risk is low, but a reviewer with Ollama
installed confirming it would be welcome.

Design: `docs/superpowers/specs/2026-08-29-local-ai-provider-design.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
BODY
)"
```

- [ ] **Step 7: Trigger the review bot**

The `code-review` workflow only fires on the `opened` event, so post the trigger
comment explicitly:

```bash
gh pr comment --body "@claude please review this PR"
```

---

## Notes for the reviewer

- **The riskiest change is Task 7.** Three gates in three modules had to change
  together. `tests/unit/test_ai_configured_gates.py` is a source-level guard against a
  fourth one being added later that reintroduces the bug.
- **`is_safe_local_ai_url` is an intentional SSRF exemption**, reasoned about in the
  spec rather than left implicit. It is deliberately not an alias for
  `is_safe_remote_url`, and a test pins that difference.
- **`DEFAULT_MODELS` no longer doubles as the provider allowlist.** `KNOWN_PROVIDERS`
  took that job. Anywhere that still membership-tests `DEFAULT_MODELS` to mean "is this
  a real provider?" is a bug.
