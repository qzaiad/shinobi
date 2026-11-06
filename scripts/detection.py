"""Motion detection as code: shinobi-config/detection.yaml -> Shinobi monitor `details`.

Used by scripts/provision_monitors.py render(); explained in docs/notes/14-motion-detection.md.

    detection.yaml ──▶ load() ──▶ Detection ──┐
    cameras.yaml (sub stream size + URL) ─────┼─▶ details_for(camera) ─▶ merged into the template

What a camera entry turns into (Shinobi keys, libs/ffmpeg/builders.js buildMainDetector and
libs/cameraThread/libs/detectorUtils.js):

    detector=1, detector_pam=1, detector_send_frames=1     built-in pam-diff motion detector on
    input_maps=[sub stream], input_map_choices.detector    ffmpeg input #1 = /<cam>-sub (input: sub)
    detector_scale_x/y = size of that input                no rescaling, no distorted aspect
    cords = {name: {name, points, sensitivity, ...}}       regions in detector pixels

Region points: `points` = fractions of the frame (0..1, survive a change of detector size) or
`points_px` = pixels of the detector frame (what Shinobi's region editor shows). Exactly one per
region. Shinobi needs >= 4 points per region (detectorUtils: "Failed to Create Region").
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from sim import gen_compose

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "shinobi-config" / "detection.yaml"

# Set by details_for(); a camera's `details:` may not override them (second source of truth).
RENDERED_KEYS = frozenset(
    {
        "detector", "detector_pam", "detector_send_frames", "detector_scale_x",
        "detector_scale_y", "input_maps", "input_map_choices", "cords",
    }
)  # fmt: skip

Point = tuple[float, float]


class DetectionError(Exception):
    """detection.yaml that cannot become a Shinobi monitor config."""


class RegionSettings(BaseModel):
    """Per-region thresholds; None = Shinobi's global (detector_sensitivity etc.)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sensitivity: int | None = Field(default=None, ge=1, le=100)  # min % of region changed
    max_sensitivity: int | None = Field(default=None, ge=1, le=100)  # drop triggers above
    threshold: int | None = Field(default=None, ge=1, le=100)  # consecutive frames needed
    color_threshold: int | None = Field(default=None, ge=1, le=255)  # min |Δluma| per pixel


class Region(RegionSettings):
    name: str = Field(pattern=r"^[A-Za-z0-9_]+$")
    points: tuple[Point, ...] | None = None
    points_px: tuple[tuple[int, int], ...] | None = None

    @model_validator(mode="after")
    def _one_kind_of_points(self) -> Self:
        given = [p for p in (self.points, self.points_px) if p is not None]
        if len(given) != 1:
            raise ValueError(f"region {self.name}: give exactly one of points, points_px")
        if len(given[0]) < 4:
            raise ValueError(f"region {self.name}: Shinobi needs >= 4 points, got {len(given[0])}")
        if self.points is not None and not all(0 <= c <= 1 for p in self.points for c in p):
            raise ValueError(f"region {self.name}: points are fractions 0..1 (or use points_px)")
        if (self.sensitivity and self.max_sensitivity) and self.max_sensitivity <= self.sensitivity:
            raise ValueError(f"region {self.name}: max_sensitivity <= sensitivity never fires")
        return self


class CameraDetection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input: Literal["sub", "main"] | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    regions: tuple[Region, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_names(self) -> Self:
        names = [r.name for r in self.regions]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"duplicate region names {dup}")
        return self


class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input: Literal["sub", "main"] = "sub"
    details: dict[str, Any] = Field(default_factory=dict)
    region: RegionSettings = RegionSettings()


class Detection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    defaults: Defaults = Defaults()
    cameras: dict[str, CameraDetection] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _no_rendered_keys(self) -> Self:
        for where, details in [("defaults", self.defaults.details)] + [
            (cam, c.details) for cam, c in self.cameras.items()
        ]:
            clash = sorted(set(details) & RENDERED_KEYS)
            if clash:
                raise ValueError(f"{where}.details: set by the renderer, remove {clash}")
        return self


def parse(raw: Any) -> Detection:
    try:
        return Detection.model_validate(raw)
    except ValidationError as e:
        lines = [
            f"{'.'.join(map(str, err['loc'])) or 'config'}: {err['msg']}" for err in e.errors()
        ]
        raise DetectionError("\n".join(lines)) from None


