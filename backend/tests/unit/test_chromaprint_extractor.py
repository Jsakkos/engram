"""Tests for chromaprint extraction and storage."""

from unittest.mock import MagicMock, patch

import pytest

from app.matcher.chromaprint_extractor import ChromaprintExtractor, ChromaprintResult
from app.models.app_config import AppConfig
from app.models.disc_job import DiscTitle


def test_disc_title_has_chromaprint_fields():
    """DiscTitle model exposes chromaprint storage fields."""
    fields = DiscTitle.model_fields
    assert "chromaprint_blob" in fields, "DiscTitle is missing chromaprint_blob"
    assert "chromaprint_extracted_at" in fields, "DiscTitle is missing chromaprint_extracted_at"


def test_app_config_has_fingerprint_fields():
    """AppConfig exposes fingerprint extraction settings."""
    fields = AppConfig.model_fields
    assert "fpcalc_path" in fields
    assert "contribution_pseudonym" in fields
    assert "enable_fingerprint_contributions" in fields


def test_enable_fingerprint_contributions_defaults_true():
    """Opt-out default: contributions enabled unless explicitly disabled."""
    cfg = AppConfig()
    assert cfg.enable_fingerprint_contributions is True


def test_enable_fingerprint_contributions_has_sql_server_default_true():
    """The column DDL must carry server_default='1' so the frozen-build path
    (_add_missing_columns in database.py) writes the correct default for existing DBs.
    Frozen builds skip Alembic entirely, so the model declaration is the only source
    of truth for that path."""
    column = AppConfig.__table__.columns["enable_fingerprint_contributions"]
    assert column.server_default is not None, (
        "enable_fingerprint_contributions needs sa_column_kwargs={'server_default': text('1')} "
        "so frozen-build users default to opt-in"
    )
    assert "1" in str(column.server_default.arg)


def test_chromaprint_result_serializes_to_bytes():
    """ChromaprintResult.to_blob() returns deterministic compressed bytes."""
    r = ChromaprintResult(
        hashes=[1, 2, 3, 4, 5],
        duration_seconds=42.0,
        fpcalc_version="fpcalc version 1.5.1",
    )
    blob = r.to_blob()
    assert isinstance(blob, bytes)
    assert len(blob) > 0
    assert r.to_blob() == blob  # deterministic


def test_chromaprint_result_roundtrip():
    """to_blob / from_blob is lossless on the hash stream and duration."""
    r = ChromaprintResult(hashes=[100, 200, 300], duration_seconds=12.5, fpcalc_version="test")
    restored = ChromaprintResult.from_blob(r.to_blob())
    assert restored.hashes == [100, 200, 300]
    assert restored.duration_seconds == 12.5


def test_extractor_construction():
    """ChromaprintExtractor takes an fpcalc_path and an optional ffmpeg_path."""
    ex = ChromaprintExtractor(fpcalc_path="/fake/fpcalc")
    assert ex.fpcalc_path == "/fake/fpcalc"
    assert ex.ffmpeg_path is None  # fallback disabled by default

    ex2 = ChromaprintExtractor(fpcalc_path="/fake/fpcalc", ffmpeg_path="/fake/ffmpeg")
    assert ex2.ffmpeg_path == "/fake/ffmpeg"


# Real fpcalc stderr for the codec-gap case (DTS/TrueHD/FLAC/E-AC-3).
_DECODER_GAP_STDERR = "ERROR: Could not find any audio stream in the file (Decoder not found)"


