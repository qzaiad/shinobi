# Session 1 — Repo bootstrap

What the project is, how the repository is organized, and the rules every later session follows.
Commit: `f1e2269 chore: bootstrap repo structure, CLAUDE.md and learning plan`.

---

## 1. What we are building

A **digital twin** is a software model of a real place that is kept up to date with live data. Here
the place is a container port and the live data comes from (simulated) CCTV cameras:

```
 ┌──────────────┐  RTSP   ┌────────────┐  RTSP  ┌───────────────────────┐
 │ simulated    │ ──────► │  MediaMTX  │ ─────► │       Shinobi         │  NVR = Network Video
 │ cameras      │  :8554  │ RTSP server│        │ record · motion ·     │  Recorder: pulls camera
 │ (FFmpeg)     │         └────────────┘        │ object detection      │  streams, records them,
 └──────────────┘                               └──────────┬────────────┘  detects events
   Phase 1 (Sessions 4–7)                                  │ events: webhook / MQTT
                                                           ▼
                                                 ┌───────────────────┐
                                                 │ Mosquitto (MQTT)  │  message broker :1883
                                                 └─────────┬─────────┘
                                                           ▼
 ┌──────────────┐  WebSocket  ┌──────────────┐   ┌───────────────────┐
 │  3D viewer   │ ◄────────── │ twin-state   │ ◄─│    evaluator      │  "truck at gate",
 │  (viewer/)   │             │ REST + WS    │   │ detections → port │  "berth occupied"
 └──────────────┘             └──────────────┘   │ events            │
   Phase 5                      Phase 5          └───────────────────┘  Phase 4
```

The pieces only talk through **MQTT topics and JSON schemas** (`docs/schema/`). None of them imports
another's code, so any part can be swapped (e.g. a real camera instead of FFmpeg, or a different
detector) or scaled (more cameras, more evaluators) without touching the rest:

```
 tightly coupled (avoided)              decoupled (this project)
 evaluator ──import──► shinobi code     evaluator ──subscribe──► port/+/detections/#
   breaks when Shinobi changes            only depends on the topic name + JSON schema
```

---

## 2. Repository layout

```
shinobi/
├── CLAUDE.md               instructions for Claude Code (read every session)
├── README.md               short project overview
├── docker-compose.yml      base stack (today: mediamtx)
├── compose/                generated compose files (cameras.generated.yml)
├── .env.example            template for local settings; .env itself is never committed
├── pyproject.toml, uv.lock Python project + exact dependency versions
├── .python-version         Python version for uv (3.14)
├── docs/
│   ├── learning-plan.md    the 35 sessions
│   ├── progress.md         one line per finished session
│   ├── notes/              per-session explanations (this file)
│   ├── adr/                architecture decision records (later)
│   └── schema/             JSON schemas = contracts between services (later)
├── media/                  footage — gitignored except manifest.tsv + SOURCES.md
├── sim/                    camera simulation (cameras.yaml, generator, publisher image)
├── tools/                  dev tools (streamprobe)
├── scripts/                fetch_media.sh, sim-up.sh, sim-down.sh, …
├── services/               evaluator, twin-state, … (later)
└── tests/                  pytest
```

Folders are created only when a session needs them. An empty `services/` would only suggest work
that doesn't exist yet.

---

## 3. `CLAUDE.md`: how Claude Code gets project context

Claude Code reads `CLAUDE.md` from the repo root at the start of **every** session and treats it as
standing instructions. Without it, each session would start with no memory of the project.

```
 new Claude Code session
        │ reads automatically
        ▼
 CLAUDE.md ──► "what is this project, how is it laid out, what are the rules"
        │        points to
        ▼
 docs/learning-plan.md + docs/progress.md ──► "which session are we in"
```

What it contains, and why:

| Section | Purpose |
|---|---|
| What this project is / Architecture | Big picture, so changes fit the design. |
| About the developer | How to explain: in depth with diagrams for video/Shinobi topics, brief for generic programming. |
| Repository layout / Common commands | Where things go and how to run them; kept up to date each session. |
| Git workflow | One commit per session on `main`, Conventional Commits, never commit secrets or media. |
| Coding conventions | Python ≥ 3.12, type hints, dataclasses, ruff, pytest; config via env/YAML, never hard-coded. |
| Shinobi facts and rules | **Don't invent endpoints or config keys.** Shinobi changes often; check the docs or source. |
| Media and simulation rules | License allowlist; the camera stream convention (H.264 Main, no B-frames, 2 s GOP, RTSP/TCP). |

---

## 4. The session workflow

