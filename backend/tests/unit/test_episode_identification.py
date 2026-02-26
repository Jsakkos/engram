"""Unit tests for episode identification with ranked voting."""

from app.matcher.episode_identification import MatchCoverage


class TestMatchCoverageRankedVoting:
    """Test suite for MatchCoverage ranked voting score calculations."""

    def test_ranked_voting_score_calculation(self):
        """Test ranked voting score matches expected formula."""
        cov = MatchCoverage("Test - S01E01", reference_duration=1800, video_duration=1800)

        # Add three matches with different confidences
        cov.add_match(start_time=300, duration=30, confidence=0.8)
        cov.add_match(start_time=600, duration=30, confidence=0.7)
        cov.add_match(start_time=900, duration=30, confidence=0.9)

        # Calculate expected score
        # weight = 30 / 1800 = 0.0167 per chunk
        # score = (0.8×0.0167 + 0.7×0.0167 + 0.9×0.0167) / (3×0.0167)
        #       = (0.8 + 0.7 + 0.9) / 3 = 0.8

        assert abs(cov.ranked_voting_score - 0.8) < 0.001

    def test_ranked_voting_empty_matches(self):
        """Test ranked voting with no matches returns 0."""
        cov = MatchCoverage("Test - S01E01", reference_duration=1800, video_duration=1800)
        assert cov.ranked_voting_score == 0.0
        assert cov.total_vote_weight == 0.0

    def test_ranked_voting_single_match(self):
        """Test ranked voting with single match returns that confidence."""
        cov = MatchCoverage("Test - S01E01", reference_duration=1800, video_duration=1800)
        cov.add_match(start_time=300, duration=30, confidence=0.75)
        assert abs(cov.ranked_voting_score - 0.75) < 0.001

    def test_ranked_voting_different_chunk_durations(self):
        """Test ranked voting with varying chunk durations."""
        cov = MatchCoverage("Test - S01E01", reference_duration=1800, video_duration=1800)

        # Add matches with different durations (different weights)
        cov.add_match(start_time=300, duration=60, confidence=0.9)  # weight: 60/1800 = 0.0333
        cov.add_match(start_time=400, duration=30, confidence=0.6)  # weight: 30/1800 = 0.0167

        # Expected: (0.9 × 60 + 0.6 × 30) / (60 + 30) = (54 + 18) / 90 = 72/90 = 0.8
        expected_score = (0.9 * 60 + 0.6 * 30) / (60 + 30)
        assert abs(cov.ranked_voting_score - expected_score) < 0.001

    def test_ranked_vs_weighted_score_sparse_matches(self):
        """Test that ranked voting differs from legacy weighted score with sparse matches."""
        cov = MatchCoverage("Test - S01E01", reference_duration=1800, video_duration=1800)

        # Add sparse matches (low file coverage)
        cov.add_match(start_time=300, duration=30, confidence=0.8)
        cov.add_match(start_time=900, duration=30, confidence=0.8)

        # Weighted score penalizes low coverage: 0.8 × (60/1800) = 0.027
        # Ranked voting maintains confidence: (0.8 + 0.8) / 2 = 0.8

        assert cov.weighted_score < 0.1  # Legacy method penalizes sparse matches
        assert cov.ranked_voting_score > 0.7  # Ranked voting maintains confidence

    def test_ranked_voting_total_vote_weight(self):
        """Test total_vote_weight calculation."""
        cov = MatchCoverage("Test - S01E01", reference_duration=1800, video_duration=1800)

        cov.add_match(start_time=300, duration=30, confidence=0.8)
        cov.add_match(start_time=600, duration=30, confidence=0.7)
        cov.add_match(start_time=900, duration=60, confidence=0.9)

        # Total weight = (30 + 30 + 60) / 1800 = 120/1800 = 0.0667
        expected_weight = 120 / 1800
        assert abs(cov.total_vote_weight - expected_weight) < 0.001

    def test_ranked_voting_with_zero_duration(self):
        """Test ranked voting handles zero video duration gracefully."""
        cov = MatchCoverage("Test - S01E01", reference_duration=1800, video_duration=0)

        cov.add_match(start_time=300, duration=30, confidence=0.8)

        # Should return 0 to avoid division by zero
        assert cov.ranked_voting_score == 0.0

    def test_get_voting_details(self):
        """Test get_voting_details returns expected metadata."""
        cov = MatchCoverage(
            "Arrested Development - S01E01", reference_duration=1800, video_duration=1800
        )

        cov.add_match(start_time=300, duration=30, confidence=0.8)
        cov.add_match(start_time=600, duration=30, confidence=0.7)

        details = cov.get_voting_details()

        assert details["episode"] == "Arrested Development - S01E01"
        assert details["vote_count"] == 2
        assert abs(details["ranked_score"] - 0.75) < 0.001  # (0.8 + 0.7) / 2
        assert abs(details["avg_confidence"] - 0.75) < 0.001
        assert details["total_weight"] > 0
        assert details["file_coverage"] > 0
        assert "legacy_weighted_score" in details

    def test_ranked_voting_high_confidence_consensus(self):
        """Test ranked voting with high-confidence consensus (typical good match)."""
        cov = MatchCoverage("Test - S01E01", reference_duration=1800, video_duration=1800)

        # Simulate 10 chunks across the episode, all with high confidence
        for i in range(10):
            cov.add_match(start_time=300 + i * 150, duration=30, confidence=0.85)

        # All chunks agree with high confidence → score should be ~0.85
        assert abs(cov.ranked_voting_score - 0.85) < 0.01

    def test_ranked_voting_mixed_confidence(self):
        """Test ranked voting with mixed confidence levels."""
        cov = MatchCoverage("Test - S01E01", reference_duration=1800, video_duration=1800)

        # Simulate realistic scenario: some high, some medium confidence
        cov.add_match(start_time=300, duration=30, confidence=0.9)
        cov.add_match(start_time=450, duration=30, confidence=0.7)
        cov.add_match(start_time=600, duration=30, confidence=0.8)
        cov.add_match(start_time=750, duration=30, confidence=0.6)

        # Expected: (0.9 + 0.7 + 0.8 + 0.6) / 4 = 3.0 / 4 = 0.75
        assert abs(cov.ranked_voting_score - 0.75) < 0.001

    def test_avg_confidence_unchanged(self):
        """Test that avg_confidence property still works as before."""
        cov = MatchCoverage("Test - S01E01", reference_duration=1800, video_duration=1800)

        cov.add_match(start_time=300, duration=30, confidence=0.8)
        cov.add_match(start_time=600, duration=30, confidence=0.6)

        # avg_confidence should be simple average: (0.8 + 0.6) / 2 = 0.7
        assert abs(cov.avg_confidence - 0.7) < 0.001

    def test_file_coverage_unchanged(self):
        """Test that file_coverage property still works as before."""
        cov = MatchCoverage("Test - S01E01", reference_duration=1800, video_duration=1800)

        cov.add_match(start_time=300, duration=30, confidence=0.8)
        cov.add_match(start_time=600, duration=30, confidence=0.6)

        # file_coverage = 60 / 1800 = 0.0333
        expected_coverage = 60 / 1800
        assert abs(cov.file_coverage - expected_coverage) < 0.001

    def test_weighted_score_unchanged(self):
        """Test that weighted_score (legacy) still works as before."""
        cov = MatchCoverage("Test - S01E01", reference_duration=1800, video_duration=1800)

        cov.add_match(start_time=300, duration=30, confidence=0.8)
        cov.add_match(start_time=600, duration=30, confidence=0.6)

        # weighted_score = avg_confidence × file_coverage
        # = 0.7 × (60/1800) = 0.7 × 0.0333 = 0.0233
        expected_weighted = 0.7 * (60 / 1800)
        assert abs(cov.weighted_score - expected_weighted) < 0.001


