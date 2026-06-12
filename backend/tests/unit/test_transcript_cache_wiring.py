"""Wiring tests for the persistent L2 transcript cache in EpisodeMatcher.

Covers the layered lookup (L1 dict → L2 SQLite → Whisper) added in
``EpisodeMatcher.transcribe_chunk_cached`` / ``transcribe_full``, the
effective-device fix in ``EpisodeMatcher.__init__``, and the golden scan-point
offsets that the persisted cache keys depend on.

The fake ASR model here is a *call-counting stand-in for Whisper only* — the
matcher methods, ``transcript_store`` and the key derivation all run for real
(against the tmp_path-scoped store from the conftest
``_isolate_transcript_store`` fixture).
"""

import sqlite3
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from app.matcher import transcript_store
from app.matcher.asr_models import set_asr_device
from app.matcher.episode_identification import EpisodeMatcher, canonical_scan_points

OFFSETS = [90, 366, 643, 920]
CHUNK_LEN = 30


@pytest.fixture(autouse=True)
def _reset_asr_device_override():
    """Reset the process-wide ASR device override around every test."""
    set_asr_device(None)
    yield
    set_asr_device(None)


class FakeModel:
    """Counts transcribe() calls; output is deterministic per audio path.

    Exposes ``device`` like a loaded FasterWhisperModel so ``_model_key_for``
    exercises its loaded-model derivation path.
    """

    device = "cpu"

    def __init__(self, text_template="spoken words from {stem} again and again and again"):
        self.calls = 0
        self.text_template = text_template

    def transcribe(self, audio_path):
        self.calls += 1
        return {"text": " " + self.text_template.format(stem=Path(audio_path).stem) + " "}


def _matcher(tmp_path):
    # Explicit device="cpu" keeps the derived model_key deterministic on
    # GPU-equipped dev machines.
    return EpisodeMatcher(
        cache_dir=tmp_path, show_name="Test Show", model_name="tiny", device="cpu"
    )


def _video(tmp_path, name="episode.mkv"):
    video = tmp_path / name
    video.write_bytes(b"\x00" * 4096)
    return video


def _fake_extract(tmp_path):
    """extract_audio_chunk stand-in: a distinct wav path per offset (never touches ffmpeg)."""
    return lambda mkv_file, start_time, duration=None: str(tmp_path / f"chunk_{start_time}.wav")


class TestRestartSurvival:
    """The headline guarantee: ASR output survives a process restart."""

    def test_fresh_matcher_instance_reuses_persisted_transcripts(self, tmp_path):
        video = _video(tmp_path)

        # Instance #1 (cold caches): every offset is computed once.
        m1 = _matcher(tmp_path)
        model1 = FakeModel()
        with patch.object(m1, "extract_audio_chunk", side_effect=_fake_extract(tmp_path)):
            texts1 = [m1.transcribe_chunk_cached(video, off, CHUNK_LEN, model1) for off in OFFSETS]
        assert model1.calls == len(OFFSETS)
        assert len(set(texts1)) == len(OFFSETS)  # sanity: per-offset transcripts are distinct

        # Instance #2 = simulated process restart: L1 empty, same L2 store.
        # Zero transcribe calls AND zero wav extractions — everything from L2.
        m2 = _matcher(tmp_path)
        model2 = FakeModel()
        no_extract = Mock(side_effect=AssertionError("L2 hit must not extract a wav"))
        with patch.object(m2, "extract_audio_chunk", no_extract):
            texts2 = [m2.transcribe_chunk_cached(video, off, CHUNK_LEN, model2) for off in OFFSETS]

        assert model2.calls == 0
        assert texts2 == texts1

    def test_transcribe_full_round_trips_across_instances(self, tmp_path):
        video = _video(tmp_path)

        m1 = _matcher(tmp_path)
        model1 = FakeModel()
        with (
            patch.object(m1, "extract_audio_chunk", side_effect=_fake_extract(tmp_path)),
            patch("app.matcher.episode_identification.get_cached_model", return_value=model1),
            patch("app.matcher.episode_identification.get_video_duration", return_value=1320),
        ):
            first = m1.transcribe_full(video)
        assert first is not None
        assert model1.calls == 1

        m2 = _matcher(tmp_path)
        model2 = FakeModel()
        no_extract = Mock(side_effect=AssertionError("L2 hit must not extract a wav"))
        with (
            patch.object(m2, "extract_audio_chunk", no_extract),
            patch("app.matcher.episode_identification.get_cached_model", return_value=model2),
            patch("app.matcher.episode_identification.get_video_duration", return_value=1320),
        ):
            second = m2.transcribe_full(video)

        assert model2.calls == 0
        assert second == first


