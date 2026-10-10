# Session 4 — First RTSP camera, explained

One clip, looped forever by FFmpeg, published to an RTSP server, playable by any client. This is the
foundation every later session builds on. Also covers camera profile v1 (`normalize.sh` and
`probe_clip.sh`), which was added during this session. All outputs are real.

Commit: `551cb87 feat(sim): stream first simulated camera via MediaMTX`.
Session 5 later replaced the hand-written service with a generated fleet; see
[05-camera-fleet.md](05-camera-fleet.md).

---

## 1. The pieces

```
 host: ./media/gate_santos_port.mp4
        │ bind mount, read-only
        ▼
 ┌──────────────────────────────┐                ┌─────────────────────────────┐
 │ cam-gate container           │  RTSP publish  │ mediamtx container          │
 │ debian + ffmpeg + publish.sh │ ─────────────► │ RTSP server :8554           │
 │ /footage/gate_santos_port.mp4│  TCP, path     │ path "cam-gate"             │
 └──────────────────────────────┘  /cam-gate     └──────────────┬──────────────┘
                                                                │ port publish
                                                                │ 127.0.0.1:8554
                                                                ▼
                                    host: ffplay rtsp://localhost:8554/cam-gate
                                    later: Shinobi pulls rtsp://mediamtx:8554/cam-gate
```

**Why an RTSP server in the middle?** A real IP camera *is* an RTSP server: Shinobi connects to it and
pulls. FFmpeg can't easily act as a server for several clients. So FFmpeg *pushes* (publishes) into
MediaMTX, and MediaMTX serves the stream to any number of readers, like a camera would:

```
                        ┌──► ffplay (you)
 ffmpeg ──publish──► MediaMTX ──► streamprobe / check_stream.sh
   1 connection         └──► Shinobi (Session 9)        one stream in, N readers out
```

---

## 2. MediaMTX configuration, line by line

`sim/mediamtx/mediamtx.yml`, mounted read-only at `/mediamtx.yml` (the image is `FROM scratch`, so
the binary and its config sit at `/`):

```yaml
logLevel: info
rtsp: true
rtspTransports: [tcp]     # only TCP-interleaved: no UDP port ranges to open or NAT
rtspAddress: :8554
rtmp: false               # MediaMTX also speaks RTMP, HLS, WebRTC, SRT, MoQ;
hls: false                # each opens ports and threads we don't need,
webrtc: false             # so all of them are off
srt: false
moq: false
paths:
  all_others:             # accept ANY path name: rtsp://mediamtx:8554/<anything>
```

`all_others` is why new cameras need no MediaMTX change. The default auth lets anyone publish and
read, which is fine on a local Docker network but not for production.

In compose:

```yaml
mediamtx:
  image: bluenviron/mediamtx:${MTX_TAG:?set MTX_TAG in .env}   # pinned version, from .env
  ports:
    - "127.0.0.1:8554:8554/tcp"     # host 127.0.0.1 only: not reachable from your LAN
```

```
 "127.0.0.1:8554:8554"            "8554:8554"
 host loopback only ✓             all host interfaces (0.0.0.0): anyone on the
                                  network could read the cameras
```

---

## 3. The publisher: `publish.sh` in env mode

Session 4's container was configured only through environment variables:

```
 CAM_NAME=cam-gate  INPUT=/footage/gate_santos_port.mp4  MODE=copy|encode  FPS=25  BITRATE=2M
          │
          ▼  publish.sh builds the command
 ffmpeg -hide_banner -loglevel warning -nostdin
        -re -stream_loop -1 -i "$INPUT" -map 0:v:0 -an
        ├─ MODE=copy:   -c:v copy
        └─ MODE=encode: -c:v libx264 -preset veryfast -tune zerolatency -profile:v main
                        -pix_fmt yuv420p -r $FPS -g $((FPS*2)) -keyint_min … -sc_threshold 0 -bf 0
                        -b:v $BITRATE -maxrate $BITRATE -bufsize 2×$BITRATE
        -f rtsp -rtsp_transport tcp rtsp://mediamtx:8554/$CAM_NAME
          │
          ▼  supervised: restart 2 s after exit, SIGTERM forwarded (details: Session 5 notes §6)
```

It fails fast on bad config: a missing `INPUT` prints "is the footage mounted? run
./scripts/fetch_media.sh", and a non-numeric `FPS` or `BITRATE` stops it before ffmpeg starts.

### copy vs encode

```
 MODE=copy                                   MODE=encode
 file ─► demux ─► [H.264 packets] ─► RTSP    file ─► demux ─► DECODE ─► frames ─► ENCODE ─► RTSP
          no decoding, bytes forwarded                full decode + x264 every frame
```

