# Session 5 — Camera fleet as config

How one YAML file becomes four live RTSP cameras: what each piece does, why it's there, and what
breaks without it. All outputs below are real, captured from this repo on 2026-10-08.

```
 sim/cameras.yaml ──► sim/gen_compose.py ──► compose/cameras.generated.yml
   (what you edit)      (validate + render)       (never edit by hand)
                                                         │
 docker-compose.yml ────────────────────────────────────┤  docker compose -f … -f …
   (mediamtx only)                                       ▼
                                          4 containers, one per camera
                                          docker-init → publish.sh → ffmpeg
                                                         │ RTSP over TCP
                                                         ▼
                                          MediaMTX  rtsp://localhost:8554/cam-gate
                                                                          cam-quay
                                                                          cam-yard
                                                                          cam-waterway
```

---

## 1. Why cameras are data, not hand-written services

In Session 4, `cam-gate` was a block hand-written into `docker-compose.yml`. That works for one camera.
It falls apart at four, and even more at the 16 planned for Session 34:

```
 Hand-written (Session 4)                 Generated (Session 5)
 ────────────────────────                 ─────────────────────
 docker-compose.yml                       cameras.yaml          ← one place, 8 lines per camera
   cam-gate:   45 lines                     cam-gate: id, role, clip, size, fps, bitrate, gop
   cam-quay:   45 lines (copy-paste)        cam-quay: …
   cam-yard:   45 lines (copy-paste)                │
   …                                                ▼ generator (validates every field)
 A typo in camera 3 is found when           cameras.generated.yml (45 lines per camera,
 the stream doesn't come up.                 always the same shape, never edited)
```

The bigger reason: **the camera list will have other consumers.** In Session 12,
`provision_monitors.py` creates one Shinobi monitor per camera from the same `cameras.yaml`. In
Session 26 the twin model places the cameras. With a single source of truth, they can't disagree.

```
                       ┌──► gen_compose.py        → compose services     (now)
 sim/cameras.yaml ─────┼──► provision_monitors.py → Shinobi monitors     (Session 12)
                       └──► tests / chaos.sh      → which streams exist  (Session 7)
```

---

## 2. `sim/cameras.yaml`: defaults and per-camera values

```yaml
version: 1                        # schema version: lets a future v2 coexist
defaults:                         # applies to every camera…
  image: shinobi-sim-camera:local
  build: ./sim/camera
  preset: veryfast
  transport: tcp
cameras:
  - id: cam-gate                  # …unless the camera sets the same key itself
    role: gate
    clip: gate_santos_port.mp4    # file under media/
    width: 1280
    height: 720
    fps: 15
    bitrate: 2M
    gop: 30
```

How a camera's effective settings are resolved (`cam.preset or defaults.preset` in the code):

```
   defaults            camera entry           effective value
 ┌──────────────┐   ┌────────────────┐     ┌────────────────────┐
 │ preset:      │   │                │     │ preset: veryfast   │ ← from defaults
 │   veryfast   │ + │ (not set)      │  =  │                    │
 │ transport:   │   │ transport: udp │     │ transport: udp     │ ← camera wins
 │   tcp        │   │                │     │                    │
 └──────────────┘   └────────────────┘     └────────────────────┘
```

The fleet as it runs now:

| id | role | clip | output | GOP |
|---|---|---|---|---|
| cam-gate | gate | gate_santos_port.mp4 | 1280×720, 15 fps, 2 Mbit/s | 30 frames = 2 s |
| cam-quay | quay | quay_sts_truck_loading.mp4 | 1280×720, 15 fps, 4 Mbit/s | 30 frames = 2 s |
| cam-yard | yard | yard_malta_freeport.mp4 | 1280×720, 10 fps, 1.5 Mbit/s | 20 frames = 2 s |
| cam-waterway | waterway | waterway_porpoise_bay.mp4 | 1280×720, 10 fps, 3 Mbit/s | 20 frames = 2 s |

---

## 3. The generator: load → validate → render → write

```
 cameras.yaml
     │  yaml.safe_load            text → Python dicts/lists
     ▼
   load ──► parse (pydantic)      dicts → typed Fleet / Camera objects, or ConfigError
     │
     ├──► check_clips             does media/<clip> exist?        (needs the disk)
     ├──► warnings_for            gop != 2 × fps → warning only
     ▼
   render(fleet) → dict           PURE: same input → same output, no I/O
     │
     ▼
   dump → text with header ──► write file        or   --check: compare + diff, exit 1
```

