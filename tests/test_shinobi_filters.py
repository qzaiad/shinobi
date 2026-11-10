"""tools/detect_eval/shinobi_filters.py against what the live Shinobi did (Session 16 §5).

Each case was injected into a running monitor (GET /<key>/motion/<ke>/<mid>?data=...) with the
filter rendered by scripts/detection.shinobi_filters; expected = the Events row Shinobi saved.
"""

import pytest

from scripts import detection as det
from tools.detect_eval import shinobi_filters as sf


def box(tag: str, conf: float) -> dict:
    return {"tag": tag, "confidence": conf, "x": 100, "y": 100, "width": 50, "height": 40}


def filters(*specs: dict) -> dict:
    return det.shinobi_filters(tuple(det.EventFilter.model_validate(s) for s in specs))


F1 = filters({"name": "f1", "drop": "on_match", "where": [["tag", "===", "traffic light"]]})
F3 = filters({"name": "f3", "drop": "on_no_match", "where": [["tag", "===", "truck"]]})
# F2 can no longer be written in detection.yaml (refused); built by hand as the UI would store it
F2 = {
    "f2": {
        "id": "f2", "enabled": "1",
        "where": [
            {"p1": "tag", "p2": "===", "p3": "person", "p4": "&&"},
            {"p1": "confidence", "p2": "<", "p3": "0.6", "p4": "&&"},
        ],
        "actions": {"halt": "1"},
    }
}  # fmt: skip


def tags(result: list[dict] | None) -> list[str] | None:
    return None if result is None else [f"{b['tag']} {b['confidence']:g}" for b in result]


@pytest.mark.parametrize(
    ("flt", "boxes", "kept"),
    [
        # A-D: drop on match, one condition
        (F1, [box("truck", 0.8), box("traffic light", 0.6)], ["truck 0.8"]),
        (F1, [box("traffic light", 0.6), box("truck", 0.5)], None),  # box 0 decides: truck lost
        (F1, [box("traffic light", 0.6)], None),
        (
            F1,
            [box("truck", 0.8), box("traffic light", 0.6), box("traffic light", 0.55)],
            ["truck 0.8"],
        ),
        # E-G: drop on match, two conditions = no per-box AND
        (F2, [box("truck", 0.9), box("person", 0.8), box("car", 0.5)], ["truck 0.9"]),
        (F2, [box("person", 0.55), box("truck", 0.5)], None),
        (F2, [box("person", 0.8), box("truck", 0.5)], ["person 0.8"]),  # truck 0.5 deleted
        # H-I: drop on failure = "keep only events that contain a truck", nothing deleted
        (F3, [box("car", 0.9), box("truck", 0.6)], ["car 0.9", "truck 0.6"]),
        (F3, [box("car", 0.9)], None),
    ],
    ids=list("ABCDEFGHI"),
)
def test_matches_live_shinobi(flt: dict, boxes: list[dict], kept: list[str] | None) -> None:
    assert tags(sf.apply(flt, boxes)) == kept


def test_no_filters_keeps_everything() -> None:
    assert tags(sf.apply({}, [box("car", 0.5)])) == ["car 0.5"]


def test_disabled_filter_is_skipped() -> None:
    off = {k: {**v, "enabled": "0"} for k, v in F1.items()}
    assert tags(sf.apply(off, [box("traffic light", 0.6)])) == ["traffic light 0.6"]


def test_strict_equality_like_javascript_on_stored_values() -> None:
    # p3 is rendered as a string, but Shinobi stores "50" as the number 50: === matches
    flt = filters({"name": "w", "drop": "on_match", "where": [["width", "===", 50]]})
    assert sf.apply(flt, [box("car", 0.9)]) is None  # width 50 === 50 -> dropped
    assert sf.stored("50") == 50 and sf.stored("traffic light") == "traffic light"
    assert sf.stored("true") == "true"  # only numbers are converted here
    assert not sf._matches(50, "===", "50")  # a string reference never equals a number
    assert sf._matches(50, "===", 50.0)


def test_or_and_precedence() -> None:
    assert sf._evaluate(["true", "||", "false", "&&", "false"]) is True
    assert sf._evaluate(["(", "true", "||", "false", ")", "&&", "false"]) is False


def test_non_box_field_is_refused() -> None:
    flt = {"t": {"where": [{"p1": "time", "p2": ">", "p3": "12"}], "actions": {"halt": "1"}}}
    with pytest.raises(ValueError, match="not emulated"):
        sf.apply(flt, [box("car", 0.5)])