class TestLayering:
    """L1 → L2 → compute ordering and write-through behaviour."""

    def test_compute_writes_through_to_l2(self, tmp_path):
        video = _video(tmp_path)
        m = _matcher(tmp_path)
        model = FakeModel()
        with patch.object(m, "extract_audio_chunk", side_effect=_fake_extract(tmp_path)):
            text = m.transcribe_chunk_cached(video, 90, CHUNK_LEN, model)

        file_key = transcript_store.file_key_for(video)
        model_key = m._model_key_for(model)
        assert transcript_store.get(file_key, 90, CHUNK_LEN, model_key) == text
        # The write landed in the tmp_path store the conftest fixture redirected
        # to — NOT the developer's real ~/.engram cache.
        assert (tmp_path / "transcripts.sqlite").exists()

    def test_l2_hit_populates_l1_so_store_is_read_once(self, tmp_path, monkeypatch):
        video = _video(tmp_path)

        m1 = _matcher(tmp_path)
        model1 = FakeModel()
        with patch.object(m1, "extract_audio_chunk", side_effect=_fake_extract(tmp_path)):
            m1.transcribe_chunk_cached(video, 90, CHUNK_LEN, model1)

        # Fresh instance: first call is an L2 hit (one store.get), second call
        # must be served from the L1 entry that hit populated (no more gets).
        get_calls = []
        real_get = transcript_store.get
        monkeypatch.setattr(
            transcript_store, "get", lambda *a, **k: get_calls.append(a) or real_get(*a, **k)
        )
        m2 = _matcher(tmp_path)
        model2 = FakeModel()
        first = m2.transcribe_chunk_cached(video, 90, CHUNK_LEN, model2)
        second = m2.transcribe_chunk_cached(video, 90, CHUNK_LEN, model2)

        assert first == second
        assert model2.calls == 0
        assert len(get_calls) == 1

    def test_l1_hit_short_circuits_same_instance(self, tmp_path):
        video = _video(tmp_path)
        m = _matcher(tmp_path)
        model = FakeModel()
        with patch.object(m, "extract_audio_chunk", side_effect=_fake_extract(tmp_path)):
            m.transcribe_chunk_cached(video, 90, CHUNK_LEN, model)
            m.transcribe_chunk_cached(video, 90, CHUNK_LEN, model)
        assert model.calls == 1

    def test_empty_transcript_is_cached_not_a_perpetual_miss(self, tmp_path):
        """{"text": None} → "" must be cached ("" is a valid value, only None misses)."""
        video = _video(tmp_path)

        class SilentModel(FakeModel):
            def transcribe(self, audio_path):
                self.calls += 1
                return {"text": None}

        m1 = _matcher(tmp_path)
        model1 = SilentModel()
        with patch.object(m1, "extract_audio_chunk", side_effect=_fake_extract(tmp_path)):
            assert m1.transcribe_chunk_cached(video, 90, CHUNK_LEN, model1) == ""
        assert model1.calls == 1

        m2 = _matcher(tmp_path)
        model2 = SilentModel()
        assert m2.transcribe_chunk_cached(video, 90, CHUNK_LEN, model2) == ""
        assert model2.calls == 0

    def test_unstatable_file_degrades_to_compute(self, tmp_path):
        """Missing file → file_key None → no persistence, but matching still works."""
        ghost = tmp_path / "ghost.mkv"  # never created
        for _ in range(2):  # two fresh instances: no cross-instance reuse, no raise
            m = _matcher(tmp_path)
            model = FakeModel()
            with patch.object(m, "extract_audio_chunk", side_effect=_fake_extract(tmp_path)):
                text = m.transcribe_chunk_cached(ghost, 90, CHUNK_LEN, model)
            assert text
            assert model.calls == 1

    def test_transcribe_full_unlinks_computed_wav_but_not_on_hit(self, tmp_path):
        """The full-file wav (tens of MB) must be deleted after a compute; an
        L1 hit produces no wav and must not delete anything."""
        video = _video(tmp_path)
        m = _matcher(tmp_path)
        # >=50 chars: transcribe_full rejects shorter transcripts as "too little text".
        model = FakeModel(text_template="a long full-file transcript about {stem} " * 3)
        wav = tmp_path / "full.wav"
        wav.write_bytes(b"RIFF")

        with (
            patch.object(m, "extract_audio_chunk", return_value=str(wav)),
            patch("app.matcher.episode_identification.get_cached_model", return_value=model),
            patch("app.matcher.episode_identification.get_video_duration", return_value=1320),
        ):
            first = m.transcribe_full(video)
            assert first is not None
            assert not wav.exists()  # compute path: wav extracted, then removed

            # L1 hit: no extraction, and a (recreated) wav is left untouched.
            wav.write_bytes(b"RIFF")
            second = m.transcribe_full(video)

        assert second == first
        assert model.calls == 1
        assert wav.exists()

    def test_temp_files_appended_only_on_compute(self, tmp_path):
        """Cleanup semantics: a wav is tracked when extracted, never on a hit."""
        video = _video(tmp_path)
        m1 = _matcher(tmp_path)
        model = FakeModel()
        temp_files = []
        with patch.object(m1, "extract_audio_chunk", side_effect=_fake_extract(tmp_path)):
            m1.transcribe_chunk_cached(video, 90, CHUNK_LEN, model, temp_files=temp_files)
            assert temp_files == [str(tmp_path / "chunk_90.wav")]
            m1.transcribe_chunk_cached(video, 90, CHUNK_LEN, model, temp_files=temp_files)
        assert len(temp_files) == 1  # L1 hit appended nothing

        m2 = _matcher(tmp_path)
        temp_files2 = []
        m2.transcribe_chunk_cached(video, 90, CHUNK_LEN, FakeModel(), temp_files=temp_files2)
        assert temp_files2 == []  # L2 hit appended nothing


