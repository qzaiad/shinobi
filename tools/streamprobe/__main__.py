"""CLI: python -m tools.streamprobe <url-or-file> [--seconds N] [--json]"""

import argparse
import json
import sys
from dataclasses import asdict

from tools.streamprobe.probe import DEFAULT_SECONDS, ProbeError, ProbeResult, probe


def _fmt(value: float | None, unit: str = "") -> str:
    return "n/a" if value is None else f"{value:.3g}{unit}"


def format_summary(r: ProbeResult) -> str:
    s, g = r.stream, r.gop
    lines = [
        f"source:     {r.source} (first {r.seconds:g} s, {r.frame_count} frames)",
        f"stream:     {s.codec_name} {s.profile or ''} {s.width}x{s.height} {s.pix_fmt} "
        f"@ {_fmt(s.fps, ' fps')}",
        f"keyframes:  {g.keyframe_count}",
        f"GOP frames: min {_fmt(g.min_gop_frames)} / max {_fmt(g.max_gop_frames)} "
        f"/ mean {_fmt(g.mean_gop_frames)}",
        f"GOP secs:   min {_fmt(g.min_gop_seconds)} / max {_fmt(g.max_gop_seconds)} "
        f"/ mean {_fmt(g.mean_gop_seconds)}",
        f"fixed GOP:  {'yes' if g.fixed_gop else 'no'}",
        f"B-frames:   {'yes' if g.has_b_frames else 'no'}",
    ]
    warnings = []
    if g.keyframe_count < 2:
        warnings.append("fewer than 2 keyframes in window: GOP unknown, probe longer")
    elif not g.fixed_gop:
        warnings.append(f"irregular GOP lengths {list(g.gop_lengths)}")
    if g.has_b_frames:
        warnings.append("B-frames present (CCTV streams should use -bf 0)")
    lines += [f"WARNING:    {w}" for w in warnings]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.streamprobe", description=__doc__)
    parser.add_argument("source", help="file path or rtsp:// URL")
    parser.add_argument(
        "--seconds", type=float, default=DEFAULT_SECONDS, help="probe window (default %(default)s)"
    )
    parser.add_argument("--json", action="store_true", help="print JSON instead of a summary")
    args = parser.parse_args(argv)

    try:
        result = probe(args.source, args.seconds)
    except ProbeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(asdict(result), indent=2) if args.json else format_summary(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
