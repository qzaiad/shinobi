# CLAUDE.md — Shinobi Port Twin

## What this project is
A learning-and-build project: simulated cameras observing a real port stream RTSP into **Shinobi** (open-source NVR/CCTV, Node.js), which records and detects; an **evaluator** turns detections into port events; a **digital twin** keeps port state and visualizes it in 3D. It is the camera pillar of the larger "smart ports" twin, so components must stay decoupled and scalable.

The learning plan lives in `docs/learning-plan.md`; current progress in `docs/progress.md`. Read both at the start of a task to know which session we are in.

## About the developer
- Senior C++ developer (15+ years), systems and Linux networking background; also Python (pytest) and vanilla JS.
- **Beginner in video/CCTV, Shinobi, MQTT and digital twins.** Explain those mechanisms (RTSP/RTP, codecs, GOP/PTS/DTS, FFmpeg flags, NVR internals, event flow) in depth and with ASCII diagrams (data flow, timelines, packet/stream structure), defining each term on first use and showing what breaks without it. See "How explanations work" in `docs/learning-plan.md`. Skip generic programming basics.
- Works on Ubuntu in VS Code.

## Architecture
```
media/ clips → FFmpeg publishers → MediaMTX (RTSP :8554)
  → Shinobi (:8080, monitors, recording, motion + object-detection plugin)
  → Mosquitto (MQTT :1883) / webhooks
  → services/evaluator → port/<site>/{detections,state,alerts}/...
  → services/twin-state (REST + WebSocket) → viewer/ (3D)
```
Contract between components = MQTT topics + JSON schemas in `docs/schema/`. Never couple a service to another service's internals.

## Repository layout
```
CLAUDE.md, README.md, Makefile, docker-compose.yml, .env.example
compose/                  generated / profile-specific compose files
sim/                      cameras.yaml, gen_compose.py (camera simulation)
media/                    footage (gitignored) — only manifest.tsv + SOURCES.md are committed
tools/                    Python dev tools (streamprobe: ffprobe stream health / GOP check)
scripts/                  fetch_media.sh, probe.sh, chaos.sh, provision_monitors.py, inject_event.py
tools/streamprobe/        ffprobe-based stream health check (codec, pix_fmt, B-frames, GOP regularity)
shinobi-config/           monitor template, detection.yaml (motion regions), generated monitors/*.json, account.yaml (account + API keys; no secrets)
services/common/          shinobi_client.py, schema.py (shared)
services/webhook-receiver/
services/evaluator/
services/detector/        optional standalone detector
services/yolo-plugin/     Shinobi YoloV9 ONNX plugin image + fixes (ORT threads, load shedding, reconnect)
services/twin-state/
twin/model/               port.yaml (layout, zones, cameras)
viewer/                   3D visualization
tests/                    pytest; fixtures in tests/fixtures/
docs/                     learning-plan.md, progress.md, nvr-insights.md, notes/, adr/, schema/
```
Create folders only when a session needs them.

