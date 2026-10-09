"""Tests for sim/gen_compose.py (camera fleet -> compose services)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from sim import gen_compose as gc

CAMERAS = [
    ("cam-gate", "gate", 1280, 720, 15, "2M", 30),
    ("cam-quay", "quay", 1920, 1080, 15, "4M", 30),
    ("cam-yard", "yard", 1280, 720, 10, "1.5M", 20),
    ("cam-waterway", "waterway", 1920, 1080, 10, "3M", 20),
]


@pytest.fixture
def raw() -> dict[str, Any]:
    return {
        "version": 2,
        "defaults": {
            "image": "sim-camera:test", "preset": "veryfast", "transport": "tcp",
            "sub": {"width": 640, "height": 360, "fps": 10, "bitrate": "384k"},
        },
        "cameras": [
            {"id": i, "role": r, "clip": f"{r}.mp4", "width": w, "height": h,
             "fps": f, "bitrate": b, "gop": g}
            for i, r, w, h, f, b, g in CAMERAS
        ],
    }  # fmt: skip


@pytest.fixture
def clips_dir(tmp_path: Path, raw: dict[str, Any]) -> Path:
    for cam in raw["cameras"]:
        (tmp_path / cam["clip"]).write_bytes(b"")
    return tmp_path


def option(cmd: list[str], flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


def outputs(cmd: list[str]) -> dict[str, list[str]]:
    """Split a command into its per-output option blocks, keyed by -map label."""
    starts = [i for i, a in enumerate(cmd) if a == "-map"]
    blocks = [cmd[a:b] for a, b in zip(starts, [*starts[1:], len(cmd)], strict=True)]
    return {b[1]: b for b in blocks}


def test_valid_config_renders_four_services(raw: dict[str, Any], clips_dir: Path) -> None:
    fleet = gc.parse(raw)
    gc.check_clips(fleet, clips_dir)
    services = gc.render(fleet)["services"]
    assert list(services) == [c[0] for c in CAMERAS]
    gate = services["cam-gate"]
    assert gate["image"] == "sim-camera:test"
    out = outputs(gate["command"])
    assert out["[main]"][-1] == "rtsp://mediamtx:8554/cam-gate"
    assert out["[sub]"][-1] == "rtsp://mediamtx:8554/cam-gate-sub"
    assert gate["volumes"] == ["./media:/clips:ro"]
    assert gate["depends_on"] == ["mediamtx"]
    assert gate["restart"] == "unless-stopped"
    assert gate["labels"] == {"port.sim.role": "gate", "port.sim.camera": "cam-gate"}
    assert "container_name" not in gate


def test_duplicate_id_rejected(raw: dict[str, Any]) -> None:
    raw["cameras"][2]["id"] = "cam-gate"
    with pytest.raises(gc.ConfigError, match="cam-gate: duplicate camera id"):
        gc.parse(raw)


def test_odd_width_rejected(raw: dict[str, Any]) -> None:
    raw["cameras"][1]["width"] = 1279
    with pytest.raises(gc.ConfigError, match=r"cam-quay width: .*must be even, got 1279"):
        gc.parse(raw)


def test_unknown_role_rejected(raw: dict[str, Any]) -> None:
    raw["cameras"][3]["role"] = "lighthouse"
    with pytest.raises(gc.ConfigError, match="cam-waterway role"):
        gc.parse(raw)


@pytest.mark.parametrize(
    ("field", "value"),
    [("id", "gate-1"), ("fps", 0), ("fps", 61), ("gop", 0), ("bitrate", "2 Mbps"), ("osd", "yes")],
)
def test_field_rules_rejected(raw: dict[str, Any], field: str, value: object) -> None:
    raw["cameras"][0][field] = value
    with pytest.raises(gc.ConfigError, match=field):
        gc.parse(raw)


def test_missing_clip_rejected(raw: dict[str, Any], clips_dir: Path) -> None:
    (clips_dir / "yard.mp4").unlink()
    with pytest.raises(gc.ConfigError, match=r"cam-yard: clip .*yard\.mp4 does not exist"):
        gc.check_clips(gc.parse(raw), clips_dir)


def test_defaults_merged_and_overridable(raw: dict[str, Any]) -> None:
    raw["cameras"][1] |= {"image": "other:1", "preset": "ultrafast", "transport": "udp"}
    services = gc.render(gc.parse(raw))["services"]

    gate = services["cam-gate"]["command"]
    assert services["cam-gate"]["image"] == "sim-camera:test"
    assert option(gate, "-preset") == "veryfast"
    assert option(gate, "-rtsp_transport") == "tcp"

    quay = services["cam-quay"]["command"]
    assert services["cam-quay"]["image"] == "other:1"
    assert option(quay, "-preset") == "ultrafast"
    assert option(quay, "-rtsp_transport") == "udp"


@pytest.mark.parametrize(
    ("bitrate", "bufsize"),
    [("2M", "4M"), ("1.5M", "3M"), ("0.75M", "1.5M"), ("750k", "1500k"), ("10M", "20M")],
)
def test_bufsize_is_twice_bitrate(bitrate: str, bufsize: str) -> None:
    assert gc.double_bitrate(bitrate) == bufsize


def test_command_gop_and_rate_control(raw: dict[str, Any]) -> None:
    services = gc.render(gc.parse(raw))["services"]
    for cam_id, _, w, h, fps, bitrate, gop in CAMERAS:
        cmd = services[cam_id]["command"]
        assert all(isinstance(a, str) for a in cmd)  # exec form, compose won't re-split
        expected = {"[main]": (fps, w, h, bitrate, gop), "[sub]": (10, 640, 360, "384k", 20)}
        graph = option(cmd, "-filter_complex")
        for label, (f, width, height, rate, g) in expected.items():
            out = outputs(cmd)[label]
            assert option(out, "-g") == option(out, "-keyint_min") == str(g)
            assert option(out, "-b:v") == option(out, "-maxrate") == rate
            assert option(out, "-bufsize") == gc.double_bitrate(rate)
            assert option(out, "-bf") == "0"
            assert option(out, "-profile:v") == "main"
            assert option(out, "-sc_threshold") == "0"
            assert f"fps={f},scale={width}:{height},format=yuv420p" in graph


def test_one_decode_split_into_main_and_sub(raw: dict[str, Any]) -> None:
    cmd = gc.render(gc.parse(raw))["services"]["cam-gate"]["command"]
    assert cmd.count("-i") == 1
    graph = option(cmd, "-filter_complex")
    assert graph.startswith("[0:v]split=2[m][s];[m]fps=15,")
    assert graph.endswith("[sub]")
    assert list(outputs(cmd)) == ["[main]", "[sub]"]


def test_sub_override_merges_with_defaults(raw: dict[str, Any]) -> None:
    raw["cameras"][0]["sub"] = {"fps": 5, "bitrate": "256k"}
    fleet = gc.parse(raw)
    assert gc.resolve_sub(fleet.cameras[0], fleet.defaults) == gc.ResolvedSub(
        width=640, height=360, fps=5, bitrate="256k", gop=10
    )
    sub = outputs(gc.render(fleet)["services"]["cam-gate"]["command"])["[sub]"]
    assert option(sub, "-g") == "10"
    assert option(sub, "-b:v") == "256k"


def test_sub_null_disables_sub_stream(raw: dict[str, Any]) -> None:
    raw["cameras"][0]["sub"] = None
    cmd = gc.render(gc.parse(raw))["services"]["cam-gate"]["command"]
    assert list(outputs(cmd)) == ["[main]"]
    assert "split" not in option(cmd, "-filter_complex")
    assert not any(a.endswith("-sub") for a in cmd)


def test_no_default_sub_means_main_only(raw: dict[str, Any]) -> None:
    del raw["defaults"]["sub"]
    services = gc.render(gc.parse(raw))["services"]
    assert all(list(outputs(s["command"])) == ["[main]"] for s in services.values())


def test_partial_sub_without_defaults_rejected(raw: dict[str, Any]) -> None:
    del raw["defaults"]["sub"]
    raw["cameras"][1]["sub"] = {"fps": 5}
    with pytest.raises(gc.ConfigError, match="cam-quay: sub is missing width, height, bitrate"):
        gc.parse(raw)


@pytest.mark.parametrize(
    ("sub", "match"),
    [
        ({"fps": 20}, "cam-gate: sub fps 20 higher than main fps 15"),
        ({"width": 1920, "height": 1080}, "cam-gate: sub 1920x1080 larger than main 1280x720"),
        ({"width": 641}, "must be even, got 641"),
        ({"bitrate": "fast"}, "bitrate"),
    ],
)
def test_bad_sub_rejected(raw: dict[str, Any], sub: dict[str, Any], match: str) -> None:
    raw["cameras"][0]["sub"] = sub
    with pytest.raises(gc.ConfigError, match=match):
        gc.parse(raw)


def test_osd_clock_and_id_on_both_streams(raw: dict[str, Any]) -> None:
    graph = option(gc.render(gc.parse(raw))["services"]["cam-gate"]["command"], "-filter_complex")
    assert graph.count("text='%{gmtime\\:%Y-%m-%d %T} UTC'") == 2
    assert graph.count("text='cam-gate'") == 2
    assert graph.count(f"fontfile={gc.OSD_FONT}") == 4
    assert "fontsize=30" in graph  # 720 // 24
    assert "fontsize=15" in graph  # 360 // 24


def test_osd_can_be_disabled(raw: dict[str, Any]) -> None:
    raw["cameras"][0]["osd"] = False
    services = gc.render(gc.parse(raw))["services"]
    assert "drawtext" not in option(services["cam-gate"]["command"], "-filter_complex")
    assert "drawtext" in option(services["cam-quay"]["command"], "-filter_complex")


def test_version_1_rejected(raw: dict[str, Any]) -> None:
    raw["version"] = 1
    with pytest.raises(gc.ConfigError, match="version"):
        gc.parse(raw)


def test_render_is_deterministic(raw: dict[str, Any]) -> None:
    first = gc.dump(gc.render(gc.parse(raw)))
    second = gc.dump(gc.render(gc.parse(copy.deepcopy(raw))))
    assert first == second
    assert first.startswith(gc.HEADER)


def test_removing_camera_removes_service(raw: dict[str, Any]) -> None:
    del raw["cameras"][2]
    assert "cam-yard" not in gc.render(gc.parse(raw))["services"]


def test_gop_warning(raw: dict[str, Any]) -> None:
    raw["cameras"][0]["gop"] = 50
    raw["cameras"][1]["sub"] = {"gop": 30}
    assert gc.warnings_for(gc.parse(raw)) == [
        "cam-gate: gop 50 != 2 x fps (30); convention is a 2 s GOP",
        "cam-quay: sub gop 30 != 2 x fps (20); convention is a 2 s GOP",
    ]


def test_committed_file_matches_generator() -> None:
    """Golden: compose/cameras.generated.yml is up to date with sim/cameras.yaml.

    Clip existence is not checked here, so the test passes on a clone without media/.
    """
    fleet = gc.load(gc.DEFAULT_CONFIG)
    assert gc.DEFAULT_OUT.read_text(encoding="utf-8") == gc.dump(gc.render(fleet)), (
        "run: uv run python sim/gen_compose.py"
    )
    assert yaml.safe_load(gc.DEFAULT_OUT.read_text(encoding="utf-8")) == gc.render(fleet)
