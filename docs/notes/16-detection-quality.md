# Session 16 — Detection quality

Goal: know **how good** the object events from Session 15 are, per camera, in numbers; and cut the
known false positives with Shinobi's event filters.

Result: a hand-labelled set of 79 frames (gate 20, quay 19, yard 20, waterway 20; 474 boxes), a
tool that reproduces the frames Shinobi sends to the plugin and scores the plugin against the
labels (`tools/detect_eval`), and per-camera numbers at the live settings (score ≥ 0.5, IoU ≥ 0.5,
gate filter on):

| Camera | Precision | Recall (COCO) | Recall (port) | Frames with objects that fire | False events | What it sees / misses |
|---|---|---|---|---|---|---|
| cam-gate | 62 % | 20 % | 10 % | 13 / 20 | 0 | cars 11/11; trucks mostly as the **tractor only**; cranes never |
| cam-quay | 88 % | 58 % | 44 % | 19 / 19 | 0 | truck 19/19; a 15 px person 3/19; crane leg called "truck" 3× |
| cam-yard | – | – | 0 % | 0 / 20 | 0 | 195 containers + 81 cranes, **no COCO class**: nothing |
| cam-waterway | 100 % | 29 % | 29 % | 5 / 19 | 0 | boats at a distance; a hull filling the frame never |
| all | 79 % | 33 % | 9 % | 37 / 58 | 0 | |

Read it as: **what the plugin reports is mostly right** (precision 79 %, and no frame fires
without a real object), **but it sees only a third of what it could and a tenth of the port**.
Decisions (§8): keep the global threshold 0.5, keep the gate traffic-light filter, no size filter,
no object detection on the waterway yet.

Also new: a third API key `SHINOBI_TRIGGER_API_KEY` (`control_monitors`) and
`ShinobiClient.trigger()`, used by an e2e test that injects synthetic object events and checks that
Shinobi's filters do what our emulator predicts (§5.4).

## 1. Terms

| Term | Meaning here |
|---|---|
| **Ground truth (labels)** | What is really in a frame, drawn by a person: one box + class per object. File `tests/fixtures/detection/labels.yaml`. |
| **TP / FP / FN** | True positive = plugin box that matches a label. False positive = plugin box with no matching label (a false alarm). False negative = label with no matching box (a miss). |
| **Precision** | TP / (TP + FP): of the boxes the plugin reports, how many are right. Low precision = false alarms. |
| **Recall** | TP / (TP + FN): of the objects that are there, how many it finds. Low recall = misses. |
| **IoU** | Intersection over union of two boxes: overlap area / combined area. 1 = identical, 0 = disjoint. A plugin box "matches" a label at IoU ≥ 0.5 (the usual COCO/PASCAL rule). |
| **IoA** | Intersection over the area of *one* box: how much of box A lies inside B. Used where IoU is unfair (a small box inside a big one). |
| **Ignore label** | An object too small, hidden or cut to demand from the detector. A box on it is neither TP nor FP, missing it is no FN. |
| **Score threshold** | The plugin drops boxes below `CONFIDENCE` (compose `YOLO_CONFIDENCE`, 0.5). One value for all cameras. |
| **Event filter** | Shinobi rule on an event's boxes (tag, confidence, x, y, width, height) that deletes boxes or drops the event before it is saved. |
| **COCO recall vs port recall** | COCO recall only counts objects the model *could* know (truck, car, person, boat, bus, traffic light). Port recall also counts container, crane and port vehicle, which YOLO-COCO can never report. |

## 2. Why measure at all: COCO vs the port

The model (yolov9t) was trained on COCO, 80 everyday classes. The port is not everyday:

