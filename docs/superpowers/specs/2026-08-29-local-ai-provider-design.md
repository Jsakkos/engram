# Local AI Provider Support (Ollama + LM Studio)

Date: 2026-08-29
Issue: [#606](https://github.com/Jsakkos/engram/issues/606)
Status: Approved, pending implementation plan

## Problem

Engram's AI features (label identification via `ai_identifier.py`, episode matching via
`llm_episode_matcher.py`) require a hosted provider and an API key. A user asked for a
local option so that disc identification and episode matching can run without sending
transcripts to a third party and without per-token cost.

## Key finding: we do not have to choose

Ollama and LM Studio both expose the **same** OpenAI-compatible surface:

| | Base URL | Chat endpoint | Model list |
|---|---|---|---|
| Ollama | `http://localhost:11434/v1` | `POST /chat/completions` | `GET /models` |
| LM Studio | `http://localhost:1234/v1` | `POST /chat/completions` | `GET /models` |

Both accept `messages`, `max_tokens`, `temperature`, and `response_format`, and both
return the `choices[0].message.content` / `finish_reason` shape. Ollama requires an
`Authorization` header but ignores its value; LM Studio requires nothing.

Engram already has an adapter for exactly this contract:
`_call_openai_compatible()` in `backend/app/core/ai_client.py`, currently shared by the
`openai` and `openrouter` slugs with only the URL differing. Supporting both local
servers is therefore primarily **"make the base URL configurable"**, not "write a new
adapter".

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Provider shape | Two slugs: `ollama` and `lmstudio` | Pre-filled default ports mean zero-config for the common case, and error messages can name the product ("is `ollama serve` running?") rather than surfacing a bare connection error. |
| Model selection | Fetch `GET /v1/models`, render a dropdown | Local model ids are unguessable and installation-specific. The fetch doubles as a reachability check. |
| Timeouts | Separate longer constants for local slugs | No new config field, no user decision, and cannot slow the remote paths. |
| Concurrency | Left at the user's `max_concurrent_matches`, plus a UI hint | An automatic override would silently ignore a configured setting. A hint informs without overriding. |

## Design

### 1. Provider vocabulary (`ai_client.py`)

Three additions, so that no other module has to hardcode the notion of "local":

```python
LOCAL_PROVIDERS = frozenset({"ollama", "lmstudio"})

LOCAL_DEFAULT_BASE_URLS = {
    "ollama": "http://localhost:11434/v1",
    "lmstudio": "http://localhost:1234/v1",
}

def resolve_local_base_url(provider: str, configured: str) -> str:
    """Configured value if set, else the slug's conventional default."""
```

`complete_json` gains two dispatch branches that both call the existing
`_call_openai_compatible()`, passing `f"{resolve_local_base_url(...)}/chat/completions"`
as the URL and the api_key verbatim (empty string is acceptable; Ollama ignores the
value and LM Studio ignores the header).

`DEFAULT_MODELS` deliberately gains **no** entry for the local slugs. There is no
correct default, and a blank `ai_model` on a local provider is a configuration error the
user must fix rather than something to paper over with a guess.

**Consequence that must not be missed:** `DEFAULT_MODELS` is currently doing double duty
as the allowlist of known providers. `validate_ai` (`backend/app/api/validation.py`)
rejects anything not in it (`if provider not in DEFAULT_MODELS`) and later indexes it
(`version=model or DEFAULT_MODELS[provider]`), so leaving the local slugs out would make
the Test button reject them and then raise `KeyError`. The membership test must move to a
new explicit `KNOWN_PROVIDERS = set(DEFAULT_MODELS) | LOCAL_PROVIDERS`, and the `version`
line must use `model or DEFAULT_MODELS.get(provider, "")`. `complete_json`'s own
`model = model or DEFAULT_MODELS.get(provider)` path needs the same care: for a local slug
that expression yields `None`, which currently falls into the "Unknown AI provider" branch
and returns `None` silently. It must instead raise or return the blank-model message from
section 6.

### 2. The "is AI configured?" invariant

Four call sites currently use "is there an API key?" as a proxy for "is AI usable?":

- `backend/app/core/ai_client.py` (early return at the top of `complete_json`)
- `backend/app/core/curator.py:608`
- `backend/app/services/matching_coordinator.py:727`
- `backend/app/services/identification_coordinator.py:1819`

Local providers have no key, so all four would silently return `None` and the feature
would appear to do nothing. Rather than adding `or is_local(...)` in four places, the
concept gets named once:

```python
def ai_is_configured(provider: str, api_key: str) -> bool:
    """True if this provider has what it needs to make a call.

    Local providers authenticate to nothing, so requiring a key would silently
    disable them at every call site that guards on one.
    """
    return provider in LOCAL_PROVIDERS or bool(api_key)
```

All four sites call it. This is the single change most likely to be missed and the one
that makes the feature work at all.

### 3. Configuration

One new field, `ai_local_base_url: str = ""`, where blank means "use the slug's
default". Per the project's three-way-sync rule it must land in **all four** of:

- `AppConfig` (`backend/app/models/app_config.py`)
- `ConfigUpdate` (`backend/app/api/routes.py:414`)
- `ConfigResponse` (`backend/app/api/routes.py:316`)
- `ConfigWizard` (`frontend/src/components/ConfigWizard.tsx`)

Omitting any one causes Pydantic to drop the value silently.

The field is **not** sensitive: it must not join the `sensitive_fields` set in
`config_service.py:213` (that set protects secrets from being blanked by an empty
string; a base URL legitimately needs to be clearable back to its default).

