"""Motion detection as code (scripts/detection.py) and its place in provision render().

uv run pytest tests/test_detection.py
"""

from __future__ import annotations

from typing import Any

import pytest

from scripts import detection as det
from scripts import provision_monitors as pm
from sim import gen_compose

SQUARE = [[0, 0], [1, 0], [1, 1], [0, 1]]


@pytest.fixture(scope="module")
def fleet() -> gen_compose.Fleet:
    return gen_compose.load(pm.DEFAULT_CONFIG)


def cam(fleet: gen_compose.Fleet, cam_id: str) -> gen_compose.Camera:
    return next(c for c in fleet.cameras if c.id == cam_id)


def config(**camera: Any) -> det.Detection:
    entry = {"regions": [{"name": "r", "points": SQUARE}], **camera}
    return det.parse({"version": 1, "cameras": {"cam-gate": entry}})


def test_committed_detection_yaml_is_valid(fleet: gen_compose.Fleet) -> None:
    detection = det.load()
    det.check_cameras(detection, fleet)


def test_sub_input_is_ffmpeg_input_1_at_sub_size(fleet: gen_compose.Fleet) -> None:
    d = det.details_for(config(), cam(fleet, "cam-gate"), fleet.defaults)
    assert d["input_maps"][0]["fulladdress"] == "rtsp://mediamtx:8554/cam-gate-sub"
    assert d["input_maps"][0]["rtsp_transport"] == "tcp"
    assert d["input_map_choices"]["detector"] == [{"map": "1:0"}]
    assert d["input_map_choices"]["record"] == []  # recording stays on input 0 (primary_input)
    assert (d["detector_scale_x"], d["detector_scale_y"]) == (640, 360)
    assert d["detector"] == d["detector_pam"] == d["detector_send_frames"] == 1


def test_main_input_needs_no_input_map(fleet: gen_compose.Fleet) -> None:
    d = det.details_for(config(input="main"), cam(fleet, "cam-gate"), fleet.defaults)
    assert "input_maps" not in d
    assert (d["detector_scale_x"], d["detector_scale_y"]) == (1280, 720)


def test_fraction_points_scale_to_detector_pixels(fleet: gen_compose.Fleet) -> None:
    regions = [{"name": "lane", "points": [[0, 0.5], [1, 0.5], [1, 1], [0.25, 1]]}]
    d = det.details_for(config(regions=regions), cam(fleet, "cam-gate"), fleet.defaults)
    assert d["cords"]["lane"]["points"] == [[0, 180], [640, 180], [640, 360], [160, 360]]


def test_pixel_points_pass_through_and_are_bounds_checked(fleet: gen_compose.Fleet) -> None:
    px = [[0, 0], [640, 0], [640, 360], [0, 360]]
    d = det.details_for(
        config(regions=[{"name": "r", "points_px": px}]), cam(fleet, "cam-gate"), fleet.defaults
    )
    assert d["cords"]["r"]["points"] == px
    outside = config(regions=[{"name": "r", "points_px": [[0, 0], [641, 0], [641, 1], [0, 1]]}])
    with pytest.raises(det.DetectionError, match="outside 640x360"):
        det.details_for(outside, cam(fleet, "cam-gate"), fleet.defaults)


@pytest.mark.parametrize(
    ("region", "message"),
    [
        ({"name": "r", "points": SQUARE[:3]}, ">= 4 points"),  # Shinobi drops the region
        ({"name": "r"}, "exactly one of"),
        ({"name": "r", "points": SQUARE, "points_px": SQUARE}, "exactly one of"),
        ({"name": "r", "points": [[0, 0], [2, 0], [2, 1], [0, 1]]}, "fractions 0..1"),
        ({"name": "bad name", "points": SQUARE}, "pattern"),
        ({"name": "r", "points": SQUARE, "sensitivity": 5, "max_sensitivity": 5}, "never fires"),
    ],
)
def test_invalid_regions_are_refused(region: dict[str, Any], message: str) -> None:
    with pytest.raises(det.DetectionError, match=message):
        det.parse({"version": 1, "cameras": {"cam-gate": {"regions": [region]}}})