def load(path: Path = DEFAULT_CONFIG) -> Detection:
    with path.open(encoding="utf-8") as f:
        return parse(yaml.safe_load(f))


def check_cameras(detection: Detection, fleet: gen_compose.Fleet) -> None:
    unknown = sorted(set(detection.cameras) - {c.id for c in fleet.cameras})
    if unknown:
        raise DetectionError(f"detection.yaml: cameras not in cameras.yaml: {unknown}")


def _pixels(region: Region, width: int, height: int) -> list[list[int]]:
    if region.points_px is not None:
        bad = [p for p in region.points_px if not (0 <= p[0] <= width and 0 <= p[1] <= height)]
        if bad:
            raise DetectionError(f"region {region.name}: {bad} outside {width}x{height}")
        return [[x, y] for x, y in region.points_px]
    return [[round(x * width), round(y * height)] for x, y in region.points or ()]


def _cord(region: Region, defaults: RegionSettings, width: int, height: int) -> dict[str, Any]:
    """One entry of details.cords, in the shape Shinobi's region editor writes
    (web/assets/js/bs5.regionEditor.js). "" = use the monitor-wide value."""

    def pick(field: str) -> int | str:
        value = getattr(region, field)
        if value is None:
            value = getattr(defaults, field)
        return "" if value is None else value

    return {
        "name": region.name,
        "points": _pixels(region, width, height),
        "sensitivity": pick("sensitivity"),
        "max_sensitivity": pick("max_sensitivity"),
        "threshold": pick("threshold"),
        "color_threshold": pick("color_threshold"),
    }


def details_for(
    detection: Detection, cam: gen_compose.Camera, fleet_defaults: gen_compose.Defaults
) -> dict[str, Any]:
    """Pure: the `details` keys that switch on motion detection for `cam` ({} = not listed,
    the template's detector=0 stays)."""
    entry = detection.cameras.get(cam.id)
    if entry is None:
        return {}
    which = entry.input or detection.defaults.input
    sub = gen_compose.resolve_sub(cam, fleet_defaults)
    if which == "sub" and sub is None:
        raise DetectionError(f"{cam.id}: input sub, but the camera has no sub stream")
    details: dict[str, Any] = {**detection.defaults.details, **entry.details}
    if which == "sub":
        assert sub is not None
        width, height = sub.width, sub.height
        url = f"{gen_compose.RTSP_BASE}/{cam.id}{gen_compose.SUB_SUFFIX}"
        # ffmpeg input #1 (#0 = main stream, primary_input "0:0" keeps record/stream on it).
        details["input_maps"] = [_input_map(url)]
        details["input_map_choices"] = {**_EMPTY_MAP_CHOICES, "detector": [{"map": "1:0"}]}
    else:
        width, height = cam.width, cam.height
    region_defaults = detection.defaults.region
    details |= {
        "detector": 1,
        "detector_pam": 1,
        "detector_send_frames": 1,  # "Send Frames": without it there is no pipe:3 output at all
        "detector_scale_x": width,
        "detector_scale_y": height,
        "cords": {r.name: _cord(r, region_defaults, width, height) for r in entry.regions},
    }
    return details


# Same keys as the template's input_map_choices (the dashboard always sends all of them).
_EMPTY_MAP_CHOICES: Mapping[str, list[Any]] = {
    k: []
    for k in (
        "detector", "detector_object", "detector_sip_buffer", "record", "record_timelapse",
        "snap", "stream",
    )
}  # fmt: skip


def _input_map(url: str) -> dict[str, Any]:
    """One extra ffmpeg input as the dashboard stores it (libs/ffmpeg/builders.js
    createInputMap reads type, fulladdress, rtsp_transport, ...)."""
    return {
        "type": "h264",
        "fulladdress": url,
        "rtsp_transport": "tcp",
        "accelerator": 0,
        "aduration": "",
        "probesize": "",
        "cust_input": "",
        "hwaccel": "",
        "hwaccel_device": "",
        "hwaccel_vcodec": "",
        "sfps": "",
        "stream_loop": 0,
    }
