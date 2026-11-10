"""tools/detect_eval: labels file, frame commands, matching and metrics (Session 16)."""

from pathlib import Path

import pytest

from sim import gen_compose
from tools.detect_eval import frames as fr
from tools.detect_eval import labels as lb
from tools.detect_eval import score as sc
from tools.detect_eval.labeler import Store

COCO = lb.DEFAULT_CLASSES


def gt(cls: str, x: int, y: int, w: int, h: int, ignore: bool = False) -> lb.Box:
    return lb.Box(cls=cls, xywh=(x, y, w, h), ignore=ignore)


def det(tag: str, score: float, x: float, y: float, w: float, h: float) -> sc.Det:
    return sc.Det(tag, score, (x, y, w, h))


# -- geometry -------------------------------------------------------------------------------


def test_iou() -> None:
    assert sc.iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1
    assert sc.iou((0, 0, 10, 10), (5, 0, 10, 10)) == pytest.approx(50 / 150)
    assert sc.iou((0, 0, 10, 10), (20, 20, 5, 5)) == 0
    assert sc.iou((0, 0, 0, 0), (0, 0, 0, 0)) == 0


def test_ioa_small_box_inside_big_region() -> None:
    assert sc.ioa((10, 10, 2, 2), (0, 0, 100, 100)) == 1
    assert sc.iou((10, 10, 2, 2), (0, 0, 100, 100)) < 0.01


# -- matching -------------------------------------------------------------------------------


def test_hit_miss_false_alarm() -> None:
    res = sc.match_frame(
        [gt("truck", 100, 100, 100, 50), gt("person", 300, 100, 10, 30)],
        [det("truck", 0.8, 105, 100, 100, 50), det("kite", 0.6, 500, 10, 20, 20)],
        COCO,
        0.5,
    )
    assert [g.cls for _, g in res.tp] == ["truck"]
    assert [(d.tag, why) for d, why in res.fp] == [("kite", "background")]
    assert [g.cls for g in res.fn] == ["person"]


def test_class_must_match_and_wrong_class_is_named() -> None:
    res = sc.match_frame([gt("crane", 0, 0, 100, 200)], [det("train", 0.6, 0, 0, 100, 190)],
                         COCO, 0.5)  # fmt: skip
    assert res.fp[0][1] == "on crane"
    assert [g.cls for g in res.fn_port] == ["crane"]  # no COCO class: never findable
    assert not res.fn


def test_best_score_takes_the_label_second_is_duplicate() -> None:
    res = sc.match_frame(
        [gt("car", 0, 0, 50, 50)],
        [det("car", 0.6, 2, 0, 50, 50), det("car", 0.9, 0, 0, 50, 50)],
        COCO,
        0.5,
    )
    assert res.tp[0][0].score == 0.9
    assert res.fp[0][1] == "duplicate"


def test_ignore_label_neither_hit_nor_false_alarm() -> None:
    labels = [gt("person", 600, 200, 6, 14, ignore=True)]
    res = sc.match_frame(labels, [det("person", 0.55, 599, 201, 6, 12)], COCO, 0.5)
    assert not res.tp and not res.fp and not res.fn
    assert len(res.ignored) == 1
    assert sc.count(res).false_events == 0
    res = sc.match_frame(labels, [], COCO, 0.5)
    assert not res.fn  # an ignored label is no miss either


def test_tractor_box_inside_truck_label_is_part_not_false_event() -> None:
    # gate: the plugin boxes only the tractor (72 px) of a truck labelled with its trailer
    res = sc.match_frame([gt("truck", 367, 282, 136, 52)],
                         [det("truck", 0.6, 366, 285, 72, 42)], COCO, 0.5)  # fmt: skip
    assert res.fp[0][1] == "part of truck"
    assert [g.cls for g in res.fn] == ["truck"]  # precision and recall stay strict
    c = sc.count(res)
    assert (c.false_events, c.frames_hit) == (0, 1)


