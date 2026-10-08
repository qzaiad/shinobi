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
        "version": 1,
        "defaults": {"image": "sim-camera:test", "preset": "veryfast", "transport": "tcp"},
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


def test_valid_config_renders_four_services(raw: dict[str, Any], clips_dir: Path) -> None:
    fleet = gc.parse(raw)
    gc.check_clips(fleet, clips_dir)
    services = gc.render(fleet)["services"]
    assert list(services) == [c[0] for c in CAMERAS]
    gate = services["cam-gate"]
    assert gate["image"] == "sim-camera:test"
    assert gate["command"][-1] == "rtsp://mediamtx:8554/cam-gate"
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
    [("id", "gate-1"), ("fps", 0), ("fps", 61), ("gop", 0), ("bitrate", "2 Mbps")],
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
        assert option(cmd, "-g") == option(cmd, "-keyint_min") == str(gop)
        assert option(cmd, "-b:v") == option(cmd, "-maxrate") == bitrate
        assert option(cmd, "-bufsize") == gc.double_bitrate(bitrate)
        assert option(cmd, "-vf") == f"scale={w}:{h},fps={fps},format=yuv420p"
        assert option(cmd, "-bf") == "0"


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
    assert gc.warnings_for(gc.parse(raw)) == [
        "cam-gate: gop 50 != 2 x fps (30); convention is a 2 s GOP"
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
