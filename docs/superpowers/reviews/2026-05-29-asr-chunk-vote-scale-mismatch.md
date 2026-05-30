# ASR/TF-IDF matcher: chunk-vs-episode cosine scale mismatch

**Date:** 2026-05-29
**Symptom:** Matching a known-season file (e.g. The Expanse S01E01, season hard-coded to 1)
returns `episode=None` ("needs review", no candidate). The matcher transcribes accurately
and *ranks* the correct episode first, but every 30s chunk scores ~0.08–0.12 cosine —
under the absolute `0.15` gate — so 0 chunks vote and the result is a total miss.

## Root cause

The per-chunk accept gate compared a **~30s chunk (30–120 words)** against a
**full-episode TF-IDF vector (~3,500 words)** with an absolute `cosine > 0.15` threshold.
Both vectors are L2-normalized, so the cosine of a short, sparse query against a long,
dense reference is **structurally bounded near √(words_chunk / words_episode) ≈ 0.1** even
for a *perfect* match. The correct episode still wins on **rank** (it leads runners-up by
1.8–5.6×), but its **absolute** cosine sits at/below the gate.

Two defects compounded:

1. **Miscalibrated gate.** `0.15` was tuned for the legacy scraped `TfidfVectorizer`
   (per-season fit, 10k features). The precomputed `HashingVectorizer` + global-IDF
   migration (`vectorizer_config.py`) lowered the cosine scale ~20–30%, tipping an
   already-marginal threshold into outright failure. The threshold constant never moved
   with the vectorizer.
2. **Unreachable safety net.** The full-file fallback (`_match_full_file`) is explicitly
   designed for this — it compares whole-vs-whole on a higher scale. But when *every* chunk
   scored below `0.15`, `best_match` was `None` and `identify_episode` early-returned
   `episode=None` **before** the fallback ran. The fallback was dead code in exactly the
   total-miss case it exists for.

## Evidence (read-only probes against the real `~/.engram/cache`)

Perfect transcription = the reference subtitle text itself, sliced into the matcher's 30s windows.

| Measurement | Result |
|---|---|
| Perfect 30s chunks where S01E01 is **top-ranked** | **9/10** |
| Perfect 30s chunks **crossing 0.15** | **3/10** (mean cosine 0.125) |
| Runner-up episodes per chunk | 0.02–0.06 (correct ep leads 1.8–5.6×) |
| **Full-file** query (3,563 words vs 3,563) | **cosine = 1.000** → proves vectorizer spaces are identical |
| Real observed ASR snippets | 0.07–0.10, **still rank #1** |
| Scraped path on identical chunks | 5/9 cross 0.15 (vs precomputed 3/9) → migration lowered scale |

Ruled out by the 1.000 full-file cosine: vectorizer/tokenization mismatch, IDF/normalization
bug. Ruled out by perfect-text still failing: ASR quality, time-window misalignment.

## Fix

Replace the absolute gate with a **rank+margin vote** (`select_chunk_vote`): a chunk votes
for its top episode iff cosine ≥ `CHUNK_VOTE_FLOOR` (0.06) **and** leads the runner-up by
`CHUNK_VOTE_MARGIN_RATIO` (1.8×). Also let `identify_episode` fall through to the full-file
fallback when no chunk votes (defense-in-depth), and preserve scan stats on total miss.

### Validation (same probes, candidate gate)

| Case | Absolute 0.15 gate | Rank+margin gate |
|---|---|---|
| S01E01 chunks vs **S01** vectors | 3 votes | **8 votes** |
| S01E01 chunks vs **S02** vectors (wrong season) | 0 | **0** (no false match) |
| End-to-end calibrated confidence | — | **0.85 → AUTO-ACCEPT** |

The margin gate ~doubles true-positive sensitivity without manufacturing a wrong-season match.

## Tests

- `tests/unit/test_chunk_vote_gate.py` — gate logic (synthetic, CI) + fallback reachability
  (red-green verified: the fallback test fails against the old early-return).
- `tests/real_data/test_chunk_vote_real_cache.py` — real Expanse cache, positive + negative
  control (skipped when the cache is absent).

This is separate from PR #264 (import-watch-folder), which only makes import paths *attempt*
matching; it does not change matcher scoring.