def test_iou_threshold_is_respected() -> None:
    labels, dets = [gt("boat", 0, 0, 100, 100)], [det("boat", 0.9, 40, 0, 100, 100)]  # IoU .43
    assert not sc.match_frame(labels, dets, COCO, 0.5).tp
    assert sc.match_frame(labels, dets, COCO, 0.3).tp


# -- counts ---------------------------------------------------------------------------------


def test_counts_and_rates() -> None:
    c = sc.Counts(tp=3, fp=1, fn=1, fn_port=4)
    assert c.precision == 0.75
    assert c.recall == 0.75
    assert c.recall_port == 3 / 8
    assert sc.Counts().precision is None
    assert (c + c).tp == 6


def test_event_view() -> None:
    empty = sc.count(sc.match_frame([], [], COCO, 0.5))
    assert (empty.events, empty.false_events, empty.frames_with_objects) == (0, 0, 0)
    false = sc.count(sc.match_frame([], [det("kite", 0.6, 0, 0, 5, 5)], COCO, 0.5))
    assert (false.events, false.false_events) == (1, 1)
    missed = sc.count(sc.match_frame([gt("car", 0, 0, 9, 9)], [], COCO, 0.5))
    assert (missed.frames_with_objects, missed.frames_hit) == (1, 0)


def test_detections_threshold_then_filters() -> None:
    raw = [
        {"tag": "traffic light", "confidence": 0.6, "x": 98, "y": 240, "width": 8, "height": 13},
        {"tag": "truck", "confidence": 0.55, "x": 90, "y": 280, "width": 170, "height": 40},
        {"tag": "car", "confidence": 0.3, "x": 0, "y": 0, "width": 10, "height": 10},
    ]
    assert [d.tag for d in sc.detections(raw, 0.5, None)] == ["traffic light", "truck"]
    drop_light = {"f": {"where": [{"p1": "tag", "p2": "===", "p3": "traffic light"}],
                        "actions": {"halt": "1"}}}  # fmt: skip
    assert sc.detections(raw, 0.5, drop_light) == []  # box 0 = light: whole event dropped
    assert [d.tag for d in sc.detections(raw[1:2] + raw[:1], 0.5, drop_light)] == ["truck"]


def test_evaluate_skips_frames_not_done() -> None:
    labels = lb.LabelSet(cameras={"cam-x": lb.CameraSet(
        clip="x.mp4", clip_sha256="0" * 64,
        frames=[lb.Frame(id="cam-x-00", index=0, done=True, boxes=[gt("car", 0, 0, 50, 50)]),
                lb.Frame(id="cam-x-01", index=1, done=False, boxes=[gt("car", 0, 0, 50, 50)])],
    )})  # fmt: skip
    car = {"tag": "car", "confidence": 0.9, "x": 0, "y": 0, "width": 50, "height": 50}
    rep = sc.evaluate(labels, {"cam-x/cam-x-00": [car], "cam-x/cam-x-01": []}, 0.5, 0.5)
    assert rep.per_camera["cam-x"] == sc.Counts(
        tp=1, frames=1, frames_with_objects=1, frames_hit=1, events=1
    )
    assert rep.per_class["cam-x"]["car"].tp == 1


# -- labels file ----------------------------------------------------------------------------


def test_pick_indices_evenly_spread() -> None:
    assert lb.pick_indices(100, 4) == [12, 37, 62, 87]
    assert lb.pick_indices(3, 20) == [0, 1, 2]
    picked = lb.pick_indices(110)
    assert len(picked) == 20 and len(set(picked)) == 20 and picked[-1] < 110


def _one_frame_set(*boxes: lb.Box) -> lb.LabelSet:
    return lb.LabelSet(cameras={"cam-gate": lb.CameraSet(
        clip="gate.mp4", clip_sha256="a" * 64,
        frames=[lb.Frame(id="cam-gate-00", index=2, done=True, boxes=list(boxes))],
    )})  # fmt: skip


