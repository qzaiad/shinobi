"""Object detection on the running stack (Session 15): plugin connected, labelled events arrive.

    uv run pytest -m e2e tests/test_object_detection.py   # stack up >= WINDOW, plugin connected

Bounds from docs/notes/15-object-detection.md (2 fps, check motion first, 2 s lock): gate
~9.6 object events/min (truck, car), quay ~13/min (truck, person); plugin time p90 ~400 ms.
The latency bound guards against the unbounded queue measured without load shedding
(services/yolo-plugin/backpressure.js): there `time` grew to 52 s within 3 min.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest

from scripts import detection as det
from scripts.motion_stats import percentile
from services.common.envfile import load_env
from services.common.shinobi_client import Event, ShinobiClient, ShinobiConfig

pytestmark = pytest.mark.e2e

WINDOW = timedelta(minutes=3)
CONFIG = det.load()
# mid -> (min events/min, labels of which at least one must appear)
EXPECTED: dict[str, tuple[float, set[str]]] = {
    "cam_gate": (3, {"truck", "car"}),
    "cam_quay": (3, {"truck"}),
}
MAX_P90_MS = 2000  # one inference ~300 ms; seconds = frames queue in the plugin


@pytest.fixture(scope="module")
def api() -> ShinobiClient:
    with ShinobiClient(ShinobiConfig.from_env(load_env())) as client:
        yield client


@pytest.fixture(scope="module")
def events(api: ShinobiClient) -> dict[str, list[Event]]:
    end = datetime.now(UTC)
    return {mid: api.events(mid, start=end - WINDOW, end=end) for mid in EXPECTED}


def test_expected_cameras_match_detection_yaml() -> None:
    with_object = {c.replace("-", "_") for c, e in CONFIG.cameras.items() if e.object}
    assert set(EXPECTED) == with_object


def test_every_configured_plugin_is_connected(api: ShinobiClient) -> None:
    wanted = set(CONFIG.defaults.object.plugins) | {
        p
        for e in CONFIG.cameras.values()
        if e.object and e.object.plugins
        for p in e.object.plugins
    }
    assert wanted <= set(api.connected_plugins())


@pytest.mark.parametrize("mid", sorted(EXPECTED))
def test_labelled_object_events_arrive(events: dict[str, list[Event]], mid: str) -> None:
    minimum, labels = EXPECTED[mid]
    objects = [e for e in events[mid] if e.reason == "object"]
    rate = len(objects) / (WINDOW.total_seconds() / 60)
    assert rate >= minimum, f"{mid}: {rate:.1f} object events/min, want >= {minimum}"
    seen = Counter(label for e in objects for label in e.labels)
    assert labels & set(seen), f"{mid}: labels {dict(seen)}, want one of {labels}"
    for e in objects:
        assert e.plug == "YoloV9" and e.image_size == (640, 360)  # sub-stream JPEGs
        assert all(0.5 <= b.confidence <= 1 for b in e.boxes)  # plugin CONFIDENCE 0.5


@pytest.mark.parametrize("mid", sorted(EXPECTED))
def test_plugin_answers_without_backlog(events: dict[str, list[Event]], mid: str) -> None:
    ms = [float(e.processing_ms) for e in events[mid] if e.processing_ms is not None]
    assert ms, f"{mid}: no object events"
    assert percentile(ms, 90) <= MAX_P90_MS, f"{mid}: plugin time p90 {percentile(ms, 90)} ms"


@pytest.mark.parametrize("mid", sorted(EXPECTED))
def test_motion_first_saves_no_motion_rows(events: dict[str, list[Event]], mid: str) -> None:
    """detector_use_motion=1: a motion trigger opens the frame window instead of being saved
    (libs/events/utils.js triggerEvent)."""
    assert not [e for e in events[mid] if e.reason == "motion"]