```
  what the port has                      COCO class?        what the plugin can do
  ─────────────────────────────────      ───────────        ───────────────────────────
  truck (tractor + trailer/chassis)      truck              find it (but which box? §7.1)
  car, van, pick-up                      car                find it
  person (worker, driver)                person             find it if > ~15 px high (§7.2)
  ship, tug, launch                      boat               find it if it fits in the frame (§7.3)
  traffic light                          traffic light      find it — and nobody cares (§5)
  container (stack, single)              —                  never; at best "truck" / "train"
  STS crane, RTG, mobile crane           —                  never; at best "train" / "truck"
  reach stacker, straddle carrier        —                  never
```

Live event counts (S15) say "the gate fires 9 events/min, labelled truck/car". They cannot say
how many of those are right or how many trucks went by unseen. Only a comparison against frames
whose content is known can; that is what this session builds.

## 3. Ground truth: which frames, and labelling them

### 3.1 The same pixels the plugin gets

A label set is only useful if the plugin sees **the same JPEGs offline as live**. Live, a frame
goes through two encoders before YOLO sees it; offline we repeat both:

```
 media/<clip>.mp4 (1280x720, x264 Main)
      │  stage 1 = gen_compose's sub branch (same code: sim/gen_compose.branch/osd_filters/encoder_output)
      │    fps=10, scale=640:360, yuv420p, drawtext OSD (UTC clock from a FIXED epoch 1767225600
      │    → 2026-01-01 00:00:00, so frames are reproducible), x264 Main, -bf 0, GOP 20, 384k
      ▼
 sub.mp4  ≈ what mediamtx serves on /cam-x-sub
      │  stage 2 = Shinobi's object output, from its generated ffmpeg command (S15):
      │    -stream_loop 1  -vf fps=2  -c:v mjpeg  (no -q: default rate control)
      ▼
 2 passes of JPEGs ─▶ keep the 2nd pass ─▶ data/detection/frames/<cam>/<id>.jpg (gitignored)
```

Why the second pass: ffmpeg's MJPEG encoder without `-q` uses rate control that needs a few
seconds to settle. The first JPEGs of a short clip came out at 21 KB instead of the live 11.8 KB:
sharper than anything YOLO ever sees live. On a looped camera the encoder runs forever, so live
frames are always "settled". Encoding the clip twice and keeping the second pass reproduces that
(quay offline 11.5 KB vs live 11.8 KB, gate 12.3 vs 12.2 KB).

Picking: 20 frames per camera, spread evenly over the clip (`labels.pick_indices`); the quay clip
only has 19 frames at 2 fps (9.36 s). `labels.yaml` stores the clip's sha256: `frames` refuses
to regenerate frames from a different clip, so labels never silently point at other pixels.

### 3.2 Labelling

`python -m tools.detect_eval label` serves a small browser labeller (stdlib HTTP server on
127.0.0.1, vanilla JS canvas). It **never shows the plugin's boxes**, so the labels are not
biased toward what the model already finds. Every change is validated (classes, box inside
640×360) and written atomically.

Guidelines (header of `labels.yaml`): box every object of the 9 classes, moving or not; a truck
is cab + attached trailer + load; containers on a vehicle are part of the vehicle, a stack is one
`container` box; `ignore` for objects < ~12 px high, mostly hidden, cut by the frame edge or
under the OSD.

### 3.3 Checking the labels (they had 30 mistakes)

The first score (before any check) said gate trucks 6 % recall, quay recall 100 %, quay precision
76 %. Drawing labels (green) and plugin boxes (magenta) on the frames showed that a third of the
"errors" were in the labels:

| Found | Frames | Effect on the numbers | Fix |
|---|---|---|---|
| truck box included a **separate parked chassis** behind the real truck (262–274 px instead of ~137) | gate 7 | every hit on that truck counted as FP + FN | cut at the attached trailer (x+w = 503) |
| crane labelled `truck` (key slip) | gate-19 | one FN that the model never could have found | → `crane` |
| truck not labelled | gate-02 | a correct box counted as FP | added |
| truck almost hidden behind an SUV | gate-12 | a miss nobody could avoid | → `ignore` |
| small car not labelled | gate-12 | a correct box counted as FP | added |
| **person on the quay not labelled** (6–9 × 15–19 px, verified by a 8× crop) | quay 19 | quay recall 100 % (wrong), the plugin's 3 correct person boxes counted as FP | added |