Measured side by side on 2026-10-09, same clip (`gate_santos_port.mp4`, normalized):

```
NAME                  CPU %    MEM
copytest (MODE=copy)   1.01%   13.8 MiB    ← 25 fps passthrough
cam-gate (encode)     60.35%  125.6 MiB    ← 15 fps, 1280×720, x264 veryfast
```

That's 60× the CPU. Copy is how real NVRs record (Session 10), but it only works if the file
**already** has the exact stream properties you want. That's why camera profile v1 exists (section 5).

---

## 4. The loop seam: where simulated cameras break

`-stream_loop -1` makes the demuxer jump back to the file's start at EOF and add the clip duration to
all timestamps:

```
 file:        [0 ────────── 54.9 s]           [0 ────────── 54.9 s]
 timestamps:   0 ────────── 54.9 s  │  54.9 ────────── 109.8 s  │ …
                                    ▲ seam: must continue smoothly, no jump, no going back
```

In theory that's seamless. In practice the seam breaks when:

| Cause | Symptom at every loop | Fix |
|---|---|---|
| audio and video durations differ | offset taken from the longer stream → video jumps forward → stall | `-an` |
| B-frames | reordering at EOF/BOF → "Non-monotonous DTS", packets dropped | `-bf 0` in the source |
| MP4 edit list (`elst`) with start offset | timestamp offset error at each wrap | first PTS must be 0 |
| clip doesn't start on an IDR | readers see garbage until the next keyframe | first frame = IDR |
| VFR (variable frame rate) footage | `-re` pacing becomes jittery | CFR (constant) 25 fps |

Our raw clips had several of these problems (Session 3 notes, §7), which is why they're normalized first.

---

## 5. Camera profile v1: make the file loop-safe once, offline

```
 media/raw/<clip>  (pristine download, pinned hash)
        │ sim/scripts/normalize.sh RAW_IN OUT      (once per clip, ~1 min)
        ▼
 media/<clip>.mp4  H.264 Main · yuv420p · ≤1280 wide · 25 fps CFR · GOP 50 fixed ·
                   no B-frames · no audio · first PTS 0 · starts with IDR
        │ sim/scripts/probe_clip.sh FILE           (11 PASS/FAIL checks)
        ▼
 safe to loop, even with -c copy
```

The full spec and the reason for each property are in [../camera-profile.md](../camera-profile.md).

**normalize.sh safety rules:**

```
 RAW_IN inside media/ but not media/raw/?  → refuse   (don't derive from derived clips)
 OUT is RAW_IN (same path, symlink, hard link)? → refuse (never destroy the pristine copy)
 encode → OUT.part → mv OUT                            (no truncated MP4 on Ctrl-C)
```

**probe_clip.sh: how each check is measured:**

```
 ffprobe stream=…          → codec, profile, pix_fmt, even dims, width ≤ 1280, fps, has_b_frames
 ffprobe -select_streams a → no audio
 ffprobe packet=pts_time   → first PTS == 0
 ffmpeg -bsf:v trace_headers -frames:v 1
                           → first VCL NAL (types 1–5) is 5 = IDR
                             (not "first NAL": in MP4, SPS/PPS sit in avcC and x264
                              puts an SEI (6) before the first slice)
 ffprobe packet=flags      → K exactly on packets 0, 50, 100, …
```

Results after normalizing all four clips (all 11 checks passed for each):

| Clip | Before | After | Size |
|---|---|---|---|
| gate | High, 1080p59.94, B-frames, GOP 4.17 s | Main, 720p25, GOP 2 s | 25.3 → 7.9 MB |
| quay | High, 1080p25, B-frames, GOP 3.04 s | Main, 720p25, GOP 2 s | 5.7 → 1.8 MB |
| yard | VP8, 1080p25, GOP 5.12 s | Main, 720p25, GOP 2 s | 99.4 → 31.4 MB |
| waterway | VP8, 720p23.976, GOP 5.34 s | Main, 720p25, GOP 2 s | 99.8 → 49.2 MB |

A copy-mode publisher of the normalized gate clip, probed live:

```
$ uv run python -m tools.streamprobe rtsp://localhost:8554/cam-copytest --seconds 6
stream:     h264 Main 1280x720 yuv420p @ 25 fps
GOP frames: min 50 / max 50 / mean 50
GOP secs:   min 2 / max 2 / mean 2
fixed GOP:  yes
B-frames:   no
```

---

## 6. Proving the seam works: `sim/scripts/check_stream.sh`

