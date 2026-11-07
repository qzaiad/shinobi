# Session 15 — Object detection plugin

Goal: Shinobi events that say **what** is in the picture (`truck`, `car`, `person`, `boat`) with a
confidence, from a YOLO detector plugin, on `cam-gate` and `cam-quay`.

Result: Shinobi's official **YoloV9 ONNX plugin** (CPU build) runs as compose service `yolo`. It
gets 640×360 JPEGs from the sub stream at 2 fps for 5 s after each motion trigger ("check for
motion first"), and Shinobi saves object events with labels, box scores and the plugin's processing
time (~300 ms). Gate: ~9.6 object events/min (truck, car); quay: ~13/min (truck, person).

On the way, four problems that would each have silently broken detection in a real deployment.
All four are fixed in a thin derived image (`services/yolo-plugin/`) or in config, with no
vendor file edited:

| # | Problem | Effect | Fix |
|---|---|---|---|
| 1 | ONNX Runtime default threads (§6.2) | 12 cores busy per inference; under `cpus: 2` **10× slower** | `sitecustomize.py`: 2 threads, no spinning |
| 2 | Plugin gives up reconnecting (§7) | container "Up", never detects again | `supervise.sh`: exit, Docker restarts it |
| 3 | No backpressure (§8) | congestion collapse, events **52 s late** and growing | `backpressure.js`: max 2 frames in flight, rest dropped |
| 4 | One lock for motion and object (§9) | object events starved (2/min instead of ~9) | `detector_lock_timeout` 5000 → 2000 |

Pinned images: Shinobi `dev` @ `sha256:aa5cd832…` (Session 8); plugin
`registry.gitlab.com/shinobi-systems/shinobi-plugins/yolo-v9-onnx:cpu` @ `sha256:c930b586…`
(built 2026-07-26, 2.2 GB). Shinobi code references are paths inside the Shinobi image
(`/home/Shinobi/…`), plugin references are paths inside the plugin image
(`/usr/src/app/plugins/yolo-v9-onnx/…`).

## 1. Terms

| Term | Meaning here |
|---|---|
| **Object detector** | A neural network that takes one picture and returns boxes, each with a class **label** and a **score** (confidence 0..1). Unlike motion detection (S14) it sees a parked truck, and it knows a truck from a wave. |
| **YOLO** | "You Only Look Once": a family of one-pass detectors. **YOLOv9** (2024); `t`/`s`/`m`/`c`/`e` = tiny … extra large. We use **yolov9t** (8.7 MB, 80 classes). |
| **COCO** | The image dataset the model was trained on: 80 everyday classes. Has `person`, `car`, `truck`, `bus`, `boat`, `train`, `traffic light`; has **no** container, crane, reach stacker, straddle carrier (S16). |
| **ONNX / ONNX Runtime** | ONNX = a file format for trained networks; ONNX Runtime (ORT) = the C++ engine that runs it (here CPU "execution provider"). |
| **Letterbox** | Scale the frame to fit 640×640 without distorting it, pad the rest grey. A 640×360 frame becomes 640×360 + 2×140 px padding. |
| **Score threshold** | Boxes below `confidence` (plugin default 0.5) are dropped inside the plugin. |
| **NMS, IoU** | Non-maximum suppression: of several overlapping boxes for one object keep the best. Overlap = IoU (intersection over union), threshold 0.45. |
| **Detector plugin** | A separate process that receives frames from Shinobi and sends detections back. Shinobi itself never runs a network. |
| **Client / host mode** | Who dials whom. *Client*: the plugin connects to Shinobi (we use this). *Host*: Shinobi connects to the plugin, listed in `conf.json` `plugins[]`. |
| **Plugin key** | Shared secret: Shinobi accepts a plugin named `X` only if it sends `conf.json` `pluginKeys.X`. |
| **Check for motion first** | `detector_use_motion`: frames go to the plugin only for 5 s after a motion trigger. |
| **Backpressure / load shedding** | A slow consumer slows down or drops input, instead of letting a queue grow. |

## 2. Data flow

```
              mediamtx                       shinobi container
 /cam-gate ──────────────▶ ffmpeg (one per monitor, input #0 main, #1 sub)
 /cam-gate-sub ──────────▶  ├─ #0 copy ─▶ HLS live view, MP4 segments            (S9, S10)
                            ├─ #1 fps=5 gray PAM ──pipe:3──▶ pam-diff (motion)    (S14)
                            └─ #1 fps=2 MJPEG 640x360 ──pipe:4──┐                 (NEW)
                                                               ▼
           camera.js main process   reassemble JPEG (…FF D9) per monitor
                                    gate: detector_use_motion=1 → forward only while a
                                          5 s window opened by a motion trigger is open
                                    socket.io server :8080 ──── emit 'f' {f:'frame', id, ke,
                                         ▲                          mon:<monitor details>, frame:<JPEG>}
                                         │                          │
             emit 'ocv' {f:'trigger', id, ke, details:{plug,       ▼
              reason:'object', matrices:[…], imgWidth, imgHeight,   yolo container
              time}, frame:<JPEG>}                                 node pluginBase.js (socket.io CLIENT)
                                         │                          │ multipart POST localhost:8993/upload
                                         └────────────────────────  python Flask + ONNX Runtime yolov9t
                                                                    (letterbox 640², NMS, score ≥ 0.5)
           triggerEvent(): filters → lock → Events row + Timelapse Frame JPEG (§10)
```

The handshake, `libs/plugins.js` `onWebSocketConnection` → `hasValidPluginKey`:

```
plugin                                    shinobi
  │ socket.io connect ws://shinobi:8080     │
  │ ── 'ocv' {f:'init', plug:'YoloV9',  ───▶│ re-read conf.json pluginKeys from disk (since this
  │          type:'detector', pluginKey:K}  │ image: no restart needed after adding a key)
  │                                         │ pluginKeys.YoloV9 === K ?
  │ ◀── (accepted: kept open) ───────────── │ yes → "Connected to plugin : Detector - YoloV9"
  │                                         │ no  → log "no pluginKeys.YoloV9 configured" /
  │ ◀── disconnect ──────────────────────── │       "does not match", disconnect
  │ ◀── 'f' {f:'monitorUpdate', …} × n ──── │ copy of every monitor config (onPluginConnected)
```

`GET /<api key>/plugins/<ke>/connected` lists connected plugins (any valid key; client
`connected_plugins()`, CLI `shinobi_client plugins`). An empty `{}` is the only API-visible sign
that detection is off.

## 3. Choosing and wiring the plugin

- The Shinobi image ships **no detector plugin** (`/home/Shinobi/plugins` has only
  `pluginBase.js`, `pluginCheck.js`, `pluginWorkerBase.js`). The official ones are separate images in
  `registry.gitlab.com/shinobi-systems/shinobi-plugins/`.
- **yolo-v9-onnx** chosen: maintained, no GPU needed, `yolov9t.onnx` + `classes.txt` baked in (works
  offline), any Ultralytics-style ONNX model can replace it later (S16/S23). Tags: `cpu`, `cuda`,
  `rocm`, `openvino`; no GPU on this laptop (Intel Core Ultra 7 265U, 14 logical CPUs).
- **License: AGPL-3.0 or Shinobi Enterprise** (plugin `LICENSE`); the YOLOv9 weights are GPL-3.0.
  Fine for this project, recorded in [ADR 0002](../adr/0002-shinobi-license.md).
- Pairing as code:

  ```
  scripts/shinobi-seed.sh ── SHINOBI_PLUGIN_KEY=<48 hex> ──▶ .env      (once, before compose up)
  docker-compose.yml
    shinobi: PLUGIN_KEYS='{"YoloV9":"${SHINOBI_PLUGIN_KEY}"}' ─▶ init.sh ─▶ conf.json pluginKeys
    yolo:    PLUGIN_KEY=${SHINOBI_PLUGIN_KEY}, HOST=shinobi, PORT=8080
  ```

  `init.sh` rewrites `pluginKeys` from `PLUGIN_KEYS` on **every** start: a key added to
  `conf.json` by hand works at once (re-read on connect) and is gone after the next restart
  (reproduced). An empty key is always rejected, so a missing `.env` value fails closed.
- `PLUGIN_NAME` (in Shinobi's docs) is written to the plugin's conf field `name`, **not** `plug`:
  the plugin always identifies as `YoloV9` (from its baked `conf.json`). The docs' example
  `"yourkey": "YoloV9 Object Detection"` has the mapping backwards; the code wants
  `pluginKeys[<plug>] = <key>`.
- **The key is logged in clear text** in both containers: the plugin's entrypoint prints
  `key: …`, and Shinobi's `pluginInitiatorSuccess` log line prints `pluginKey`. Treat
  `docker compose logs` like `.env`.
- The plugin image declares `VOLUME …/weights`: compose creates an anonymous volume and **keeps it
  across container recreation**, so a new plugin digest with new baked weights would still see the
  old ones. Irrelevant while we use the baked `yolov9t`; remove the volume
  (`docker compose rm -v yolo`) when changing models.

## 4. Monitor settings (object detection as code)

`shinobi-config/detection.yaml` got an `object:` block (defaults + per camera; absent = off);
`scripts/detection.py` renders it (keys from `libs/ffmpeg/builders.js` `buildMainDetector` and
`libs/monitor/utils.js`):

| detection.yaml | Shinobi key | Value | Why / what breaks otherwise |
|---|---|---|---|
| (block present) | `detector_use_detect_object` | `"1"` | adds the `pipe:4` MJPEG output |
| | `detector_send_frames_object` | `"1"` | with pam on, `"0"` would suppress pipe:4 |
| `input: sub` | `input_map_choices.detector_object` | `[{"map":"1:0"}]` | the sub stream, already input #1 for motion: no extra RTSP pull |
| | `detector_scale_x/y_object` | `640`/`360` | Shinobi's default **1280×720** would *upscale* the sub stream; YOLO letterboxes to 640 anyway, so 640×360 carries the same information at ¼ the pixels |
| `fps: 2` | `detector_fps_object` | `"2"` | JPEGs per second; default = `detector_fps` (5) |
| `check_motion_first: true` | `detector_use_motion` | `"1"` | §9 |
| `plugins: [YoloV9]` | `detectors_selected` | `["YoloV9"]` | `["all"]` = every connected plugin |

The generated command gets one more output on the same decode of input #1 (Logs, `cam_gate`):

```
-map 1:0 -vf "fps=5" -s 640x360 -an -c:v pam -pix_fmt gray -f image2pipe pipe:3   ← motion (S14)
-map 1:0 -s 640x360 -vf "fps=2" -an -f mjpeg pipe:4                               ← object (S15)
```

Cost of that output: +0.1–0.2 % CPU per camera (cam_gate ffmpeg 5.0 → 5.1 %, cam_quay 5.7 →
5.9 %). FFmpeg encodes the JPEGs **all the time**; "check for motion first" only decides whether
Shinobi forwards them.

The provisioner saved the change as a live update (no recording gap). Flags are stored as strings
(S14 §8), so `=== '1'` checks hold.

## 5. Offline baseline (before touching Shinobi)

The plugin's own detector class (`tools/yolov9_onnx_server.py` `OnnxYoloDetector`) run inside the
plugin image on 1 fps frames of each clip, 640×360, score ≥ 0.5:

```
gate      frames= 55 with_det= 55 p50=96ms | truck 62 (max 0.88), car 27 (max 0.93), traffic light 5 (max 0.54)
quay      frames=  9 with_det=  9 p50=98ms | truck 15 (max 0.83)
waterway  frames=160 with_det= 52 p50=96ms | boat 58 (max 0.91)
yard      frames=142 with_det=  0 p50=96ms |
```

- **yard: nothing.** Containers, stacks and reach stackers are not COCO classes (S16).
- Score 0.3 instead of 0.5: more boxes, mostly junk (gate `traffic light` 5 → 53, waterway
  `train`, `kite`, `stop sign`).
- 1280×720 input: same labels and scores (letterbox scales to 640 anyway), up to 184 ms p95.
- yolov9s (29 MB): 2× the time (191 ms), slightly higher scores (gate truck max 0.93 vs 0.88),
  same picture. **yolov9t kept**; a port-specific model is S16/S23 material.

## 6. CPU: ONNX Runtime threads

### 6.1 The surprise

```
                              wall/frame   CPU/frame    note
default, no limit                95 ms      1134 ms     = 12 cores busy for 95 ms
default, --cpus 2               965 ms      1928 ms     10× slower
default, --cpuset-cpus 0-1     1362 ms      2711 ms     + "pthread_setaffinity_np failed" ×10
```

`ort.InferenceSession(model, providers=…)` without `SessionOptions` → ORT creates one intra-op
thread **per core of the host** (14; it does not look at the cgroup CPU quota) and lets idle
threads **spin** (busy-wait) between operators. Without a limit that buys 95 ms with 12 cores.
With `cpus: 2` (CFS quota: 200 ms CPU per 100 ms period), 14 runnable threads exhaust the quota
early in each period, the container is throttled for the rest of it, and threads waiting at a
barrier for a throttled sibling spin away the quota. `--cpuset-cpus` is worse: ORT tries to pin its
14 threads to CPUs that are not in the set.

### 6.2 The real cost (own session, 30 gate frames)

```
threads=1 spin=0   wall=215ms cpu= 220ms/frame  max_fps=4.7
threads=2 spin=1   wall=123ms cpu= 254ms/frame  max_fps=8.1
threads=2 spin=0   wall=140ms cpu= 265ms/frame  max_fps=7.1
threads=4 spin=0   wall=121ms cpu= 392ms/frame  max_fps=8.3
threads=6 spin=0   wall=104ms cpu= 448ms/frame  max_fps=9.6
```

One inference of yolov9t on a 640×360 frame ≈ **220 ms of CPU**. More threads cut latency but
burn more CPU per frame. Chosen: **2 threads, no spinning**, `cpus: 2` on the container:
`services/yolo-plugin/sitecustomize.py` injects `SessionOptions` when the plugin passes none
(Python imports `sitecustomize` from `PYTHONPATH` at startup; vendor files untouched). Env:
`ORT_INTRA_OP_THREADS` (compose `YOLO_THREADS`), `ORT_ALLOW_SPINNING`.

In production, inside the plugin, a frame costs more than the bare inference: JPEG decode,
multipart HTTP to Flask, socket.io, two cameras in parallel. Measured ≈ 340 ms wall and ≈ 450 ms
CPU per frame (§8.2).

## 7. Finding: the plugin silently stops reconnecting

`pluginBase.js` (client mode):

```js
const MAX_RETRY = parseInt(config.maxRetryConnection, 10) || 5;
const reconnect = () => {
    if(stayDisconnected)return;
    if (++retry > MAX_RETRY){ stayDisconnected = true; s.disconnectWebSocket(); return plugLog("Max retries reached"); }
    setTimeout(connect, 3_000);
};
["disconnect", "connect_error", "error"].forEach((ev) => sock.on(ev, reconnect));
```

- A successful TCP connect resets `retry = 0`. A **rejected key** therefore retries every 3 s
  forever (Shinobi logs `no pluginKeys."YoloV9" configured` every 3 s).
- Shinobi **down**: one failed attempt fires up to three of these events, so "5 retries" ran out
  **5 s** after `docker compose stop shinobi` (expected 15 s).
- After that, `stayDisconnected` is permanent, but **the node process keeps running**.
  Reproduced with the vanilla image: Shinobi restarted, key correct again → 0 log lines, 0 connect
  attempts in 60 s, `plugins/<ke>/connected` = `{}`, container `running`, restart count 0.
- It also happens on a cold `sim-up`: Shinobi needs 10–45 s to boot, the plugin needs ~1 s.

Fix: `services/yolo-plugin/supervise.sh` is the entrypoint. It runs the vendor entrypoint, passes
its output through and exits 1 on `Max retries reached`, so `restart: unless-stopped` starts a fresh
process (model load ~1 s). Verified:

```
13:18:32  docker compose stop shinobi
13:18:38  YoloV9 Max retries reached
          [supervise] plugin stopped reconnecting to Shinobi; exiting so Docker restarts it
13:18:39 … 13:19:13   6 restarts, Docker back-off 1 → 4 s
13:19:03  docker compose start shinobi
13:19:13  YoloV9 Connected to host            (Shinobi itself needed ~10 s)
cold sim-up 13:45:53 → 1 restart → connected 13:46:09 (16 s)
```

## 8. Finding: no backpressure → congestion collapse

### 8.1 What happened

Continuous mode (`check_motion_first: false`), 2 cameras × 2 fps = 4 JPEG/s, `cpus: 2`, vanilla
plugin flow: the plugin's `time` field (frame received → answer, so it includes waiting) per event:

```
cam_gate 13:26:19   5800 ms        cam_quay 13:26:20   5910 ms
cam_gate 13:27:13  23695 ms        cam_quay 13:27:14  20294 ms
cam_gate 13:28:13  37089 ms        cam_quay 13:28:19  38298 ms
cam_gate 13:29:03  41810 ms        cam_quay 13:29:16  52489 ms     ← +13 s per minute, unbounded
```

Nobody waits for anybody:

```
Shinobi:  every JPEG ──emit──▶ (no ack, no window)
plugin:   on 'frame' → detect(buf) immediately → POST /upload (no limit)
Flask:    one thread per request → N concurrent session.run(), 2 ORT threads each, on 2 cores
```

A small burst (the monitors restart after the config change) puts 3+ inferences in parallel.
Each one gets slower (2N threads on 2 cores, CFS throttling: plugin throttled in **600/600**
periods), throughput falls below the arrival rate, the backlog grows, more requests run in
parallel. It never recovers. That is **congestion collapse**, not a capacity problem (§8.2).

Why it matters: **Shinobi stamps an event when the trigger arrives** (`Events.time`), and the
trigger carries no frame time. After 3 minutes, an event at 13:29:16 described a frame from
13:28:24, so a twin would have shown a truck at the gate almost a minute after it left.

### 8.2 Fix and result

`services/yolo-plugin/backpressure.js`, loaded with `NODE_OPTIONS=--require`, wraps the plugin's
`detect()` (it hooks `require('./libs/multiYolo')`): at most `YOLO_MAX_IN_FLIGHT` (2) frames in
inference, a frame arriving while 2 are busy is dropped (empty result = no detection). It logs once
a minute:

```
[backpressure] last 60 s: inferred 237 (avg 339 ms), dropped 0
[backpressure] last 60 s: inferred 239 (avg 330 ms), dropped 0
```

Same load as §8.1: all 4 fps inferred, 0 dropped, plugin `time` avg 343 ms, max 472 ms (was 52 s
and growing). The plugin *can* keep up; it just must never run more than it has cores for. A drop
loses one frame; a queue loses the meaning of every later event.

## 9. Finding: motion and object share one lock

`libs/events/utils.js` `triggerEvent()`, the same function for motion and object triggers:

```
checkEventFilters → checkMotionLock (detector_lock_timeout, ONE timer per monitor) →
   d.doObjectDetection = reason !== 'object' && plugin connected && use_detect_object && use_motion
   doObjectDetection ? sendFramesFromSecondaryOutput(…, 5000 ms)    ← open/extend the frame window
                     : runEventExecutions()                         ← save the Events row
```

Two consequences of "check for motion first":

1. A motion trigger **no longer saves a motion row**: it only opens the frame window. With
   object detection on, Events holds object events only (verified: 0 motion rows;
   `tests/test_motion.py` now skips such cameras).
2. With our S14 lock of 5 s:

```
t=0     motion trigger → passes lock → lock until 5.0 s, frame window until 5.0 s
t=0.5   JPEG → YOLO → "truck 0.8" → triggerEvent → lock held → dropped ("Ignored : Motion Lock")
t=1-4.5 every detection dropped; motion triggers dropped too → window NOT extended
t=5.0   lock free, window closes at the same moment → only a late answer gets through
```

Measured (2 fps, load shedding on, 3 min each):

| mode | lock | yolo CPU | gate obj/min | quay obj/min | plugin ms avg |
|---|---|---|---|---|---|
| motion first | 5 s | 0.8–1.0 core | 2.0 | 3.3–4.0 | 280–310 |
| continuous, no shedding | 5 s | 2.0 (pinned) | 6.3 | 5.3 | 25 000 and growing |
| continuous + shedding | 5 s | 1.8 | 8.0 | 7.3 | 340 |
| **motion first + shedding** | **2 s** | **1.1** | **9.3** | **13.0** | **300** |

The plugin found trucks in most frames in the 5 s-lock run (140–200 inferences/min), and the lock
threw them away. **Chosen: motion first, 2 s lock.** It costs 40 % less CPU than continuous and
scales with activity, not with the number of cameras. The quay hoist moves almost all the time,
so quay's window is nearly always open; a quiet camera costs nothing. Several events per truck are
expected now: one-event-per-object (tracking) is the evaluator's job (S22).

## 10. What an object event looks like

`events/<ke>/cam_gate` (fixture `tests/fixtures/shinobi/events_cam_gate_object.json`):

```json
{"ke": "GROUP_KEY", "mid": "cam_gate", "time": "2026-11-07T13:44:59.000Z", "archive": 0,
 "details": {"plug": "YoloV9", "name": "yolov9-onnx", "reason": "object",
             "matrices": [{"tag": "car",   "confidence": 0.65, "x": …, "y": …, "width": …, "height": …},
                          {"tag": "truck", "confidence": 0.53, …}],
             "imgWidth": 640, "imgHeight": 360, "time": 317}}
```

- `matrices[].x/y/width/height`: pixels of the **640×360 JPEG** (`imgWidth/imgHeight`), top-left
  origin, not of the 1280×720 recording.
- `details.confidence` is absent (motion events have one); the score is per box, 0..1 (motion:
  % of region).
- `details.time` = plugin processing ms, *not* a timestamp (client: `Event.processing_ms`).
- `time` = when Shinobi accepted the trigger, whole seconds; the frame is `processing_ms` older.
- Each accepted object event also stores the JPEG as a **Timelapse Frame**
  (`videos/<ke>/<mid>_timelapse/<date>/…jpg` + row in `Timelapse Frames`, boxes in `details`;
  `saveImageFromEvent`, at most 1/s per monitor): 83 + 123 frames in 10 min, ~15 KB each,
  ≈ 170–230 MB per camera per day. S16 can use them as frames with model boxes.

## 11. Tools

| Command | What |
|---|---|
| `uv run python -m services.common.shinobi_client plugins` | connected detector plugins |
| `uv run python -m services.common.shinobi_client events cam_gate` | events incl. labels, scores, `[plugin ms]` |
| `uv run python scripts/object_stats.py [--minutes N] [mid …]` | object events/min, plugin ms p50/p90/max per monitor; per label share and score p50/p90/max |
| `docker compose logs yolo \| grep backpressure` | inferred/dropped per minute |
| `uv run pytest -m e2e tests/test_object_detection.py` | plugin connected, ≥ 3 labelled events/min, p90 ≤ 2 s, no motion rows |

## 12. Measurements (final config)

See §13 for the done-when run. CPU per camera with object detection (motion first, quay window
almost always open): ffmpeg +0.1–0.2 %, plugin ≈ 1.1 cores for two cameras on CPU. 14 cameras
(the license ceiling, ADR 0002) would need ≈ 5–8 cores of plugin CPU at this rate: a GPU, a smaller
input, fewer fps, or tighter motion gating.

Labels over 4.4 min (13:37:30–13:41:53):

```
MONITOR        LABEL            EVENTS   /MIN SHARE%      P50    P90    MAX   (* = plugin ms, label = score)
cam_gate       *                    42    9.6             319    401    467
               truck                31    7.1     74     0.63   0.72   0.77
               car                  16    3.6     38     0.83   0.88   0.88
               traffic light        14    3.2     33     0.53   0.59   0.60
               train                 1    0.2      2     0.76   0.76   0.76
cam_quay       *                    58   13.2             287    389    457
               truck                56   12.8     97     0.78   0.85   0.86
               person               21    4.8     36     0.56   0.61   0.65
               bus                   1    0.2      2     0.56   0.56   0.56
```

- gate `traffic light` (8×13 px box at x 98, y 240, scores 0.50–0.60): a third of gate events,
  some carry nothing else. A fixed false positive; event filters / min box size in S16.
- quay `train`/`bus`: occasional mislabels of the truck or the crane portal.
- quay `person` 0.56 median: plausible (workers), unverified until S16's labelled set.

## 13. Done when

Events with labels and confidences appear. Final run after a cold `sim-down && sim-up` at
13:45:53 (plugin restarted once by `supervise.sh` while Shinobi booted, connected 13:46:09; both
`plan`s exit 0):

```
window 13:47:32-13:51:32 UTC (4.0 min), object events only
MONITOR        LABEL            EVENTS   /MIN SHARE%      P50    P90    MAX   (* = plugin ms, label = score)
cam_gate       *                    36    9.0             220    257    299
               truck                25    6.2     69     0.57   0.64   0.79
               car                  13    3.2     36     0.83   0.88   0.89
               traffic light        12    3.0     33     0.53   0.57   0.60
cam_quay       *                    54   13.5             226    272    283
               truck                54   13.5    100     0.81   0.85   0.86
               person               21    5.2     39     0.54   0.61   0.64
               traffic light         1    0.2      2     0.57   0.57   0.57

[backpressure] last 60 s: inferred 161 (avg 235 ms), dropped 0     (125-165/min, 0-2 dropped)
yolo: 96 % of a core over 60 s, throttled periods +6 (of 600)
shinobi container 17.1 % (ffmpeg cam_gate 5.1 %, cam_quay 5.9 %; motion-only baseline 22.5 %*)
```

\* The baseline was taken while both cameras still had S14's motion settings; the container total
varies ±5 % between runs (laptop clock), so the extra MJPEG output is within the noise.

`uv run pytest -m e2e`: 23 passed, 6 skipped (test_motion on the two motion-first cameras).
`uv run pytest`: 188 passed.

## 14. Insights

1. Shinobi only routes frames; detection quality and cost live in a separate process with its
   own failure modes. Monitor the plugin like a camera (connected? latency? drops?).
2. A detector must shed load. Without a bound on in-flight work, a short burst turns into a
   permanent, growing delay, and late events lie because the event time is the arrival time.
3. Libraries that size thread pools from the host's core count misbehave in containers with a
   CPU quota. Set thread counts explicitly.
4. "Up" ≠ working: a process that has given up must exit, so the supervisor can do its job.
5. Feed the model what it uses: YOLO letterboxes to 640, so the 640×360 sub stream is lossless
   input at ¼ the cost of 1280×720.
6. Motion-first gating scales with activity, but in Shinobi it couples to the motion lock: tune
   them together.

## 15. Open items

- S16: COCO vs port (no container/crane classes), `traffic light` false positive, score
  threshold, `detector_obj_region` (objects only inside regions), min box size; labelled set
  (Timelapse Frames as a source).
- S16/S22: several events per vehicle (2 s lock); tracking/dedup in the evaluator.
- S17: object event webhooks and payload; does the webhook carry the frame time?
- S23: own detector path with frame timestamps (capture time instead of arrival time) and a real
  queue/drop policy per camera (ours is global, not fair between cameras).
- Plugin restart while Shinobi runs: frames in flight are lost; not measured.
- Upstream: report the reconnect bug (count once per attempt, exit or retry forever) and the
  missing `SessionOptions`/backpressure.
- `yolo` has no healthcheck; `supervise.sh` covers only the "gave up" path, not a hung Python server.
