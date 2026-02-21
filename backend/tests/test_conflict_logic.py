import json
from dataclasses import dataclass


# Mock Models
@dataclass
class MockTitle:
    id: int
    title_index: int
    matched_episode: str
    match_confidence: float
    match_details: str | None = None
    state: str = "matched"
    output_filename: str = ""


# Mock Logic from _finalize_disc_job
def simulate_conflict_resolution(titles: list[MockTitle]):
    print(f"--- Simulating Conflict Resolution for {len(titles)} titles ---")

    candidates = {}
    for t in titles:
        if t.state == "matched" and t.matched_episode:
            if t.matched_episode not in candidates:
                candidates[t.matched_episode] = []
            candidates[t.matched_episode].append(t)

    for ep_code, title_list in candidates.items():
        print(f"\nProcessing {ep_code}:")

        # Extract ranked voting metrics for each candidate
        ranked_candidates = []
        for t in title_list:
            score = 0.0
            vote_count = 0
            file_cov = 0.0

            if t.match_details:
                try:
                    details = json.loads(t.match_details)
                    score = details.get("score", 0.0)
                    vote_count = details.get("vote_count", 0)
                    file_cov = details.get("file_cov", 0.0)
                except Exception:
                    pass

            # Fallback to confidence if no score
            if score == 0.0:
                score = t.match_confidence

            ranked_candidates.append(
                {
                    "title": t,
                    "score": score,
                    "vote_count": vote_count,
                    "file_coverage": file_cov,
                }
            )

        # Sort by ranked voting criteria
        # Primary: vote_count (more votes = more reliable)
        # Secondary: score (confidence)
        # Tertiary: file_coverage (more coverage = better)
        ranked_candidates.sort(
            key=lambda x: (x["vote_count"], x["score"], x["file_coverage"]), reverse=True
        )

        best = ranked_candidates[0]
        best_title = best["title"]

        print(
            f"  Winner: Title {best_title.id} (votes={best['vote_count']}, score={best['score']:.3f}, cov={best['file_coverage']:.1%})"
        )

        for i, cand in enumerate(ranked_candidates, 1):
            status = "WINNER" if cand["title"].id == best_title.id else "LOSER"
            print(
                f"    {i}. Title {cand['title'].id}: "
                f"votes={cand['vote_count']}, "
                f"score={cand['score']:.3f}, "
                f"coverage={cand['file_coverage']:.1%} "
                f"[{status}]"
            )

        if len(ranked_candidates) > 1:
            runner_up = ranked_candidates[1]
            if (
                best["vote_count"] == runner_up["vote_count"]
                and abs(best["score"] - runner_up["score"]) < 0.05
            ):
                print(
                    f"  [WARNING] Ambiguous match! Same votes, score margin: {best['score'] - runner_up['score']:.3f}"
                )


if __name__ == "__main__":
    # Test Case 1: Clear Winner (Individual vs Play All) - vote count wins
    titles_1 = [
        MockTitle(
            id=1,
            title_index=1,
            matched_episode="S01E01",
            match_confidence=0.9,
            match_details=json.dumps({"score": 0.714, "vote_count": 10, "file_cov": 0.95}),
        ),  # Individual episode
        MockTitle(
            id=2,
            title_index=0,
            matched_episode="S01E01",
            match_confidence=0.9,
            match_details=json.dumps({"score": 0.737, "vote_count": 1, "file_cov": 0.017}),
        ),  # Play All (opening credits)
    ]
    simulate_conflict_resolution(titles_1)

    # Test Case 2: Ambiguous (Two similar files with same votes)
    titles_2 = [
        MockTitle(
            id=3,
            title_index=3,
            matched_episode="S01E02",
            match_confidence=0.8,
            match_details=json.dumps({"score": 0.72, "vote_count": 5, "file_cov": 0.15}),
        ),
        MockTitle(
            id=4,
            title_index=4,
            matched_episode="S01E02",
            match_confidence=0.79,
            match_details=json.dumps({"score": 0.71, "vote_count": 5, "file_cov": 0.14}),
        ),
    ]
    simulate_conflict_resolution(titles_2)

    # Test Case 3: Mixed bag (old format vs new)
    titles_3 = [
        MockTitle(
            id=5, title_index=5, matched_episode="S01E03", match_confidence=0.9, match_details=None
        ),  # Old format (no votes)
        MockTitle(
            id=6,
            title_index=0,
            matched_episode="S01E03",
            match_confidence=0.9,
            match_details=json.dumps({"score": 0.15, "vote_count": 1, "file_cov": 0.02}),
        ),  # Play All
    ]
    simulate_conflict_resolution(titles_3)

    # Test Case 4: Low-vote false positives (Arrested Development scenario)
    titles_4 = [
        MockTitle(
            id=7,
            title_index=52,
            matched_episode="S01E06",
            match_confidence=0.737,
            match_details=json.dumps({"score": 0.737, "vote_count": 1, "file_cov": 0.017}),
        ),
        MockTitle(
            id=8,
            title_index=53,
            matched_episode="S01E06",
            match_confidence=0.737,
            match_details=json.dumps({"score": 0.737, "vote_count": 1, "file_cov": 0.017}),
        ),
        MockTitle(
            id=9,
            title_index=54,
            matched_episode="S01E06",
            match_confidence=0.714,
            match_details=json.dumps({"score": 0.714, "vote_count": 10, "file_cov": 0.152}),
        ),  # Actual episode
    ]
    simulate_conflict_resolution(titles_4)
