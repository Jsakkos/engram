# De-personalize the fingerprint server URL

- **Date:** 2026-05-30
- **Status:** Approved design (pre-implementation)
- **Repos touched:** `engram` (client default URL — code only); `engram-fingerprint-server` / Cloudflare (ops, user-driven)
- **Related:** Phase 2 fingerprint network ([2026-05-27-phase2-fingerprint-server-design.md](2026-05-27-phase2-fingerprint-server-design.md)).

## Problem

The default fingerprint server URL is `https://engram-fp-prod.jonathansakkos.workers.dev`. It embeds the maintainer's name. The URL has two parts:

- `engram-fp-prod` — the **worker name** (`wrangler.toml` `name`).
- `jonathansakkos` — the **account's `workers.dev` subdomain**, which is account-wide. **This is the personal part.**

Removing the name is therefore primarily a **Cloudflare ops decision**, not a code change. The code change is only to make swapping the URL a one-liner — and to fix a latent duplicate.

## Goals

- The default server URL carries no personal identifier.
- Changing the URL is a **single-constant** edit with green tests.
- Existing installs keep working across the change.

## Non-goals

- Choosing/registering a specific domain (the maintainer decides, with Cloudflare access).
- Any change to the contribution protocol or auth.

## Design

### Code-prep now (`engram`, URL-agnostic)

1. **Single source of truth.** `DEFAULT_FINGERPRINT_SERVER_URL` in `backend/app/models/app_config.py` stays the only place the literal lives.
2. **Fix the duplicate.** `backend/app/core/curator.py:398` hardcodes `"https://engram-fp-prod.jonathansakkos.workers.dev"` again as a fallback, bypassing the constant — a latent bug (it won't track a future URL change). Replace with `DEFAULT_FINGERPRINT_SERVER_URL`.
3. **Test asserts the constant, not the literal.** `tests/integration/test_contribution_uploader.py:72` asserts the exact string. Change it to assert against `DEFAULT_FINGERPRINT_SERVER_URL` (and keep the structural checks, e.g. "does not end with `/v1`"), so renaming the URL doesn't require touching tests.

After this prep, de-personalizing is a one-line change to the constant.

### Ops options (maintainer-driven; pick one)

1. **Custom domain (recommended).** Point a neutral domain you control (e.g. `fp.engram.app`) at the worker via a `routes`/custom-domain entry in `wrangler.toml` + a Cloudflare DNS record. Stable, professional, and decouples the public URL from both the account subdomain and the worker name forever.
2. **Rename the account's `workers.dev` subdomain.** Cloudflare dashboard → Workers → Subdomain → change `jonathansakkos` to something neutral (e.g. `engram`), yielding `engram-fp-prod.<neutral>.workers.dev`. Free, no domain needed, but it is **account-wide** (renames the subdomain for every worker in the account) and still ends in `.workers.dev`.

### Migration / backward compatibility

- Installs with a **NULL** stored `fingerprint_server_url` resolve to `DEFAULT_FINGERPRINT_SERVER_URL` at call time, so they pick up the new default automatically on update.
- Installs that **explicitly saved** the old URL keep sending there; keep the **old URL reachable** until those clients have updated. Custom domain (option 1) makes this trivial — the old `*.workers.dev` URL keeps resolving alongside the new domain. Low risk regardless: the catalog is freshly bootstrapped with effectively one contributor.
- The existing SSRF allow-check on `fingerprint_server_url` (`routes.py:1210`) is unaffected — a new public https host still passes.

## Testing

- `test_contribution_uploader.py` asserts the default equals `DEFAULT_FINGERPRINT_SERVER_URL` (constant-relative) and still does not end in `/v1`.
- `curator.py` fallback resolves to the same constant (add/adjust a small assertion if one doesn't already cover it).