**Why `render()` is pure** (no file reads, no clock, no environment): tests can call it directly
with an in-memory config, and two runs are guaranteed byte-identical. Determinism matters because
the generated file is committed to git. If the output shuffled key order between runs, every run
would show up as a diff.

**Why `check_clips` is separate from validation:** `media/` is gitignored, so on a fresh clone the
clips don't exist yet. The golden test (section 9) must still pass there, so it validates and
renders without touching `media/`. The CLI (and therefore `sim-up.sh`) does check the clips,
because it is about to start cameras that need them.

### Validation rules and why each exists

| Rule | Why |
|---|---|
| `id` matches `^cam-[a-z0-9-]+$` | The id becomes the compose service name (= DNS hostname on the Docker network) **and** the RTSP path. Both dislike uppercase, spaces, `_`, `/`. |
| unique `id` | Two services with the same name would silently merge; two publishers on one RTSP path would fight. |
| `role` ∈ {gate, quay, yard, waterway} | Later stages (evaluator rules, twin) branch on role. A typo like `gaet` must fail here, not as a "no events" mystery in Session 22. |
| even `width`/`height` | See the 4:2:0 diagram below. |
| `1 ≤ fps ≤ 60` | 0 is meaningless; > 60 is no CCTV camera. |
| `gop ≥ 1` | A GOP of 0 frames is not a valid keyframe interval. |
| `bitrate` like `2M`, `1.5M`, `750k` | Has to be something ffmpeg understands **and** something we can double for `-bufsize`. |
| clip exists | Otherwise ffmpeg would fail inside the container every 2 s forever. |
| `gop != 2 × fps` → **warning** | Project convention (2 s GOP), but a deliberate exception is allowed. |

**Even dimensions, explained.** `yuv420p` stores brightness (Y) for every pixel, but colour (U, V)
only once per 2×2 block of pixels. A frame with an odd width has a last column of pixels with no
complete 2×2 block, and the H.264 encoder refuses it.

```
 Y (luma): every pixel              U/V (chroma): one sample per 2×2 block
 ┌──┬──┬──┬──┐                     ┌─────┬─────┐
 │  │  │  │  │                     │     │     │
 ├──┼──┼──┼──┤        ──►          │  U  │  U  │      width 4 → 2 chroma columns ✓
 │  │  │  │  │                     │     │     │      width 5 → 2.5 columns     ✗
 └──┴──┴──┴──┘                     └─────┴─────┘
```

Errors name the camera, not a list index. Real output for a config with odd widths and a `berth` role:

```
$ uv run python sim/gen_compose.py --config bad.yaml
gen_compose: invalid bad.yaml:
cam-gate width: Value error, must be even, got 1279
cam-quay width: Value error, must be even, got 1279
cam-yard role: Input should be 'gate', 'quay', 'yard' or 'waterway'
cam-yard width: Value error, must be even, got 1279
cam-waterway width: Value error, must be even, got 1279
```

Pydantic reports errors with a location like `cameras → 2 → role`. `parse()` looks up the `id` of
camera #2 in the raw YAML and prints `cam-yard role` instead.

---

## 4. The ffmpeg command, stage by stage

Each camera runs one ffmpeg process. It reads a file, decodes it, filters it, re-encodes it like
an IP camera would, and pushes it to MediaMTX:

```
 /clips/gate_santos_port.mp4
   │ demux + decode                       -re -stream_loop -1 -i …   -an
   ▼
 raw frames, 1280×720, 25 fps
   │ filter graph                         -vf scale=1280:720,fps=15,format=yuv420p
   ▼
 raw frames, 1280×720, 15 fps, yuv420p
   │ H.264 encoder                        -c:v libx264 -preset veryfast -tune zerolatency
   │                                      -profile:v main -bf 0
   │                                      -b:v 2M -maxrate 2M -bufsize 4M
   │                                      -g 30 -keyint_min 30 -sc_threshold 0
   ▼
 H.264 packets
   │ RTSP muxer                           -f rtsp -rtsp_transport tcp rtsp://mediamtx:8554/cam-gate
   ▼
 MediaMTX
```

### 4.1 Input: `-re -stream_loop -1 -i … -an`

- **`-stream_loop -1`** loops the input forever. At the end of the file the demuxer seeks back to
  the start and **adds the clip duration to all timestamps**, so time keeps increasing.
- **`-re`** reads the input at its native speed (one second of video per second of wall time). Without
  it, ffmpeg would decode as fast as the CPU allows: a 55 s clip in about 2 s, flooding MediaMTX.
  A real camera can't send the future.
