"""Preview clip generation for the review page.

A rip is not something a browser can play — a DVD track is MPEG-2 video with AC3
audio — so previews are transcoded on demand and streamed from ffmpeg's stdout.
These tests pin the parts that decide *what* runs: the seek-before-input ordering
that makes a preview appear instantly, the fragmented-MP4 flags that make a pipe
work at all, and the copy fast path for sources that are already playable.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.core.preview import (
    DEFAULT_CLIP_SECONDS,
    MAX_CLIP_SECONDS,
    MediaProbe,
    build_preview_command,
    probe_media,
    resolve_ffmpeg_binary,
)

SOURCE = Path("/staging/title_t01.mkv")


def _index(cmd: list[str], flag: str) -> int:
    return cmd.index(flag)


@pytest.mark.unit
class TestBuildPreviewCommand:
    def test_seeks_before_the_input(self):
        # -ss AFTER -i decodes everything up to the offset: a preview at 20min
        # would take as long as the offset instead of appearing at once.
        cmd = build_preview_command("ffmpeg", SOURCE, 300, 20, copy_streams=False)
        assert _index(cmd, "-ss") < _index(cmd, "-i")

    def test_writes_fragmented_mp4_to_stdout(self):
        # A normal MP4 puts its index at the end of the file, which a pipe can
        # never go back and write — the clip would be unplayable.
        cmd = build_preview_command("ffmpeg", SOURCE, 0, 20, copy_streams=False)
        assert cmd[-1] == "pipe:1"
        assert "frag_keyframe+empty_moov+default_base_moof" in cmd
        assert cmd[_index(cmd, "-f") + 1] == "mp4"

    def test_transcodes_when_the_source_is_not_browser_playable(self):
        cmd = build_preview_command("ffmpeg", SOURCE, 0, 20, copy_streams=False)
        assert "libx264" in cmd
        assert "aac" in cmd

    def test_copies_when_the_source_already_plays(self):
        cmd = build_preview_command("ffmpeg", SOURCE, 0, 20, copy_streams=True)
        assert "libx264" not in cmd
        assert cmd[_index(cmd, "-c") + 1] == "copy"

    def test_takes_one_video_and_one_audio_stream_and_no_subtitles(self):
        # A DVD rip carries several audio tracks and bitmap subtitles, which MP4
        # cannot hold at all — mapping everything would fail the mux.
        cmd = build_preview_command("ffmpeg", SOURCE, 0, 20, copy_streams=False)
        assert "-sn" in cmd
        assert cmd[_index(cmd, "-map") + 1] == "0:v:0"

    def test_negative_start_is_clamped(self):
        cmd = build_preview_command("ffmpeg", SOURCE, -5, 20, copy_streams=False)
        assert cmd[_index(cmd, "-ss") + 1] == "0.000"

    def test_clip_bounds_are_sane(self):
        assert 0 < DEFAULT_CLIP_SECONDS <= MAX_CLIP_SECONDS


@pytest.mark.unit
class TestMediaProbe:
    def test_dvd_rip_cannot_be_copied(self):
        # The finding that shapes this whole feature: no browser decodes either
        # stream, so remuxing is not an option.
        probe = MediaProbe(
            duration=1327.0, video_codec="mpeg2video", audio_codec="ac3", chapters=[]
        )
        assert probe.can_copy is False

    def test_h264_aac_source_can_be_copied(self):
        probe = MediaProbe(duration=1327.0, video_codec="h264", audio_codec="aac", chapters=[])
        assert probe.can_copy is True

    async def test_parses_duration_codecs_and_chapters(self):
        payload = json.dumps(
            {
                "format": {"duration": "1327.759000"},
                "streams": [
                    {"codec_type": "video", "codec_name": "mpeg2video"},
                    {"codec_type": "audio", "codec_name": "ac3"},
                    {"codec_type": "subtitle", "codec_name": "dvd_subtitle"},
                ],
                "chapters": [
                    {"start_time": "0.000000", "end_time": "31.164467", "tags": {"title": "Ch 1"}},
                    {"start_time": "31.164467", "end_time": "442.642200"},
                ],
            }
        ).encode()

        proc = AsyncMock()
        proc.communicate.return_value = (payload, b"")
        proc.returncode = 0
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            probe = await probe_media(SOURCE, "ffprobe")

        assert probe.duration == pytest.approx(1327.759)
        assert probe.video_codec == "mpeg2video"
        assert probe.audio_codec == "ac3"
        assert [c.title for c in probe.chapters] == ["Ch 1", "Chapter 02"]
        assert probe.chapters[1].start == pytest.approx(31.164467)

    async def test_probe_failure_degrades_instead_of_raising(self):
        proc = AsyncMock()
        proc.communicate.return_value = (b"", b"boom")
        proc.returncode = 1
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            probe = await probe_media(SOURCE, "ffprobe")

        assert probe.chapters == []
        assert probe.can_copy is False  # unknown codecs → re-encode, never assume

    async def test_unparseable_output_degrades_instead_of_raising(self):
        proc = AsyncMock()
        proc.communicate.return_value = (b"not json", b"")
        proc.returncode = 0
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            probe = await probe_media(SOURCE, "ffprobe")

        assert probe.duration is None
        assert probe.chapters == []


@pytest.mark.unit
class TestResolveFfmpegBinary:
    def test_finds_ffprobe_beside_a_configured_ffmpeg(self, tmp_path):
        # Users who set a path point at the folder holding both binaries.
        ffmpeg = tmp_path / "ffmpeg.exe"
        ffprobe = tmp_path / "ffprobe.exe"
        ffmpeg.write_text("")
        ffprobe.write_text("")
        assert resolve_ffmpeg_binary(str(ffmpeg), "ffprobe") == str(ffprobe)

    def test_returns_the_configured_ffmpeg_itself(self, tmp_path):
        ffmpeg = tmp_path / "ffmpeg.exe"
        ffmpeg.write_text("")
        assert resolve_ffmpeg_binary(str(ffmpeg), "ffmpeg") == str(ffmpeg)

    def test_falls_back_to_path_when_unconfigured(self, tmp_path):
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            assert resolve_ffmpeg_binary("", "ffmpeg") == "/usr/bin/ffmpeg"

    def test_ignores_a_configured_path_that_does_not_exist(self, tmp_path):
        missing = str(tmp_path / "nope" / "ffmpeg.exe")
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            assert resolve_ffmpeg_binary(missing, "ffmpeg") == "/usr/bin/ffmpeg"
