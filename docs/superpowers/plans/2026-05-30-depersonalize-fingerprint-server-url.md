# De-personalize Fingerprint Server URL (code-prep) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the fingerprint-server default URL a single-constant edit by routing `curator.py`'s hardcoded fallback through `DEFAULT_FINGERPRINT_SERVER_URL`, and assert tests against the constant rather than the literal — without changing the URL value.

**Architecture:** `DEFAULT_FINGERPRINT_SERVER_URL` in `backend/app/models/app_config.py` is already the source of truth (the `fingerprint_server_url` field default references it). The only drift point is `curator.py:398`, which re-hardcodes the same literal as an `or` fallback. We replace that literal with the constant and rewrite the URL-default tests to be constant-relative, so a future rename touches exactly one line.

**Tech Stack:** Python 3.11, FastAPI, SQLModel, pytest, ruff (pre-commit).

**Source of truth:** `docs/superpowers/specs/2026-05-30-depersonalize-fingerprint-server-url-design.md`

---

### Task 1: Make the test suite constant-relative (test-first)

**Files:**
- Test: `backend/tests/integration/test_contribution_uploader.py:64-73` (modify existing `test_app_config_has_fingerprint_server_url`)
- Test: `backend/tests/integration/test_contribution_uploader.py` (add `test_curator_routes_fallback_through_constant`)

- [ ] **Step 1: Rewrite the value-equality assertion to reference the constant**

Replace the hardcoded-literal assertion (line 72) so renaming the URL never requires touching this test. Import the constant at module top alongside the existing imports (or inside the test — the existing `test_uploader_falls_back_to_default_url_when_unset` already imports it locally; prefer a module-level import for reuse).

```python
# at top of file, near the other app.* imports
from app.models.app_config import DEFAULT_FINGERPRINT_SERVER_URL
```

```python
def test_app_config_has_fingerprint_server_url():
    """AppConfig defaults fingerprint_server_url to the network base origin.

    Asserted constant-relative (not the literal string) so de-personalizing the
    URL is a one-line edit to DEFAULT_FINGERPRINT_SERVER_URL. The default must be
    the BASE (no /v1 suffix) — the uploader appends /v1/contribute, so a /v1 here
    would double to /v1/v1/... and 404.
    """
    cfg = AppConfig()
    assert hasattr(cfg, "fingerprint_server_url")
    assert cfg.fingerprint_server_url == DEFAULT_FINGERPRINT_SERVER_URL
    assert not cfg.fingerprint_server_url.endswith("/v1")
```

- [ ] **Step 2: Add a guard test proving curator routes through the constant**

This is the real regression anchor: it fails *before* the curator fix (the source still holds the literal) and stays green through any future rename, because `curator.py` will reference the constant by name rather than embedding a host literal.

```python
def test_curator_routes_fallback_through_constant():
    """curator.py must use DEFAULT_FINGERPRINT_SERVER_URL for its server-URL
    fallback, not a re-hardcoded literal. Guarantees the URL value lives in
    exactly one place (app_config.py), so de-personalizing is a one-line edit.
    """
    import inspect

    import app.core.curator as curator_mod

    source = inspect.getsource(curator_mod)
    assert "DEFAULT_FINGERPRINT_SERVER_URL" in source, (
        "curator.py should reference the shared constant for its server-URL fallback"
    )
    assert ".workers.dev" not in source, (
        "curator.py must not hardcode a fingerprint host literal; route through "
        "DEFAULT_FINGERPRINT_SERVER_URL instead"
    )
```

- [ ] **Step 3: Run the tests to verify the guard fails (red)**

Run: `cd backend; uv run pytest tests/integration/test_contribution_uploader.py::test_curator_routes_fallback_through_constant tests/integration/test_contribution_uploader.py::test_app_config_has_fingerprint_server_url -v`
Expected: `test_curator_routes_fallback_through_constant` FAILS on the `.workers.dev` assertion (literal still present in `curator.py`); `test_app_config_has_fingerprint_server_url` PASSES (constant == literal value today).