class TestEpisodeMatcherConfiguration:
    """Test suite for EpisodeMatcher configuration options."""

    def test_ranked_voting_enabled_by_default(self):
        """Test that ranked voting is enabled by default."""
        from pathlib import Path

        from app.matcher.episode_identification import EpisodeMatcher

        matcher = EpisodeMatcher(cache_dir=Path.home() / ".engram" / "cache", show_name="Test Show")

        assert matcher.use_ranked_voting is True

    def test_ranked_voting_can_be_disabled(self):
        """Test that ranked voting can be explicitly disabled."""
        from pathlib import Path

        from app.matcher.episode_identification import EpisodeMatcher

        matcher = EpisodeMatcher(
            cache_dir=Path.home() / ".engram" / "cache",
            show_name="Test Show",
            use_ranked_voting=False,
        )

        assert matcher.use_ranked_voting is False

    def test_ranked_voting_can_be_explicitly_enabled(self):
        """Test that ranked voting can be explicitly enabled."""
        from pathlib import Path

        from app.matcher.episode_identification import EpisodeMatcher

        matcher = EpisodeMatcher(
            cache_dir=Path.home() / ".engram" / "cache",
            show_name="Test Show",
            use_ranked_voting=True,
        )

        assert matcher.use_ranked_voting is True