```
 check_stream.sh URL CLIP
   duration = ffprobe(CLIP)                       e.g. 9.36 s
   window   = 2.5 × duration                      → crosses ≥ 2 loop boundaries
        │
        ▼
 ffprobe -rtsp_transport tcp -read_intervals %+window -show_entries packet=dts_time URL
        │ one DTS per video packet
        ▼
 awk:  DTS must strictly increase              (else: "non-increasing DTS at packet N")
       largest gap between packets ≤ 0.5 s     (else: a stall at the seam)
       covered span ≥ 90 % of the window       (else: packets were lost or not parsed)
 timeout window+20 s around ffprobe            (fires = stream stalled completely)
```

Real run against `cam-quay` (9.4 s clip, so the 23 s window crosses two seams):

```
$ sim/scripts/check_stream.sh rtsp://localhost:8554/cam-quay media/quay_sts_truck_loading.mp4
clip duration 9.360000s, sampling rtsp://localhost:8554/cam-quay for 23s
packets: 345
DTS span: 22.933 s
non-increasing DTS: 0
max DTS gap: 0.067 s (after DTS 16.200)      ← 0.067 s = one frame at 15 fps: no stall
OK
```

The original Session 4 run (copy mode, gate clip): 8207 packets over 136.9 s, 2 loop boundaries,
0 non-increasing DTS, max gap 33 ms.

### Two gotchas found while building it

```
 1. Killing ffprobe loses output
    ffprobe … | awk     ffprobe's stdout is a pipe → block-buffered (4–64 KB)
    timeout kills it →  the last buffer is never flushed → packets "missing"
    fix: -read_intervals %+N, so ffprobe stops itself, flushes, exits 0;
         timeout is only a watchdog for real stalls

 2. Trailing comma in CSV
    0.040000            ← normal packet
    0.000000,           ← keyframe with side data (SPS/PPS): ffprobe appends an empty field
    fix: awk -F, and use $1
```

---

## 7. Compose details from this session

```yaml
cam-gate:
  build: ./sim/camera              # Dockerfile: debian:bookworm-slim + apt ffmpeg + publish.sh
  volumes:
    - ./media:/footage:ro          # :ro: a bug in the container can't damage footage
  environment: {CAM_NAME: cam-gate, INPUT: /footage/gate_santos_port.mp4}
  depends_on: [mediamtx]
  init: true                       # tini as PID 1 (see Session 5 notes §6)
  restart: unless-stopped
```

**`depends_on` only orders startup. It doesn't wait for readiness.** Compose starts `mediamtx`
first, but `cam-gate` *can* start before MediaMTX listens on 8554. It hasn't happened in our logs so
far (MediaMTX boots in milliseconds), but nothing prevents it:

```
 possible sequence:
 t=0.0  mediamtx container started (process booting)
 t=0.1  cam-gate started → ffmpeg connects → "Connection refused" → exits
 t=2.1  publish.sh restarts ffmpeg → MediaMTX ready now → publishing ✓
```

The 2 s retry loop absorbs this. Session 32 adds healthchecks with `condition: service_healthy`.

**Why Debian's ffmpeg and not a slim ffmpeg image?** One `apt install`, a known version (5.1 in
bookworm), and `bash` for `publish.sh`. Size doesn't matter for a simulator.

---

## 8. Verify it yourself

```bash
cp .env.example .env                                    # once: MTX_TAG
scripts/sim-up.sh                                       # (since Session 5) mediamtx + cameras
ffplay -rtsp_transport tcp rtsp://localhost:8554/cam-gate
sim/scripts/check_stream.sh rtsp://localhost:8554/cam-quay media/quay_sts_truck_loading.mp4
sim/scripts/probe_clip.sh media/gate_santos_port.mp4    # profile v1: 11 × PASS
docker compose -f docker-compose.yml -f compose/cameras.generated.yml logs mediamtx | grep publishing
docker stats --no-stream                                # CPU per camera
```

## Glossary

| Term | Meaning |
|---|---|
| Publish / read (MediaMTX) | Push a stream into a path / pull a stream out of it. |
| Path | Stream name in the URL, e.g. `/cam-gate`. |
| Stream copy (`-c copy`) | Forward compressed packets without decoding. |
| Loop seam | The point where `-stream_loop` jumps from the file's end back to its start. |
| Edit list (`elst`) | MP4 box that shifts or trims the timeline; causes offsets at loop seams. |
| CFR / VFR | Constant / variable frame rate. |
| VCL NAL | Video Coding Layer NAL unit (slice types 1–5), the actual picture data. |
| Bind mount | Host directory made visible inside a container (`./media:/footage:ro`). |
