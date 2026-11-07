"""Object event statistics per monitor and label: what the detector plugin reports (Session 15).

    uv run python scripts/object_stats.py [--minutes 5 | --since 2026-11-07T13:37:22Z] [MID ...]

Reads the Events table through the API (read-only key). Per monitor ("*" row): object events,
events/min and the plugin's processing time (ms from frame received to answer, incl. queueing:
p50/p90/max). Per label: events containing it, its share of the monitor's events, and the box
score (p50/p90/max; one event counts a label once, at its best box).

How to read it (docs/notes/15-object-detection.md):
- events/min near 60 / lock_s (2 s -> 30/min) = the detector lock is the only limit.
- ms p90 well above one inference (~300 ms on 2 cores) = frames wait in the plugin; growing from
  run to run = the plugin is overloaded (YOLO_MAX_IN_FLIGHT drops frames instead).
- a label nobody expects at this camera (traffic light at a gate) = a false positive to filter.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.motion_stats import LIMIT, percentile  # noqa: E402
from services.common.envfile import load_env  # noqa: E402
from services.common.shinobi_client import (  # noqa: E402
    Event,
    ShinobiClient,
    ShinobiConfig,
    ShinobiError,
)


@dataclass(frozen=True, slots=True)
class Row:
    mid: str
    label: str  # "*" = all object events of the monitor
    events: int
    per_min: float
    share: float  # % of the monitor's object events (100 for "*")
    p50: float  # "*": processing ms; label: box score 0..1
    p90: float
    max: float


def summarize(events: Iterable[Event], minutes: float, mids: Sequence[str] = ()) -> list[Row]:
    """Pure: object events -> one "*" row per monitor plus one row per label. Monitors in
    `mids` without events get a 0 row."""
    per_monitor: dict[str, list[float]] = defaultdict(list)
    per_label: dict[tuple[str, str], list[float]] = defaultdict(list)
    for e in events:
        if e.reason != "object":
            continue
        per_monitor[e.mid].append(float(e.processing_ms or 0))
        best: dict[str, float] = {}
        for b in e.boxes:
            best[b.tag] = max(b.confidence, best.get(b.tag, 0.0))
        for tag, score in best.items():
            per_label[(e.mid, tag)].append(score)
    for mid in mids:
        per_monitor.setdefault(mid, [])

    def row(mid: str, label: str, values: list[float], total: int) -> Row:
        return Row(
            mid=mid,
            label=label,
            events=len(values),
            per_min=len(values) / minutes if minutes > 0 else 0.0,
            share=100 * len(values) / total if total else 0.0,
            p50=percentile(values, 50),
            p90=percentile(values, 90),
            max=max(values, default=0.0),
        )

    rows: list[Row] = []
    for mid in sorted(per_monitor):
        total = len(per_monitor[mid])
        rows.append(row(mid, "*", per_monitor[mid], total))
        labels = [(lbl, v) for (m, lbl), v in per_label.items() if m == mid]
        for label, scores in sorted(labels, key=lambda kv: (-len(kv[1]), kv[0])):
            rows.append(row(mid, label, scores, total))
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mids", nargs="*", help="monitor ids (default: all)")
    when = ap.add_mutually_exclusive_group()
    when.add_argument("--minutes", type=float, default=5.0, help="window ending now")
    when.add_argument("--since", type=datetime.fromisoformat, help="window start (with zone)")
    ap.add_argument("--until", type=datetime.fromisoformat, help="window end (default: now)")
    args = ap.parse_args(argv)

    end = args.until or datetime.now(UTC)
    start = args.since or end - timedelta(minutes=args.minutes)
    if start.tzinfo is None or end.tzinfo is None:
        ap.error("--since/--until need a time zone, e.g. 2026-11-07T13:37:22Z")
    minutes = (end - start).total_seconds() / 60

    try:
        with ShinobiClient(ShinobiConfig.from_env(load_env(REPO / ".env"))) as api:
            mids = args.mids or [m.mid for m in api.monitors()]
            events = [e for mid in mids for e in api.events(mid, start=start, end=end, limit=LIMIT)]
    except ShinobiError as exc:
        print(f"object_stats: {exc}", file=sys.stderr)
        return 1

    print(f"window {start:%H:%M:%S}-{end:%H:%M:%S} UTC ({minutes:.1f} min), object events only")
    print(f"{'MONITOR':<14} {'LABEL':<16} {'EVENTS':>6} {'/MIN':>6} {'SHARE%':>6}   "
          f"{'P50':>6} {'P90':>6} {'MAX':>6}   (* = plugin ms, label = score)")  # fmt: skip
    for r in summarize(events, minutes, mids):
        if r.label == "*":
            print(f"{r.mid:<14} {'*':<16} {r.events:>6} {r.per_min:>6.1f} {'':>6}   "
                  f"{r.p50:>6.0f} {r.p90:>6.0f} {r.max:>6.0f}")  # fmt: skip
        else:
            print(f"{'':<14} {r.label:<16} {r.events:>6} {r.per_min:>6.1f} {r.share:>6.0f}   "
                  f"{r.p50:>6.2f} {r.p90:>6.2f} {r.max:>6.2f}")  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
