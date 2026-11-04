"""Audio-stream probe and plan (issue #241)."""

import json
import re
import shutil
import subprocess
from types import SimpleNamespace

import pytest

import app.matcher.audio_streams as audio_streams

AudioStream = audio_streams.AudioStream
plan_audio = audio_streams.plan_audio
probe_audio_streams = audio_streams.probe_audio_streams


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


@pytest.mark.unit
class TestPlanDetails:
    def test_usable_languages_exclude_commentary(self):
        p = plan_audio([s(0, "ja"), s(1, "en", commentary=True)], "en")
        assert p.languages == ["ja", "en"]
        assert p.usable_languages == ["ja"]

    def test_auto_index_prefers_default_then_channels(self):
        streams = [s(0, "en", ch=2), s(1, "de", ch=6, default=True)]
        assert plan_audio(streams, "en").auto_index == 1
        streams = [s(0, "en", ch=2), s(1, "de", ch=6)]
        assert plan_audio(streams, "en").auto_index == 1
        streams = [s(0, "en", ch=6), s(1, "de", ch=6)]
        assert plan_audio(streams, "en").auto_index == 0

    def test_follows_auto_pick(self):
        follows_auto_pick = audio_streams.follows_auto_pick

        assert follows_auto_pick(plan_audio([s(0, "en", default=True), s(1, "de")], "en"))
        assert not follows_auto_pick(plan_audio([s(0, "en"), s(1, "de", default=True)], "en"))
        assert follows_auto_pick(plan_audio(None, "en"))


@pytest.mark.unit
@pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed"
)
@pytest.mark.parametrize(
    "dispositions",
    [
        ["-disposition:a:0", "default", "-disposition:a:1", "0"],  # default beats channels
        ["-disposition:a:0", "0", "-disposition:a:1", "default"],
        ["-disposition:a:0", "0", "-disposition:a:1", "0"],  # no default: most channels
    ],
)
def test_auto_index_matches_real_ffmpeg_choice(tmp_path, dispositions):
    """The cache-key and fingerprint guards rest on predicting ffmpeg's own pick."""
    audio_streams._probe_memo.clear()
    source = tmp_path / "two_audio.mkv"
    made = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=f=440:d=2",
            "-f", "lavfi", "-i", "sine=f=880:d=2,aformat=channel_layouts=5.1",
            "-map", "0:a", "-map", "1:a", "-c:a", "pcm_s16le",
            *dispositions, "-y", str(source),
        ],
        capture_output=True,
        text=True,
    )  # fmt: skip
    if made.returncode != 0:
        # CI can leave ffmpeg on PATH but unable to start (exit 127).
        pytest.skip(f"ffmpeg present but unusable here (exit {made.returncode})")

    mapping = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(source), "-vn", "-f", "null", "-"],
        capture_output=True,
        text=True,
    ).stderr
    chosen = re.search(r"Stream #0:(\d+) -> #0:0", mapping)
    assert chosen, mapping

    plan = plan_audio(probe_audio_streams(source), "en")
    assert plan.auto_index == int(chosen.group(1))


class _Matcher:
    """``EpisodeMatcher.audio_plan`` over fixed streams, for ``plan_title_audio``."""

    def __init__(self, streams):
        self.streams = streams

    def audio_plan(self, video_file, language=None):
        from app.core.match_language import current_match_language

        return plan_audio(self.streams, language or current_match_language())


@pytest.mark.unit
class TestPlanTitleAudio:
    """The per-title ladder for a show matched in its own language (#241, phase 2)."""

    @pytest.mark.parametrize(
        "streams,language,verdict,index",
        [
            ([s(0, "en", default=True), s(1, "de")], "de", "match", 1),
            ([s(0, None)], "de", "untagged", 0),
            ([s(0, "en"), s(1, "de", commentary=True)], "en", "match", 0),
            ([s(0, "ja"), s(1, "en")], "en", "match", 1),
        ],
        ids=["show-language", "untagged", "commentary-only-falls-back", "english-dub"],
    )
    def test_german_job(self, streams, language, verdict, index):
        from app.core.match_language import match_language_context

        with match_language_context("de"):
            lang, plan = audio_streams.plan_title_audio(_Matcher(streams), "f.mkv")
        assert (lang, plan.verdict, plan.stream.audio_index) == (language, verdict, index)

    @pytest.mark.parametrize(
        "streams,verdict",
        [([s(0, "ja")], "foreign"), ([], "no_audio")],
    )
    def test_german_job_refused_keeps_its_language(self, streams, verdict):
        from app.core.match_language import match_language_context

        with match_language_context("de"):
            lang, plan = audio_streams.plan_title_audio(_Matcher(streams), "f.mkv")
        assert (lang, plan.verdict) == ("de", verdict)

    def test_unprobed_tries_the_show_language(self):
        from app.core.match_language import match_language_context

        with match_language_context("de"):
            lang, plan = audio_streams.plan_title_audio(_Matcher(None), "f.mkv")
        assert (lang, plan.verdict) == ("de", "unprobed")

    def test_english_job_is_one_plain_call(self):
        calls = []

        class Recording(_Matcher):
            def audio_plan(self, video_file, *args, **kwargs):
                calls.append((video_file, args, kwargs))
                return super().audio_plan(video_file, *args, **kwargs)

        lang, plan = audio_streams.plan_title_audio(Recording([s(0, "ja")]), "f.mkv")
        assert (lang, plan.verdict) == ("en", "foreign")
        assert calls == [("f.mkv", (), {})]
