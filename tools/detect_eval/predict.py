"""Run the plugin's own detector over the labelled frames -> data/detection/predictions.json.

    docker run --rm  <pinned plugin image>  yolov9-venv/bin/python3 /eval/predict_in_plugin.py
         /frames (ro) ── cv2.imread ──▶ OnnxYoloDetector(yolov9t.onnx, conf=LOW, iou=0.45)
                                        = the class the plugin's Flask server runs
         stdout: {"<cam>/<frame id>": [{tag, confidence, x, y, width, height}, ...]}

Run once at a LOW score threshold (0.25) and keep every box with its score: scoring then applies
any threshold >= 0.25 without running the network again (one curve instead of N runs).
Same weights, same letterbox/NMS code and the same JPEG input as production; what differs is
only the transport (no socket.io/HTTP), which does not change detections.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from tools.detect_eval import frames as fr
from tools.detect_eval import labels as lb

LOW_THRESHOLD = 0.25
OUT = fr.DATA / "predictions.json"
PLUGIN_DIR = "/usr/src/app/plugins/yolo-v9-onnx"

# Runs inside the plugin image (Python 3.9, its venv). Kept tiny: import the vendor class, loop.
_IN_PLUGIN = f"""
import json, sys, glob, os, time
import cv2
sys.path.insert(0, "{PLUGIN_DIR}/tools")
from yolov9_onnx_server import OnnxYoloDetector
d = OnnxYoloDetector("{PLUGIN_DIR}/weights/yolov9t.onnx", "{PLUGIN_DIR}/weights/classes.txt",
                     "cpu", float(sys.argv[1]), 0.45)
out = {{}}
for path in sorted(glob.glob("/frames/*/*.jpg")):
    cam = os.path.basename(os.path.dirname(path))
    fid = os.path.basename(path)[:-4]
    out[cam + "/" + fid] = d.detect(cv2.imread(path, cv2.IMREAD_COLOR))
json.dump(out, sys.stdout)
"""


def plugin_image() -> str:
    digest = os.environ.get("YOLO_PLUGIN_DIGEST") or _env_file_value("YOLO_PLUGIN_DIGEST")
    if not digest:
        raise lb.LabelsError("YOLO_PLUGIN_DIGEST not set (.env)")
    return f"registry.gitlab.com/shinobi-systems/shinobi-plugins/yolo-v9-onnx@{digest}"


def _env_file_value(key: str) -> str | None:
    from services.common.envfile import load_env

    return load_env(lb.REPO / ".env").get(key)


def run(labels: lb.LabelSet) -> int:
    frames_dir = fr.DATA / "frames"
    missing = [
        f"{cam}/{f.id}"
        for cam, cs in labels.cameras.items()
        for f in cs.frames
        if not fr.frame_path(cam, f.id).exists()
    ]
    if missing:
        raise lb.LabelsError(f"{len(missing)} frames missing, run `frames` first: {missing[:3]}")
    cmd = [
        "docker", "run", "--rm", "--network", "none", "--entrypoint",
        f"{PLUGIN_DIR}/yolov9-venv/bin/python3",
        "-e", "OMP_NUM_THREADS=2",
        "-v", f"{frames_dir}:/frames:ro",
        plugin_image(), "-c", _IN_PLUGIN, str(LOW_THRESHOLD),
    ]  # fmt: skip
    res = subprocess.run(cmd, check=True, capture_output=True, text=True)
    preds = json.loads(res.stdout[res.stdout.index("{") :])  # skip the detector's [onnx] banner
    OUT.write_text(json.dumps({"threshold": LOW_THRESHOLD, "frames": preds}, indent=1),
                   encoding="utf-8")  # fmt: skip
    boxes = sum(len(v) for v in preds.values())
    print(f"{len(preds)} frames, {boxes} boxes >= {LOW_THRESHOLD} -> {OUT.relative_to(lb.REPO)}")
    return 0


def load(path: Path = OUT) -> tuple[float, dict[str, list[dict]]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return float(raw["threshold"]), raw["frames"]
