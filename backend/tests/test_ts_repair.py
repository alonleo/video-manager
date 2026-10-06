from pathlib import Path

import pytest

from app.services.ts_repair import (
    inspect_ts_duration,
    ts_repair_command,
    validate_ts_repair_output,
)


def probe(stream="12.0", container="12.25"):
    return {
        "streams": [{"codec_type": "audio", "duration": "9"}, {"codec_type": "video", "duration": stream}],
        "format": {"duration": container},
    }


def test_detects_first_video_stream_duration_mismatch():
    result = inspect_ts_duration(Path("movie.ts"), probe())
    assert result.needs_repair is True
    assert result.stream_duration == 12
    assert result.format_duration == 12.25
    assert result.difference == pytest.approx(0.25)


def test_small_difference_does_not_need_repair():
    assert inspect_ts_duration(Path("movie.ts"), probe("12", "12.05")).needs_repair is False


def test_missing_duration_is_repairable_and_non_ts_is_rejected():
    result = inspect_ts_duration(Path("movie.ts"), probe("N/A", "12"))
    assert result.needs_repair is True and result.difference is None
    with pytest.raises(ValueError, match="只有 .*ts"):
        inspect_ts_duration(Path("movie.mp4"), probe())


def test_repair_command_copies_every_stream_and_rebuilds_timestamps():
    command = ts_repair_command(Path("input.ts"), Path("output.ts"), "ffmpeg")
    assert command[command.index("-map") + 1] == "0"
    assert command[command.index("-c") + 1] == "copy"
    assert command[command.index("-avoid_negative_ts") + 1] == "make_zero"
    assert "-t" not in command and "-y" not in command


def test_repair_validation_preserves_all_stream_codecs():
    original = {"streams": [
        {"codec_type": "video", "codec_name": "h264"},
        {"codec_type": "audio", "codec_name": "aac"},
        {"codec_type": "subtitle", "codec_name": "dvb_subtitle"},
    ]}
    validate_ts_repair_output(original, original)
    with pytest.raises(ValueError, match="字幕轨道"):
        validate_ts_repair_output(original, {"streams": original["streams"][:2]})
