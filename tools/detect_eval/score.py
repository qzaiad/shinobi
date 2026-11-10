"""Precision / recall of the plugin's boxes against labels.yaml.

Matching, per frame (greedy, highest score first, like the COCO evaluation):

    detection d (tag, score >= threshold, box)
      ├─ unmatched label of the same class (port class -> COCO tag), IoU >= iou → TP
      ├─ inside an `ignore` label of that class (>= 50 % of d's area)           → ignored
      ├─ >= 80 % inside an unmatched label of that class, IoU too low          → FP "part of truck"
      ├─ IoU >= iou with a label already matched, same class                    → FP duplicate
      ├─ IoU >= iou with a label of another class (crane called "train")        → FP "on crane"
      └─ otherwise                                                               → FP background
    label not matched (and not ignore):  class has a COCO tag → FN (the model could have)
                                         no COCO tag           → FN port-only (it never can)

    precision      = TP / (TP + FP)                     how often a reported box is right
    recall (COCO)  = TP / (TP + FN)                     how much of what it knows it finds
    recall (port)  = TP / (TP + FN + FN port-only)      how much of the port it sees

Event view (one frame = one plugin answer = at most one Shinobi event): a frame "fires" when at
least one box survives threshold (and filters); it is a true event with >= 1 TP, a false event
with only FPs. A "part of" box (the plugin boxed the tractor of a truck labelled with its
trailer) is a wrong box but a right event: it neither makes a false event nor leaves the frame
missed (frames hit), while precision and recall stay strict.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from tools.detect_eval import labels as lb
from tools.detect_eval import predict
from tools.detect_eval import shinobi_filters as sf

XYWH = tuple[float, float, float, float]
PART = 0.8  # share of a detection inside a same-class label to call it "part of" that object
SWEEP = (0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8)


@dataclass(frozen=True, slots=True)
class Det:
    tag: str
    score: float
    xywh: XYWH

    @classmethod
    def from_plugin(cls, raw: Mapping[str, Any]) -> Det:
        return cls(raw["tag"], float(raw["confidence"]),
                   (raw["x"], raw["y"], raw["width"], raw["height"]))  # fmt: skip

    def as_matrix(self) -> dict[str, Any]:
        x, y, w, h = self.xywh
        return {"tag": self.tag, "confidence": self.score, "x": x, "y": y, "width": w, "height": h}


def iou(a: XYWH, b: XYWH) -> float:
    """Intersection over union of two (x, y, w, h) boxes; 0 for disjoint ones."""
    ix = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def ioa(a: XYWH, region: XYWH) -> float:
    """Share of a's area inside region (for ignore labels: a small det in a big label)."""
    ix = max(0.0, min(a[0] + a[2], region[0] + region[2]) - max(a[0], region[0]))
    iy = max(0.0, min(a[1] + a[3], region[1] + region[3]) - max(a[1], region[1]))
    return ix * iy / (a[2] * a[3]) if a[2] * a[3] > 0 else 0.0


@dataclass(slots=True)
class FrameResult:
    tp: list[tuple[Det, lb.Box]] = field(default_factory=list)
    fp: list[tuple[Det, str]] = field(default_factory=list)  # (det, why)
    ignored: list[Det] = field(default_factory=list)
    fn: list[lb.Box] = field(default_factory=list)  # label has a COCO tag
    fn_port: list[lb.Box] = field(default_factory=list)  # label without a COCO tag

    @property
    def fired(self) -> bool:
        return bool(self.tp or self.fp or self.ignored)

    @property
    def parts(self) -> int:
        return sum(why.startswith("part of") for _, why in self.fp)


def match_frame(
    gts: Sequence[lb.Box], dets: Iterable[Det], coco: Mapping[str, str | None], iou_min: float
) -> FrameResult:
    """Pure: one frame's labels and detections -> TP / FP / FN (see module doc)."""
    res = FrameResult()
    matched: set[int] = set()
    for d in sorted(dets, key=lambda d: -d.score):
        same = [(i, g) for i, g in enumerate(gts) if coco.get(g.cls) == d.tag]
        best, best_iou = None, iou_min
        for i, g in same:
            if not g.ignore and i not in matched and (v := iou(d.xywh, g.xywh)) >= best_iou:
                best, best_iou = i, v
        if best is not None:
            matched.add(best)
            res.tp.append((d, gts[best]))
        elif any(g.ignore and ioa(d.xywh, g.xywh) >= 0.5 for _, g in same):
            res.ignored.append(d)
        elif part := [
            g for i, g in same if not g.ignore and i not in matched and ioa(d.xywh, g.xywh) >= PART
        ]:
            res.fp.append((d, f"part of {part[0].cls}"))
        elif any(i in matched and iou(d.xywh, g.xywh) >= iou_min for i, g in same):
            res.fp.append((d, "duplicate"))
        elif other := [
            g for g in gts if coco.get(g.cls) != d.tag and iou(d.xywh, g.xywh) >= iou_min
        ]:  # noqa: E501
            res.fp.append((d, f"on {max(other, key=lambda g: iou(d.xywh, g.xywh)).cls}"))
        else:
            res.fp.append((d, "background"))
    for i, g in enumerate(gts):
        if g.ignore or i in matched:
            continue
        (res.fn if coco.get(g.cls) else res.fn_port).append(g)
    return res