## Common commands
```bash
docker compose up -d                 # start stack (use --profile sim|core|twin when profiles exist)
docker compose logs -f shinobi
ffplay rtsp://localhost:8554/cam-gate
mosquitto_sub -v -t 'shinobi/#' -t 'port/#'
uv sync                              # create .venv (Python 3.14 pinned) + dev deps
uv run pytest                        # unit + integration (e2e deselected via addopts)
uv run pytest -m "not integration"   # pure unit tests only
uv run pytest -m e2e                 # end-to-end, e.g. tests/test_streams.py stream health (needs running stack)
uv run python -m tools.streamprobe rtsp://localhost:8554/cam-gate --seconds 10 [--json]  # check stream conventions
uvx ruff check . && uvx ruff format .
uv run python -m tools.streamprobe rtsp://localhost:8554/cam-gate [--seconds N] [--json]
./scripts/fetch_media.sh [--pin] [--only ID] [--probe]   # restore footage (run before the simulator)
cp .env.example .env                 # once; sets MTX_TAG for compose
sim/scripts/check_stream.sh rtsp://localhost:8554/cam-gate media/gate_santos_port.mp4  # DTS continuity across loop boundaries (~2.5x clip length)
sim/scripts/normalize.sh media/raw/<clip> media/<clip>.mp4   # re-encode to the camera profile (docs/camera-profile.md)
sim/scripts/probe_clip.sh media/<clip>.mp4                   # PASS/FAIL per profile check; exit 1 on any FAIL
scripts/sim-up.sh                    # generate fleet from sim/cameras.yaml, seed Shinobi config, start mediamtx + shinobi + all cameras (main /cam-x + sub /cam-x-sub)
scripts/sim-down.sh                  # stop and remove them (volumes kept; never add -v: it wipes Shinobi accounts + recordings)
scripts/shinobi-seed.sh              # once: data/shinobi/{conf,super}.json from the pinned image (called by sim-up.sh)
docker compose exec shinobi mysql ccio -e 'show tables'   # look into Shinobi's built-in MariaDB (read only)
uv run pytest -m e2e tests/test_shinobi.py   # Shinobi up, superuser password changed, admin account persists (needs .env creds)
uv run python sim/gen_compose.py [--check]   # regenerate compose/cameras.generated.yml (--check: exit 1 if stale)
sim/scripts/snapshot.sh rtsp://localhost:8554/cam-gate-sub [out.jpg]   # one JPEG; compare OSD clock with host UTC
scripts/shinobi-cpu.sh [SECONDS] [RUNS]   # CPU per Shinobi process + container total (cgroup), 100 % = 1 core
scripts/chaos.sh status|stop|start|crash|freeze|thaw|flap|netem|clear <cam> [args]   # fault injection (netem: e.g. loss 10%)
docker compose exec shinobi mysql ccio -N -B --raw -e "select time, json_value(info,'$.type'), json_value(info,'$.msg.cmd') from Logs where mid='cam_gate' order by time desc limit 5"   # ffmpeg command Shinobi generated
uv run python -m services.common.shinobi_client monitors|plugins|videos [mid] [--limit N]|events [mid] [--limit N]|snapshot <mid> <out.jpg>   # read-only API client (.env key)
uv run python scripts/motion_stats.py [--minutes N | --since T [--until T]] [mid ...]   # motion events/min + confidence per monitor and region
scripts/region_signal.sh rtsp://localhost:8554/cam-gate-sub X Y W H [SECONDS] [FPS] [DELTA]   # per-frame % changed in a rectangle (what the motion detector sees)
uv run pytest -m e2e tests/test_motion.py   # motion event rates per region over the last 3 min (cameras without motion-first object detection)
uv run python scripts/object_stats.py [--minutes N | --since T] [mid ...]   # object events/min, plugin ms, labels + scores per monitor
uv run pytest -m e2e tests/test_object_detection.py   # plugin connected, labelled object events, plugin latency (last 3 min)
docker compose logs yolo | grep backpressure   # plugin frames inferred/dropped per minute
uv run python scripts/provision_monitors.py plan|apply [--prune]   # monitors from cameras.yaml + shinobi-config/ (edit key); plan exit 2 = changes pending
uv run python scripts/provision_monitors.py render [--check]|export [--out DIR]   # desired files in shinobi-config/monitors/ | live configs as files
uv run python scripts/shinobi_bootstrap.py plan|apply [--prune] [--wait 180]   # superuser, account, API keys from shinobi-config/account.yaml + .env; apply rewrites generated values (uid, key codes) in .env
uv run python scripts/record_shinobi_fixtures.py   # re-record tests/fixtures/shinobi/*.json (keys, session tokens, hashes, e-mails redacted; aborts on any leak)
( umask 077; docker compose exec -T shinobi mysqldump --single-transaction ccio > data/backups/ccio-$(date -u +%Y%m%dT%H%M%SZ).sql )   # local DB backup (gitignored; holds secrets)
```
Update this section when commands change.

## Git workflow (important)
- Work directly on branch **`main`**. Do not create other branches unless asked.
- **One commit per completed step/session.** At the end of a step:
  1. run the relevant tests/checks and report results,
  2. summarize the diff,
  3. propose a commit message in **Conventional Commits** format (`feat|fix|docs|test|chore|refactor(scope): subject`, subject ≤ 72 chars, imperative; body explains what and why),
  4. commit after the developer confirms.
- Append a line to `docs/progress.md` (date, session, short note) as part of the same commit.
- When a step adds or changes a note in `docs/notes/` (not `*-explained.md`), update `docs/nvr-insights.md` (own-NVR insights with note references; procedure at its top) in the same commit.
- Never `push --force`, rewrite history, or push without being asked.
- Never commit: media files, recordings, `.env`, API keys, group keys, passwords, database dumps.
- Never put the local OS username in a committed or published file: write home paths as `/home/username/...` (also in pasted command output).

## Coding conventions
- Python ≥ 3.12, type hints everywhere, `@dataclass(slots=True)` (frozen where sensible) for data, `httpx` for HTTP, `paho-mqtt` (or `aiomqtt`) for MQTT, FastAPI for small HTTP services.
- Tests with pytest; prefer recorded real payloads in `tests/fixtures/` over hand-made mocks.
- Lint/format with ruff.
- C++ (when used, e.g. detector/frame pipeline): C++23, CMake, warnings as errors, no raw owning pointers.
- Config via environment variables / YAML; no hard-coded hosts, keys or camera lists in code.
- Each service has its own Dockerfile and is wired into compose.