- [ ] **Step 4: Commit the tests**

```bash
git add backend/tests/integration/test_contribution_uploader.py
git commit -m "test(fingerprint): assert server URL against the shared constant"
```

---

### Task 2: Route curator's fallback through the constant (green)

**Files:**
- Modify: `backend/app/core/curator.py` (top-of-file imports + line ~398)

- [ ] **Step 1: Import the constant at module top**

Add next to the existing `from app.matcher.llm_episode_matcher import match_episode_via_llm` import (line 12). `app_config.py` imports only sqlalchemy/sqlmodel, so there is no circular-import risk.

```python
from app.matcher.llm_episode_matcher import match_episode_via_llm
from app.models.app_config import DEFAULT_FINGERPRINT_SERVER_URL
```

- [ ] **Step 2: Replace the hardcoded fallback literal**

```python
        server_url = cfg.fingerprint_server_url or DEFAULT_FINGERPRINT_SERVER_URL
```

(Replaces the multi-line `cfg.fingerprint_server_url or "https://engram-fp-prod.jonathansakkos.workers.dev"` at line 397-399.)

- [ ] **Step 3: Run the guard + URL-default tests to verify green**

Run: `cd backend; uv run pytest tests/integration/test_contribution_uploader.py -v`
Expected: all PASS, including `test_curator_routes_fallback_through_constant`.

- [ ] **Step 4: Run curator unit tests + lint to confirm no regression**

Run: `cd backend; uv run pytest tests/unit/test_curator.py -v; uv run ruff check app/core/curator.py tests/integration/test_contribution_uploader.py`
Expected: tests PASS, ruff reports no issues.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/curator.py
git commit -m "fix(fingerprint): route curator server-URL fallback through DEFAULT_FINGERPRINT_SERVER_URL"
```

---

### Task 3: Verify no other production code hardcodes the hostname

**Files:** none modified — verification only.

- [ ] **Step 1: Grep for the personal hostname across the repo**

Run (Grep tool, or): `cd backend; rg -n "jonathansakkos|engram-fp-prod" app`
Expected: the only remaining match under `backend/app/` is the constant definition in `app/models/app_config.py:19`. (The `frontend/src/components/ConfigWizard.tsx` input *placeholder* is display-only UI text, cannot import a Python constant, and is out of scope for this code-prep — note it in the PR but do not change it.)

- [ ] **Step 2: Full backend test sanity pass (optional but recommended)**

Run: `cd backend; uv run pytest tests/integration/test_contribution_uploader.py tests/unit/test_curator.py -q`
Expected: all PASS.

---

## Notes for the PR description

Summarize the two maintainer-driven Cloudflare ops options (from the spec) and the migration note — the URL value itself is intentionally unchanged in this PR:

- **Option 1 (recommended): custom domain.** Point a neutral domain (e.g. `fp.engram.app`) at the worker via a `routes`/custom-domain entry in `wrangler.toml` + a Cloudflare DNS record. Decouples the public URL from both the account subdomain and the worker name; keeps the old `*.workers.dev` URL resolving alongside it.
- **Option 2: rename the account's `workers.dev` subdomain.** Cloudflare dashboard → Workers → Subdomain → rename `jonathansakkos` to something neutral. Free, but account-wide and still ends in `.workers.dev`.
- **Migration:** NULL-config installs resolve to `DEFAULT_FINGERPRINT_SERVER_URL` at call time, so they pick up the new default automatically on update. Installs that explicitly saved the old URL keep sending there — keep the old URL alive until clients update (trivial with the custom-domain option). Low risk: the catalog is freshly bootstrapped with effectively one contributor.

After this PR, de-personalizing is a one-line edit to `DEFAULT_FINGERPRINT_SERVER_URL`.
