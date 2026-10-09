# Shinobi Port Twin — Learning Plan

Goal: simulated port cameras → Shinobi (ingest, record, detect) → evaluation service → digital twin (state + 3D view).

- Time budget: 1–2 h per session, ~5 sessions per week → 35 sessions ≈ 7–9 weeks including buffer.
- Every session ends with **one commit on `main`** (Conventional Commits). The commit message is listed per session; adapt the body to what you actually did.
- Track where you are in `docs/progress.md` (one line per session: date, session, commit hash, notes).
- Rule of thumb: if a session overruns, split it and commit what works. Don't leave a session uncommitted.

## How explanations work in this plan

The developer is an experienced C++/Linux engineer but a **beginner in video, CCTV, NVRs (Shinobi), MQTT
and digital twins**. So every "Learn" topic is explained in depth, not as a summary:

- **Diagrams first.** Each mechanism gets an ASCII diagram in the explanation and in the session's notes:
  data flow (who sends what to whom, over which protocol/port), timelines (frames, GOPs, PTS/DTS,
  loop boundaries, reconnects), and structure (packets, NAL units, compose services, MQTT topic trees).
- **Build up from zero.** Define every video/streaming/Shinobi term the first time it appears and say
  why it matters for this project; don't assume prior knowledge of codecs, containers, RTSP or NVRs.
- **Show, then explain.** Pair each concept with a real command and its real output from this repo
  (ffprobe, ffmpeg, docker, mosquitto_sub), annotated line by line.
- **Say what breaks.** For each setting, show what goes wrong without it (stall, gap, artefact, false event).
- Generic programming basics (Python, bash, git, Docker CLI usage) can stay brief.
- Notes go in `docs/notes/` or `docs/theory/` and keep the diagrams, so they can be re-read later.

## Target architecture

```
 port footage (licensed clips, media/ gitignored; normalized to camera profile v1)
        │  sim/cameras.yaml → compose/cameras.generated.yml → FFmpeg loop (-re -stream_loop -1), H.264, fixed GOP, timestamp overlay
        ▼
 MediaMTX (RTSP :8554)  rtsp://mediamtx:8554/cam-gate | cam-quay | cam-yard | cam-waterway
        │
        ▼
 Shinobi (:8080)  monitors · recording · motion · object-detection plugin (YOLO/TensorFlow)
        │  webhook / MQTT outbound / REST API
        ▼
 Mosquitto (MQTT :1883)  shinobi/<group>/<monitor>/...
        │
        ▼
 evaluator (Python)  normalize → zone logic (queue, berth occupancy, intrusion) → port/<site>/events/...
        │
        ▼
 twin-state service (Python)  port model + live state, REST/WebSocket
        │
        ▼
 3D viewer (free C++/Python tool, chosen in Session 28)
```

Everything runs from one `docker compose` stack. Services talk only via MQTT topics and documented JSON schemas, so any part can be replaced or scaled.

## Phase overview

| Phase | Sessions | Hours (est.) | Outcome |
|---|---|---|---|
| 0 · Foundations | 1–3 | 4–5 | Repo skeleton, tooling, licensed port footage |
| 1 · Camera simulation | 4–7 | 5–6 | 4 realistic RTSP cameras incl. fault injection |
| 2 · Shinobi fundamentals | 8–13 | 8–10 | Shinobi in Docker, monitors as code, API client |
| 3 · Detection & events in Shinobi | 14–19 | 8–10 | Motion + object detection, events via webhook & MQTT |
| 4 · Evaluation service | 20–25 | 8–10 | Normalized events, port domain logic, own model path |
| 5 · Digital twin | 26–31 | 8–10 | Port model, twin state service, live 3D view |
| 6 · Integration & scaling | 32–35 | 5–6 | One-command stack, E2E tests, load test, v0.1.0 |

---

## Phase 0 · Foundations

### Session 1 — Repo bootstrap
- **Learn:** How this project is organized; how Claude Code uses `CLAUDE.md`.
- **Do:** Add `CLAUDE.md`, `README.md`, `docs/learning-plan.md`, `docs/progress.md`, `.gitignore` (media/, recordings, `.env`, `__pycache__`, `.venv`), `.env.example`, folder skeleton from `CLAUDE.md`. Verify `docker compose version`, `ffmpeg -version`, Python ≥ 3.12.
- **Done when:** `git status` clean after commit; tool versions noted in `docs/progress.md`.
- **Commit:** `chore: bootstrap repo structure, CLAUDE.md and learning plan`