def _make_proc(returncode, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


@pytest.mark.asyncio
async def test_extract_falls_back_to_ffmpeg_on_decoder_error():
    """When fpcalc can't decode (decoder-not-found) and ffmpeg is configured,
    the audio is re-decoded via ffmpeg and fingerprinted from the temp WAV."""

    def fake_run(cmd, **kwargs):
        exe = cmd[0]
        if exe == "/fake/ffmpeg":
            return _make_proc(0)  # transcode "succeeds"
        if "-version" in cmd:
            return _make_proc(0, stdout="fpcalc version 1.5.1\n")
        target = cmd[-1]
        if target.endswith(".wav"):  # fpcalc on the ffmpeg-decoded WAV
            return _make_proc(0, stdout="DURATION=1304\nFINGERPRINT=10,20,30\n")
        return _make_proc(2, stderr=_DECODER_GAP_STDERR)  # fpcalc on the source

    with patch("app.matcher.chromaprint_extractor.subprocess.run", side_effect=fake_run):
        ex = ChromaprintExtractor(fpcalc_path="/fake/fpcalc", ffmpeg_path="/fake/ffmpeg")
        result = await ex.extract("/fake/dts.mkv")

    assert result.hashes == [10, 20, 30]
    assert result.duration_seconds == 1304.0


@pytest.mark.asyncio
async def test_extract_decoder_error_raises_without_ffmpeg():
    """No ffmpeg configured → the decoder-not-found failure propagates as-is."""

    def fake_run(cmd, **kwargs):
        if "-version" in cmd:
            return _make_proc(0, stdout="fpcalc version 1.5.1\n")
        return _make_proc(2, stderr=_DECODER_GAP_STDERR)

    with patch("app.matcher.chromaprint_extractor.subprocess.run", side_effect=fake_run):
        ex = ChromaprintExtractor(fpcalc_path="/fake/fpcalc")  # no ffmpeg → no fallback
        with pytest.raises(RuntimeError, match="Decoder not found"):
            await ex.extract("/fake/dts.mkv")


@pytest.mark.asyncio
async def test_extract_ffmpeg_failure_propagates():
    """If the ffmpeg pre-decode itself fails, surface that error (not a fingerprint)."""

    def fake_run(cmd, **kwargs):
        exe = cmd[0]
        if exe == "/fake/ffmpeg":
            return _make_proc(1, stderr="ffmpeg: boom")
        if "-version" in cmd:
            return _make_proc(0, stdout="fpcalc version 1.5.1\n")
        return _make_proc(2, stderr=_DECODER_GAP_STDERR)

    with patch("app.matcher.chromaprint_extractor.subprocess.run", side_effect=fake_run):
        ex = ChromaprintExtractor(fpcalc_path="/fake/fpcalc", ffmpeg_path="/fake/ffmpeg")
        with pytest.raises(RuntimeError, match="ffmpeg pre-decode failed"):
            await ex.extract("/fake/dts.mkv")


@pytest.mark.asyncio
async def test_missing_fpcalc_binary_wrapped_as_runtimeerror():
    """A non-launchable fpcalc (FileNotFoundError) surfaces as RuntimeError,
    honoring the documented contract instead of leaking an OSError subclass."""

    def fake_run(cmd, **kwargs):
        raise FileNotFoundError(2, "No such file or directory")

    with patch("app.matcher.chromaprint_extractor.subprocess.run", side_effect=fake_run):
        ex = ChromaprintExtractor(fpcalc_path="/no/such/fpcalc")
        with pytest.raises(RuntimeError, match="fpcalc could not be launched"):
            await ex.extract("/fake/movie.mkv")


@pytest.mark.asyncio
async def test_missing_ffmpeg_binary_wrapped_as_runtimeerror():
    """If the ffmpeg fallback binary can't be launched, surface a RuntimeError."""

    def fake_run(cmd, **kwargs):
        if cmd[0] == "/no/such/ffmpeg":
            raise FileNotFoundError(2, "No such file or directory")
        if "-version" in cmd:
            return _make_proc(0, stdout="fpcalc version 1.5.1\n")
        return _make_proc(2, stderr=_DECODER_GAP_STDERR)

    with patch("app.matcher.chromaprint_extractor.subprocess.run", side_effect=fake_run):
        ex = ChromaprintExtractor(fpcalc_path="/fake/fpcalc", ffmpeg_path="/no/such/ffmpeg")
        with pytest.raises(RuntimeError, match="ffmpeg could not be launched"):
            await ex.extract("/fake/dts.mkv")


@pytest.mark.asyncio
async def test_extract_parses_fpcalc_output():
    """extract() parses DURATION and FINGERPRINT from fpcalc -raw output."""
    # We mock asyncio.to_thread so the subprocess never runs.
    fake_output = "DURATION=1304\nFINGERPRINT=112114628,250527685,250521542\n"
    mock_completed = MagicMock()
    mock_completed.returncode = 0
    mock_completed.stdout = fake_output
    mock_completed.stderr = ""

    async def fake_to_thread(func, *args, **kwargs):
        return mock_completed

    with patch("asyncio.to_thread", side_effect=fake_to_thread):
        ex = ChromaprintExtractor(fpcalc_path="/fake/fpcalc")
        result = await ex.extract("/fake/movie.mkv")

    assert result.duration_seconds == 1304.0
    assert result.hashes == [112114628, 250527685, 250521542]


@pytest.mark.asyncio
async def test_extract_raises_on_fpcalc_failure():
    """A non-zero fpcalc exit raises a clean RuntimeError, not subprocess noise."""
    mock_completed = MagicMock()
    mock_completed.returncode = 1
    mock_completed.stdout = ""
    mock_completed.stderr = "fpcalc: ERROR: cannot decode audio"

    async def fake_to_thread(func, *args, **kwargs):
        return mock_completed

    with patch("asyncio.to_thread", side_effect=fake_to_thread):
        ex = ChromaprintExtractor(fpcalc_path="/fake/fpcalc")
        with pytest.raises(RuntimeError, match="fpcalc"):
            await ex.extract("/fake/no-audio.mkv")


@pytest.mark.asyncio
async def test_extract_raises_when_no_fingerprint_line():
    """fpcalc returned 0 but no FINGERPRINT line — should fail loudly."""
    mock_completed = MagicMock()
    mock_completed.returncode = 0
    mock_completed.stdout = "DURATION=10\n"
    mock_completed.stderr = ""

    async def fake_to_thread(func, *args, **kwargs):
        return mock_completed

    with patch("asyncio.to_thread", side_effect=fake_to_thread):
        ex = ChromaprintExtractor(fpcalc_path="/fake/fpcalc")
        with pytest.raises(RuntimeError, match="FINGERPRINT"):
            await ex.extract("/fake/silent.mkv")