- **`-an`** drops audio. The normalized clips have none, so this is only a guard.

```
 file timeline:   0 ─────── 54.9 s │ 0 ─────── 54.9 s │ 0 ──── …
 output PTS:      0 ─────── 54.9 s │ 54.9 ──── 109.8 s │ 109.8 ── …   (always increasing)
                                   ▲ loop boundary: demuxer seeks to 0, adds offset
```

### 4.2 Filter graph: `scale=1280:720,fps=15,format=yuv420p`

A filter graph is a chain of operations on **decoded** frames, read left to right:

1. **`scale=1280:720`** resizes. The normalized clips are already 1280×720, so this does no work
   today. It lets the YAML ask for a different size later.
2. **`fps=15`** changes the frame rate by **dropping** frames. The clips are 25 fps, which is
   5 frames per 0.2 s. At 15 fps, 3 of every 5 survive; at 10 fps, 2 of every 5:

```
 source 25 fps:  F0  F1  F2  F3  F4 │ F5  F6  F7  F8  F9 │ …     (one F every 40 ms)
 fps=15 keeps:   F0      F2  F3     │ F5      F7  F8     │ …     (~67 ms apart)
 fps=10 keeps:   F0          F3*    │ F5          F8*    │ …     (100 ms apart)
                                    (* nearest source frame to each 100 ms tick)
```

3. **`format=yuv420p`** forces the pixel format the Main profile needs (section 3). It does no work
   when the input already is yuv420p.

Lower fps on the yard and waterway cameras mirrors real deployments: slow scenes get fewer frames
to save bandwidth and storage.

### 4.3 Encoder: libx264 settings

| Flag | Meaning | What breaks without it |
|---|---|---|
| `-c:v libx264` | Encode H.264 in software (x264). | — |
| `-preset veryfast` | Trade compression efficiency for speed. 4 live encoders must keep up in real time. | `slow` would fall behind `-re` and frames would drop. |
| `-tune zerolatency` | No lookahead buffer, no B-frames, every frame leaves the encoder at once. | x264 would queue frames before emitting the first packet (rate-control lookahead, B-frame reordering, frame threads), adding noticeable latency. |
| `-profile:v main` | Restrict to the Main profile feature set (what cameras and decoders universally accept). | Default is High, which some NVR/browser paths handle worse. |
| `-bf 0` | No B-frames (explicit, although zerolatency already disables them). | Out-of-order frames: decode order ≠ display order, more latency, DTS trouble at loop seams. |

**B-frames, why CCTV avoids them.** A B-frame is predicted from a frame *before* and a frame *after* it.
The encoder therefore has to send the later frame first:

```
 display order:  I  B  B  P            no B-frames (-bf 0):
 decode order:   I  P  B  B              display = decode:  I  P  P  P  P
                    ▲ P must arrive        each frame can be shown as soon as it arrives
                      before the Bs        → lower latency, DTS == PTS order
```

### 4.4 Rate control: `-b:v 2M -maxrate 2M -bufsize 4M`

Compressed frame sizes vary a lot. An I-frame (full picture) can be 10–20× a P-frame (changes only).
Rate control decides how many bits each frame may use:

- **`-b:v 2M`**: target average bitrate, 2 Mbit/s.
- **`-maxrate 2M`**: the long-run rate must never exceed 2 Mbit/s.
- **`-bufsize 4M`**: the VBV buffer (Video Buffering Verifier), the window over which "never exceed" is
  measured. 4 Mbit at 2 Mbit/s = **2 s of slack**, one GOP's worth. Large I-frames can borrow from the
  small P-frames around them.

```
 VBV = a leaky bucket of size bufsize (4 Mbit)
          frames pour bits in
            │ ██ I-frame (big)
            ▼ ▪  P-frames (small)
        ┌──────────┐
        │░░░░░░░░░░│  must never overflow
        │░░░░░░░░░░│
        └────┬─────┘
             ▼ drains at maxrate (2 Mbit/s) = what the network link carries
```

The generator computes `bufsize = 2 × bitrate` and keeps the unit: `2M → 4M`, `1.5M → 3M`, `750k → 1500k`.
It uses `Decimal`, not float, so `1.5 × 2` prints exactly `3`.

`maxrate = b:v` makes the stream behave close to a CBR camera (constant bitrate). That is predictable
for the network and the NVR's disk budget (Session 10).

### 4.5 GOP: `-g 30 -keyint_min 30 -sc_threshold 0`

