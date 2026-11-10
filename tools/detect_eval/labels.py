"""The labelled frame set: tests/fixtures/detection/labels.yaml (ground truth, hand-made).

Only labels are committed, never pixels (media policy): each frame is identified by its camera,
the clip's sha256 and its index in the 2 fps JPEG sequence that `frames` regenerates from the clip.

    version: 1
    width: 640, height: 360, fps: 2      the JPEGs Shinobi sends the plugin (sub stream, pipe:4)
    classes: {truck: truck, crane: null}  port class -> COCO tag the model can say (null = none)
    cameras:
      cam-gate:
        clip: gate_santos_port.mp4
        clip_sha256: <hex>                 labels belong to exactly this clip
        frames:
          - id: cam-gate-03                # stable name, also the JPEG file name
            index: 15                      # frame number in the 2 fps sequence (t = index / fps)
            done: true                     # labelled (and reviewed); false = still to do
            boxes:                         # pixels of the 640x360 frame, top-left origin
              - {cls: truck, xywh: [212, 140, 180, 120]}
              - {cls: person, xywh: [600, 200, 6, 14], ignore: true}   # too small to expect

`ignore` = an object a detection may hit without counting as a hit or a false alarm (too small,
mostly hidden, cut by the frame edge): the COCO "crowd" idea.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

REPO = Path(__file__).resolve().parents[2]
DEFAULT_LABELS = REPO / "tests" / "fixtures" / "detection" / "labels.yaml"
FRAMES_PER_CAMERA = 20

# Port classes and what YOLO (COCO, 80 classes) can call them. null = no COCO class: the model
# cannot be right about it, whatever it says. Order = labeller's hotkeys 1..9.
DEFAULT_CLASSES: dict[str, str | None] = {
    "truck": "truck",  # tractor unit incl. trailer and load (container on a chassis = truck)
    "car": "car",  # cars, vans, pick-ups
    "person": "person",
    "boat": "boat",  # any vessel: tug, carrier, launch
    "bus": "bus",
    "container": None,  # containers NOT on a vehicle: one box per stack block
    "crane": None,  # STS, RTG, mobile crane: the visible structure
    "port_vehicle": None,  # reach stacker, straddle carrier, forklift, terminal tractor
    "traffic_light": "traffic light",  # real ones; a COCO-correct answer, useless to a port
}

HEADER = """\
# Ground truth for docs/notes/16-detection-quality.md. Edit with the labeller:
#   uv run python -m tools.detect_eval label        (http://127.0.0.1:8765)
# Boxes are pixels of the 640x360 frame. Guidelines:
#   truck   whole vehicle incl. trailer and load; a container on a chassis is part of the truck
#   container  only containers not on a vehicle; one box per stack block, not per box
#   crane   visible structure of STS/RTG/mobile cranes; port_vehicle = reach stacker, straddle
#           carrier, forklift, terminal tractor
#   ignore  objects nobody would expect a detector to report: < ~12 px high, mostly hidden,
#           cut by the frame edge, or behind the OSD
"""


class LabelsError(Exception):
    """labels.yaml that is not a usable label set."""


class Box(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cls: str
    xywh: tuple[int, int, int, int]
    ignore: bool = False

    @model_validator(mode="after")
    def _positive(self) -> Self:
        if self.xywh[2] <= 0 or self.xywh[3] <= 0:
            raise ValueError(f"box {self.xywh}: width and height must be > 0")
        return self


class Frame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9-]+$")
    index: int = Field(ge=0)
    done: bool = False
    boxes: list[Box] = Field(default_factory=list)


class CameraSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clip: str
    clip_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    frames: list[Frame]

    @model_validator(mode="after")
    def _unique(self) -> Self:
        ids = [f.id for f in self.frames]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate frame ids")
        return self


class LabelSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    width: int = 640
    height: int = 360
    fps: float = 2
    classes: dict[str, str | None] = Field(default_factory=lambda: dict(DEFAULT_CLASSES))
    cameras: dict[str, CameraSet] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _known_classes_and_inside(self) -> Self:
        for cam, cs in self.cameras.items():
            for f in cs.frames:
                for b in f.boxes:
                    if b.cls not in self.classes:
                        raise ValueError(f"{f.id}: unknown class {b.cls!r}")
                    x, y, w, h = b.xywh
                    if x < 0 or y < 0 or x + w > self.width or y + h > self.height:
                        raise ValueError(f"{cam}/{f.id}: box {b.xywh} outside the frame")
        return self

    def frame(self, cam: str, frame_id: str) -> Frame:
        for f in self.cameras[cam].frames:
            if f.id == frame_id:
                return f
        raise KeyError(f"{cam}/{frame_id}")


def pick_indices(available: int, wanted: int = FRAMES_PER_CAMERA) -> list[int]:
    """Pure: `wanted` frame indices spread evenly over 0..available-1 (centre of each slice),
    so quiet stretches and empty frames are sampled as often as busy ones. Fewer frames than
    wanted -> all of them."""
    if available <= wanted:
        return list(range(available))
    return [int((k + 0.5) * available / wanted) for k in range(wanted)]


def parse(raw: object) -> LabelSet:
    try:
        return LabelSet.model_validate(raw)
    except ValidationError as e:
        lines = [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()]
        raise LabelsError("\n".join(lines)) from None


def load(path: Path = DEFAULT_LABELS) -> LabelSet:
    with path.open(encoding="utf-8") as f:
        return parse(yaml.safe_load(f))


class _Dumper(yaml.SafeDumper):
    pass


def _box_repr(dumper: yaml.SafeDumper, data: dict) -> yaml.Node:
    return dumper.represent_mapping("tag:yaml.org,2002:map", data, flow_style=True)


def dump(labels: LabelSet) -> str:
    """Stable YAML: one line per box, keys in model order, header with the guidelines."""
    raw = labels.model_dump(mode="json")
    for cs in raw["cameras"].values():
        for f in cs["frames"]:
            boxes = []
            for b in f["boxes"]:
                d = {"cls": b["cls"], "xywh": _Flow(b["xywh"])}
                if b["ignore"]:
                    d["ignore"] = True
                boxes.append(_FlowMap(d))
            f["boxes"] = boxes
    return HEADER + yaml.dump(raw, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)


class _Flow(list):
    pass


class _FlowMap(dict):
    pass


_Dumper.add_representer(
    _Flow, lambda d, v: d.represent_sequence("tag:yaml.org,2002:seq", v, flow_style=True)
)
_Dumper.add_representer(_FlowMap, _box_repr)


def save(labels: LabelSet, path: Path = DEFAULT_LABELS) -> None:
    """Atomic write (temp file + rename): the labeller saves on every frame change."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = dump(labels)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