The quay case is the instructive one: one forgotten 15-pixel person made the camera look
*perfect* on recall and *bad* on precision, the opposite of the truth (88 % / 58 %). Rule: before
believing a precision/recall number, look at every FP and FN on the picture. The corrections are
in the same `labels.yaml` (no second file); the table above is their record.

## 4. Scoring

### 4.1 Matching, per frame

```
plugin boxes, best score first            labels of the frame
 ┌──────────────────────┐
 │ d: truck 0.68 (x,y,w,h) ─────▶ unmatched label, same class, IoU ≥ 0.5 ? ─ yes ─▶ TP (label used up)
 └──────────────────────┘                       │ no
                                                ├─ ≥ 50 % inside an ignore label of that class ─▶ ignored
                                                ├─ ≥ 80 % inside an unmatched same-class label  ─▶ FP "part of truck"
                                                ├─ IoU ≥ 0.5 with an already matched label       ─▶ FP "duplicate"
                                                ├─ IoU ≥ 0.5 with a label of another class       ─▶ FP "on crane"
                                                └─ else                                          ─▶ FP "background"
 labels left unmatched (not ignore):  class has a COCO tag ─▶ FN       no COCO tag ─▶ FN port-only
```

Greedy by score is how the COCO evaluation does it: the most confident box gets the label first,
a second box on the same object is a duplicate (NMS should have removed it).

Port classes map to the COCO tag that would count as right: `traffic_light → "traffic light"`;
`container`, `crane`, `port_vehicle` map to nothing, so they can only ever be FN port-only.

### 4.2 Event view

Precision and recall are about boxes. Shinobi's output is **events**: one plugin answer with ≥ 1
box = at most one Events row. So `score` also counts per frame:

```
frame ──▶ any box left after threshold (+ filters)? ── no ──▶ no event
                         │ yes
                         ▼
          ≥ 1 TP, ignored box or "part of" box ? ── yes ──▶ true event
                         │ no
                         ▼
                    FALSE event (only background / other-class / duplicate boxes)
```

`HIT/OBJ-FRAMES` = frames with ≥ 1 real object in which an event fires (with a TP or a part box).
For the port twin this is the number that matters first ("a truck is at the gate"); the box
quality matters when we count, measure or track.

A **"part of" box** is a wrong box but a right event: the gate truck's tractor alone (§7.1) says
"truck at the gate" correctly. It stays FP + FN in precision/recall (strict, comparable with
any published number), and does not make a false event.

### 4.3 Commands

```bash
uv run python -m tools.detect_eval frames            # JPEGs for labels.yaml (needs ffmpeg + media/)
uv run python -m tools.detect_eval label             # labeller on http://127.0.0.1:8765
uv run python -m tools.detect_eval predict           # plugin image's detector on the frames, conf 0.25
uv run python -m tools.detect_eval score [--filters] [--iou 0.3] [--threshold 0.45] [--json]
```

`predict` runs `OnnxYoloDetector` from the pinned plugin image (`docker run --network none`, same
model, letterbox, NMS 0.45) at score 0.25 and stores every box in
`data/detection/predictions.json`. `score` applies the real threshold afterwards, so one
prediction run gives the whole threshold sweep (§6.2).

## 5. Event filters: what Shinobi really does

### 5.1 Where they run

```
plugin answer (boxes in score order) ─▶ triggerEvent()
     ├─ 1. checkEventFilters()      ← drop here: no lock taken, answer reason "Ignored by Event Filters"
     ├─ 2. checkMotionLock()        ← 2 s per-monitor lock (S15 §9)
     ├─ 3. detector_obj_region      ← per-region object filter
     └─ 4. Events row, webhooks, MQTT, recording trigger
```