A **GOP** (group of pictures) starts with an **IDR keyframe**, a self-contained picture. Every P-frame
after it depends on the frames before it. A decoder can only *start* at an IDR.

```
 cam-gate, 15 fps, -g 30:
 time   0 s                     2 s                     4 s
        I P P P … P P P P P P P I P P P … P P P P P P P I …
        └──────── 30 frames ────┘└──────── 30 frames ────┘
```

- **`-g 30`**: at most 30 frames between keyframes.
- **`-keyint_min 30`**: at least 30. Together with `-g` this makes the interval **exactly** 30.
- **`-sc_threshold 0`**: no extra keyframe on scene cuts. x264 normally inserts an IDR when the
  picture changes abruptly (a truck enters the frame), which would make the GOP irregular.

Why a fixed 2 s matters downstream:

```
 Shinobi connects at t = 2.7 s:
   … P P P P P [I] P P P …    waits for the next I at t = 4.0 s → at most 2 s black screen
 Recording cut into segments:
   segment boundaries can only fall on I-frames → with a fixed GOP every segment is a whole
   number of 2 s GOPs, so segment lengths are predictable
```

### 4.6 Output: `-f rtsp -rtsp_transport tcp rtsp://mediamtx:8554/cam-gate`

ffmpeg acts as an RTSP **publisher**. It opens one TCP connection to MediaMTX and talks RTSP (text,
HTTP-like) to set up the session. Then it sends the video as RTP packets **interleaved** on the same TCP
connection:

```
 ffmpeg (cam-gate)                                MediaMTX :8554
   │── OPTIONS ──────────────────────────────────────►│
   │── ANNOUNCE  (SDP: "1 video track, H264") ───────►│  path cam-gate created
   │── SETUP     (transport: TCP interleaved 0-1) ───►│
   │── RECORD ───────────────────────────────────────►│  "is publishing to path 'cam-gate'"
   │══ $ ch0 len [RTP: H.264 NAL fragment] ══════════►│
   │══ $ ch0 len [RTP …] ════════════════════════════►│  ← forever, same TCP socket
```

`mediamtx` resolves because Docker Compose puts all services on one network (`shinobi_default`) with a
built-in DNS server: every **service name** is a hostname.

MediaMTX logs from the start-up (real):

```
INF [path cam-quay] stream is available and online, 1 track (H264)
INF [RTSP] [session 320f2669] is publishing to path 'cam-quay'
INF [path cam-quay] RTP packets are too big (1460 > 1440), remuxing them into smaller ones
INF [RTSP] [session e30adf85] is reading from path 'cam-gate', with TCP, 1 track (H264)
```

The "too big" line is harmless. ffmpeg's RTP packets go up to 1460 bytes. MediaMTX caps outgoing RTP
packets at 1440 bytes, so they still fit in a 1500-byte Ethernet frame for readers that use UDP, and
it re-splits the H.264 data into smaller fragments. The "reading" line is streamprobe connecting as a
reader.

---

## 5. Two compose files merged

```
 docker compose -f docker-compose.yml -f compose/cameras.generated.yml up -d
                   └── base: mediamtx ──┘ └── generated: 4 cameras ───┘
                                 merged into one project "shinobi"
```

- **Project directory** = directory of the first `-f` file = repo root. Relative paths in *all* files
  resolve against it. That is why the generated file says `./media`, not `../media`:
  ```
  $ docker inspect shinobi-cam-gate-1 --format '{{range .Mounts}}{{.Source}}->{{.Destination}}{{end}}'
  /home/abuahmad/git/shinobi/media->/clips      (read-only)
  ```
- **Container names** are `<project>-<service>-<n>`, e.g. `shinobi-cam-gate-1`. There is deliberately no
  `container_name:`. It would block `docker compose up --scale` and collide across projects.
- **One image, four containers.** Every service has `image: shinobi-sim-camera:local` + `build: ./sim/camera`,
  so Compose builds once and tags the result. The cameras differ only in their `command`.
- **Labels** let tools find cameras without knowing their names:
  ```
  $ docker ps --filter label=port.sim.role --format '{{.Names}}  role={{.Label "port.sim.role"}}'
  shinobi-cam-gate-1  role=gate
  shinobi-cam-waterway-1  role=waterway
  shinobi-cam-quay-1  role=quay
  shinobi-cam-yard-1  role=yard
  ```

---

## 6. Inside one container: who runs what

