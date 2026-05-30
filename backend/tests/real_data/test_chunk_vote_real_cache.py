"""Real-cache fidelity test for the rank+margin chunk-vote gate.

Uses the shipped precomputed subtitle-vector cache (~/.engram/cache) and the
real downloaded SRTs to reproduce the production bug: feeding a known episode's
own subtitle text (sliced into the matcher's 30s windows = best-case "perfect
transcription") must accumulate enough votes for the CORRECT episode under the
rank+margin gate, while the old absolute 0.15 gate accepted too few to match.

Skipped when the cache/SRTs aren't present (CI and most dev machines).
Run locally with:  uv run pytest tests/real_data/ -v -m real_data
"""

import json
from pathlib import Path

import numpy as np
import pytest

CACHE = Path.home() / ".engram" / "cache"
SHOW = "The Expanse"
PRE = CACHE / "precomputed" / SHOW
DATA = CACHE / "data" / SHOW
SRT = DATA / "The Expanse - S01E01.srt"

pytestmark = pytest.mark.real_data

_missing = (
    not (PRE / "S01.npz").exists()
    or not SRT.exists()
    or not (CACHE / "precomputed" / "idf.npy").exists()
)


@pytest.mark.skipif(_missing, reason="Real Expanse precomputed cache / SRTs not present")
class TestChunkVoteRealCache:
    def _load_season(self, season):
        from scipy.sparse import load_npz

        from app.matcher.vectorizer_config import apply_tfidf

        idf = np.load(CACHE / "precomputed" / "idf.npy")
        refs = apply_tfidf(load_npz(PRE / f"S{season:02d}.npz"), idf)
        codes = json.loads((PRE / f"S{season:02d}.index.json").read_text(encoding="utf-8"))
        return refs, codes, idf

    def _perfect_chunks(self, idf):
        """Real S01E01 subtitle text sliced into the matcher's evenly-spaced 30s
        windows, projected through the real runtime query path."""
        from app.matcher.episode_identification import SubtitleReader, _clean_subtitle_text
        from app.matcher.vectorizer_config import transform_query

        content = SubtitleReader.read_srt_file(str(SRT))
        last_end = 0.0
        for block in content.strip().split("\n\n"):
            lines = block.split("\n")
            if len(lines) >= 3 and "-->" in lines[1]:
                try:
                    last_end = max(
                        last_end, SubtitleReader.parse_timestamp(lines[1].split(" --> ")[1].strip())
                    )
                except (ValueError, IndexError):
                    # Malformed timestamp/block: skip it when scanning for the
                    # last subtitle end time (matches extract_subtitle_chunk).
                    continue
        skip_initial, skip_final, chunk_len, n = 300, 120, 30, 10
        interval = (last_end - skip_initial - skip_final) / (n - 1)
        queries = []
        for i in range(n):
            start = int(skip_initial + i * interval)
            txt = _clean_subtitle_text(
                " ".join(SubtitleReader.extract_subtitle_chunk(content, start, start + chunk_len))
            )
            if txt:
                queries.append(transform_query(txt, idf))
        return queries

    def _votes(self, refs, codes, queries):
        from collections import defaultdict

        from sklearn.metrics.pairwise import cosine_similarity

        from app.matcher.episode_identification import select_chunk_vote

        tally = defaultdict(int)
        for q in queries:
            sims = cosine_similarity(q, refs)[0]
            results = sorted(
                zip(codes, sims.tolist(), strict=False), key=lambda x: x[1], reverse=True
            )
            vote = select_chunk_vote(results)
            if vote is not None:
                tally[vote[0]] += 1
        return tally

    def test_correct_episode_accumulates_min_votes(self):
        refs, codes, idf = self._load_season(1)
        queries = self._perfect_chunks(idf)
        assert queries, "no perfect chunks extracted"

        tally = self._votes(refs, codes, queries)

        # The correct episode wins, with enough votes to clear min_vote_count (2),
        # and no wrong episode out-votes it.
        assert tally.get("S01E01", 0) >= 2, f"S01E01 under-voted: {dict(tally)}"
        assert max(tally, key=tally.get) == "S01E01", f"wrong winner: {dict(tally)}"

    def test_wrong_season_does_not_confidently_match(self):
        # Negative control: S01E01 chunks vs S02 vectors must not manufacture a
        # confident (>= min_vote_count) match for any S02 episode.
        _, _, idf = self._load_season(1)
        refs2, codes2, _ = self._load_season(2)
        queries = self._perfect_chunks(idf)

        tally = self._votes(refs2, codes2, queries)

        top = max(tally.values(), default=0)
        assert top < 2, f"wrong-season false match: {dict(tally)}"