class TestIdentifyEpisodeWiredPath:
    """The L2 keys identify_episode precomputes and threads through its chunk loop.

    ``transcribe_chunk_cached`` is covered directly above; this exercises the
    WIRED path — identify_episode derives ``l2_file_key``/``l2_model_key`` once
    per call and passes them into every chunk lookup — and proves the rows land
    in the store under the file's real ``file_key_for`` key and the matcher's
    model key (not under None, and not under a per-chunk re-derivation that
    could drift).
    """

    def test_identify_episode_persists_chunk_rows_under_real_keys(self, tmp_path):
        video = _video(tmp_path)
        m = _matcher(tmp_path)
        model = FakeModel()

        # Lightest identify_episode drive (mirrors TestTranscriptionCache in
        # test_episode_identification.py): the precomputed-season and TF-IDF
        # seams are mocked so only extraction/ASR/L2 wiring run for real. The
        # matrix/idf in the precomputed tuple are inert sentinels — the patched
        # _get_tfidf_matcher never touches them.
        tfidf = Mock()
        tfidf.is_prepared = True
        tfidf.match.return_value = [("S01E01", 0.9)]
        precomputed = (object(), ["S01E01"], object())
        fallback_guard = Mock(side_effect=AssertionError("full-file fallback must not run"))

        with (
            patch.object(m, "_get_tfidf_matcher", return_value=tfidf),
            patch.object(m, "_load_precomputed_season", return_value=precomputed),
            patch.object(m, "extract_audio_chunk", side_effect=_fake_extract(tmp_path)),
            patch.object(m, "_match_full_file", fallback_guard),
            patch("app.matcher.episode_identification.get_cached_model", return_value=model),
            patch("app.matcher.episode_identification.get_video_duration", return_value=2700),
        ):
            result = m.identify_episode(video, tmp_path, season_number=1)

        # Sanity: the decisive votes were accepted directly (no fallback row).
        assert result is not None
        assert result["episode"] == 1

        offsets = canonical_scan_points(2700, skip_initial=90, num_points=10)
        assert model.calls == len(offsets)

        # Every scan offset is retrievable under the PRODUCTION keys: the
        # file's stat-based file_key and the matcher's derived model key.
        file_key = transcript_store.file_key_for(video)
        assert file_key is not None
        model_key = "whisper_tiny_cpu_int8"  # device="cpu" matcher + FakeModel.device="cpu"
        assert m._model_key_for(model) == model_key
        for off in offsets:
            assert transcript_store.get(file_key, off, CHUNK_LEN, model_key) is not None

        # ...and ONLY those rows: nothing landed under a None/drifted key and
        # the accepted match never produced the (0, duration) full-file span.
        conn = sqlite3.connect(transcript_store.CACHE_DB_PATH)
        try:
            rows = set(
                conn.execute(
                    "SELECT file_key, start_s, duration_s, model_key FROM transcripts"
                ).fetchall()
            )
        finally:
            conn.close()
        assert rows == {(file_key, int(off), CHUNK_LEN, model_key) for off in offsets}