```
 ┌────────────────────────────────────────────────────────────────────┐
 │ 1. read learning-plan.md + progress.md → which session?            │
 │ 2. learn the topic (notes with diagrams)                           │
 │ 3. build the "Do" part in small, verifiable steps                  │
 │ 4. run tests / checks, report results                              │
 │ 5. summarize the diff, propose a Conventional Commit message       │
 │ 6. append one line to progress.md (same commit)                    │
 │ 7. commit after confirmation                                       │
 └──────────────────────────────┬─────────────────────────────────────┘
                                └── next session starts at 1
```

**Conventional Commits.** Every commit message has a machine-readable first line:

```
 feat(sim): generate camera fleet from cameras.yaml
 └┬─┘ └┬┘   └──────────────┬─────────────────────┘
  │    │                   subject: imperative, ≤ 72 chars
  │    scope: which part of the repo (sim, tools, shinobi, eval, twin, …)
  type: feat = new capability · fix = bug fix · docs · test · refactor · chore = tooling/housekeeping

 (blank line)
 Body: what changed and WHY. The diff already shows how.
```

The types make history scannable (`git log --oneline | grep '^.\{8\} feat'`) and could later
generate a changelog.

---

## 5. `.gitignore`: what must never enter git

```gitignore
# Footage: only the manifest and attribution are committed
media/*
!media/manifest.tsv
!media/SOURCES.md
```

Why `media/*` and not `media/`: git cannot re-include a file whose **parent directory** is excluded.
`media/` would ignore the directory itself, and the `!` lines would have no effect.

```
 media/      → directory ignored → git never looks inside → !manifest.tsv ignored too  ✗
 media/*     → directory tracked, every entry ignored  → !manifest.tsv re-included     ✓
```

The other groups:

| Pattern | Why |
|---|---|
| `*.mp4 *.mkv *.webm`, `/recordings/`, `/videos/` | Video is large and binary, and footage has license terms. Recordings may show people. |
| `.env`, `.env.*` (but `!.env.example`) | API keys, Shinobi group keys, passwords. |
| `.venv/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/` | Generated locally; each machine rebuilds them. |
| `*.sql`, `/mysql/`, `/data/` | Database dumps and Docker volume data. |

---

## 6. `.env` and `.env.example`: settings without secrets in git

```
 .env.example (committed)              .env (local only, gitignored)
 ┌──────────────────────────┐  cp     ┌──────────────────────────┐
 │ SHINOBI_API_KEY=         │ ──────► │ SHINOBI_API_KEY=abc123…  │ ← real secrets filled in
 │ MTX_TAG=1.21.1           │         │ MTX_TAG=1.21.1           │
 └──────────────────────────┘         └────────────┬─────────────┘
   documents WHICH settings exist                  │ read automatically by
                                                    ▼
                                docker compose: image: bluenviron/mediamtx:${MTX_TAG:?…}
                                Python services: os.environ["SHINOBI_API_KEY"] (later)
```

`${MTX_TAG:?set MTX_TAG in .env}` means: substitute the variable, or **stop with this error** if it's
missing. That's better than silently pulling a random `latest` tag.

---

## 7. Python toolchain: uv

```
 .python-version  "3.14"        which interpreter
 pyproject.toml                 what the project needs: dependencies, dev deps (pytest), ruff, pytest config
 uv.lock                        exact resolved versions (committed → same versions everywhere)
        │
        │ uv sync
        ▼
 .venv/  ← Python 3.14.6 + pinned packages (gitignored)
        │
        │ uv run <cmd>   runs <cmd> inside .venv
        ▼
 uv run pytest · uv run python -m tools.streamprobe …
```

Your machine also has pyenv with a global Python 3.8. `uv run` ignores it and always uses `.venv`:

```
$ uv run python -c 'import sys; print(sys.executable)'
/home/abuahmad/git/shinobi/.venv/bin/python3
$ uv run python --version
Python 3.14.6
```

`pyproject.toml` also configures pytest. Its markers separate test speeds:

```
 uv run pytest -m "not integration"   pure logic, milliseconds, no tools needed
 uv run pytest                        + integration: needs ffmpeg/ffprobe installed
 uv run pytest -m e2e                 needs the running docker compose stack (later)
```

---

## 8. Tool versions (recorded in progress.md)

| Tool | Version | Used for |
|---|---|---|
| Ubuntu | 24.04.5 | host OS |
| Docker / Compose | 29.8 / v5.6 | running all services |
| ffmpeg / ffprobe | 6.1.1 (Ubuntu package) | encoding, probing streams |
| git | 2.43.0 | version control |
| Python (via uv) | 3.14.6 | tools, tests, services |

## 9. Verify it yourself

```bash
cat CLAUDE.md | head -30                 # the project brief
git log --oneline                        # one commit per session
git check-ignore -v media/gate_santos_port.mp4 .env   # which .gitignore rule matches
git status --ignored --short | head      # what git deliberately ignores
uv run python --version                  # 3.14.x from .venv
uv run pytest -q                         # all tests
```