A filtered event never takes the lock, so the next frame 0.5 s later can still become an event.

### 5.2 Semantics (read in `libs/events/utils.js`, then verified live)

A filter = conditions (`where` rows: field, operator, value, `&&`/`||`, brackets) + action
`halt` ("drop the event"). The code does **not** evaluate the conditions per box with AND; it is:

```
for each condition:
  for each box, position 0..n:
     box matches the condition?
        position > 0 and halt-on-match  → box DELETED from the event
        otherwise                       → condition = true
evaluate the conditions' true/false with && / ||
     true  and halt-on-match ("1")   → whole event dropped
     false and halt-on-failure ("2") → whole event dropped
```

Consequences, each injected into a live monitor (cases A–I in `tests/test_shinobi_filters.py`):

| Case | Filter | Boxes (plugin order) | Saved |
|---|---|---|---|
| A | drop `tag === traffic light` | truck .8, light .6 | truck (light deleted) |
| B | same | **light .6**, truck .5 | **nothing**: box 0 decides, the truck is lost too |
| C | same | light | nothing |
| D | same | truck, light, light | truck |
| E | drop `tag === person && confidence < 0.6` | truck .9, person .8, car .5 | truck only: person .8 **and** car .5 deleted (each matched one condition) |
| F | same | person .55, truck .5 | nothing |
| G | same | person .8, truck .5 | person (truck .5 deleted because .5 < .6) |
| H | drop unless `tag === truck` | car, truck | both (halt 2 deletes nothing) |
| I | same | car | nothing |

So:
- **One condition per drop-filter.** With two, each condition deletes boxes on its own (E, G):
  "small persons" deletes every small box. `detection.yaml` refuses `drop: on_match` with more
  than one condition.
- **Box 0 drops the whole event** (B). Plugin boxes come in score order (OpenCV NMS), so an
  event is lost when the unwanted object scores higher than the wanted one.
- **Values are JSON-parsed on save** (S12 coercion): `"50"` is stored as the number 50, so
  `width === 50` matches a box of width 50 (verified: 50 dropped, 51 saved). `traffic light`
  stays a string.

### 5.3 Filters as code

`detection.yaml`:

```yaml
cam-gate:
  object:
    filters:
      - name: no_traffic_light
        drop: on_match                       # halt "1"; on_no_match = halt "2"
        where: [[tag, "===", traffic light]]
```

`scripts/detection.py` renders this into the dashboard's shape (`detector_filters` =
`{id: {enabled, filter_name, where: [{p1,p2,p3,p4,openBracket,closeBracket}], actions: {halt,…}}}`
+ `use_detector_filters "1"`). It validates fields and operators and checks names are unique.
Nested values end up as numbers in Shinobi (only top-level `details` are kept as strings, S14 §8);
the filter code compares them with `String(...)`, so that is harmless.

### 5.4 Emulator + e2e test

`tools/detect_eval/shinobi_filters.py` re-implements the loop above (incl. stored-value
coercion, `&&` before `||`, odd brackets ignored). `score --filters` uses it, so the offline
numbers include the filters exactly as Shinobi applies them.

`tests/test_event_filters.py` (e2e) keeps the emulator honest: for every configured filter it
injects synthetic object events into the live monitor, with the matching box first and second,
and compares the saved row with `shinobi_filters.apply()`. A new Shinobi digest that changes
filter semantics fails there.

Injection is `GET /<key>/motion/<ke>/<mid>?data={"plug":…,"reason":"object","matrices":[…]}`
with a key that has `control_monitors` (new in `account.yaml`: `SHINOBI_TRIGGER_API_KEY`).
Answers (fixtures `tests/fixtures/shinobi/trigger_*.json`):