class TestModelKeySeparation:
    """Transcripts must never be reused across different ASR model identities."""

    def test_different_model_key_is_a_miss(self, tmp_path):
        video = _video(tmp_path)

        m1 = _matcher(tmp_path)
        model1 = FakeModel()
        with patch.object(m1, "extract_audio_chunk", side_effect=_fake_extract(tmp_path)):
            m1.transcribe_chunk_cached(
                video, 90, CHUNK_LEN, model1, model_key="whisper_tiny_cpu_int8"
            )
        assert model1.calls == 1

        # Fresh instance, same file/offset, DIFFERENT model identity → recompute.
        m2 = _matcher(tmp_path)
        model2 = FakeModel()
        with patch.object(m2, "extract_audio_chunk", side_effect=_fake_extract(tmp_path)):
            m2.transcribe_chunk_cached(
                video, 90, CHUNK_LEN, model2, model_key="whisper_large-v3_cpu_int8"
            )
        assert model2.calls == 1

    def test_model_key_prefers_loaded_model_device(self, tmp_path):
        """CUDA→CPU load fallback: the key follows the model's actual device,
        so a transcript produced on cpu/int8 is never filed under cuda/float16."""
        m = _matcher(tmp_path)
        m.device = "cuda"  # matcher config claims cuda...
        model = FakeModel()  # ...but the loaded model fell back to cpu
        key = m._model_key_for(model)
        assert "_cpu_" in key
        assert "_cuda_" not in key

    def test_model_key_falls_back_to_config_for_fakes_without_device(self, tmp_path):
        m = _matcher(tmp_path)  # device="cpu"
        key = m._model_key_for(object())  # no .device attribute
        assert key == "whisper_tiny_cpu_int8"


class TestEffectiveDeviceFix:
    """EpisodeMatcher must honor the startup-pinned device, not the raw GPU probe."""

    def test_pinned_cpu_wins_over_available_gpu(self, tmp_path):
        with patch("app.matcher.asr_models.ctranslate2.get_cuda_device_count", return_value=1):
            set_asr_device("cpu")  # user disabled GPU ASR / CUDA libs missing
            m = EpisodeMatcher(cache_dir=tmp_path, show_name="X", model_name="tiny")
            assert m.device == "cpu"
            # _model_config feeds the L2 model_key — it must not claim cuda.
            assert m._model_config()["device"] == "cpu"

    def test_unpinned_falls_back_to_probe(self, tmp_path):
        with patch("app.matcher.asr_models.ctranslate2.get_cuda_device_count", return_value=0):
            m = EpisodeMatcher(cache_dir=tmp_path, show_name="X", model_name="tiny")
        assert m.device == "cpu"


class TestGoldenScanOffsets:
    """Cache-stability guard: scan offsets are PERSISTED transcript-cache keys.

    These exact values are baked into every user's ~/.engram/cache/
    transcripts.sqlite as the start_s key component. Changing the lattice math
    (canonical_scan_points) silently invalidates every existing transcript
    cache — every chunk becomes a miss and gets re-transcribed. If this test
    fails, you broke cache compatibility, not just a unit test: either revert
    the lattice change or ship it knowingly as a cache-busting change.
    """

    def test_level_10_offsets_for_45min_episode(self):
        assert canonical_scan_points(2700, skip_initial=90, num_points=10) == [
            90, 366, 643, 920, 1196, 1473, 1750, 2026, 2303, 2580,
        ]  # fmt: skip

    def test_level_19_offsets_for_45min_episode(self):
        assert canonical_scan_points(2700, skip_initial=90, num_points=19) == [
            90, 228, 366, 505, 643, 781, 920, 1058, 1196, 1335,
            1473, 1611, 1750, 1888, 2026, 2165, 2303, 2441, 2580,
        ]  # fmt: skip
