# LLM Episode Matcher — Design

**Issue:** [#109](https://github.com/Jsakkos/engram/issues/109)
**Status:** Approved — ready for implementation plan
**Date:** 2026-05-25

## Goal

Add an opt-in LLM-based episode identification path as a fallback for when Engram's primary audio-fingerprint matcher returns low confidence or no match. The LLM matches the ripped episode's transcript against TMDB season-synopsis candidates and produces a suggested episode that the user confirms via the existing Human-in-the-Loop review queue. Never auto-organizes.

## Background

Two prior eval studies in project memory ([[project_llm_episode_id_research]], [[project_llm_episode_id_gemini]]) established:

- Within-season synopsis matching with Gemini Flash-Lite hits ~66-73% top-1 (~73-79% incl. runner-up).
- Episodic shows match excellently (Star Trek TNG 100%, Arrow 96%, Breaking Bad S02 92%); framing-device serialization is the real failure mode (13 Reasons Why 31%).
- Anthropic Haiku 4.5 underperforms Gemini on this task (49% on the prior all-episodes variant).
- `confidence == 0` from Gemini is a reliable "wrong show/season" oracle.
- Confidence is partially calibrated (correct ~0.99, incorrect ~0.76) but not enough to auto-accept.

A working prototype lives at `artifacts/issue-109-llm-episode-id/` in the main checkout (gitignored).

## Pre-decided constraints

| Decision | Choice |
|---|---|
| Transcript source | Whisper full-file ASR of the ripped MKV (reuses the existing `_match_full_file` transcription path). |
| Trigger | Auto-fallback when primary match is low-confidence/needs_review, AND a "Try AI match" button on titles in REVIEW. |
| Config gate | New `ai_episode_matching_enabled` flag, independent of `ai_identification_enabled`. Both features still share `ai_provider` / `ai_api_key`. |
| Provider routing | Shared `app/core/ai_client.py` over anthropic, openai, openrouter, **gemini** (new). Both AI disc-ID and the new matcher route through it. |
| Provider selection | Whatever the user configured globally; UI hint near the toggle recommends Gemini Flash-Lite for this task. |
| Unknown season | Skip the LLM matcher entirely. No whole-show fallback. |
| Confidence handling | Always route through review (never auto-organize). Drop `confidence == 0` results entirely. Surface top-1 + runner-up with raw LLM confidence. |
| PR sequencing | Branch from main now; rebase before merge if PR #202 lands first (no file conflict expected). |

## Architecture

### New modules

**`backend/app/core/ai_client.py`** — Shared AI client.

```python
async def complete_json(
    *,
    prompt: str,
    schema: dict | None,
    provider: str,
    api_key: str,
    model: str | None = None,
    max_tokens: int = 1024,
) -> dict | None: ...
```

Provider adapters (`_call_anthropic`, `_call_openai_compatible`, `_call_gemini`) handle the per-API quirks:

- **Anthropic**: prompt-only JSON instruction + lenient parse (preserves existing `ai_identifier` behaviour).
- **OpenAI / OpenRouter**: `response_format={"type": "json_object"}` when schema present.
- **Gemini**: endpoint `https://generativelanguage.googleapis.com/v1beta/models/<model>:generateContent`, header `x-goog-api-key`, `generationConfig.responseMimeType=application/json` + `responseSchema`.

Per-provider default models:

| Provider | Default model |
|---|---|
| anthropic | `claude-haiku-4-5-20251001` |
| openai | `gpt-4o-mini` |
| openrouter | `anthropic/claude-haiku-4-5-20251001` |
| gemini | `gemini-flash-lite-latest` |

Built-in 429 backoff (1s/2s/4s, max 3 attempts) — Gemini free tier needs this.

**`backend/app/matcher/llm_episode_matcher.py`** — The feature.

```python
@dataclass
class LLMEpisodeMatch:
    episode: int
    confidence: float
    reasoning: str
    runner_up: dict | None  # {"episode": int, "confidence": float}
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
) -> LLMEpisodeMatch | None: ...
```

Fetches season synopses, cleans transcript with the existing `_clean_subtitle_text` from `app/matcher/episode_identification.py`, builds the prompt (ported from `artifacts/issue-109-llm-episode-id/PROMPT_TEMPLATE.md`), calls `ai_client.complete_json` with schema `{episode: int, confidence: float, reasoning: str, runner_up: {episode: int, confidence: float} | null}`. Returns None when `confidence == 0.0` (the "wrong show/season" signal) or when synopses are unavailable.

### Modified modules

- **`backend/app/core/ai_identifier.py`** — Refactor internals to call `ai_client.complete_json`. Public `identify_from_label(...)` API unchanged.
- **`backend/app/matcher/tmdb_client.py`** — Extend `fetch_season_episodes` to include `overview` (additive). Existing callers receive the extra field harmlessly.
- **`backend/app/matcher/episode_identification.py`** — Extract the full-file transcription logic currently inside `_match_full_file` into a reusable `transcribe_full(video_file) -> str | None`. `_match_full_file` continues to use it.
- **`backend/app/core/curator.py`** — In `match_single_file`, after the primary match returns `needs_review=True` (or no episode), and gating conditions are met, run `transcribe_full` then `match_episode_via_llm` and attach the suggestion to `MatchResult.match_details["llm_suggestion"]`. Always keep `needs_review=True`.
- **`backend/app/api/routes.py`** — One new endpoint: `POST /api/titles/{title_id}/llm-match` for the on-demand "Try AI match" button. Returns `{"suggestion": LLMEpisodeMatch | null, "reason": str | null}`.
- **`backend/app/models/app_config.py`** — Add `ai_episode_matching_enabled: bool = False`. Handled by `database.py`'s `_add_missing_columns()` for existing user databases (Engram skips Alembic in frozen builds).
- **`frontend/src/components/ConfigWizard.tsx`** — Add `gemini` to provider dropdown, labels, placeholders; add the `aiEpisodeMatchingEnabled` checkbox with the Gemini-recommendation hint.
- **`frontend/src/components/ReviewQueue/TVTitleCard.tsx`** — Render the LLM suggestion as a distinct candidate row when `match_details.llm_suggestion` is present; add the "Try AI match" button when gated on.

### Boundary discipline

The `app/matcher/` layer never imports from `app/services/` or reads config directly. The curator (the caller) reads config and passes provider/key/tmdb_key into `match_episode_via_llm` explicitly. This matches the existing matcher-layer convention.

## Data flow

### Auto-fallback path

```
Primary TF-IDF match in EpisodeMatcher.identify_episode
  ↓
EpisodeCurator.match_single_file receives result
  ↓
Primary confidence ≥ 0.7?  ──yes──►  MATCHED, no LLM, no review
  ↓ no  (needs_review OR no episode)
ai_episode_matching_enabled AND ai_api_key AND job.detected_season?
  ↓ yes
Resolve tmdb_show_id (fetch_show_id, cached)
  ↓
Get full transcript — reuse the transcript produced by _match_full_file
when the primary already took that path; otherwise call transcribe_full
  ↓
Fetch season synopses via TMDB (fetch_season_episodes with overview)
  ↓
ai_client.complete_json(prompt, schema, provider, key)
  ↓
result.confidence == 0?  ──yes──► discard
  ↓ no
Attach llm_suggestion to match_details:
  {"episode": int, "confidence": float, "reasoning": str,
   "runner_up": {...} | null, "model": str}
  ↓
MatchResult(needs_review=True, episode_code=primary's low-conf code or None,
            match_details={...llm_suggestion...})
  ↓
MatchingCoordinator persists + broadcasts title_update (no logic change)
  ↓
Review UI renders LLM suggestion alongside audio candidates
  ↓
User accepts via existing review-accept path; organizer uses the accepted code
```

### Manual-trigger path

`POST /api/titles/{title_id}/llm-match` looks up the title's `file_path` + `job.detected_title` + `job.detected_season`, runs the same `transcribe_full → match_episode_via_llm` pipeline, returns the suggestion as JSON, and writes it into `title.match_details` so a page reload preserves it. No state transition (REVIEW stays REVIEW).

## Error handling

| Failure | Response |
|---|---|
| `ai_api_key` empty / `ai_episode_matching_enabled` false | Skip silently — primary result is final. |
| `job.detected_season` is None | Skip + log info. No whole-show fallback. |
| `fetch_show_id` returns None | Skip + log info. |
| `fetch_season_episodes` returns [] | Skip + log warning. |
| `transcribe_full` fails (ffmpeg/Whisper error) | Log warning, skip LLM; title keeps primary result. |
| `transcribe_full` returns < 500 chars (silent / corrupt audio) | Skip LLM + log info — too little signal for synopsis matching. |
| AI provider HTTP error (non-429) | Log warning with provider/status, return None. |
| AI provider 429 | Exponential backoff in `ai_client` (1s/2s/4s, max 3 attempts), then give up + log. |
| AI returns malformed JSON | Log + return None. (Schema-enforced for Gemini/OpenAI; Anthropic uses lenient parse.) |
| Manual-trigger endpoint hits any failure | Returns 200 with `{"suggestion": null, "reason": "..."}` so the UI can show "couldn't suggest" without an error dialog. |

## State invariants

- LLM matcher never sets `title.state = MATCHED`. Only the user's review-accept action does that.
- LLM matcher never sets `title.match_source = "engram"` — that badge means a confident audio match. When the user accepts an LLM suggestion via review, `match_source` is set to a new value `"ai_llm"` to keep that signal distinguishable.
- `match_confidence` on `DiscTitle` reflects the accepted-path confidence. The LLM's raw confidence lives in `match_details.llm_suggestion.confidence` so it's never confused with the calibrated TF-IDF confidence.

## Concurrency and cost

- Full-file Whisper transcription dominates wall-time (~1–3 min CPU per 22-min episode). It runs under the existing `max_concurrent_matches` semaphore — a TV disc doesn't fan out N parallel transcriptions.
- One AI request per title; ≤ ~4k input tokens. At Gemini Flash-Lite pricing this is well under $0.01/episode. No per-job hard ceiling for v1.

## Testing

### Backend unit tests (`backend/tests/unit/`)

- **`test_ai_client.py`** — Mock `httpx.AsyncClient`. Per provider: request shape (URL, headers, body), response parsing, JSON-schema enforcement. Tests for: 429 retry/backoff with eventual success, 429 exhaustion → None, malformed JSON → None, unknown provider → None, empty key → None. Verify `responseSchema` is present for Gemini and `response_format` for OpenAI when a schema is passed.
- **`test_ai_identifier.py`** — Existing tests stay green after refactor (regression guard); add coverage confirming Anthropic prompt format is preserved.
- **`test_llm_episode_matcher.py`** — Mock `ai_client.complete_json` + `fetch_season_episodes`. Tests: prompt assembly (synopses + transcript in correct shape), `confidence == 0` → returns None, normal result → `LLMEpisodeMatch` populated, runner-up surfaced, missing synopses → returns None + warning, transcript shorter than threshold → returns None.
- **`test_tmdb_client.py`** — Extend coverage so `fetch_season_episodes` returns `overview` (with `""` fallback when absent).
- **`test_curator.py`** (extend) — LLM-fallback path: gated off → no LLM call, gated on but no season → primary unchanged, gated on + season known + primary low-confidence → `match_details.llm_suggestion` populated, gated on + primary high-confidence → no LLM call. `needs_review` stays True regardless.

### Backend integration tests (`backend/tests/integration/`)

- **`test_llm_matching_workflow.py`** — Mock AI client + TMDB at the boundary, drive a full disc through the simulation endpoints. Assert: title ends in REVIEW state with both raw candidates and an LLM suggestion in `match_details`, WebSocket broadcasts the suggestion, `POST /api/titles/{id}/llm-match` returns and persists a suggestion, `match_source` is NOT `"engram"`, accepting the suggestion via existing review-accept sets `match_source = "ai_llm"`.

### Frontend

- One Playwright spec injecting a fixture title with `match_details.llm_suggestion`; verifies the suggestion row renders distinctly from audio candidates and the "Try AI match" button appears when gated on.

### TDD discipline

- New shared `ai_client.py`: red → green → refactor, one provider adapter at a time.
- `llm_episode_matcher.py`: happy-path test + `confidence == 0` drop test first, then implement.
- Curator integration test goes red before the curator change lands.

## Documentation deliverables

User-facing docs ship as part of this feature, not as a follow-up. The mkdocs-material site at https://jsakkos.github.io/engram/ is updated in the same PR.

### New page

- **`docs/guide/llm-episode-matcher.md`** — dedicated feature page. Sections:
  - **What it is** — one-paragraph plain-English description of the LLM fallback.
  - **When it runs** — auto-fallback conditions (primary low-confidence + season known + config enabled) and the manual "Try AI match" button.
  - **Enabling it** — link to `getting-started/configuration.md` for the toggle and provider setup.
  - **Provider recommendation** — Gemini Flash-Lite as the highest-accuracy/$ option for this task, with a link to https://aistudio.google.com/apikey for getting a free-tier key.
  - **Accuracy expectations** — summary table from the two eval memos (episodic shows excellent, framing-device serialization weak), with shows-known-to-work and shows-known-to-struggle examples. Source: [[project_llm_episode_id_research]], [[project_llm_episode_id_gemini]].
  - **Privacy** — explicit note that the cleaned transcript is sent to the configured AI provider over the network. Disabled by default.
  - **Cost** — sub-cent-per-episode at Gemini Flash-Lite pricing; free tier suffices for typical home use.
  - **Confirmation requirement** — the LLM never auto-organizes; results always route through the review queue.

### Updated pages

- **`docs/getting-started/configuration.md`** — add settings-reference entries for `ai_episode_matching_enabled` and the new `gemini` provider option. Cross-link to the new feature page and to the existing AI-disc-ID section.
- **`docs/guide/review-queue.md`** — describe the LLM suggestion row (visually distinct from audio candidates) and the "Try AI match" button (when it appears, what it does, what to expect).
- **`docs/api/rest.md`** — document `POST /api/titles/{title_id}/llm-match`: path params, response shape (`{"suggestion": LLMEpisodeMatch | null, "reason": str | null}`), error conditions.
- **`mkdocs.yml`** — add the new feature page to the "User Guide" nav section.
- **`README.md`** — one-line Features bullet near the existing "Audio fingerprint matching" bullet, gated on AI configuration being enabled.
- **`CHANGELOG.md`** — `### Added` entry under the next unreleased version describing the user-visible behaviour: opt-in LLM episode-matching fallback with Gemini support, surfaces through the review queue.

### Out of scope for docs (v1)

- No screenshots — the review-queue spec image surface already covers the relevant area; new screenshots can come after first real-world use shapes the UI.
- No deep-dive eval write-up in the public docs — the project-memory memos and the experiment harness in `artifacts/issue-109-llm-episode-id/` are sufficient internal references.

## Out of scope (explicit YAGNI for v1)

- No prompt-engineering harness inside the app — the experiment harness in `artifacts/issue-109-llm-episode-id/` remains the reference.
- No per-job cost telemetry — single AI request per title at sub-cent cost.
- No multi-season iteration when season is unknown.
- No auto-accept on high LLM confidence (always review).
- No new model picker in the UI — sensible per-provider defaults.

## Open questions

None — all design decisions are pre-decided in the table above.

## References

- Issue: https://github.com/Jsakkos/engram/issues/109
- Prototype harness: `artifacts/issue-109-llm-episode-id/` (gitignored, in main checkout)
- Prior eval memos: [[project_llm_episode_id_research]], [[project_llm_episode_id_gemini]]
- Related: PR #202 (tvsubtitles bugfix, unrelated module — rebase before merge if it lands first)