| Answer | Meaning |
|---|---|
| `{ok:true, triggered:true}` | went through `triggerEvent`, row saved (asynchronously, the test polls) |
| `{ok:false, triggered:false, reason:"Ignored : Motion Lock"}` | lock held by a real event; the test retries |
| `{ok:false, triggered:false, reason:"Ignored by Event Filters"}` | dropped by a filter |
| `{ok:false, msg:…}` | refused before `triggerEvent` (unknown monitor, key without permission) |

Injected events carry `plug: "e2e-inject"`; `test_object_detection.py` ignores them.

### 5.5 The gate filter, measured

Live, 40 min, 146 gate object events (before the filter): 109 had no light, 25 had a vehicle at
box 0 plus a light (light deleted by the filter, vehicle kept), 8 were light only (dropped,
correct), **4 had the light at box 0 next to a vehicle (2.7 %, whole event lost)**. Offline, the
filter turns 5 TP traffic-light boxes into nothing and 0 false events into 0, at the price of
gate recall 28 → 20 % (the lights were real, just useless). Kept: a lost event is usually
re-detected 0.5 s later.

## 6. Results

### 6.1 Per class (score ≥ 0.5, IoU ≥ 0.5, filters on)

| Camera | Class | TP | FP | FN | FN port-only | Note |
|---|---|---|---|---|---|---|
| gate | car | 11 | 0 | 0 | | every car, good boxes (IoU 0.68–0.92) |
| gate | truck | 2 | 8 | 30 | | 8 FP all "part of truck" (tractor only) |
| gate | traffic light | 0 | 0 | 21 | | filtered on purpose (score 0.32–0.49 anyway) |
| gate | crane, container | | | | 62 | |
| quay | truck | 19 | 3 | 0 | | 3 FP = the RTG crane leg, "background" |
| quay | person | 3 | 0 | 16 | | 15 px high, scores 0.31–0.57 |
| quay | container | | | | 12 | |
| yard | container, crane | | | | 276 | |
| waterway | boat | 6 | 0 | 15 | | 9 FN = hull filling the frame |

### 6.2 Threshold sweep (precision % / recall %)

```
THR    cam-gate   cam-quay   cam-waterway   all        false events (gate, quay, waterway)
0.35   63 / 50    80 / 84     78 / 33       71 / 58    0, 0, 1
0.40   65 / 48    79 / 79     75 / 29       71 / 54    1, 0, 1
0.45   68 / 41    80 / 74     86 / 29       75 / 49    0, 0, 1
0.50   67 / 28    88 / 58    100 / 29       79 / 37    0, 0, 0     ← live
0.60   62 / 12    95 / 47    100 / 14       83 / 24
0.70   89 / 12   100 / 45    100 /  5       96 / 21
```

(Filters off, so the threshold effect is not mixed with the gate filter.)

The classic trade-off: down = more found, more wrong. Between 0.5 and 0.35, recall rises by
half (37 → 58 %), mostly from the quay person (scores 0.31–0.57) and gate tractors, and the first
false events appear (a background "truck" on the waterway, a gate box on nothing).

### 6.3 IoU 0.3 instead of 0.5

Filters off, gate goes from TP 18 / FP 9 to **TP 27 / FP 0** (precision 67 → 100 %, recall
28 → 42 %); the other cameras don't change. All 9 gate FPs were tractor boxes inside a truck label
(IoU 0.42–0.45, §7.1). They pass a loose match, and a strict one calls them errors. That is a
box-extent disagreement, not position noise. The headline numbers stay at IoU 0.5 so they are
comparable with published ones; the event view (§4.2) already counts these boxes as right.

## 7. Why it misses

### 7.1 Gate: the tractor without its trailer

```
 label (what a truck is):    ┌──────────────────────────────────────────────┐  137 px
                             │ ▄▄▄▄▄▄                                       │
 plugin box:                 │ █cab█ ══════════ empty skeletal chassis ═════ │
                             ├────────┤  72 px                              │
                             └──────────────────────────────────────────────┘
                              IoU = 72·42 / (137·52) ≈ 0.42   →  FP + FN
```

