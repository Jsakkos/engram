"""Audio-stream probe and plan (issue #241)."""

import json
import subprocess
from types import SimpleNamespace

import pytest

import app.matcher.audio_streams as audio_streams
from app.matcher.audio_streams import AudioStream, plan_audio, probe_audio_streams


def s(i, lang, *, title="", default=False, commentary=False, ch=2):
    return AudioStream(i, lang, title, ch, default, commentary)


@pytest.mark.unit
class TestPlanAudio:
    def test_match_prefers_default_among_language_streams(self):
        p = plan_audio([s(0, "ja", default=True), s(1, "en"), s(2, "en", default=True)], "en")
        assert (p.verdict, p.stream.audio_index) == ("match", 2)

    def test_match_first_language_stream_when_none_default(self):
        assert plan_audio([s(0, "ja", default=True), s(1, "en")], "en").stream.audio_index == 1

    def test_untagged_used_when_no_language_stream(self):
        p = plan_audio([s(0, None), s(1, "fr")], "en")
        assert (p.verdict, p.stream.audio_index) == ("untagged", 0)

    def test_untagged_commentary_is_not_used(self):
        p = plan_audio([s(0, None, commentary=True), s(1, "fr")], "en")
        assert p.verdict == "foreign"

    def test_foreign_when_every_usable_stream_is_other_language(self):
        p = plan_audio([s(0, "ja"), s(1, "en", commentary=True)], "en")
        assert p.verdict == "foreign"
        assert p.stream is None
        assert p.languages == ["ja", "en"]

    def test_no_audio_and_unprobed(self):
        assert plan_audio([], "en").verdict == "no_audio"
        unprobed = plan_audio(None, "en")
        assert unprobed.verdict == "unprobed"
        assert unprobed.stream is None


def _ffprobe_json(*streams):
    return json.dumps({"streams": list(streams)})


def _fake_run(stdout, returncode=0):
    def run(cmd, **kwargs):
        run.calls.append(cmd)
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")

    run.calls = []
    return run


@pytest.mark.unit
class TestProbeAudioStreams:
    @pytest.fixture(autouse=True)
    def _clear_memo(self):
        audio_streams._probe_memo.clear()

    def test_probe_parses_ffprobe_json(self, monkeypatch, tmp_path):
        f = tmp_path / "a.mkv"
        f.write_bytes(b"x")
        run = _fake_run(
            _ffprobe_json(
                {
                    "index": 1,
                    "channels": 6,
                    "tags": {"language": "jpn"},
                    "disposition": {"default": 1, "comment": 0, "visual_impaired": 0},
                },
                {
                    "index": 2,
                    "channels": 2,
                    "tags": {"language": "eng", "title": "Director's Commentary"},
                    "disposition": {"default": 0, "comment": 0, "visual_impaired": 0},
                },
                {
                    "index": 3,
                    "channels": 2,
                    "tags": {"language": "eng"},
                    "disposition": {"default": 0, "comment": 0, "visual_impaired": 1},
                },
                {"index": 4, "channels": 2},
            )
        )
        monkeypatch.setattr(audio_streams.subprocess, "run", run)

        streams = probe_audio_streams(f)

        assert streams == [
            AudioStream(0, "ja", "", 6, True, False),
            AudioStream(1, "en", "Director's Commentary", 2, False, True),
            AudioStream(2, "en", "", 2, False, True),  # audio description
            AudioStream(3, None, "", 2, False, False),
        ]
        assert run.calls[0][0] == "ffprobe"
        assert "-select_streams" in run.calls[0]

    def test_probe_is_memoized_per_file(self, monkeypatch, tmp_path):
        f = tmp_path / "a.mkv"
        f.write_bytes(b"x")
        run = _fake_run(_ffprobe_json({"index": 1, "channels": 2}))
        monkeypatch.setattr(audio_streams.subprocess, "run", run)
        probe_audio_streams(f)
        probe_audio_streams(f)
        assert len(run.calls) == 1

    def test_probe_returns_none_when_ffprobe_missing(self, monkeypatch, tmp_path):
        f = tmp_path / "a.mkv"
        f.write_bytes(b"x")

        def missing(*a, **k):
            raise FileNotFoundError("ffprobe")

        monkeypatch.setattr(audio_streams.subprocess, "run", missing)
        assert probe_audio_streams(f) is None

    @pytest.mark.parametrize("stdout,rc", [("not json", 0), ("{}", 1), ('{"streams": 5}', 0)])
    def test_probe_returns_none_on_bad_output(self, monkeypatch, tmp_path, stdout, rc):
        f = tmp_path / "a.mkv"
        f.write_bytes(b"x")
        monkeypatch.setattr(audio_streams.subprocess, "run", _fake_run(stdout, rc))
        assert probe_audio_streams(f) is None

    def test_probe_returns_none_on_timeout(self, monkeypatch, tmp_path):
        f = tmp_path / "a.mkv"
        f.write_bytes(b"x")

        def slow(*a, **k):
            raise subprocess.TimeoutExpired("ffprobe", 30)

        monkeypatch.setattr(audio_streams.subprocess, "run", slow)
        assert probe_audio_streams(f) is None

    def test_missing_file_is_none(self, tmp_path):
        assert probe_audio_streams(tmp_path / "gone.mkv") is None
