"""Motion detection on the running stack (Session 14): events are meaningful, not constant.

    uv run pytest -m e2e tests/test_motion.py     # needs the stack up >= WINDOW with detection on

Bounds come from the tuning measurements in docs/notes/14-motion-detection.md: the gate clip has
~7-8 vehicle passages per 55 s loop, the quay clip one container lowering per 9.4 s loop; the
berth edge and quay floor only see camera shake and the loop seam, which must not fire.

Since Session 15, a camera with object detection and check_motion_first (detection.yaml) saves
no motion rows: its motion triggers only open the plugin's frame window. Those cameras are
skipped here; set check_motion_first: false to measure their motion regions again
(docs/notes/15-object-detection.md).
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest

from scripts import detection as det
from services.common.envfile import load_env
from services.common.shinobi_client import ShinobiClient, ShinobiConfig

pytestmark = pytest.mark.e2e

WINDOW = timedelta(minutes=3)
CONFIG = det.load()
LOCK_S = CONFIG.defaults.details["detector_lock_timeout"] / 1000
# region -> (min, max) events per minute over WINDOW
EXPECTED: dict[str, dict[str, tuple[float, float]]] = {
    "cam_gate": {"gate_lane": (3, 12)},
    "cam_quay": {"hoist": (3, 12), "berth_edge": (0, 0), "restricted_quay": (0, 0)},
}


def motion_saved(mid: str) -> bool:
    obj = det.object_settings(CONFIG.cameras[mid.replace("_", "-")].object, CONFIG.defaults.object)
    return not (obj and obj.check_motion_first)


@pytest.fixture(autouse=True)
def _skip_motion_gating_cameras(request: pytest.FixtureRequest) -> None:
    mid = request.node.callspec.params.get("mid") if hasattr(request.node, "callspec") else None
    if mid and not motion_saved(mid):
        pytest.skip(f"{mid}: motion only gates object frames (check_motion_first), no rows")


@pytest.fixture(scope="module")
def events() -> dict[str, list]:
    env = load_env()
    end = datetime.now(UTC)
    with ShinobiClient(ShinobiConfig.from_env(env)) as api:
        return {mid: api.events(mid, start=end - WINDOW, end=end) for mid in EXPECTED}


def test_expected_regions_match_detection_yaml() -> None:
    for cam_id, entry in CONFIG.cameras.items():
        mid = cam_id.replace("-", "_")
        assert set(EXPECTED[mid]) == {r.name for r in entry.regions}


@pytest.mark.parametrize("mid", sorted(EXPECTED))
def test_event_rate_per_region(events: dict[str, list], mid: str) -> None:
    minutes = WINDOW.total_seconds() / 60
    counts = Counter(r for e in events[mid] for r in e.regions)
    for region, (lo, hi) in EXPECTED[mid].items():
        rate = counts[region] / minutes
        assert lo <= rate <= hi, f"{mid}/{region}: {rate:.1f} events/min, want {lo}..{hi}"


@pytest.mark.parametrize("mid", sorted(EXPECTED))
def test_events_come_from_the_sub_stream_detector(events: dict[str, list], mid: str) -> None:
    for e in events[mid]:
        assert (e.plug, e.reason) == ("built-in", "motion")
        assert e.image_size == (640, 360)


@pytest.mark.parametrize("mid", sorted(EXPECTED))
def test_motion_lock_spaces_events(events: dict[str, list], mid: str) -> None:
    times = sorted(e.time for e in events[mid])
    gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:], strict=False)]
    assert all(g >= LOCK_S - 1 for g in gaps), gaps  # whole-second timestamps: allow 1 s
