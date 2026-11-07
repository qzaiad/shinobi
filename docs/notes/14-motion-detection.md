# Session 14 — Motion detection

Goal: motion events from Shinobi's built-in detector that mean something: a vehicle in the gate
lane, a container coming down under the crane. Not the OSD clock, water, camera shake or the
loop seam of the simulated cameras.

Result: motion detection on `cam-gate` (1 region) and `cam-quay` (3 regions), decoded from the
**sub stream**, configured as code in `shinobi-config/detection.yaml`. It fires about once per
vehicle burst and once per container lowering, with 0 events on quiet road and in the shake-only
regions. On the way we found a Shinobi bug: a no-restart ("live") update of the detection settings
**silently stops event saving**. The provisioner now stores monitor flags as strings, which
avoids it (§8).

Pinned image: Shinobi `dev` @ `sha256:aa5cd832…` (Session 8). Code references are paths inside
the image (`/home/Shinobi/…`); read them with `docker compose exec shinobi sed -n …`.

## 1. Terms

| Term | Meaning here |
|---|---|
| motion detection | comparing two consecutive frames and counting changed pixels. No AI, no background model |
| PAM | Netpbm "portable arbitrary map": header + raw pixels. ffmpeg writes gray PAM frames to a pipe |
| pam-diff / pixel-change | Node module + C++ addon (both by Kevin Godell) that Shinobi uses to compare PAM frames |
| luma, Δluma | brightness of a pixel (0..255) and its change between two frames |
| region | a polygon (≥ 4 points) in detector pixels; Shinobi stores them in `details.cords` |
| blob | a connected group of changed pixels (connected-component labelling) |
| detector frame | one gray frame at `detector_scale_x × detector_scale_y`, `detector_fps` per second |
| trigger | one region that passed the thresholds on one detector frame |
| event | a trigger accepted by the main process (after filters and the motion lock): one `Events` row |
| motion lock | `detector_lock_timeout`: minimum gap between accepted events of one monitor |
| live update | a save that only changes detection values; Shinobi pushes them into the running detector instead of restarting ffmpeg |

## 2. How the detector decides (pixel-change `engine.cc`, pam-diff 1.2.1)

```
 detector frame t-1          detector frame t            |Δluma|          changed = |Δluma| >= color_threshold
 ┌────────────────┐          ┌────────────────┐          ┌──────────┐     ┌──────────┐
 │     ▓▓▓▓       │    −     │       ▓▓▓▓     │    =     │     ▒▒ ▒▒│ ──▶ │     ██ ██│  only the moving EDGES change;
 └────────────────┘          └────────────────┘          └──────────┘     └──────────┘  a uniform body does not

 per region:  percent = changed pixels inside the polygon / pixels of the polygon × 100
   1. percent >= sensitivity ?                                    no → nothing
   2. label blobs; at least ONE blob >= sensitivity % of region?  no → nothing   ← kills scattered noise
   3. Shinobi (detectorUtils.js): percent > sensitivity, percent < max_sensitivity,
      `threshold` frames in a row (within (threshold+0.5)/fps s)  → trigger {name, confidence=percent, blobs}
```

What follows from this, and is seen in the measurements below:

- **Motion ≠ presence.** A vehicle waiting at the gate changes 0 % of the pixels and is invisible.
  Presence needs object detection (Session 15) or tracking.
- **Changed area < object area.** Only edges change, so `sensitivity` values are small (3–15 %).
- **Sensitivity is relative to the region.** A 2 000-pixel car is 4 % of the gate lane but 0.9 %
  of the full frame, so full-frame defaults (10 %) miss cars (baseline, §6.1).
- **Frame-to-frame, not background.** A slow object at high `detector_fps` changes little per
  frame. Halving the fps roughly doubles the per-frame change of slow objects.
- **Everything that changes brightness counts:** OSD clock, water, clouds, camera shake,
  compression noise, auto-exposure, and in the simulation the loop seam.

## 3. Where it runs in Shinobi

