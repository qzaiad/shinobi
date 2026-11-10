"""Regenerate the labelled JPEGs from the pinned clips, the way the plugin gets them live.

    media/<clip>.mp4 (normalized, 1280x720 25 fps)
      │ stage 1 = the camera's sub branch (compose/cameras.generated.yml):
      │   fps=<sub fps>, scale=640:360, OSD drawtext (fixed clock text), x264 Main, no B-frames,
      │   GOP 2 s, <sub bitrate>          -> data/detection/sub/<cam>.mp4
      │ stage 2 = Shinobi's object output (S15 §4): `-vf fps=2 -f mjpeg` with ffmpeg's default
      │   MJPEG rate control (no -q: 200 kb/s, ~12 KB per frame at 2 fps); played twice, the
      │   first pass only warms up the rate control
      ▼
    data/detection/frames/<cam>/<frame id>.jpg      (gitignored: derived from the footage)

Why not grab frames from the running stream: its timing is not reproducible, and Shinobi's
Timelapse Frames (S15 §10) exist only where YOLO already found something (recall would be
inflated). Host ffmpeg is not the camera's (5.1) or Shinobi's (4.3), so pixels differ slightly
from the live path; the labels (boxes on objects) do not depend on that.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from sim import gen_compose
from tools.detect_eval import labels as lb

DATA = lb.REPO / "data" / "detection"
OSD_EPOCH = 1_767_225_600  # 2026-01-01T00:00:00Z: the OSD clock reads this at the clip start
_WALL_CLOCK = "%{gmtime\\:%Y-%m-%d %T}"
_PTS_CLOCK = f"%{{pts\\:gmtime\\:{OSD_EPOCH}\\:%Y-%m-%d %T}}"


@dataclass(frozen=True, slots=True)
class SubProfile:
    cam: str
    clip: Path
    width: int
    height: int
    fps: float
    bitrate: str
    gop: int


def sub_profiles(fleet: gen_compose.Fleet) -> list[SubProfile]:
    out = []
    for cam in fleet.cameras:
        sub = gen_compose.resolve_sub(cam, fleet.defaults)
        if sub is None:
            continue
        out.append(
            SubProfile(cam.id, lb.REPO / "media" / cam.clip, sub.width, sub.height, sub.fps,
                       sub.bitrate, sub.gop)
        )  # fmt: skip
    return out


def stage1_cmd(p: SubProfile, out: Path) -> list[str]:
    """Pure: clip -> sub stream as the camera publishes it: gen_compose's own branch, OSD and
    encoder flags, except that the OSD clock follows the frame PTS (from OSD_EPOCH) instead of
    the wall clock, so every run burns the same digits into the same frame."""
    filters = gen_compose.branch(p.fps, p.width, p.height, p.cam, osd=False).split(",")
    osd = gen_compose.osd_filters(p.cam, p.height)
    filters += [f.replace(_WALL_CLOCK, _PTS_CLOCK) for f in osd]
    enc = gen_compose.encoder_output("x", p.bitrate, p.gop, "veryfast", "tcp", "x")
    enc = enc[enc.index("-c:v") : enc.index("-f")]  # codec flags only, no -map / RTSP output
    return [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(p.clip),
        "-vf", ",".join(filters), "-an", *enc, str(out),
    ]  # fmt: skip


def stage2_cmd(sub: Path, out_dir: Path, fps: float) -> list[str]:
    """Pure: sub stream played twice -> JPEG sequence, Shinobi's `-vf fps=2 -an -f mjpeg` (no
    quality flag). Without -q the MJPEG encoder aims at 200 kb/s and adapts its quantizer, so
    the first frames of a sequence are 2-4x bigger (sharper) than in a long-running stream;
    only the second pass is used (second_pass())."""
    return [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-stream_loop", "1", "-i", str(sub),
        "-vf", f"fps={fps:g}", "-an", "-c:v", "mjpeg", "-start_number", "0",
        str(out_dir / "%05d.jpg"),
    ]  # fmt: skip


def second_pass(seq: list[Path]) -> list[Path]:
    """Pure: the frames of the second play-through (rate control settled, as live)."""
    return seq[len(seq) // 2 :]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def frame_path(cam: str, frame_id: str) -> Path:
    return DATA / "frames" / cam / f"{frame_id}.jpg"


def generate(labels: lb.LabelSet, fleet: gen_compose.Fleet, init: bool) -> lb.LabelSet:
    """Write the JPEGs of every labelled frame. init=True: add cameras missing from the label set
    with FRAMES_PER_CAMERA evenly spaced, empty frames (existing labels are never touched)."""
    for p in sub_profiles(fleet):
        if not p.clip.exists():
            raise lb.LabelsError(f"{p.clip} missing: run ./scripts/fetch_media.sh + normalize.sh")
        digest = sha256(p.clip)
        cs = labels.cameras.get(p.cam)
        if cs is not None and cs.clip_sha256 != digest:
            raise lb.LabelsError(
                f"{p.cam}: {p.clip.name} sha256 {digest[:12]}… differs from the labelled clip "
                f"{cs.clip_sha256[:12]}…: the labels describe other pixels"
            )
        if cs is None and not init:
            continue
        work = DATA / "work" / p.cam
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        sub = work / "sub.mp4"
        subprocess.run(stage1_cmd(p, sub), check=True)
        subprocess.run(stage2_cmd(sub, work, labels.fps), check=True)
        seq = second_pass(sorted(work.glob("*.jpg")))
        if cs is None:
            cs = lb.CameraSet(
                clip=p.clip.name,
                clip_sha256=digest,
                frames=[
                    lb.Frame(id=f"{p.cam}-{k:02d}", index=i)
                    for k, i in enumerate(lb.pick_indices(len(seq)))
                ],
            )
            labels.cameras[p.cam] = cs
        out = DATA / "frames" / p.cam
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True)
        for f in cs.frames:
            if f.index >= len(seq):
                raise lb.LabelsError(f"{f.id}: index {f.index} >= {len(seq)} frames in the clip")
            shutil.copyfile(seq[f.index], frame_path(p.cam, f.id))
        shutil.rmtree(work)
        print(f"{p.cam}: {len(cs.frames)} frames of {len(seq)} -> {out.relative_to(lb.REPO)}")
    return labels
