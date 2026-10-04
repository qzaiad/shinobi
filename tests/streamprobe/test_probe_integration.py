"""Integration test: encode a real H.264 clip with known GOP settings, then probe it.

Unlike test_gop.py (synthetic Frame lists), this exercises the whole path:
ffmpeg encode -> ffprobe subprocess -> JSON parsing -> compute_gop_stats.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from tools.streamprobe.probe import probe

# Applies to every test in this module:
# - `integration` marker: deselect with `pytest -m "not integration"`.
# - skipif: report as skipped (not failed) on machines without ffmpeg/ffprobe.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
        reason="ffmpeg/ffprobe not on PATH",
    ),
]


def test_probe_fixed_gop_without_b_frames(tmp_path: Path) -> None:
    # tmp_path: pytest fixture, a fresh per-test directory (auto-cleaned later).
    clip = tmp_path / "testsrc2.mp4"

    # Encode 4 s of synthetic video with CCTV-style settings. The list form runs
    # ffmpeg directly (no shell), so no quoting/escaping issues.
    # It's the stdlib way to run an external program and wait for it to finish.
    # blocks until the program exits. For long-running processes you want to stream from, like a camera publisher in later sessions, you'd use 
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",  # only print errors; overwrite output
            # lavfi = libavfilter virtual input; testsrc2 generates moving test frames.
            # 25 fps x 4 s = exactly 100 frames.
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=4",
            # H.264 in 4:2:0, the format real IP cameras send.
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            # Keyframe every 25 frames (1 s GOP); no B-frames, so PTS order == DTS order.
            "-g", "25", "-bf", "0",
            "-sc_threshold", "0",  # no extra keyframes on scene cuts
            str(clip),
        ],
        check=True,  # raise CalledProcessError if ffmpeg exits non-zero
        timeout=60,  # raise TimeoutExpired instead of hanging the test run
    )  # fmt: skip

    # Probe window (10 s) is longer than the clip, so all 100 frames are read.
    # probe() runs ffprobe on a file or RTSP URL for a time window, then turns ffprobe's JSON output into a typed ProbeResult with the stream metadata and GOP statistics. It never decodes video itself; all the media work happens in ffprobe.

    r = probe(str(clip), seconds=10)

    # Stream metadata matches what we encoded.
    assert r.stream.codec_name == "h264"
    assert (r.stream.width, r.stream.height) == (320, 240)
    assert r.stream.fps == pytest.approx(25.0)  # parsed from r_frame_rate "25/1"
    assert r.frame_count == 100

    # Keyframes at frames 0, 25, 50, 75 -> 4 keyframes but only 3 complete GOPs;
    # frames 75..99 are a trailing partial GOP (no closing keyframe), so ignored.
    assert r.gop.keyframe_count == 4
    assert r.gop.gop_lengths == (25, 25, 25)
    assert r.gop.fixed_gop
    assert r.gop.mean_gop_seconds == pytest.approx(1.0)  # 25 frames / 25 fps, from PTS
    assert not r.gop.has_b_frames