```
$ docker top shinobi-cam-gate-1 -o pid,ppid,args
PID     PPID    COMMAND
260041  260017  /sbin/docker-init -- /usr/local/bin/publish.sh ffmpeg -hide_banner …
260094  260041  bash /usr/local/bin/publish.sh ffmpeg -hide_banner …
260096  260094  ffmpeg -hide_banner -loglevel warning -re -stream_loop -1 -i /clips/gate_santos_port.mp4 …
```

```
 PID 1 in the container
 ┌──────────────────────────────┐
 │ docker-init (tini)           │  init: true → reaps zombies, forwards signals
 │  └─ publish.sh  (bash)       │  ENTRYPOINT of the image; supervisor
 │       └─ ffmpeg …            │  the compose `command:` list, passed as "$@"
 └──────────────────────────────┘
```

**Exec form.** `command:` is a YAML **list**. Docker appends it to the ENTRYPOINT as an argv array: no
shell parses it, nothing is re-split, nothing needs quoting. `scale=1280:720,fps=15,format=yuv420p` arrives
as exactly one argument. With a single command *string*, the string would have to be split into words
first, and one stray quote or space would change the command.

**`publish.sh` has two modes:**

```
 arguments given? ──yes──► command mode: cmd = "$@"                 (the generated fleet)
        │
        no
        ▼
 env mode: build cmd from CAM_NAME, INPUT, MODE, FPS, BITRATE       (Session 4 style)
```

In both modes the same loop runs the command, waits, logs the exit code, sleeps 2 s, and starts again.

### Experiment: kill ffmpeg

```
$ docker exec shinobi-cam-gate-1 sh -c 'kill -KILL $(pidof ffmpeg)'
cam-gate-1 | publish.sh: line 88:  9 Killed   "${cmd[@]}" < /dev/null
cam-gate-1 | 2026-10-08T16:20:20Z [cam-gate] command exited with code 137, restarting in 2 s
mediamtx-1 | 2026/10/08 16:20:23 INF [RTSP] [session b83325d0] is publishing to path 'cam-gate'
```

Exit code 137 = 128 + 9 (SIGKILL). About 3 s later the camera is publishing again, and the container
never restarted. Two layers of recovery:

```
 ffmpeg dies ─────────────► publish.sh restarts it after 2 s      (fast, container stays up)
 publish.sh itself dies ──► Docker restart: unless-stopped        (slower, backoff grows)
```

### Experiment: `docker stop`

```
$ time docker stop shinobi-cam-yard-1
real    0m0.262s
cam-yard-1 | 2026-10-08T16:20:24Z [cam-yard] signal received, stopping
```

```
 docker stop ──SIGTERM──► tini (PID 1) ──forwards──► publish.sh
                                                      trap shutdown:
                                                        kill -TERM ffmpeg ──► ffmpeg closes RTSP cleanly
                                                        wait, exit 0
 Docker waits up to 10 s, then SIGKILL. Here it took 0.26 s, so no SIGKILL was needed.
```

What each piece prevents:
- **Without `init`**, bash would be PID 1. The kernel does not apply default signal actions to PID 1, so
  SIGTERM only works because of the trap. Nothing would reap zombie processes either.
- **Without the trap**, bash would die on SIGTERM and leave ffmpeg to be SIGKILLed when the container
  ends, with no clean RTSP teardown. Without both, `docker compose down` hangs 10 s per camera and
  ends in a SIGKILL.

---

## 7. Measured cost

```
$ docker stats --no-stream
NAME                     CPU %     MEM USAGE
shinobi-cam-gate-1       66.60%    130.4MiB      1280×720 15 fps 2M
shinobi-cam-quay-1       96.02%    130.7MiB      1280×720 15 fps 4M
shinobi-cam-waterway-1   80.01%    132.6MiB      1280×720 10 fps 3M
shinobi-cam-yard-1       55.06%    127.8MiB      1280×720 10 fps 1.5M
shinobi-mediamtx-1        4.86%     24.8MiB      just forwards packets
```

100 % = one CPU core. The four cameras use about 3 of the machine's 14 cores, and nearly all of it is
decoding plus x264 encoding. MediaMTX only copies packets, so it is nearly free. Keep this number in
mind for Session 34 (16 cameras ≈ 12 cores just for simulation). The alternative is `MODE=copy`
(no encoder, a few % per camera), which loses per-camera fps and bitrate.

**Why encode twice?** `normalize.sh` (offline, once) makes the *file* clean: CFR 25 fps, fixed GOP,
no B-frames. The live encode makes each *stream* look like the camera described in the YAML.
The clean file is what keeps the live loop seam stall-free.

