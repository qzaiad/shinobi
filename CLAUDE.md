# CLAUDE.md — Shinobi Port Twin

## What this project is
A learning-and-build project: simulated cameras observing a real port stream RTSP into **Shinobi** (open-source NVR/CCTV, Node.js), which records and detects; an **evaluator** turns detections into port events; a **digital twin** keeps port state and visualizes it in 3D. It is the camera pillar of the larger "smart ports" twin, so components must stay decoupled and scalable.

The learning plan lives in `docs/learning-plan.md`; current progress in `docs/progress.md`. Read both at the start of a task to know which session we are in.

## About the developer
- Senior C++ developer (15+ years), systems and Linux networking background; also Python (pytest) and vanilla JS.
- New to Shinobi. Explain Shinobi- and video-specific mechanisms (RTSP/RTP, codecs, FFmpeg flags, event flow) at a senior level; skip generic programming basics.
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
shinobi-config/           exported monitor JSON templates (no secrets)
services/common/          shinobi_client.py, schema.py (shared)
services/webhook-receiver/
services/evaluator/
services/detector/        optional standalone detector
services/twin-state/
twin/model/               port.yaml (layout, zones, cameras)
viewer/                   3D visualization
tests/                    pytest; fixtures in tests/fixtures/
docs/                     learning-plan.md, progress.md, notes/, adr/, schema/
```
Create folders only when a session needs them.

## Common commands
```bash
docker compose up -d                 # start stack (use --profile sim|core|twin when profiles exist)
docker compose logs -f shinobi
ffplay rtsp://localhost:8554/cam-gate
mosquitto_sub -v -t 'shinobi/#' -t 'port/#'
uv sync                              # create .venv (Python 3.14 pinned) + dev deps
uv run pytest                        # all tests (unit + integration)
uv run pytest -m "not integration"   # pure unit tests only
uv run pytest -m e2e                 # end-to-end (needs running stack)
uv run python -m tools.streamprobe rtsp://localhost:8554/cam-gate --seconds 10 [--json]  # check stream conventions
uvx ruff check . && uvx ruff format .
uv run python -m tools.streamprobe rtsp://localhost:8554/cam-gate [--seconds N] [--json]
./scripts/fetch_media.sh [--pin] [--only ID] [--probe]   # restore footage (run before the simulator)
cp .env.example .env                 # once; sets MTX_TAG for compose
sim/scripts/check_stream.sh rtsp://localhost:8554/cam-gate media/gate_santos_port.mp4  # DTS continuity across loop boundaries (~2.5x clip length)
sim/scripts/normalize.sh media/raw/<clip> media/<clip>.mp4   # re-encode to the camera profile (docs/camera-profile.md)
sim/scripts/probe_clip.sh media/<clip>.mp4                   # PASS/FAIL per profile check; exit 1 on any FAIL
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
- Never `push --force`, rewrite history, or push without being asked.
- Never commit: media files, recordings, `.env`, API keys, group keys, passwords, database dumps.

## Coding conventions
- Python ≥ 3.12, type hints everywhere, `@dataclass(slots=True)` (frozen where sensible) for data, `httpx` for HTTP, `paho-mqtt` (or `aiomqtt`) for MQTT, FastAPI for small HTTP services.
- Tests with pytest; prefer recorded real payloads in `tests/fixtures/` over hand-made mocks.
- Lint/format with ruff.
- C++ (when used, e.g. detector/frame pipeline): C++23, CMake, warnings as errors, no raw owning pointers.
- Config via environment variables / YAML; no hard-coded hosts, keys or camera lists in code.
- Each service has its own Dockerfile and is wired into compose.

## Shinobi facts and rules
- Image: `registry.gitlab.com/shinobi-systems/shinobi:dev` (see ShinobiDocker on GitLab). UI on `:8080`, superuser panel at `/super`.
- API URLs follow `/[API_KEY]/<endpoint>/[GROUP_KEY]/[MONITOR_ID]`. Keys come from `.env`.
- Events leave Shinobi via detector webhooks, MQTT outbound (`mqttClient` enabled in `conf.json`), the REST API, or WebSocket. The chosen primary transport is recorded in `docs/adr/`.
- Monitor configs are provisioned from `sim/cameras.yaml` + `shinobi-config/` by `scripts/provision_monitors.py`; don't rely on hand-made UI changes — export them back to the repo.
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
  - main + sub stream, timestamp overlay.
- Check every new or changed camera with `uv run python -m tools.streamprobe <url>`; it must report fixed GOP ≈ 2 s and no B-frames.

## How to work with me
- Before larger changes, give a short plan and wait for confirmation.
- Prefer small, verifiable steps; tell me exactly how to verify each one.
- When something fails, show the relevant log lines and explain the root cause before fixing.