def test_dump_load_roundtrip(tmp_path: Path) -> None:
    labels = _one_frame_set(gt("truck", 1, 2, 30, 40), gt("person", 5, 5, 4, 9, ignore=True))
    path = tmp_path / "labels.yaml"
    lb.save(labels, path)
    text = path.read_text(encoding="utf-8")
    assert "- {cls: truck, xywh: [1, 2, 30, 40]}" in text  # one line per box, ignore omitted
    assert "ignore: true}" in text
    assert text.startswith("# Ground truth")
    assert lb.load(path) == labels


@pytest.mark.parametrize(
    ("box", "msg"),
    [
        ({"cls": "whale", "xywh": [0, 0, 5, 5]}, "unknown class"),
        ({"cls": "car", "xywh": [630, 0, 20, 5]}, "outside the frame"),
    ],
)
def test_invalid_labels_refused(box: dict, msg: str) -> None:
    raw = _one_frame_set().model_dump(mode="json")
    raw["cameras"]["cam-gate"]["frames"][0]["boxes"] = [box]
    with pytest.raises(lb.LabelsError, match=msg):
        lb.parse(raw)


def test_zero_size_box_refused() -> None:
    with pytest.raises(ValueError, match="width and height"):
        gt("car", 0, 0, 0, 5)


# -- labeller store -------------------------------------------------------------------------


def test_store_validates_before_writing(tmp_path: Path) -> None:
    path = tmp_path / "labels.yaml"
    lb.save(_one_frame_set(), path)
    store = Store(path)
    before = path.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        store.update(
            "cam-gate", "cam-gate-00", {"boxes": [{"cls": "car", "xywh": [600, 0, 90, 5]}]}
        )
    assert path.read_text(encoding="utf-8") == before  # nothing written
    body = {"done": True, "boxes": [{"cls": "car", "xywh": [1, 1, 9, 9]}]}
    frame = store.update("cam-gate", "cam-gate-00", body)
    assert frame.boxes[0].cls == "car"
    assert lb.load(path).frame("cam-gate", "cam-gate-00").boxes == frame.boxes
    with pytest.raises(KeyError):
        store.update("cam-gate", "../../etc", {})


# -- frame commands -------------------------------------------------------------------------


def test_stage1_reproduces_the_camera_sub_branch() -> None:
    fleet = gen_compose.load(gen_compose.DEFAULT_CONFIG)
    profile = next(p for p in fr.sub_profiles(fleet) if p.cam == "cam-gate")
    cmd = fr.stage1_cmd(profile, Path("out.mp4"))
    vf = cmd[cmd.index("-vf") + 1]
    assert vf.startswith("fps=10,scale=640:360,format=yuv420p,drawtext=")
    assert "%{pts\\:gmtime\\:1767225600\\:%Y-%m-%d %T}" in vf  # deterministic OSD clock
    assert "gmtime\\:%Y" not in vf.replace("pts\\:gmtime\\:1767225600\\:", "")
    assert "text='cam-gate'" in vf
    for flag, value in [("-profile:v", "main"), ("-bf", "0"), ("-g", "20"), ("-b:v", "384k"),
                        ("-sc_threshold", "0")]:  # fmt: skip
        assert cmd[cmd.index(flag) + 1] == value
    assert "-f" not in cmd and "-map" not in cmd  # file output, no RTSP


def test_stage2_is_shinobis_object_output() -> None:
    cmd = fr.stage2_cmd(Path("sub.mp4"), Path("out"), 2)
    assert cmd[cmd.index("-vf") + 1] == "fps=2"
    assert cmd[cmd.index("-c:v") + 1] == "mjpeg"
    assert "-q:v" not in cmd and "-qscale" not in cmd  # Shinobi sets no quality: default RC
    assert cmd[cmd.index("-stream_loop") + 1] == "1"


def test_second_pass() -> None:
    seq = [Path(f"{i}.jpg") for i in range(38)]
    assert fr.second_pass(seq) == seq[19:]