```
 MediaMTX ─RTSP/TCP─▶ ONE ffmpeg per monitor (libs/ffmpeg/builders.js)
  /cam-gate     ─ -i #0 ─┬─ -map 0:0 -c copy -f hls        s.m3u8                live view
                         ├─ -map 0:0 -c copy -f segment    videos/…/%Y….mp4      recording (S10)
  /cam-gate-sub ─ -i #1 ─┴─ -map 1:0 -vf fps=5 -s 640x360 -c:v pam -pix_fmt gray -f image2pipe pipe:3
                                                │ fd 3
 camera thread (node libs/cameraThread/singleCamera.js, one per monitor)
   pipe2pam ─▶ pam-diff ─▶ detectorUtils.js filters ─▶ {f:'trigger', details:{plug:'built-in',
                                                │        reason:'motion', confidence, matrices}}
                                                ▼ dataPort (local WebSocket /dataPort)
 main process (camera.js) libs/events/utils.js triggerEvent()
   event filters ─▶ motion lock (detector_lock_timeout) ─▶ runEventExecutions():
     ├─ INSERT Events            iff detector_save === '1'        → GET /<key>/events/<ke>/<mid>
     ├─ snapshot JPEG            iff detector_motion_save_frame === '1', ≤ 1 per 60 s (extra ffmpeg)
     ├─ webhook / command        iff detector_webhook === '1' …   (Session 17)
     ├─ event-based recording    iff mode === 'start' (Watch-Only) and detector_trigger === '1'
     └─ s.tx detector_trigger    to dashboards (WebSocket), always
```

The generated command (Logs `Process Started`, cam_gate, abbreviated):

```
-progress pipe:5 -analyzeduration 1000000 -probesize 1000000 -fflags +igndts -rtsp_transport tcp
  -loglevel warning -i "rtsp://mediamtx:8554/cam-gate"          ← input #0 main (record + live)
  -rtsp_transport tcp -i "rtsp://mediamtx:8554/cam-gate-sub"    ← input #1 from details.input_maps
  -map 0:0 … -f hls … s.m3u8                                    ← primary_input "0:0"
  -map 0:0 … -f segment … %Y-%m-%dT%H-%M-%S.mp4
  -map 1:0 -vf "fps=5" -s 640x360 -an -c:v pam -pix_fmt gray -f image2pipe pipe:3
     │        │         │          └ raw 8-bit gray frames, no encoding cost
     │        │         └ detector_scale_x/y = sub size: no scaling, no 4:3 distortion
     │        └ detector_fps: the fps filter drops frames AFTER decoding (all 10 fps are decoded)
     └ input_map_choices.detector = [{map: "1:0"}]
```

**Trade-off of two inputs in one process:** if the sub stream fails, ffmpeg exits and the main
recording restarts with it. A separate detector process would decouple them, at the cost of a
second RTSP session (S34 / own NVR).

### Merged regions

