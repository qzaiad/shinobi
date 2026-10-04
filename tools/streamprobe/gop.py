"""GOP statistics from a decoded frame sequence (pure, no I/O)."""

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean


@dataclass(slots=True, frozen=True)
class Frame:
    pict_type: str  # "I", "P", "B", or "?" when ffprobe can't tell
    key_frame: bool
    pts_time: float | None  # None when ffprobe reports N/A


@dataclass(slots=True, frozen=True)
class GopStats:
    keyframe_count: int
    # One entry per *complete* GOP (keyframe to next keyframe). Frames before the
    # first keyframe (e.g. joining an RTSP stream mid-GOP) and after the last one
    # are partial and ignored.
    gop_lengths: tuple[int, ...]
    gop_seconds: tuple[float, ...]  # from keyframe PTS deltas; empty if any PTS is missing
    min_gop_frames: int | None
    max_gop_frames: int | None
    mean_gop_frames: float | None
    min_gop_seconds: float | None
    max_gop_seconds: float | None
    mean_gop_seconds: float | None
    has_b_frames: bool
    fixed_gop: bool  # at least one complete GOP and all have the same frame count


def compute_gop_stats(frames: Sequence[Frame]) -> GopStats:
    key_idx = [i for i, f in enumerate(frames) if f.key_frame]
    lengths = tuple(b - a for a, b in zip(key_idx, key_idx[1:], strict=False))

    key_pts = [p for i in key_idx if (p := frames[i].pts_time) is not None]
    seconds: tuple[float, ...] = ()
    if lengths and len(key_pts) == len(key_idx):
        seconds = tuple(b - a for a, b in zip(key_pts, key_pts[1:], strict=False))

    return GopStats(
        keyframe_count=len(key_idx),
        gop_lengths=lengths,
        gop_seconds=seconds,
        min_gop_frames=min(lengths, default=None),
        max_gop_frames=max(lengths, default=None),
        mean_gop_frames=fmean(lengths) if lengths else None,
        min_gop_seconds=min(seconds, default=None),
        max_gop_seconds=max(seconds, default=None),
        mean_gop_seconds=fmean(seconds) if seconds else None,
        has_b_frames=any(f.pict_type == "B" for f in frames),
        fixed_gop=len(set(lengths)) == 1,
    )