@dataclass(frozen=True, slots=True)
class Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    fn_port: int = 0
    frames: int = 0
    frames_with_objects: int = 0  # frames with >= 1 label the model could find
    frames_hit: int = 0  # ... of which at least one TP
    events: int = 0  # frames that fire
    false_events: int = 0  # fire with no TP, no ignored box and no "part of" box

    def __add__(self, o: Counts) -> Counts:
        return Counts(*(a + b for a, b in zip(astuple(self), astuple(o), strict=True)))

    @property
    def precision(self) -> float | None:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else None

    @property
    def recall(self) -> float | None:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else None

    @property
    def recall_port(self) -> float | None:
        n = self.tp + self.fn + self.fn_port
        return self.tp / n if n else None


def astuple(c: Counts) -> tuple[int, ...]:
    return tuple(asdict(c).values())


def count(res: FrameResult) -> Counts:
    has_objects = bool(res.fn or any(not g.ignore for _, g in res.tp))
    return Counts(
        tp=len(res.tp), fp=len(res.fp), fn=len(res.fn), fn_port=len(res.fn_port), frames=1,
        frames_with_objects=int(has_objects),
        frames_hit=int(has_objects and bool(res.tp or res.parts)),
        events=int(res.fired),
        false_events=int(res.fired and not res.tp and not res.ignored and not res.parts),
    )  # fmt: skip


def detections(
    raw: Sequence[Mapping[str, Any]], threshold: float, filters: Mapping[str, Any] | None
) -> list[Det]:
    """Pure: plugin boxes (its order) -> what reaches the Events row: score cut-off like the
    plugin's CONFIDENCE, then Shinobi's event filters (None = no filters)."""
    dets = [Det.from_plugin(r) for r in raw if float(r["confidence"]) >= threshold]
    if filters:
        kept = sf.apply(filters, [d.as_matrix() for d in dets])
        dets = [] if kept is None else [Det.from_plugin(m) for m in kept]
    return dets


@dataclass(slots=True)
class Report:
    per_camera: dict[str, Counts]
    per_class: dict[str, dict[str, Counts]]  # camera -> class (label class or "tag:<coco>")
    fp_why: dict[str, Counter[str]]  # camera -> "tag why" -> n


def evaluate(
    labels: lb.LabelSet,
    preds: Mapping[str, Sequence[Mapping[str, Any]]],
    threshold: float,
    iou_min: float,
    filters: Mapping[str, Mapping[str, Any]] | None = None,  # camera -> detector_filters
) -> Report:
    """Pure: done frames only."""
    per_camera: dict[str, Counts] = {}
    per_class: dict[str, dict[str, Counts]] = {}
    fp_why: dict[str, Counter[str]] = {}
    coco = labels.classes
    tag_to_cls = {v: k for k, v in coco.items() if v}
    for cam, cs in labels.cameras.items():
        total, classes, why = Counts(), defaultdict(Counts), Counter()
        flt = (filters or {}).get(cam)
        for f in cs.frames:
            if not f.done:
                continue
            dets = detections(preds.get(f"{cam}/{f.id}", []), threshold, flt)
            res = match_frame(f.boxes, dets, coco, iou_min)
            total += count(res)
            for _, g in res.tp:
                classes[g.cls] += Counts(tp=1)
            for d, reason in res.fp:
                classes[tag_to_cls.get(d.tag, f"tag:{d.tag}")] += Counts(fp=1)
                why[f"{d.tag} ({reason})"] += 1
            for g in res.fn:
                classes[g.cls] += Counts(fn=1)
            for g in res.fn_port:
                classes[g.cls] += Counts(fn_port=1)
        per_camera[cam], per_class[cam], fp_why[cam] = total, dict(classes), why
    return Report(per_camera, per_class, fp_why)