### Session 2 — Video fundamentals for CCTV
- **Learn:** Container vs codec, H.264 profiles, GOP/keyframe interval, B-frames and why CCTV avoids them, RTSP (control) vs RTP (media), TCP vs UDP transport, PTS/DTS.
- **Do:** `ffprobe -show_streams -show_frames` on a sample clip; script `scripts/probe.sh` printing codec, fps, GOP, resolution, bitrate. Write `docs/notes/video-basics.md` (your own words, focus on edge cases).
- **Done when:** You can explain why a 2 s GOP matters for recording segment cuts and stream startup latency.
- **Commit:** `docs: add video and RTSP fundamentals notes and probe script`

### Session 3 — Source real port footage
- **Learn:** Licensing of stock/CC video (Pexels/Pixabay licenses, Wikimedia Commons CC-BY/CC-BY-SA attribution). Live port webcams are usually NOT reusable — don't restream them.
- **Do:** Pick 4+ clips with fixed camera positions (gate/trucks, quay/ship at berth, container yard, waterway traffic). Record URL, author, license, retrieval date in `media/SOURCES.md` (committed); `scripts/fetch_media.sh` downloads into `media/` (gitignored), verifies sha256.
- **Done when:** `./scripts/fetch_media.sh` reproduces the media folder on a fresh clone.
- **Commit:** `feat(sim): add port footage manifest and reproducible download script`

## Phase 1 · Camera simulation

### Session 4 — First RTSP camera
- **Learn:** MediaMTX as RTSP server (publish vs read paths), FFmpeg `-re`, `-stream_loop -1`, `-c copy` vs re-encode.
- **Do:** `docker-compose.yml` with `mediamtx` and one `cam-gate` FFmpeg publisher. Watch with `ffplay rtsp://localhost:8554/cam-gate`. Added: camera profile v1 (`docs/camera-profile.md`): `sim/scripts/normalize.sh` re-encodes `media/raw/` clips (H.264 Main, ≤ 1280 wide, 25 fps, GOP 50, no B-frames/audio) and `sim/scripts/probe_clip.sh` checks them.
- **Done when:** Stream plays continuously across loop boundaries without a stall.
- **Commit:** `feat(sim): stream first simulated camera via MediaMTX`

### Session 5 — Camera fleet as config
- **Learn:** Why cameras should be data, not hand-written services.
- **Do:** `sim/cameras.yaml` (`version: 1`, now v2 since Session 6, `defaults`, per camera: id, role, clip under `media/`, resolution, fps, bitrate, GOP); `sim/gen_compose.py` (pydantic validation, pure `render()`, `--check`) generates `compose/cameras.generated.yml`, one service per camera running an exec-form ffmpeg command under `publish.sh` (restart + SIGTERM). Four cameras named after the footage roles: `cam-gate`, `cam-quay` (720p15), `cam-yard`, `cam-waterway` (720p10), all 2 s GOP. `scripts/sim-up.sh` / `sim-down.sh`. pytest for the generator incl. a golden test of the committed file.
- **Done when:** Changing the YAML and re-running the generator adds/removes cameras.
- **Commit:** `feat(sim): generate camera fleet from cameras.yaml`

### Session 6 — Realism
- **Learn:** Encoder settings real IP cameras use; main stream vs sub stream and why an NVR uses each (record vs detect/preview). Live re-encoding with fixed GOP and no B-frames already happens since Session 5.
- **Do:** `drawtext` overlay with camera id + wall-clock time; a low-res sub stream per camera (`/cam-gate-sub`), configured as optional fields in `sim/cameras.yaml` and rendered by `gen_compose.py` (one ffmpeg, two outputs). Optional: snapshot endpoint simulation via `ffmpeg -frames:v 1`.
- **Done when:** `uv run python -m tools.streamprobe` against each main and sub stream shows the configured resolution, GOP and no B-frames; the overlay clock matches wall-clock time.
- **Commit:** `feat(sim): realistic encoding, timestamp overlay and sub streams`

### Session 7 — Fault injection & health checks
- **Learn:** How real cameras fail: dropouts, reboots, packet loss, jitter.
- **Do:** `scripts/chaos.sh` (stop/start a camera container, `tc netem` loss/delay on its interface). `tests/test_streams.py` uses ffprobe to assert all streams are up.
- **Done when:** You can make one camera flap on demand and the test detects it.
- **Commit:** `test(sim): add stream health checks and fault injection script`

