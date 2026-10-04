"""Run ffprobe on a file or RTSP URL and parse the first video stream."""

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from tools.streamprobe.gop import Frame, GopStats, compute_gop_stats

DEFAULT_SECONDS = 10.0


class ProbeError(RuntimeError):
    """ffprobe missing, failed, timed out, or returned no video stream."""


@dataclass(slots=True, frozen=True)
class StreamInfo:
    codec_name: str | None
    profile: str | None
    width: int | None
    height: int | None
    pix_fmt: str | None
    fps: float | None  # from r_frame_rate; None for "0/0"


@dataclass(slots=True, frozen=True)
class ProbeResult:
    source: str
    seconds: float
    stream: StreamInfo
    frame_count: int
    gop: GopStats


def build_command(source: str, seconds: float = DEFAULT_SECONDS) -> list[str]:
    cmd = ["ffprobe", "-v", "error"]
    if source.startswith(("rtsp://", "rtsps://")):
        # Interleave RTP in the RTSP TCP connection: no UDP port negotiation/NAT
        # issues and no packet loss that would corrupt the frame statistics.
        cmd += ["-rtsp_transport", "tcp"]
    cmd += [
        "-select_streams", "v:0",
        "-show_entries",
        "stream=codec_name,profile,width,height,pix_fmt,r_frame_rate"
        ":frame=pict_type,pts_time,key_frame",
        "-read_intervals", f"%+{seconds:g}",
        "-of", "json",
        source,
    ]  # fmt: skip
    return cmd


def probe(
    source: str, seconds: float = DEFAULT_SECONDS, timeout: float | None = None
) -> ProbeResult:
    """Probe `source`. `timeout` defaults to `seconds` plus a margin for connect/decode."""
    cmd = build_command(source, seconds)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",  # ffprobe JSON is UTF-8 regardless of the locale
            errors="replace",
            check=False,
            timeout=timeout if timeout is not None else seconds + 15.0,
        )
    except FileNotFoundError as e:
        raise ProbeError("ffprobe not found on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise ProbeError(f"ffprobe timed out after {e.timeout:g} s on {source}") from e
    if proc.returncode != 0:
        raise ProbeError(f"ffprobe exited {proc.returncode}: {proc.stderr.strip()}")
    return parse_output(source, seconds, json.loads(proc.stdout))


def parse_output(source: str, seconds: float, data: dict[str, Any]) -> ProbeResult:
    streams = data.get("streams") or []
    if not streams:
        raise ProbeError(f"no video stream in {source}")
    frames = [_parse_frame(f) for f in data.get("frames") or []]
    return ProbeResult(
        source=source,
        seconds=seconds,
        stream=_parse_stream(streams[0]),
        frame_count=len(frames),
        gop=compute_gop_stats(frames),
    )


def _parse_stream(s: dict[str, Any]) -> StreamInfo:
    return StreamInfo(
        codec_name=s.get("codec_name"),
        profile=s.get("profile"),
        width=s.get("width"),
        height=s.get("height"),
        pix_fmt=s.get("pix_fmt"),
        fps=_parse_rate(s.get("r_frame_rate")),
    )


def _parse_frame(f: dict[str, Any]) -> Frame:
    return Frame(
        pict_type=f.get("pict_type", "?"),
        key_frame=bool(f.get("key_frame", 0)),
        pts_time=_to_float(f.get("pts_time")),
    )


def _parse_rate(rate: str | None) -> float | None:
    try:
        return float(Fraction(rate)) if rate else None
    except (ValueError, ZeroDivisionError):
        return None


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
