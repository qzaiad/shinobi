"""scripts/motion_stats.py: pure aggregation over recorded event payloads."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import motion_stats as ms
from services.common.shinobi_client import Event

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "shinobi" / "events_cam_quay.json"


@pytest.fixture(scope="module")
def events() -> list[Event]:
    return [Event.from_api(e) for e in json.loads(FIXTURE.read_text(encoding="utf-8"))]


def test_percentile_nearest_rank() -> None:
    assert ms.percentile([], 50) == 0.0
    assert ms.percentile([5.0], 90) == 5.0
    assert ms.percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.0
    assert ms.percentile([1.0, 2.0, 3.0, 4.0], 90) == 4.0


def test_summary_has_monitor_row_then_region_rows(events: list[Event]) -> None:
    rows = ms.summarize(events, minutes=1.0, mids=["cam_quay", "cam_idle"])
    by_key = {(r.mid, r.region): r for r in rows}
    assert by_key[("cam_idle", "*")].events == 0
    quay = by_key[("cam_quay", "*")]
    assert quay.events == len(events)
    region_rows = [r for r in rows if r.mid == "cam_quay" and r.region != "*"]
    assert region_rows, "fixture events carry region-tagged boxes"
    # a region is counted at most once per event
    assert all(r.events <= quay.events for r in region_rows)


def test_merged_event_counts_each_region_once() -> None:
    raw = {
        "mid": "m",
        "time": "2026-11-06T20:00:00.000Z",
        "details": {
            "plug": "built-in", "reason": "motion", "name": "multipleRegions", "confidence": 6,
            "matrices": [
                {"tag": "a", "confidence": 3, "x": 0, "y": 0, "width": 1, "height": 1},
                {"tag": "a", "confidence": 5, "x": 0, "y": 0, "width": 1, "height": 1},
                {"tag": "b", "confidence": 7, "x": 0, "y": 0, "width": 1, "height": 1},
            ],
        },
    }  # fmt: skip
    rows = {r.region: r for r in ms.summarize([Event.from_api(raw)], minutes=1.0)}
    assert (rows["a"].events, rows["a"].max) == (1, 5.0)
    assert rows["b"].events == 1
    assert Event.from_api(raw).regions == ("a", "b")


def test_gaps_median_seconds(events: list[Event]) -> None:
    gap = ms.gaps(events)["cam_quay"]
    assert gap >= 2.0  # motion lock: never two accepted events within detector_lock_timeout