---

## 8. Adding and removing cameras: `sim-up.sh` / `sim-down.sh`

```
 scripts/sim-up.sh
   1. uv run python sim/gen_compose.py          YAML → generated file (fails on invalid config)
   2. docker compose -f … -f … up -d --build --remove-orphans
                                    │        └─ delete containers whose service no longer exists
                                    └─ rebuild image if sim/camera/ changed (cached otherwise)
```

Removing `cam-yard` from the YAML (real generator output):

```
$ uv run python sim/gen_compose.py --config three.yaml --out three.yml
gen_compose: wrote three.yml (3 cameras)
  cam-gate:
  cam-quay:
  cam-waterway:
```

```
 before:  cam-gate  cam-quay  cam-yard  cam-waterway     running
 YAML:    cam-gate  cam-quay            cam-waterway
 up --remove-orphans:
          cam-gate  cam-quay  [removed]  cam-waterway    cam-yard was an "orphan": a container of
                                                         this project with no service in any -f file
```

Without `--remove-orphans`, `cam-yard` would keep running and publishing, invisible to the config.

`sim-down.sh` runs `down --remove-orphans` with the same files: it stops and removes all containers
and the network.

---

## 9. Keeping the generated file honest: `--check` and the golden test

The generated file is committed, which makes it reviewable in diffs. The risk is someone editing
`cameras.yaml` and forgetting to regenerate. Two guards:

```
 --check:      generate in memory ─► compare with file on disk ─► equal? exit 0
                                                                  differ? print diff, exit 1
 golden test:  tests/test_gen_compose.py::test_committed_file_matches_generator
               (runs in every `uv run pytest`, no media/ needed)
```

Real `--check` output after changing `gop: 20 → 25` for cam-yard without regenerating:

```
gen_compose: warning: cam-yard: gop 25 != 2 x fps (20); convention is a 2 s GOP
--- compose/cameras.generated.yml (committed)
+++ compose/cameras.generated.yml (generated)
@@ -141,9 +141,9 @@
     - -g
-    - '20'
-    - -keyint_min
-    - '20'
+    - '25'
+    - -keyint_min
+    - '25'
```

---

## 10. Verify it yourself

```bash
scripts/sim-up.sh                                              # generate + start everything
docker compose -f docker-compose.yml -f compose/cameras.generated.yml ps
ffplay -rtsp_transport tcp rtsp://localhost:8554/cam-quay      # watch one camera
for c in cam-gate cam-quay cam-yard cam-waterway; do
  uv run python -m tools.streamprobe rtsp://localhost:8554/$c --seconds 10
done                                                           # expect fixed GOP 2 s, no B-frames
docker exec shinobi-cam-gate-1 sh -c 'kill -KILL $(pidof ffmpeg)'   # watch it come back:
docker compose -f docker-compose.yml -f compose/cameras.generated.yml logs -f cam-gate
uv run python sim/gen_compose.py --check; echo $?              # 0 = generated file up to date
uv run pytest tests/test_gen_compose.py -v
scripts/sim-down.sh
```

streamprobe result for `cam-gate` (real):

```
stream:     h264 Main 1280x720 yuv420p @ 15 fps
keyframes:  5
GOP frames: min 30 / max 30 / mean 30
GOP secs:   min 2 / max 2 / mean 2
fixed GOP:  yes
B-frames:   no
```

## Glossary

| Term | Meaning |
|---|---|
| IDR | Instantaneous Decoder Refresh: a keyframe after which no frame refers back past it; decoding can start here. |
| GOP | Group of pictures: an IDR plus the frames that depend on it, up to the next IDR. |
| B-frame / P-frame | Predicted from past and future frames / from past frames only. |
| PTS / DTS | Presentation (display) / decoding timestamp. Equal order when there are no B-frames. |
| CFR / VFR | Constant / variable frame rate. |
| VBV | Video Buffering Verifier: the leaky-bucket model behind `-maxrate`/`-bufsize`. |
| yuv420p | Planar Y'CbCr with chroma subsampled 2×2. Required by the H.264 Main profile. |
| RTSP / RTP | Control protocol (setup, play, record) / packet format that carries the media. |
| Interleaved TCP | RTP sent inside the RTSP TCP connection, framed by `$ <channel> <length>`. |
| Orphan container | A container of a compose project whose service is no longer in any compose file. |
| Exec form | Command given as a list of arguments, run without a shell. |
