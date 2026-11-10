"""Event filters on the running stack (Session 16): Shinobi does what our emulator predicts.

    uv run pytest -m e2e tests/test_event_filters.py   # needs SHINOBI_TRIGGER_API_KEY (.env)

For every camera with `object.filters` in detection.yaml, synthetic object events are injected
(GET /<key>/motion/<ke>/<mid>?data=..., as if YoloV9 had answered) with boxes built to hit and
to miss each condition, in both orders (box 0 decides, docs/notes/16-detection-quality.md §5).
Expected = tools/detect_eval/shinobi_filters.apply() on the rendered filters, the same function
the offline precision/recall numbers use. A new Shinobi digest that changes filter semantics
fails here. Injected events carry plug "e2e-inject"; accepted ones are saved like real ones.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from scripts import detection as det
from services.common.envfile import load_env
from services.common.shinobi_client import ShinobiClient, ShinobiConfig, TriggerResult
from sim import gen_compose
from tools.detect_eval import shinobi_filters as sf

pytestmark = pytest.mark.e2e

PLUG = "e2e-inject"
LOCK = "Ignored : Motion Lock"


def _box(tag: str = "truck", conf: float = 0.8, x: int = 300, w: int = 120, h: int = 60) -> dict:
    return {"tag": tag, "confidence": conf, "x": x, "y": 200, "width": w, "height": h}


def _hit(cond: dict[str, Any]) -> dict:
    """A box that satisfies one rendered condition (the other fields: a plain big truck)."""
    p1, p2, p3 = cond["p1"], cond["p2"], cond["p3"]
    if p1 == "tag":
        return _box(tag=p3 if p2 in ("===", "indexOf") else "e2e-other")
    v = float(p3)
    value = {">=": v, ">": v + 1, "<": v - 0.01 if v < 1 else v - 1, "<=": v}.get(p2, v)
    if p1 == "confidence":
        return _box(conf=min(max(value, 0.01), 0.99))
    return _box(**{{"width": "w", "height": "h"}.get(p1, "x"): max(int(value), 1)})


def cases() -> Iterator[tuple[str, str, dict, list[dict]]]:
    fleet = gen_compose.load(gen_compose.DEFAULT_CONFIG)
    detection = det.load()
    for cam in fleet.cameras:
        details = det.details_for(detection, cam, fleet.defaults)
        filters = details.get("detector_filters") or {}
        mid = cam.id.replace("-", "_")
        for name, flt in filters.items():
            for n, cond in enumerate(flt["where"]):
                hit, plain = _hit(cond), _box(tag="truck", conf=0.7, x=10)
                for order, boxes in (("hit first", [hit, plain]), ("hit second", [plain, hit])):
                    yield mid, f"{name}[{n}] {order}", filters, boxes
            yield mid, f"{name} plain only", filters, [_box()]


CASES = list(cases())


@pytest.fixture(scope="module")
def clients() -> Iterator[tuple[ShinobiClient, ShinobiClient]]:
    env = load_env()
    if not env.get("SHINOBI_TRIGGER_API_KEY"):
        pytest.skip("SHINOBI_TRIGGER_API_KEY not set: run scripts/shinobi_bootstrap.py apply")
    trig = ShinobiClient(ShinobiConfig.from_env(env, key_var="SHINOBI_TRIGGER_API_KEY"))
    read = ShinobiClient(ShinobiConfig.from_env(env))
    with trig, read:
        yield trig, read


def inject(trig: ShinobiClient, mid: str, boxes: list[dict]) -> TriggerResult:
    """Retry while the monitor's 2 s lock is held by a real detection (filters run first, so a
    filter drop never waits for the lock)."""
    details = {"plug": PLUG, "name": "e2e", "reason": "object", "matrices": boxes,
               "imgWidth": 640, "imgHeight": 360}  # fmt: skip
    for _ in range(10):
        result = trig.trigger(mid, details)
        if result.reason != LOCK:
            return result
        time.sleep(0.7)
    pytest.fail(f"{mid}: motion lock held for 7 s")


@pytest.mark.skipif(not CASES, reason="no object.filters in detection.yaml")
@pytest.mark.parametrize(("mid", "case", "filters", "boxes"), CASES, ids=[c[1] for c in CASES])
def test_shinobi_applies_filters_as_emulated(
    clients: tuple[ShinobiClient, ShinobiClient],
    mid: str,
    case: str,
    filters: dict,
    boxes: list[dict],
) -> None:
    trig, read = clients
    expected = sf.apply(filters, boxes)
    start = datetime.now(UTC) - timedelta(seconds=2)
    result = inject(trig, mid, boxes)
    if expected is None:
        assert not result.triggered, f"{case}: Shinobi kept the event"
        assert result.reason == "Ignored by Event Filters"
        return
    assert result.triggered, f"{case}: dropped ({result.reason}), expected {expected}"
    for _ in range(10):  # the INSERT is asynchronous to the HTTP answer
        rows = [e for e in read.events(mid, start=start) if e.plug == PLUG]
        if rows:
            break
        time.sleep(0.5)
    assert rows, f"{case}: accepted but no Events row"
    saved = [(b.tag, b.confidence) for b in rows[0].boxes]
    assert saved == [(b["tag"], b["confidence"]) for b in expected]
    time.sleep(2.1)  # let the lock this event took expire before the next case