An empty skeletal trailer is a few dark beams and wheels against a busy background: there is
little "truck" texture in it, and COCO trucks are mostly box trucks seen whole. YOLO boxes the
tractor, which it knows. All 9 such boxes say "truck at the gate" correctly, and they are why
4 gate frames count as hit instead of false events. The box is unusable for length or lane
occupancy. A long tipper truck seen close (gate-14) came out
as `train` 0.42: class confusion on an unusual view, below threshold anyway.

### 7.2 Quay: small persons

The person on the quay is 6–9 px wide and 15–19 px high in the 640×360 sub stream. yolov9t
letterboxes to 640, so it stays that small, near the lower limit of what a tiny model resolves.
Scores swing between 0.31 and 0.57 from frame to frame for a person standing still: at 0.5 it is
found in 3 of 19 frames. For safety use cases (person on the quay apron) that is not enough; the
fix is more pixels (main stream crop, or a region at higher resolution), not a lower threshold.

### 7.3 Waterway: the ship is the whole picture

In 9 of 20 frames the passing tanker's hull fills the frame (label 639 × 340). With no edges, no
water line and no sky around it, a detector trained on whole objects sees a red wall: no box at
any score. When the ship is at a distance (bow or stern in view) it is found at 0.53–0.86 with
good boxes. Recall 29 % is therefore mostly a camera-placement property: a waterway camera should
see whole ships (wider angle, further back).

### 7.4 Yard: nothing to find

Containers and cranes have no COCO class: 276 labels, 0 detections, not even false ones. A
port-class model (S23 detector service) is the only way to see the yard.

## 8. Decisions

| Question | Decision | Why |
|---|---|---|
| Global threshold (`YOLO_CONFIDENCE`) | **keep 0.5** | 0 false events on all cameras; 0.45/0.4 gains are mostly the tiny person (not reliable at any threshold) and tractor boxes; a lower value is global and adds a false event on gate and waterway |
| Gate traffic-light filter | **keep** | 2.7 % events lost whole, 8 light-only events/40 min removed, light boxes stripped from the rest |
| Minimum-size filter | **no** | the only small boxes ≥ 0.5 are a real person and the (filtered) light; a size filter would delete true positives |
| Quay crane leg as "truck" (3 FP) | **no filter** | the quay camera pans, the leg moves through the frame; a position filter would also drop real trucks |
| Object detection on cam-waterway | **not now** | the camera has no motion zones (water, S14), so frames would flow continuously at 2 fps, +120 inferences/min on a plugin already busy ~50 s of every 60 s (155 inferences/min × 320 ms); and it would add a boat label on 5 of 19 ship frames only. Revisit with the S23 detector service or a wider camera |
| Object detection on cam-yard | **no** | 0 % port recall: no COCO classes |

## 9. Lessons

1. Live event counts don't measure quality; a small labelled set does, and 79 frames are enough
   to see the big effects (each camera's numbers are rough: ±10–20 points).
2. **Audit the labels with the predictions drawn on top** before trusting a number: 30 label
   mistakes moved quay recall from a false 100 % to 58 %.
3. Reproduce the exact input pipeline: a single extra JPEG quality step changes detections.
4. The box convention matters as much as the detector: "truck" = tractor or tractor + trailer
   decides whether gate recall is 6 % or most of the trucks.
5. Event filters are box-ordered and not per-box AND; one condition per drop filter, and test
   them against the live server (emulator + e2e), not against the UI text.

## 10. Open questions

- Is a person on the quay detectable from the main stream (1280×720 crop) at acceptable CPU? (S23)
- Should the evaluator merge a "part of truck" tractor box with the motion zone extent to get the
  truck's length?
- Per-camera thresholds need either Shinobi filters on `confidence` (box 0 rule applies) or our
  own detector service (S23).