## Shinobi facts and rules
- Image: `registry.gitlab.com/shinobi-systems/shinobi:dev`, pinned by digest (`SHINOBI_DIGEST` in `.env`), built-in MariaDB; only data is mounted (volumes `shinobi-db`, `shinobi-videos`, `data/shinobi/{conf,super}.json`). See `docs/adr/0001-shinobi-image-and-database.md`. UI on `127.0.0.1:8080`, superuser panel at `/super`.
- `init.sh` rewrites `conf.json` keys `db`, `ssl`, `pluginKeys`, `subscriptionId`, `databaseType`, `cpuUsageMarker` from env on every start; keep `DB_PASSWORD` empty. Max 5 failed logins per IP, then 15 min lock.
- API URLs follow `/[API_KEY]/<endpoint>/[GROUP_KEY]/[MONITOR_ID]`. Keys come from `.env`. A bad key or missing permission is HTTP 200 with `{"ok": false}` (or `[]`); check the body. Answers embed the key in URLs: redact before saving. See `docs/notes/11-shinobi-api.md`.
- Events leave Shinobi via detector webhooks, MQTT outbound (`mqttClient` enabled in `conf.json`), the REST API, or WebSocket. The chosen primary transport is recorded in `docs/adr/`.
- Object detection: plugin service `yolo` (socket.io client, dials `shinobi:8080`), paired by `SHINOBI_PLUGIN_KEY` (`.env`, generated by `scripts/shinobi-seed.sh`) → Shinobi env `PLUGIN_KEYS` → `conf.json` `pluginKeys.YoloV9` (rewritten on every start). With "check for motion first" motion triggers save no Events rows. See `docs/notes/15-object-detection.md`.
- Monitor configs are provisioned from `sim/cameras.yaml` + `shinobi-config/` (template, `detection.yaml`) by `scripts/provision_monitors.py`; the superuser, admin account and API keys by `scripts/shinobi_bootstrap.py` from `shinobi-config/account.yaml` + `.env`. Don't rely on hand-made UI changes — put them in the repo.
- Monitor `details` flags are stored as strings (`"1"`, sent double-encoded by the provisioner): a no-restart save of detection settings otherwise leaves numbers in memory and `=== '1'` checks (event saving) fail. See `docs/notes/14-motion-detection.md` §8.
- Change Shinobi state through its API, never by SQL: Shinobi caches users, keys and monitors in memory. Operations, logs, backup and troubleshooting: `docs/notes/13-shinobi-cheatsheet.md`.
- **Shinobi docs and APIs change. Do not invent endpoints, config keys, payload fields or plugin names.** If unsure, say so and check https://docs.shinobi.video or the Shinobi source on GitLab, or inspect real payloads from the running instance.

## Media policy
- **Never commit media.** Everything under `media/` is gitignored except `manifest.tsv` and `SOURCES.md`.
- **`media/manifest.tsv` is the source of truth** for footage: id, role, filename, URL, pinned sha256, license, author. Add or change clips there (plus a matching row in `media/SOURCES.md`), never by dropping files into `media/`.
- **NC and ND licenses are excluded.** Allowed: CC0-1.0, CC-BY-*, CC-BY-SA-*, PD, Pexels, Pixabay (enforced by `tests/test_media_manifest.py`). No live webcams.
- **Run `./scripts/fetch_media.sh` before starting the simulator** (fresh clone, or after the manifest changes). New rows: `--pin` records the hash. Selection rationale and source ffprobe data: `docs/notes/03-port-footage.md`.

## Simulation rules
- Only use port footage whose license allows reuse; record source, author, license and date in `media/SOURCES.md`. Do not restream live public webcams.
- Simulated cameras must behave like real IP cameras. Convention:
  - **H.264 Main profile, `yuv420p`, `-bf 0` (no B-frames), fixed GOP = 2 s** (`-g` = 2 × fps, `-keyint_min` = same, `-sc_threshold 0`),
  - **RTSP over TCP** (`-rtsp_transport tcp`) for publishing and pulling,
  - main + sub stream (`/<id>` and `/<id>-sub`, sub 640×360 @ 10 fps by default), UTC timestamp + camera id OSD (`drawtext`).
- Check every new or changed camera with `uv run python -m tools.streamprobe <url>`; it must report fixed GOP ≈ 2 s and no B-frames.

## How to work with me
- Before larger changes, give a short plan and wait for confirmation.
- Prefer small, verifiable steps; tell me exactly how to verify each one.
- When something fails, show the relevant log lines and explain the root cause before fixing.