## Phase 2 · Shinobi fundamentals

### Session 8 — Install Shinobi in Docker
- **Learn:** Shinobi architecture: Node.js app (`camera.js` under PM2), MariaDB, `conf.json`, `super.json`, videos dir. Official image `registry.gitlab.com/shinobi-systems/shinobi:dev` and the ShinobiDocker compose files. Superuser panel at `:8080/super`.
- **Do:** Add `shinobi` + its database to the compose stack on the same network as MediaMTX, with persistent volumes. Log into `/super`, change the default superuser password, create your admin account (store credentials in `.env`, never in git).
- **Done when:** `docker compose down && up` keeps your account and settings.
- **Commit:** `feat(shinobi): add Shinobi and database to compose stack`

### Session 9 — First monitor
- **Learn:** Monitor modes (Disabled / Watch-Only / Record), input types, stream output types (HLS, MJPEG, Websocket/FLV), how Shinobi spawns one FFmpeg per monitor.
- **Do:** Add `cam-gate` (`rtsp://mediamtx:8554/cam-gate`, TCP). Find the FFmpeg command Shinobi generated in the logs/`docker top`; annotate it in `docs/notes/shinobi-monitor.md`.
- **Done when:** Live view works and you can explain each FFmpeg flag Shinobi used.
- **Commit:** `docs(shinobi): document first monitor and generated ffmpeg pipeline`

### Session 10 — Recording, storage, CPU
- **Learn:** Record mode, segment length, `copy` vs transcode, retention (max storage / days), sub stream for detection.
- **Do:** Add all 4 monitors; recording with `copy`; set retention. Measure CPU per monitor (`docker stats`) for copy vs transcode and note results.
- **Done when:** Recordings appear per monitor and old ones get pruned.
- **Commit:** `docs(shinobi): record all cameras and benchmark copy vs transcode`

### Session 11 — The API
- **Learn:** API key + group key + monitor id URL scheme; Get Monitors, Get Streams, Get Videos, snapshots; WebSocket connection.
- **Do:** Create an API key (UI). `curl` the endpoints. Write `services/common/shinobi_client.py` (typed, `httpx`), pytest with recorded fixtures.
- **Done when:** `python -m services.common.shinobi_client monitors` lists your 4 monitors.
- **Commit:** `feat(api): add typed Shinobi API client with tests`

### Session 12 — Monitors as code
- **Learn:** Add/Edit/Delete Monitor API; monitor JSON structure.
- **Do:** Export monitor configs to `shinobi-config/monitors/*.json` (no secrets). `scripts/provision_monitors.py` creates/updates them idempotently from `sim/cameras.yaml` + templates.
- **Done when:** Wipe Shinobi data, run provisioning, all monitors come back identically.
- **Commit:** `feat(shinobi): provision monitors idempotently via API`

### Session 13 — Consolidation
- **Learn:** Backup/migration of monitors; Shinobi logs and troubleshooting.
- **Do:** Write `docs/notes/shinobi-cheatsheet.md` (ports, paths, API patterns, common failures). Close gaps from sessions 8–12.
- **Done when:** You can rebuild the whole Shinobi side from the repo in < 10 min.
- **Commit:** `docs(shinobi): add operations cheatsheet`

## Phase 3 · Detection & events in Shinobi

### Session 14 — Motion detection
- **Learn:** Detector settings, regions, sensitivity/thresholds, "Send Frames", detector resolution/fps, event-based recording and buffer time.
- **Do:** Regions: gate lane (`cam-gate`), berth edge and restricted quay zone (`cam-quay`). Tune until events are meaningful, not constant (water movement and lighting are your enemies).
- **Done when:** Motion events appear in the event list with sane frequency; tuning values documented.
- **Commit:** `feat(shinobi): configure motion detection regions for port cameras`

### Session 15 — Object detection plugin
- **Learn:** Detector plugins (TensorFlow, YOLO, DeepStack, YOLOv9-ONNX Docker plugin), plugin key in `conf.json`, host vs client plugin modes. Start with ONE plugin.
- **Do:** Run a YOLO-based plugin container, connect it, enable Object Detection on `cam-gate` and `cam-quay`.
- **Done when:** Events with labels (e.g. `truck`, `boat`, `person`) and confidences appear.
- **Commit:** `feat(shinobi): add object detection plugin to stack`