`conf.json` has `detectorMergePamRegionTriggers: true` (from Shinobi's `conf.sample.json`): all
regions that fire on one frame become **one** event `name: "multipleRegions"` with the *average*
confidence. The regions that fired are only in `matrices[].tag`, one box per blob. The client's
`Event.regions` and `motion_stats.py` count by tag.

### Event-based recording and buffer time (not used here)

In Watch-Only mode (`start`) with `detector_trigger: 1`, `detector_record_method: sip`, ffmpeg
gets a 4th output: a rolling HLS buffer (`detectorStream.m3u8`, `detector_buffer_seconds_before`
= 5 s → `hls_list_size` 5). An event copies the buffer ("pre-roll") into a clip and keeps
recording until `detector_timeout` **minutes** (×60 000 ms; default 10, template 0.5) pass without
a new event (`watchdog_reset: 1` re-arms it; capped at 15 min per clip).

Our monitors record continuously (`record`), so event recording never runs, but with
`detector_trigger: 1` Shinobi still **builds the buffer output** (measured: 4th output in the
command) and sets a **15 s motion lock after every start** (`setMotionLock`). The template now
has `detector_trigger: 0`.

## 4. Settings and what breaks

| Key (details) | Ours | Meaning | Wrong value → |
|---|---|---|---|
| `detector`, `detector_pam` | 1 | master switch; built-in pam-diff detector | no pipe:3 output, nothing |
| `detector_send_frames` | 1 | "Send Frames": builds the detector output at all | 0 → silent: detector reads nothing |
| `input_maps`, `input_map_choices.detector` | sub, `1:0` | which input the detector decodes | main: 3–4× CPU (§6.2) |
| `detector_scale_x/y` | 640×360 | detector frame | template default 640×480 squashes 16:9 |
| `detector_fps` | 5 | frames compared per s | 2: fast cars cross the lane between frames; 10+: slow objects < threshold, CPU |
| `color_threshold` (region) | 9 | min Δluma per pixel | low: compression noise; high: grey truck on grey road vanishes |
| `sensitivity` (region) | 3–15 | min % of region changed (and one blob that big) | the main knob, relative to region size |
| `max_sensitivity` (region) | 40/60 | drop triggers above | none: loop seam / PTZ move / IR switch fire |
| `threshold` (region) | 2–3 | consecutive frames | 1: one-frame blips fire |
| `detector_lock_timeout` | 5000 | ms between accepted events | 2000: ~3 events per container lowering, lock-limited rates |
| `detector_save` | 1 | write `Events` rows | as **number** after a live update: no rows (§8) |
| `detector_trigger` | 0 | event-based recording (Watch-Only) | 1 in record mode: unused HLS buffer + 15 s lock after start |
| `detector_motion_save_frame` | 1 | JPEG per event, ≤ 1/min | extra ffmpeg spawn per saved frame |
| regions (`cords`) | per camera | polygons, ≥ 4 points | < 4 points: dropped ("Failed to Create Region"); none: `FULL_FRAME` |

Live-updatable (no ffmpeg restart, `LIVE_UPDATABLE_DETAIL_FIELDS` in libs/monitor/utils.js):
`cords`, `detector_sensitivity`, `detector_max_sensitivity`, `detector_color_threshold`,
`detector_threshold`, `detector_motion_tile_mode`, `detector_tile_size`. Everything else restarts.

## 5. Detection as code

```
 sim/cameras.yaml ───────────┐ sub size + URL (gen_compose.resolve_sub, RTSP_BASE/<id>-sub)
 shinobi-config/             │
   monitor.template.json ────┼─▶ provision_monitors.render() ─▶ monitors/<mid>.json ─▶ apply
   detection.yaml ───────────┘   scripts/detection.py details_for(camera)
```

`detection.yaml` (hand-edited): `defaults` (input, raw `details`, region defaults) and per camera
`regions` with either `points` (fractions 0..1, robust to a change of detector size) or
`points_px` (pixels of the detector frame, what Shinobi's region editor shows). Exactly one per
region. Validated: ≥ 4 points, inside the frame, unique names `[A-Za-z0-9_]+`, `max > min`,
cameras must exist, and the keys the renderer sets cannot be overridden. Cameras not listed keep
`detector: 0`.

Regions (sub stream 640×360, chosen on a 0.1 grid over the frames):

```
 cam-gate                                          cam-quay
 ┌──────────────────────────────────────────┐      ┌──────────────────────────────────────────┐
 │[OSD clock]                               │      │[OSD clock]  ┌crane beam──────────────┐   │
 │        cranes, sky (no region)           │      │             │  ┌───────┐ hoist       │   │
 │                                          │ 0.76 │  0.15 ───── │  │ cont. │ .40-.64     │   │
 ├──────────────────────────────────────────┤      │             │  └───────┘             │   │
 │ gate_lane: road, both lanes              │      │  0.48 ───── │  truck                 │   │
 │                              ┌───────────┤ 0.92 ├berth_edge──┐│ (.03-.43, .71-.77)     │   │
 │                              │[cam-gate] │      ├────────────┴┴────── restricted_quay ─┐   │
 └──────────────────────────────┴───────────┘      │ floor .80-1.0, x 0-.86       [cam-quay]  │
                                0.86               └──────────────────────────────────────────┘
```

## 6. Measurements

Laptop on AC, power profile `power-saver`. 100 % CPU = one core.

### 6.1 Baseline: full frame, Shinobi defaults, main stream (20:29–20:34, 5 min)

`detector: 1` only (10 %, Δ9, 2 fps, 640×480 scaled from the 1280×720 main):

| Camera | events/min | p50 % | max % | Reading |
|---|---|---|---|---|
| gate | 2.8 | 12 | 16 | only trucks reach 10 % of the whole frame; cars missed |
| quay | **24.8** | 19 | **86** | lock-limited (gap 2.0 s): shake + loop seam |
| waterway | **15.8** | 24 | 35 | **100 % false**: water and clouds (negative control) |
| yard | 0.4 | 26 | 33 | static scene |

Stored as `INTEGER 1` and still working after a restart: the start path converts numbers to
strings (§8). S13's inference ("`"1"` has no effect") was wrong for that path.

### 6.2 CPU: detector on main vs sub (shinobi-cpu.sh, 20 s)

| | ffmpeg cam_gate | ffmpeg cam_quay | node thread | container (4 monitors) |
|---|---|---|---|---|
| record copy only (yard/waterway) | 0.7–1.1 % | | 0.1 % | |
| detector on **main** (720p15 decode, 2 fps) | 11.1 % | 17.2 % | 0.5–1.7 % | 61.4 % (all 4 detecting) |
| detector on **sub** (360p10 decode, 5 fps) | 5.0 % | 5.7 % | 0.9–1.9 % | 16–20 % (gate + quay detecting) |

The decode dominates: sub ≈ ⅓ of main per camera at 2.5× the detector frame rate.

### 6.3 What each scene sends to the detector

`scripts/region_signal.sh` reproduces the per-pixel rule on the live sub stream (crop, 5 fps,
`tblend` difference, `lut` threshold Δ ≥ 9, `signalstats` mean). It gives the total %, without
the blob step, so it is an upper bound of pam-diff's `percent`.

- **Gate lane** (max per second over a loop): quiet 0–3 %, single cars 4–10 %, trucks 20–52 %.
  Fixed camera: no noise floor.
- **Quay floor**: 10–14 % on **every** frame. The thresholded difference image shows every edge of
  the picture (crane beam, ladder, truck outline, floor markings): **the camera moves**. Real
  equivalent: a crane-mounted camera swaying in the wind.
- **Quay loop seam**: 81–96 % on 1–2 frames, then 35–50 %. Events ≥ 50 % came every 9, 18–19 and 27 s
  = multiples of the 9.36 s clip. Real equivalent: PTZ preset move, IR day/night switch, reboot.
- **Hoist** (above the truck): 0–1 % while the container is above the box (uniform sky), ramps
  3 → 21 % as it descends into it (~6 s), seam 88–96 %. Real activity, clean floor.
- **OSD clock** (box 210×17 px): 7–13 % once per second (the seconds digit). Harmless over the full
  frame (0.15 %), but a small region that includes it would fire every second.

### 6.4 Tuning rounds

| Round | gate_lane | hoist | berth_edge | restricted_quay | lock | Result (events/min) |
|---|---|---|---|---|---|---|
| 1 | 2 % / thr 1 | 2 / 1 | 2 / 1 | 2 / 1 | 2 s | gate 18.9, quay 27.8: both lock-limited; all quay regions fire on shake |
| 2 | 4 / 2 | 5 / max 60 / 2 | 15 / max 40 / 3 | 12 / max 40 / 3 | 2 s | gate 11, hoist 19 (≈ 3 per lowering), berth + floor **0** |
| 3 | 4 / 2 | same | same | same | 5 s | gate 6.5 (0 on quiet road, one burst missed), hoist 6.0, **gap 9 s = 1 per lowering** |
| 4 (final) | **3 / 2** | same | same | same | 5 s | gate 7.6, every burst hit, 0 on quiet road |

Ground truth: gate ≈ 7–8 vehicle passages per 55 s loop; quay 1 lowering per 9.36 s loop.

Gate events against the lane signal (round 4, 110 s, `#` ≥ 10 %, `+` 4–10 %, `.` < 4 %; 1 char =
1 s; my probe joins ~1–2 s late):

```
  0s ......#++++++++...++#++#######+##+..+###########+++....
              E             E    E    E    E     E    E
 55s ......#+++.+++++.++++#+#######+##+..+###########++++...
             E             E     E    E    E     E    E
```

Final 5 min: see §10.

### 6.5 What motion detection cannot do on this footage

The quay floor's shake noise (10–14 % total, blobs up to ~7 %) is far above what a person on the
floor would change (≈ 1.5 % of the region). With sensitivity 12 %, `restricted_quay` is silent but
blind to people. That's honest, and a reason for object detection (S15) and "motion first, then
objects" (S16). The berth edge is a 21-pixel strip, dominated by shake and seam, and silent too.

## 7. Tools

| Tool | Use |
|---|---|
| `ShinobiClient.events(mid, start=, end=, limit=)` → `Event`, `EventBox` | bare JSON array, newest first; window `start ≤ time ≤ end` (route hard-codes `endIsStartTo`); whole-second `time`; default 500 rows |
| `python -m services.common.shinobi_client events [mid]` | quick look, boxes per region |
| `scripts/motion_stats.py [--minutes N \| --since T --until T] [mid…]` | events/min, p50/p90/max per monitor and region, median gap; gap ≈ lock = lock-limited |
| `scripts/region_signal.sh URL X Y W H [s] [fps] [Δ]` | per-frame % of a rectangle, the detector's view, for choosing thresholds |
| `tests/test_motion.py` (e2e) | rate bounds per region over 3 min, sub-stream frame size, lock spacing |

`motion_stats.py` only sees *accepted* events (after the lock), so a lock-limited distribution is
a sample, not the full picture. Use `region_signal.sh` for the full per-frame signal.

## 8. Finding: a live update silently stops event saving (Shinobi bug)

```
 save that changes only cords/sensitivities (libs/monitor.js addOrEditMonitor)
  ├─ cleanStringsInObject(form)                 "1" → 1 (number), as on every save (S12)
  ├─ rawMonitorConfigurations[mid] = form       main-process copy: details.detector_save = 1
  ├─ getMonitorEditScope → canUpdateLive        only LIVE_UPDATABLE fields changed
  └─ updateLiveDetectionSettings → thread       sends cords etc. stringified → thread detects ✔
                                                  NO s.camera() → NO convertNumbersToStrings
 trigger → triggerEvent → runEventExecutions:  detector_save === '1' → 1 === '1' → false
                                                → no INSERT Events ✘ (and no webhook, …)
 restart via s.camera (any non-live change)    convertNumbersToStrings mutates the SHARED details
                                                object (copyMonitorConfiguration is shallow) → '1' ✔
 ffmpeg crash → auto-restart                    reuses the in-memory object, no conversion → still ✘
```

Proof on cam_gate (gate lane busy throughout, verified with `region_signal.sh`):

| Step | State | Events/min |
|---|---|---|
| after first live update (both cameras, 20:39:10) | numbers in memory | 0 (also after killing ffmpeg) |
| 1. save with a non-live change (`notes`) → restart | strings | 19.1 |
| 2. live update (sensitivity 2→3), 0 `Process Started` | numbers | 0 (217/305 frames > 3 %) |
| 3. `detector_save` sent as `"\"1\""` → STRING, then live update | `detector_save` string | 14.2 |

Pitfall on the way: re-sending a stored `"1"` (from GET) turns it back into `1`. A string only
stays a string if **every** save encodes it again. The dashboard's Save goes through the same
`cleanStringsInObject`, so this hits UI users too.

**Fix in the provisioner** (`scripts/provision_monitors.py`):

```
 template (numbers) ─ stored_form ─ runtime_form ─▶ desired ─ wire_form ─▶ POST configureMonitor
                                    1 → "1"           "1"      "1" → "\"1\""   Shinobi parses one
                                    0.5 → "0.5"                               layer → "1" (STRING)
 invariant (tested): stored_form(wire_form(desired)) == desired
```

`runtime_form` = what a started monitor holds anyway (top-level `details` numbers as strings,
nested values unchanged). Migration: 288 changed values, 1 restart per monitor, then plan 0
changes. Afterwards a live-only change (round 4) saved 14 events in 110 s with 0 restarts.

Also: S13's `sqllog` measurement only looked at the stored type. With flags now stored as
strings, `sqllog` would work as `1` in the template (not re-tested).

## 9. Insights

1. Motion = change, not presence. Stopped vehicles are invisible; size thresholds are relative to
   the region.
2. Regions are the main tool: they exclude OSD, sky, water and the shake-prone parts.
   `max_sensitivity` is the guard against whole-frame changes; `threshold` debounces.
3. The motion lock, not the detector, sets the event rate during sustained activity. Shinobi has
   no "episode"; the evaluator has to merge pulses (S21).
4. Detect on the sub stream: ⅓ of the CPU, no scaling. The price is that the sub stream couples
   into the recording process.
5. Live updates avoid S12's 4–7 s recording gap, but leave the main process with numbers. Store
   flags as strings.
6. A loop seam or camera shake in the simulation is not a defect to hide. It stands in for real
   camera behaviour (PTZ, IR switch, wind) that the detector settings have to survive.

## 10. Done when

Final 5 min (21:16:14–21:21:14, config unchanged):

```
window 21:16:14-21:21:14 UTC (5.0 min), motion events only
MONITOR        REGION             EVENTS   /MIN   P50%   P90%   MAX%  GAP s
cam_gate       *                      35    7.0    9.0   23.0   27.0    5.5
               gate_lane              35    7.0    6.3   21.3   25.9  
cam_quay       *                      34    6.8    6.0    8.0   19.0    9.0
               hoist                  34    6.8    5.9    7.1    8.4  
cam_waterway   *                       0    0.0    0.0    0.0    0.0    0.0
cam_yard       *                       0    0.0    0.0    0.0    0.0    0.0
```

| Region | events/min | ground truth | |
|---|---|---|---|
| gate_lane | 7.0 | 7–8 passages / 55 s loop = 7.6–8.7/min | close vehicles inside the 5 s lock merge |
| hoist | 6.8 | 1 lowering / 9.36 s = 6.4/min | median gap 9.0 s; a few lowerings give 2 events |
| berth_edge, restricted_quay | 0 | no activity (shake + seam only) | |
| waterway, yard | 0 | detection off (baseline waterway: 15.8/min, all false) | |

`uv run pytest -m e2e tests/test_motion.py`: 7 passed.

## 11. Open items

- Session 15: object detection on `cam-gate`/`cam-quay`. The quay floor needs it to see people;
  "motion first" (`detector_use_motion`) will use these regions.
- Session 17: webhooks rely on `detector_webhook === '1'`; verify after a live update.
- Session 18: `conf.json` (`detectorMergePamRegionTriggers`) as code.
- Upstream: report the live-update bug (live path should run `convertNumbersToStrings`).
- `detector_motion_save_frame` CPU and disk cost per event not measured.
- `Events.time` has whole-second resolution and the table has no id: two events of one monitor
  in the same second can't be told apart (prevented here by the 5 s lock).
- S13 open item `width/height`/`snap`: Shinobi now decodes, but only the detector input; `snap`
  stays off.




how are these pixels connected? is there a predefined algorithm?

Yes. The usual way to determine whether changed pixels are connected is a connected-component algorithm. However, there is an important distinction: the Session 14 material mentions blob filtering, but it does not specify which exact connectivity algorithm Shinobi uses.

### 1. What does “connected” mean?

Imagine a small grid where `1` means a changed pixel and `0` means an unchanged pixel:

```
0 0 0 0 0
0 1 1 0 0
0 1 1 0 1
0 0 0 0 1
0 0 0 0 0
```

There are two separate groups:

- The first group contains 4 connected pixels.
- The second group contains 2 connected pixels.

The algorithm identifies these groups as separate connected components, often called blobs.

### 2. Two common connectivity rules

4-connectivity

A pixel is connected to its horizontal and vertical neighbours.

8-connectivity

A pixel is connected to its horizontal, vertical, and diagonal neighbours.

Illustrations of the two neighbourhood definitions.

For example, two changed pixels touching only at a corner:

- Are separate under 4-connectivity.
- Are connected under 8-connectivity.

### 3. How does the algorithm find groups?

A common method is Connected Component Labeling (CCL):

1. Scan the image pixel by pixel.
2. Find a changed pixel that has not been assigned to a group.
3. Check its neighbouring pixels according to the chosen connectivity rule.
4. Continue exploring neighbouring changed pixels, assigning them the same group label.
5. Repeat until all changed pixels have been processed.

This can be implemented using a flood-fill algorithm or a two-pass labeling algorithm.

### 4. How does this relate to Shinobi?

The Session 14 material tells us that the detector:

- Compares consecutive frames.
- Measures changed pixels inside each polygon region.
- Applies blob filtering.
- Uses sensitivity and consecutive-frame thresholds.

But it does not establish whether Shinobi uses 4-connectivity, 8-connectivity, flood fill, or another implementation. We would need to inspect the actual detector code to confirm that.

Resources

- [Connected-component labeling — Wikipedia](https://en.wikipedia.org/wiki/Connected-component_labeling?utm_source=chatgpt.com)
- [Flood fill — Wikipedia](https://en.wikipedia.org/wiki/Flood_fill?utm_source=chatgpt.com)
- Your Session 14 material, motion-detection section.

My Trust: 9/10

Check the uncertain points: The connectivity definitions and algorithms are established computer-vision concepts. The exact algorithm and connectivity rule used by your Shinobi setup remain unverified.


“According to the document, at least one blob must reach the configured percentage of the region.”
is this sensitivity or another threshold?

It is another threshold related to blob size, not the sensitivity threshold itself. They work at different stages.

### The distinction

1. Sensitivity threshold — determines how much pixel change is required for a pixel to be considered changed.
2. Blob-size threshold — determines whether a connected group of changed pixels is large enough to be accepted.
3. Consecutive-frame threshold — determines how many consecutive frames must satisfy the detection conditions.

### Example

Suppose the region contains 20,000 pixels:

- Sensitivity is set to 5%.
- A blob-size threshold is set to 2% of the region.
- The detector finds a connected blob containing 500 changed pixels.

The blob percentage is:

\\[ \frac{500}{20,000}\times100=2.5\\% \\]

So the blob reaches the 2% threshold.

Important: The 2% blob-size threshold in this example is illustrative. The Session 14 document mentions blob filtering, but does not establish that the blob threshold is configured as a percentage of the region. It also does not provide enough detail to confirm the exact relationship between blob filtering and sensitivity.

In short: sensitivity determines pixel change; blob filtering evaluates the size or shape of groups of changed pixels.

Resources: Your Session 14 document, motion-detection and blob-filtering sections; [Connected-component labeling](https://en.wikipedia.org/wiki/Connected-component_labeling?utm_source=chatgpt.com).

My Trust: 9/10

Check the uncertain points: The conceptual distinction is clear, but the exact blob-filtering parameter and its units in your Shinobi implementation need verification.



“The threshold parameter specifies how many consecutive detector frames must satisfy the detection conditions.”
this means that a blob must occur in threshold consecutive frames?

Yes, that's the basic idea — but more precisely, the detection conditions must be satisfied in the required number of consecutive detector frames.

For example, suppose `threshold = 3`:

| Detector frame | Blob meets detection conditions? |
| -------------- | -------------------------------- |
| Frame 1        | Yes                              |
| Frame 2        | Yes                              |
| Frame 3        | Yes                              |
| Frame 4        | No                               |

The detection condition is satisfied after the first three consecutive successful frames.

If instead the sequence is:

| Detector frame | Blob meets detection conditions? |
| -------------- | -------------------------------- |
| Frame 1        | Yes                              |
| Frame 2        | No                               |
| Frame 3        | Yes                              |
| Frame 4        | Yes                              |

The three-frame requirement has not been met because Frame 2 interrupted the sequence.

Does it have to be the exact same blob?

Not necessarily. The document describes consecutive frames satisfying detection conditions, but it does not establish whether Shinobi tracks the identity of one particular blob across frames or simply checks whether the region passes the detection criteria each time.

So the safest interpretation is: the detection condition must pass for three consecutive frames, not necessarily that the exact same group of pixels must remain unchanged.

My Trust: 9/10

Check the uncertain points: The precise blob-tracking behavior is not established by the Session 14 document.

Resource: Your Session 14 material, section on consecutive-frame thresholds.



what does live update mean

In your Shinobi motion-detection context, live update means changing a setting while the system is running, rather than stopping the system and restarting or reconfiguring it manually.

### Example

Imagine your camera has motion detection enabled:

1. Shinobi is monitoring the camera.
2. You change a detection setting in the interface.
3. You save the change.
4. Shinobi applies the updated setting while the system is running.

That is a live update.

### Why does it matter in Session 14?

Your document describes a particular issue with live updates:

- A setting such as `detector_save` could be stored as the number `1`.
- Another part of the code expected the text string `'1'`.
- Because these values have different data types, the event-saving check could fail.
- The provisioning fix stores the flags as strings.

So, in this context, a live update is a way of changing configuration without restarting the monitoring process, and the document describes a bug that could occur when applying such changes.

Resource: Your Session 14 document, section discussing the `detector_save` bug and provisioning fix.

My Trust: 10/10

Check the uncertain points: The exact settings that Shinobi can apply live may depend on the version and configuration.