def test_duplicate_region_names_are_refused() -> None:
    regions = [{"name": "r", "points": SQUARE}, {"name": "r", "points": SQUARE}]
    with pytest.raises(det.DetectionError, match="duplicate region names"):
        det.parse({"version": 1, "cameras": {"cam-gate": {"regions": regions}}})


def test_rendered_keys_cannot_be_overridden() -> None:
    with pytest.raises(det.DetectionError, match="set by the renderer"):
        det.parse({"version": 1, "defaults": {"details": {"cords": {}}}})


def test_unknown_camera_is_refused(fleet: gen_compose.Fleet) -> None:
    detection = det.parse(
        {"version": 1, "cameras": {"cam-nope": {"regions": [{"name": "r", "points": SQUARE}]}}}
    )
    with pytest.raises(det.DetectionError, match="cam-nope"):
        det.check_cameras(detection, fleet)


def test_region_settings_fall_back_to_defaults_then_to_shinobi(
    fleet: gen_compose.Fleet,
) -> None:
    detection = det.parse(
        {
            "version": 1,
            "defaults": {"region": {"sensitivity": 4}},
            "cameras": {
                "cam-gate": {
                    "regions": [
                        {"name": "a", "points": SQUARE},
                        {"name": "b", "points": SQUARE, "sensitivity": 7, "threshold": 2},
                    ]
                }
            },
        }
    )
    cords = det.details_for(detection, cam(fleet, "cam-gate"), fleet.defaults)["cords"]
    assert cords["a"] | {"points": None} == {
        "name": "a", "points": None, "sensitivity": 4, "max_sensitivity": "",
        "threshold": "", "color_threshold": "",
    }  # fmt: skip
    assert (cords["b"]["sensitivity"], cords["b"]["threshold"]) == (7, 2)


def test_camera_details_override_defaults(fleet: gen_compose.Fleet) -> None:
    detection = det.parse(
        {
            "version": 1,
            "defaults": {"details": {"detector_fps": 5, "detector_lock_timeout": 2000}},
            "cameras": {
                "cam-gate": {
                    "details": {"detector_fps": 3},
                    "regions": [{"name": "r", "points": SQUARE}],
                }
            },
        }
    )
    d = det.details_for(detection, cam(fleet, "cam-gate"), fleet.defaults)
    assert (d["detector_fps"], d["detector_lock_timeout"]) == (3, 2000)


def test_unlisted_camera_keeps_template_detector_off(fleet: gen_compose.Fleet) -> None:
    template = pm.load_template(pm.DEFAULT_TEMPLATE)
    desired = pm.render(fleet, template, config())
    assert desired["cam_gate"]["details"]["detector"] == "1"  # runtime form, see wire_form
    assert desired["cam_yard"]["details"]["detector"] == "0"
    assert desired["cam_yard"]["details"]["input_maps"] == []


def test_rendered_detection_survives_shinobi_storage(fleet: gen_compose.Fleet) -> None:
    """What apply sends is stored as exactly the desired config, so plan() after apply() sees
    no difference (no restart loop, cf. Session 12)."""
    desired = pm.render(fleet, pm.load_template(pm.DEFAULT_TEMPLATE), det.load())
    for cfg in desired.values():
        assert pm.stored_form(pm.wire_form(cfg)) == cfg
        assert cfg["details"]["detector_save"] == "1"  # === '1' holds even after a live update


def test_record_mode_template_has_no_event_recording_trigger() -> None:
    """detector_trigger=1 adds an unused pre-roll HLS output and a 15 s post-start motion lock
    on record-mode monitors (Session 14)."""
    template = pm.load_template(pm.DEFAULT_TEMPLATE)
    assert template["mode"] == "record"
    assert template["details"]["detector_trigger"] == 0