**Provider switching:** the field is shared by both slugs. ConfigWizard resets it to the
newly selected slug's default whenever the provider changes, so a user who customized an
Ollama port cannot silently inherit it for LM Studio.

### 4. Security: a deliberate SSRF exemption

`is_safe_remote_url()` (`backend/app/core/security.py:165`) rejects loopback, private,
and link-local hosts by design. Those are precisely the addresses a local AI server lives
at, so `ai_local_base_url` needs its own guard, modelled on the existing
`is_safe_dashboard_url` precedent:

```python
def is_safe_local_ai_url(url: str) -> bool:
    """http(s) only, host present, no embedded credentials, no query or fragment."""
```

This is an intentional weakening and is recorded here rather than buried in a commit.
Unlike `dashboard_base_url` (never fetched, only rendered as a link), the server **does**
issue POSTs to this URL, which makes it a blind-SSRF primitive against the host's own
loopback interface. Accepted because:

- The config surface is already fully trusted: it holds API keys, filesystem paths, and
  library roots. Anyone able to write `ai_local_base_url` can already do worse.
- `PUT /api/config` and the new model-list endpoint are gated by
  `require_localhost_or_lan`, matching the existing AI validator.
- Only http(s) is accepted, embedded credentials are rejected, and the response body is
  parsed as JSON and discarded unless it matches the requested schema. The primitive is
  blind, not an exfiltration channel.

The implementation diff should still be run through `/security-review` before merge.

### 5. Timeouts

```python
LOCAL_TIMEOUT_SECONDS = 300.0           # matching / identification
LOCAL_VALIDATE_TIMEOUT_SECONDS = 120.0  # the Test button
```

Selected by slug in `complete_json` and in `validate_ai`. The wide margin is deliberate:
LM Studio JIT-loads model weights on the *first* request, so a cold 7B model can spend 30
to 60 seconds before emitting a token, and CPU-only generation runs into the minutes. The
existing 30s and 10s values would make local support look broken on first use.

### 6. Error messages

`_CAUSE_MESSAGES` is worded for remote paid APIs ("the account has no API credits"),
which is meaningless locally. Local slugs get an override table:

| Condition | Message |
|---|---|
| Connection refused / network | `Could not reach Ollama at {url}. Is the server running? (ollama serve)` LM Studio variant names its Developer tab instead. |
| HTTP 404 | `Model '{model}' is not available. Pull it first: ollama pull {model}` |
| Blank `ai_model` | `Select a model. Local providers have no default.` |

`_PROVIDER_LABELS` gains `ollama: "Ollama"` and `lmstudio: "LM Studio"`.

### 7. Model picker endpoint

```
GET /api/ai/local-models?provider=ollama&base_url=...
  -> {"models": ["llama3.1:8b", ...], "error": null}
```

Gated by `require_localhost_or_lan` for the same reason as `validate_ai`: it triggers an
outbound request the caller can provoke without owning the destination. It never returns
5xx. An unreachable server yields HTTP 200 with an empty list and a populated `error`
string, so ConfigWizard can degrade gracefully to a free-text model field.

### 8. Shared-code fix: preamble-tolerant JSON parsing

`_parse_json_text()` already strips ` ```json ` fences. Small local models additionally
emit reasoning preamble *before* the JSON object. A last-resort fallback that extracts
the first balanced `{...}` block is added. It only runs where parsing already failed, so
it cannot regress the remote providers, and without it match quality on local models is
visibly worse.

### 9. Frontend

- `AI_PROVIDER_LABELS`, `AI_KEY_PLACEHOLDERS`, `AI_DEFAULT_MODELS` gain the two slugs.
  The key field is **hidden** (not merely optional) for local providers.
- A base-URL text input appears only for local providers, placeholdered with the slug
  default.
- The model field becomes a dropdown populated from `/api/ai/local-models`, falling back
  to free text when the fetch fails or returns empty.
- **Concurrency hint.** When a local provider is selected and `max_concurrent_matches`
  exceeds 1, the AI section shows an inline note: local inference is served by a single
  GPU or CPU, so parallel requests queue rather than speed up and may exhaust VRAM;
  consider setting concurrent matches to 1. Advisory only; the setting is not overridden.

## Testing

Follows the existing `unittest.mock.patch("app.core.ai_client.httpx.AsyncClient")` style
in `backend/tests/unit/test_ai_client.py`. No new test dependency.

- URL resolution for both slugs, default and override.
- `ai_is_configured` returns True with an empty key for local slugs, False for remote.
- The local timeout constant actually reaches the `httpx` call.
- Local error-message overrides for refused-connection and HTTP 404.
- `is_safe_local_ai_url` accepts loopback and rejects `file://`, embedded credentials,
  and non-http schemes.
- Preamble-tolerant JSON parsing, including a case that must still fail.
- `_is_safe_model_name` accepts real local model ids. The existing regex allows `.`, `_`,
  `:`, `-`, `@` and slash-separated segments, so `llama3.1:8b`,
  `qwen2.5:7b-instruct-q4_K_M`, and `lmstudio-community/Model-GGUF` should all pass. This
  is asserted rather than assumed, because a rejection surfaces to the user as a
  misleading "clear the AI model field" message.
- A local slug with a blank `ai_model` reports the section 6 blank-model message rather
  than silently returning `None`.
- `GET /api/ai/local-models` unreachable-server path returns 200 with an empty list.
- A config round-trip asserting `ai_local_base_url` survives `PUT` then `GET` (the
  three-way-sync regression guard).

## Out of scope

- Automatic discovery of a running local server (port scanning).
- Bundling or installing Ollama / LM Studio.
- Local ASR provider selection; this spec covers text completion only.
- Overriding `max_concurrent_matches` automatically.