### Session 16 — Detection quality
- **Learn:** COCO classes vs port reality (boat/truck/person exist; container, crane, reach stacker don't). Event filters, confidence thresholds, "check for motion first".
- **Do:** Event filters per monitor; a small labelled set of 20 frames per camera and a script measuring hits/misses against plugin output.
- **Done when:** Precision/recall rough numbers per camera recorded in `docs/notes/detection-quality.md`.
- **Commit:** `test(detect): measure plugin detection quality on port frames`

### Session 17 — Events out: webhooks
- **Learn:** Shinobi's two webhook mechanisms (monitor detector webhook vs account-level inner events); payload shape.
- **Do:** `services/webhook-receiver` (FastAPI) logs raw payloads; configure webhooks; save representative payloads to `tests/fixtures/shinobi/`.
- **Done when:** You have fixtures for motion, object, and monitor-died events.
- **Commit:** `feat(events): add webhook receiver and capture event fixtures`

### Session 18 — Events out: MQTT
- **Learn:** MQTT basics (QoS, retained, topic design), Shinobi MQTT outbound/inbound (`mqttClient` config). Compare webhook vs MQTT vs API polling vs WebSocket.
- **Do:** Add Mosquitto to compose; enable Shinobi MQTT outbound; `mosquitto_sub -v -t 'shinobi/#'`. Decision record `docs/adr/0001-event-transport.md`.
- **Done when:** Detections arrive on MQTT and the ADR names the chosen primary transport.
- **Commit:** `feat(events): publish Shinobi events to MQTT; add transport ADR`

### Session 19 — Synthetic event injection
- **Learn:** Monitor triggers via API (motion/object trigger, hookTester) and MQTT inbound.
- **Do:** `scripts/inject_event.py` fires deterministic events for testing downstream without waiting for video.
- **Done when:** A test can trigger an event and assert it arrives on MQTT.
- **Commit:** `test(events): add synthetic event injection for downstream tests`

## Phase 4 · Evaluation service

### Session 20 — Event schema
- **Learn:** Separating raw vendor payloads from your domain model.
- **Do:** `services/common/schema.py` with `@dataclass(slots=True, frozen=True)` for `RawShinobiEvent` → `Detection` → `PortEvent`; JSON Schema in `docs/schema/`; normalizer tested against Session 17 fixtures.
- **Done when:** All fixtures normalize; unknown fields don't crash the parser.
- **Commit:** `feat(eval): define normalized event schema and parser`

### Session 21 — Evaluator skeleton
- **Do:** `services/evaluator`: MQTT subscribe → normalize → publish `port/<site>/detections/<camera>`; config from env; Dockerfile; added to compose.
- **Done when:** Normalized detections flow end to end.
- **Commit:** `feat(eval): add evaluator service pipeline`

### Session 22 — Port domain logic
- **Learn:** Debounce, hysteresis, sliding windows — turning noisy detections into stable states.
- **Do:** Rules: gate truck queue length, berth occupied/free, person in restricted quay zone (alert). Publish `port/<site>/state/...` and `port/<site>/alerts`. Unit tests with timed event sequences.
- **Done when:** Flickering detections don't toggle berth state.
- **Commit:** `feat(eval): add queue, berth occupancy and intrusion rules`

### Session 23 — Own detection path
- **Learn:** When to bypass Shinobi's plugin: custom classes, model control. ONNX Runtime / Ultralytics, frame grabbing from RTSP sub stream.
- **Do:** `services/detector` pulls frames (sub stream, low fps), runs a YOLO model, publishes `Detection` in the same schema. Compare with Session 16 numbers.
- **Done when:** Both detection sources feed the evaluator interchangeably.
- **Commit:** `feat(detect): add standalone detector service using shared schema`

### Session 24 — Custom Shinobi detector plugin (optional, advanced)
- **Learn:** Shinobi plugin structure (`pluginBase.js`, `s.detectObject`), how frames reach plugins.
- **Do:** Thin plugin that forwards frames to your detector service and returns matrices to Shinobi, so Shinobi records on your detections.
- **Done when:** Shinobi event list shows your model's labels.
- **Commit:** `feat(shinobi): add custom detector plugin bridging to detector service`

### Session 25 — Robustness
- **Learn:** Reconnect strategies, backpressure, clock skew between camera overlay, Shinobi and evaluator, idempotency/dedup.
- **Do:** Reconnect + backoff, event ids/dedup, Prometheus-style counters or structured logs. Run `scripts/chaos.sh` while the pipeline runs.
- **Done when:** Camera flapping doesn't produce false berth/queue state changes or crash services.
- **Commit:** `fix(eval): harden pipeline against reconnects, duplicates and clock skew`

## Phase 5 · Digital twin

### Session 26 — Port model
- **Learn:** Twin = static model + live state + history. Coordinate frames (local metric frame vs WGS84).
- **Do:** `twin/model/port.yaml`: berths, gate lanes, yard blocks, quay zones, cameras (position, heading, FOV). Validation + tests.
- **Done when:** The model loads and every zone used by evaluator rules exists in it.
- **Commit:** `feat(twin): add port layout model with cameras and zones`

### Session 27 — Twin state service
- **Do:** `services/twin-state`: subscribes to `port/<site>/state|alerts`, holds current state, persists history (SQLite first), exposes REST + WebSocket.
- **Done when:** `GET /state` reflects the simulated scene in near real time.
- **Commit:** `feat(twin): add twin state service with REST and WebSocket API`

### Session 28 — Choose the 3D tool
- **Learn:** Free C++/Python options, e.g. VTK/PyVista, Panda3D, Godot (C++ engine), O3DE. Criteria: scalability, live updates, licensing, your C++ leverage, deployment.
- **Do:** 1-hour spike with the top two; `docs/adr/0002-3d-visualization.md`.
- **Done when:** Decision recorded with trade-offs.
- **Commit:** `docs(twin): add ADR for 3D visualization tool`

### Session 29 — Static 3D scene
- **Do:** `viewer/`: render quay, berths, yard blocks, gate from `port.yaml`, plus camera frustums.
- **Done when:** Scene matches the model; camera FOVs cover the zones they monitor.
- **Commit:** `feat(viewer): render static port scene and camera frustums`

### Session 30 — Live updates
- **Do:** Viewer subscribes to twin WebSocket: berth occupancy, truck queue, alerts as visual states.
- **Done when:** Simulated ship arrival in the footage changes the berth in 3D within a few seconds.
- **Commit:** `feat(viewer): animate live twin state`

### Session 31 — Image to world coordinates
- **Learn:** Ground-plane homography per camera, its limits (non-planar objects, lens distortion).
- **Do:** Calibrate each camera with 4+ point pairs (image ↔ model); place detections in world coordinates; show them in the viewer.
- **Done when:** A truck detection lands in the right gate lane in 3D.
- **Commit:** `feat(twin): project detections to world coordinates via homography`

## Phase 6 · Integration & scaling

### Session 32 — One-command stack
- **Do:** Compose profiles (`sim`, `core`, `twin`), healthchecks, `depends_on: condition: service_healthy`, `.env.example` complete, `make up/down/test`.
- **Done when:** Fresh clone → `make up` → working twin.
- **Commit:** `chore: one-command stack with profiles and healthchecks`

### Session 33 — End-to-end scenario tests
- **Do:** Scenario clips/injected events with expected twin state; pytest E2E (marked `e2e`).
- **Done when:** `pytest -m e2e` passes reproducibly.
- **Commit:** `test: add end-to-end scenario tests from camera to twin`

### Session 34 — Scaling
- **Learn:** Shinobi resource limits per monitor, detector plugins in cluster mode, MQTT topic fan-out, where C++ would pay off (detector, frame pipeline).
- **Do:** Scale simulated fleet to 16 cameras via `cameras.yaml`; measure CPU/RAM/latency; `docs/notes/scaling.md` with bottlenecks and next steps.
- **Done when:** You know the first bottleneck and its numbers.
- **Commit:** `docs: add scaling measurements for 16 simulated cameras`

### Session 35 — Retrospective & release
- **Do:** Update README (architecture, quick start), clean up TODOs, tag `v0.1.0`.
- **Done when:** Someone else could run and understand the project from the README.
- **Commit:** `docs: finalize README for v0.1.0` (then `git tag -a v0.1.0`)

---

## Key references (verify details here; Shinobi changes often)
- Shinobi docs: https://docs.shinobi.video (installation/docker, detect, api)
- ShinobiDocker: https://gitlab.com/Shinobi-Systems/ShinobiDocker
- Shinobi source: https://gitlab.com/Shinobi-Systems/Shinobi
- MediaMTX: https://github.com/bluenviron/mediamtx
- FFmpeg docs: https://ffmpeg.org/documentation.html
