# Engram Stack Review

## Overall Verdict

Well-chosen. The stack fits the problem domain. Below is a breakdown by layer with the areas that deserve attention.

---

## Backend — Strong Fits

**FastAPI + async SQLite** is exactly right for this workload. Disc ripping is IO-bound subprocess management — async everywhere means MakeMKV calls, WebSocket broadcasts, and subtitle downloads don't contend. SQLite is the correct database for a single-user local tool; there's no reason to add Postgres overhead.

**uv + Ruff** is the modern Python toolchain. No complaints.

**loguru** over stdlib `logging` is a quality-of-life win — structured output and sinks are much easier to configure.

**rapidfuzz** over `difflib` for fuzzy matching is the right call — it's orders of magnitude faster on large title lists.

---

## Backend — Issues Worth Fixing

### 1. Blocking HTTP calls in async paths — the most significant problem

`requests` (sync) is used in `tmdb_classifier.py`, `discdb_classifier.py`, `matcher/tmdb_client.py`, `addic7ed_client.py`, and `opensubtitles_scraper.py`. Calling a synchronous HTTP library from an async handler blocks the entire event loop for the duration of the network round-trip. This serializes all jobs during any external API call.

**Fix**: consolidate on `httpx` (already used in `routes.py`, `discdb_submitter.py`, `upc_lookup.py`, `ai_identifier.py`) with `async with httpx.AsyncClient()` throughout. `httpx` has a nearly identical API to `requests` so migration is low-effort.

Neither `requests` nor `httpx` appears in `pyproject.toml` — both are being pulled in as transitive deps from `faster-whisper` / `huggingface_hub`. That's fragile. Add `httpx>=0.27` as an explicit dep and remove all `requests` usage.

Files to migrate:
- `app/core/tmdb_classifier.py`
- `app/core/discdb_classifier.py`
- `app/core/upc_lookup.py` (already httpx, just explicit dep needed)
- `app/matcher/tmdb_client.py`
- `app/matcher/addic7ed_client.py`
- `app/matcher/opensubtitles_scraper.py`
- `app/api/validation.py`
- `app/api/routes.py` (lines 639, 837 — two inline `import requests` calls)

### 2. Heavy ML dependency weight

`faster-whisper` + `ctranslate2` + `librosa` + `scikit-learn` pull in hundreds of MB. This is fine for a developer install, but worth keeping in mind for distribution. Consider making these optional via a `[matching]` extras group so users who skip audio fingerprinting can install light.

### 3. SQLModel maturity

SQLModel is still pre-1.0 and has historically lagged behind SQLAlchemy releases. It works today, but if edge cases appear in async session behavior or complex query composition, raw SQLAlchemy 2.0 + Pydantic for schema would be more battle-tested. Not a blocker — just keep it on the radar.

---

## Frontend — Strong Fits

**React 18 + TypeScript + Vite** is the right baseline. Vite's dev-server speed and HMR are noticeable in a feedback-heavy app.

**shadcn/ui** (Radix primitives + Tailwind) is an excellent choice for a local tool. You own the component source, there's no version lock-in, and the accessibility primitives are solid.

**Playwright for E2E** is well-matched — simulation endpoints make it possible to test the full disc-insertion-to-completion flow without hardware, which is rare and valuable.

**Tailwind v4** with `@tailwindcss/vite` directly (no PostCSS config) is the correct adoption path. The cyberpunk `@theme inline` approach sidesteps the config.js-to-CSS migration cleanly.

---

## Frontend — Issues Worth Fixing

### 1. Dependency bloat from shadcn scaffolding

`package.json` has 20+ `@radix-ui/*` packages. shadcn's `init` installs all Radix primitives upfront, but only the ones backing components actually in use are needed. The following look like scaffold artifacts:

- `input-otp`
- `react-day-picker`
- `embla-carousel-react`
- `react-slick`
- `react-responsive-masonry`
- `@popperjs/core` + `react-popper` (superseded by Floating UI in modern setups)
- Potentially: `@radix-ui/react-context-menu`, `@radix-ui/react-hover-card`, `@radix-ui/react-menubar`, `@radix-ui/react-navigation-menu`

Run `npx depcheck` to identify unused packages. Vite tree-shakes so these don't inflate the bundle, but they increase `node_modules` size, `npm audit` surface, and update maintenance burden.

### 2. `react-router-dom` is v6, not v7

`CLAUDE.md` documents it as "React Router v7" but `package.json` shows `^6.21.0`. This isn't a problem — v6 is the right choice for a client-side SPA — but the docs should be corrected to avoid confusion when looking for v7 data-router features (loaders/actions) that aren't present.

### 3. `next-themes` is a mismatch

`next-themes` was built for Next.js SSR theme management. It works in pure React, but for a local single-user tool there's no server rendering HTML, so the main problem it solves (flash of unstyled content during SSR hydration) doesn't apply. A `useState` + `localStorage` + CSS `prefers-color-scheme` approach would do the same job with zero deps. Low priority, but worth cleaning up eventually.

---

## Priority Summary

| Layer | Verdict | Top Action |
|---|---|---|
| FastAPI + SQLite | Excellent fit | — |
| SQLModel | Good, watch maturity | Plan migration to raw SA 2.0 if issues arise |
| HTTP clients | **Fix now** | Replace `requests` with async `httpx`; add explicit dep |
| ML deps | Fine, could be optional | Consider `[matching]` optional extras group |
| React + Vite + TS | Excellent fit | — |
| shadcn/ui + Tailwind v4 | Good, owned components | Run `depcheck`, prune scaffold leftovers |
| React Router | Fine (it's v6) | Fix CLAUDE.md: document as v6, not v7 |
| `next-themes` | Minor mismatch | Low priority; replaceable with a few lines |

The blocking-HTTP-in-async issue is the one worth prioritizing — everything else is polish.
