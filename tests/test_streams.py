"""Health check of every simulated camera stream on the running MediaMTX (e2e).

One test per stream (main and, if configured, sub), derived from sim/cameras.yaml, so a camera
added to the fleet is checked without touching this file. Run with the fleet up:

    scripts/sim-up.sh && uv run pytest -m e2e
    RTSP_BASE=rtsp://otherhost:8554 uv run pytest -m e2e   # other server

A stream is healthy when, within a wall-clock budget, ffprobe receives about WINDOW seconds of
H.264 at the configured size and frame rate. scripts/chaos.sh makes cameras fail on demand:
stop/crash -> connect error, freeze -> timeout, heavy netem loss -> too few frames or too slow.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass

import pytest

from sim import gen_compose as gc
from tools.streamprobe.probe import ProbeError, probe

RTSP_BASE = os.environ.get("RTSP_BASE", "rtsp://localhost:8554").rstrip("/")
WINDOW = 4.0  # seconds of decodable video required (2 GOPs at the 2 s convention)
# A reader joins mid-GOP; frames before the first keyframe can't be decoded and never show up,
# so read one extra GOP (2 s convention) to still get WINDOW seconds of decoded frames.
JOIN = 2.0
# Wall-clock budget = WINDOW + JOIN + this: RTSP connect and ffprobe start-up, but not a stream
# that stalls for seconds (that must fail).
SLACK = 3.0
MIN_FRAME_RATIO = 0.8  # tolerate frames lost at the window edges

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe not on PATH"),
]


@dataclass(slots=True, frozen=True)
class Expected:
    path: str
    width: int
    height: int
    fps: int


def expected_streams() -> list[Expected]:
    fleet = gc.load(gc.DEFAULT_CONFIG)
    out: list[Expected] = []
    for cam in fleet.cameras:
        out.append(Expected(cam.id, cam.width, cam.height, cam.fps))
        sub = gc.resolve_sub(cam, fleet.defaults)
        if sub is not None:
            out.append(Expected(cam.id + gc.SUB_SUFFIX, sub.width, sub.height, sub.fps))
    return out


@pytest.mark.parametrize("exp", expected_streams(), ids=lambda e: e.path)
def test_stream_healthy(exp: Expected) -> None:
    url = f"{RTSP_BASE}/{exp.path}"
    seconds = WINDOW + JOIN
    budget = seconds + SLACK

    start = time.monotonic()
    try:
        r = probe(url, seconds=seconds, timeout=budget)
    except ProbeError as e:
        pytest.fail(f"{exp.path}: down: {e}")
    elapsed = time.monotonic() - start

    min_frames = int(MIN_FRAME_RATIO * exp.fps * WINDOW)
    assert r.frame_count >= min_frames, (
        f"{exp.path}: {r.frame_count} frames in {seconds:g} s, expected >= {min_frames}"
    )

    s = r.stream
    assert s.codec_name == "h264", f"{exp.path}: codec {s.codec_name}"
    assert (s.width, s.height) == (exp.width, exp.height), f"{exp.path}: size {s.width}x{s.height}"

    # ffprobe stops after `seconds` of *stream* time; a stalling stream still gets there,
    # just late. Wall-clock time is what tells "slow" apart from "fine".
    assert elapsed <= budget, f"{exp.path}: {seconds:g} s of video took {elapsed:.1f} s"
    assert not r.gop.has_b_frames, f"{exp.path}: B-frames present"