def _pct(v: float | None) -> str:
    return "   -" if v is None else f"{100 * v:4.0f}"


def format_report(rep: Report, title: str) -> str:
    head = "CAMERA         FRAMES  TP  FP  FN FN-PORT  PREC%  REC%  REC-PORT%   EVENTS FALSE"
    out = [title, "", head + "  HIT/OBJ-FRAMES"]
    total = Counts()
    for cam, c in rep.per_camera.items():
        total += c
        out.append(_row(cam, c))
    out.append(_row("all", total))
    out += ["", "CAMERA         CLASS             TP  FP  FN FN-PORT  PREC%  REC%"]
    for cam, classes in rep.per_class.items():
        for cls, c in sorted(classes.items(), key=lambda kv: -(kv[1].tp + kv[1].fp + kv[1].fn)):
            out.append(f"{cam:14s} {cls:16s} {c.tp:3d} {c.fp:3d} {c.fn:3d} {c.fn_port:7d}"
                       f"  {_pct(c.precision):>5s} {_pct(c.recall):>5s}")  # fmt: skip
    out += ["", "FALSE POSITIVES (tag, why)"]
    for cam, why in rep.fp_why.items():
        if why:
            out.append(f"{cam:14s} " + ", ".join(f"{k} ×{n}" for k, n in why.most_common()))
    return "\n".join(out)


def _row(name: str, c: Counts) -> str:
    return (f"{name:14s} {c.frames:6d} {c.tp:3d} {c.fp:3d} {c.fn:3d} {c.fn_port:7d}  "
            f"{_pct(c.precision):>5s} {_pct(c.recall):>5s} {_pct(c.recall_port):>10s}   "
            f"{c.events:6d} {c.false_events:5d}  "
            f"{c.frames_hit:3d}/{c.frames_with_objects}")  # fmt: skip


def format_sweep(labels: lb.LabelSet, preds: Mapping[str, Any], iou_min: float) -> str:
    cams = list(labels.cameras)
    out = ["THRESHOLD SWEEP (precision% / recall% per camera; all = pooled)", "",
           "THR    " + "".join(f"{c:>16s}" for c in [*cams, "all"])]  # fmt: skip
    for t in SWEEP:
        rep = evaluate(labels, preds, t, iou_min)
        cells = [*rep.per_camera.values()]
        total = Counts()
        for c in cells:
            total += c
        out.append(f"{t:<5.2f}  " + "".join(
            f"{_pct(c.precision):>9s} /{_pct(c.recall):>5s}" for c in [*cells, total]))  # fmt: skip
    return "\n".join(out)


def camera_filters(labels: lb.LabelSet) -> dict[str, dict[str, Any]]:
    """detector_filters per camera as provisioned (detection.yaml + cameras.yaml)."""
    from scripts import detection as det
    from sim import gen_compose

    fleet = gen_compose.load(gen_compose.DEFAULT_CONFIG)
    detection = det.load()
    out = {}
    for cam in fleet.cameras:
        if cam.id in labels.cameras:
            details = det.details_for(detection, cam, fleet.defaults)
            if details.get("detector_filters"):
                out[cam.id] = details["detector_filters"]
    return out


def main(args: argparse.Namespace) -> int:
    labels = lb.load(args.labels)
    low, preds = predict.load()
    if args.threshold < low:
        raise lb.LabelsError(f"predictions were made at >= {low}; --threshold {args.threshold}")
    todo = sum(not f.done for cs in labels.cameras.values() for f in cs.frames)
    note = f" ({todo} frames not labelled yet, skipped)" if todo else ""
    rep = evaluate(labels, preds, args.threshold, args.iou)
    flt = camera_filters(labels) if args.filters else {}
    rep_f = evaluate(labels, preds, args.threshold, args.iou, flt) if flt else None
    if args.json:
        doc = {"threshold": args.threshold, "iou": args.iou,
               "plugin": {c: asdict(v) for c, v in rep.per_camera.items()}}  # fmt: skip
        if rep_f:
            doc["filtered"] = {c: asdict(v) for c, v in rep_f.per_camera.items()}
        print(json.dumps(doc, indent=1))
        return 0
    print(format_report(rep, f"PLUGIN BOXES, score >= {args.threshold}, IoU >= {args.iou}{note}"))
    if rep_f:
        print()
        print(format_report(rep_f, f"AFTER SHINOBI EVENT FILTERS ({', '.join(flt)})"))
    print()
    print(format_sweep(labels, preds, args.iou))
    return 0
