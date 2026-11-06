"""Motion event statistics per monitor and region: the tuning loop of Session 14.

    uv run python scripts/motion_stats.py [--minutes 5 | --since 2026-11-06T20:28:00Z] [MID ...]

Reads the Events table through the API (read-only key) and prints, per monitor and per region:
events, events/min, and the confidence distribution (p50/p90/max). "Region" = the box tags of
an event: with detectorMergePamRegionTriggers (conf.json) one event can carry several regions.

How to read it (docs/notes/14-motion-detection.md):
- events/min near 60 / lock_s (default 2 s -> 30/min) = the motion lock is the only limit,
  i.e. the region fires on (almost) every detector frame: noise, or a far too low sensitivity.
- p50 of a noise-only region tells you where its sensitivity must at least go.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from services.common.envfile import load_env  # noqa: E402
from services.common.shinobi_client import (  # noqa: E402
    Event,
    ShinobiClient,
    ShinobiConfig,
    ShinobiError,
)

LIMIT = 100_000  # Shinobi answers at most 500 rows unless a limit is given


@dataclass(frozen=True, slots=True)
class Row:
    mid: str
    region: str  # "*" = all events of the monitor
    events: int
    per_min: float
    p50: float
    p90: float
    max: float


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile (q in 0..100); 0.0 for no values."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, round(q / 100 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def summarize(events: Iterable[Event], minutes: float, mids: Sequence[str] = ()) -> list[Row]:
    """Pure: events -> one "*" row per monitor (event confidence) plus one row per region
    (box confidence = that region's blob %). Monitors in `mids` without events get a 0 row."""
    per_monitor: dict[str, list[float]] = defaultdict(list)
    per_region: dict[tuple[str, str], list[float]] = defaultdict(list)
    for e in events:
        per_monitor[e.mid].append(e.confidence)
        boxes_by_tag: dict[str, float] = {}
        for b in e.boxes:  # a region can have several blobs: count it once, at its largest
            boxes_by_tag[b.tag] = max(b.confidence, boxes_by_tag.get(b.tag, 0.0))
        for tag, conf in boxes_by_tag.items():
            per_region[(e.mid, tag)].append(conf)
    for mid in mids:
        per_monitor.setdefault(mid, [])

    def row(mid: str, region: str, conf: list[float]) -> Row:
        return Row(
            mid=mid,
            region=region,
            events=len(conf),
            per_min=len(conf) / minutes if minutes > 0 else 0.0,
            p50=percentile(conf, 50) if conf else 0.0,
            p90=percentile(conf, 90),
            max=max(conf, default=0.0),
        )

    rows: list[Row] = []
    for mid in sorted(per_monitor):
        rows.append(row(mid, "*", per_monitor[mid]))
        for (m, region), conf in sorted(per_region.items()):
            if m == mid:
                rows.append(row(mid, region, conf))
    return rows


def gaps(events: Iterable[Event]) -> dict[str, float]:
    """Median seconds between consecutive events per monitor (0.0 with < 2 events)."""
    times: dict[str, list[datetime]] = defaultdict(list)
    for e in events:
        times[e.mid].append(e.time)
    out: dict[str, float] = {}
    for mid, ts in times.items():
        ts.sort()
        deltas = [(b - a).total_seconds() for a, b in zip(ts, ts[1:], strict=False)]
        out[mid] = statistics.median(deltas) if deltas else 0.0
    return out


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
        ap.error("--since/--until need a time zone, e.g. 2026-11-06T20:28:00Z")
    minutes = (end - start).total_seconds() / 60

    try:
        with ShinobiClient(ShinobiConfig.from_env(load_env(REPO / ".env"))) as api:
            mids = args.mids or [m.mid for m in api.monitors()]
            events = [
                e
                for mid in mids
                for e in api.events(mid, start=start, end=end, limit=LIMIT)
                if e.reason == "motion"
            ]
    except ShinobiError as exc:
        print(f"motion_stats: {exc}", file=sys.stderr)
        return 1

    print(f"window {start:%H:%M:%S}-{end:%H:%M:%S} UTC ({minutes:.1f} min), motion events only")
    print(f"{'MONITOR':<14} {'REGION':<18} {'EVENTS':>6} {'/MIN':>6} {'P50%':>6} {'P90%':>6} "
          f"{'MAX%':>6}  {'GAP s':>5}")  # fmt: skip
    median_gap = gaps(events)
    for r in summarize(events, minutes, mids):
        gap = f"{median_gap.get(r.mid, 0.0):5.1f}" if r.region == "*" else ""
        print(f"{r.mid if r.region == '*' else '':<14} {r.region:<18} {r.events:>6} "
              f"{r.per_min:>6.1f} {r.p50:>6.1f} {r.p90:>6.1f} {r.max:>6.1f}  {gap}")  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
