"""scripts/object_stats.py: pure aggregation over recorded object event payloads."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import object_stats as os_
from services.common.shinobi_client import Event

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "shinobi"


def load(name: str) -> list[Event]:
    raw = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return [Event.from_api(e) for e in raw]


@pytest.fixture(scope="module")
def events() -> list[Event]:
    return load("events_cam_gate_object")


def test_monitor_row_then_labels_most_frequent_first(events: list[Event]) -> None:
    rows = os_.summarize(events, minutes=2.0, mids=["cam_gate", "cam_idle"])
    by_key = {(r.mid, r.label): r for r in rows}
    assert by_key[("cam_idle", "*")].events == 0
    gate = by_key[("cam_gate", "*")]
    assert (gate.events, gate.per_min) == (6, 3.0)
    assert 0 < gate.p50 <= gate.p90 <= gate.max  # plugin ms
    labels = [r for r in rows if r.mid == "cam_gate" and r.label != "*"]
    assert [r.events for r in labels] == sorted((r.events for r in labels), reverse=True)
    assert {r.label for r in labels} == {lbl for e in events for lbl in e.labels}


def test_label_counted_once_per_event_at_best_score() -> None:
    def event(*boxes: tuple[str, float]) -> Event:
        matrices = [
            {"tag": t, "confidence": c, "x": 0, "y": 0, "width": 1, "height": 1} for t, c in boxes
        ]
        details = {"reason": "object", "time": 300, "matrices": matrices}
        return Event.from_api(
            {"mid": "cam_gate", "time": "2026-11-07T13:42:19Z", "details": details}
        )

    events = [event(("car", 0.6), ("car", 0.9), ("truck", 0.5)), event(("truck", 0.7))]
    rows = {(r.mid, r.label): r for r in os_.summarize(events, minutes=1.0)}
    car, truck = rows[("cam_gate", "car")], rows[("cam_gate", "truck")]
    assert (car.events, car.max, car.share) == (1, 0.9, 50.0)  # two cars, one event
    assert (truck.events, truck.p50, truck.max) == (2, 0.5, 0.7)
    assert rows[("cam_gate", "*")].p50 == 300.0


def test_motion_events_are_ignored() -> None:
    assert os_.summarize(load("events_cam_quay"), minutes=1.0) == []
