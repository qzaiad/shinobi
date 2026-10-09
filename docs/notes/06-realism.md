# Session 6 — Realism: OSD clock and sub streams

How each simulated camera became more like a real IP camera. It now burns a UTC clock and its id into
the picture (OSD), and it publishes a second, smaller **sub stream** next to the main stream, from one
ffmpeg process. All outputs below are real, captured from this repo on 2026-10-09.

```
 sim/cameras.yaml (version 2)                       MediaMTX :8554
   defaults.sub: 640x360 @10 fps, 384k                  │
   defaults.osd: true                                   │
        │ gen_compose.py                                │
        ▼                                               │
 one container per camera ── ffmpeg ─┬─ main 1280x720 ─▶ /cam-gate      ─▶ Shinobi: record   (S10)
                                     └─ sub   640x360 ─▶ /cam-gate-sub  ─▶ Shinobi: detect, live grid
```

---

## 1. What a real IP camera's encoder page looks like

A real camera has a hardware H.264/H.265 encoder (an ASIC, a fixed-function chip) and a web settings
page with these knobs. Every column on the right is already, or now becomes, a YAML field:

| Camera UI setting | Meaning | Typical CCTV value | Our setting |
|---|---|---|---|
| Video encoding | Codec + profile | H.264 Main (H.265 on newer models) | `-c:v libx264 -profile:v main` |
| Resolution | Pixel size per stream | main 1080p / 4 MP, sub 640×360, D1 (704×576) or CIF (352×288) | `width`/`height`, `sub.width`/`sub.height` |
| Frame rate | Frames per second | 10–15 fps (not 25–30: storage costs money) | `fps`, `sub.fps` |
| I-frame interval | Keyframe distance, entered **in frames** | 2 × fps | `gop`, `sub.gop` (default 2 × `sub.fps`) |
| Bitrate type | **CBR** (constant bitrate) or **VBR** (variable, with a cap) | VBR with max | `-b:v = -maxrate`, `-bufsize = 2×` |
| Max bitrate | Cap for VBR | 2–4 Mb/s main, 256–512 kb/s sub | `bitrate`, `sub.bitrate` |
| B-frames | Usually not even offered | off | `-bf 0` |
| OSD | On-screen display: text burned into the pixels | date/time + camera name | `osd: true` → `drawtext` |

Why the I-frame interval matters to an NVR (network video recorder: the box that records the streams,
here Shinobi). A recording segment can only start on a keyframe (IDR, a frame that decodes without
reference to earlier frames), and a viewer joining live waits for the next one:

```
 frames  I P P P … P I P P P … P I            I = IDR keyframe, P = predicted frame
         │◄── 2 s ──►│◄── 2 s ──►│
         ▲           ▲           ▲
   segment cut / join points: never more than 2 s apart
```

---

## 2. Main stream vs sub stream: why an NVR wants both

A real camera has **one sensor and two encoders**. The image pipeline (ISP, image signal processor)
produces frames once and hands each frame to both encoders:

```
                         ┌──▶ encoder 1  1280x720 @15 fps  2 Mb/s   GOP 30 ──▶ rtsp://…/cam-gate      MAIN
 sensor ─▶ ISP ─▶ OSD ───┤
                         └──▶ encoder 2   640x360 @10 fps  384 kb/s GOP 20 ──▶ rtsp://…/cam-gate-sub  SUB
```

The NVR uses each stream for a different job:

```
                         ┌────────────────────────── Shinobi ───────────────────────────┐
 MAIN (2 Mb/s) ──RTSP──▶ │ record: -c copy → MP4 segments     no decode, a few % CPU   │ evidence quality
 SUB (384 kb/s) ─RTSP──▶ │ decode → motion / object detector  decode = the CPU cost    │
                         │ decode → live grid (16 tiles)                               │
                         └─────────────────────────────────────────────────────────────┘
```

- **Recording copies packets** (`-c copy`). It never decodes, so the resolution barely matters for CPU.
  Only disk space grows with bitrate.
- **Detection and the live grid must decode** every frame they look at. Decoding cost scales with
  pixels × fps: 1280×720×15 = 13.8 Mpx/s vs 640×360×10 = 2.3 Mpx/s, **6× less** for the sub stream.
  Detectors also downscale to their input size (YOLO: 640 px) anyway, so the extra main-stream
  pixels would be thrown away.

**What breaks without a sub stream:** the NVR decodes the main stream of every camera for detection.
With N cameras the decoder falls behind real time. Frames are dropped, detections arrive late or for
the wrong frame, and the live grid stutters. Recording keeps working, which makes the problem easy to
miss until Session 14 (motion) or 15 (objects).

---

## 3. One ffmpeg, two outputs: the filter graph

`-vf` (Session 5) is a single chain: one input, one output. Two encoders need a **filter graph**
(`-filter_complex`): named pads (`[label]`) connect filters, and `split` duplicates a stream of frames.
This is the real generated graph for `cam-gate`, wrapped for reading:

```
[0:v]split=2[m][s];
[m]fps=15,scale=1280:720,format=yuv420p,drawtext=…clock…,drawtext=…id…[main];
[s]fps=10,scale=640:360,format=yuv420p,drawtext=…clock…,drawtext=…id…[sub]
```

```
                 decode once                 main branch
 clip.mp4 ─▶ h264 ─▶ [0:v] ─▶ split ─[m]─▶ fps=15 ─▶ scale 1280x720 ─▶ yuv420p ─▶ OSD ─[main]─▶ x264 ─▶ RTSP /cam-gate
 (25 fps)                          │
                                   └─[s]─▶ fps=10 ─▶ scale 640x360 ──▶ yuv420p ─▶ OSD ─[sub]──▶ x264 ─▶ RTSP /cam-gate-sub
                                           sub branch
```

Each output then gets its own options, in order: everything after one `-map` up to its URL belongs
to that output:

```
-map [main] -c:v libx264 … -b:v 2M   -g 30 … -f rtsp -rtsp_transport tcp rtsp://mediamtx:8554/cam-gate
-map [sub]  -c:v libx264 … -b:v 384k -g 20 … -f rtsp -rtsp_transport tcp rtsp://mediamtx:8554/cam-gate-sub
```

Design choices:

- **`fps` before `scale`:** dropping frames first means fewer frames to scale.
- **`fps=10` on the sub branch** takes 10 of the 25 source frames per second. The `fps` filter picks
  the frame closest to each output timestamp, so motion stays even.
- **One process instead of a second container:**
  - Both streams show the same source frame at the same instant, like one sensor.
  - The clip is decoded once.
  - Main and sub fail together. If ffmpeg dies, both RTSP paths drop and `publish.sh` restarts both,
    which is what a camera reboot looks like (Session 7 fault injection).
- **The GOPs are independent:** main has an IDR every 30 frames and sub every 20, both 2 s. Their
  keyframes are not aligned in time, and nothing needs them to be, because Shinobi records and
  detects on separate connections.
- **`sub: null`** disables the sub stream for one camera. The graph then has no `split`: it is a single
  chain `[0:v]fps=…,scale=…[main]` with one output.

### How the YAML resolves the sub stream

```
   defaults.sub              camera.sub              effective sub
 ┌──────────────────┐   ┌─────────────────┐   ┌─────────────────────────┐
 │ width:   640     │   │                 │   │ width:   640            │
 │ height:  360     │ + │ (absent)        │ = │ height:  360            │  every camera today
 │ fps:     10      │   │                 │   │ fps:     10             │
 │ bitrate: 384k    │   │                 │   │ bitrate: 384k           │
 │                  │   │                 │   │ gop:     20  ← 2 × fps  │
 └──────────────────┘   └─────────────────┘   └─────────────────────────┘
                        │ fps: 5          │ → fps 5, gop 10, rest from defaults  (field-wise merge)
                        │ sub: null       │ → no sub stream
```

New validation rules (all with the camera id in the message):

| Rule | Why / what breaks otherwise |
|---|---|
| sub `width`/`height` even | Same 4:2:0 chroma rule as the main stream. |
| sub not larger than main | The sub is derived from the same frames. Upscaling adds bits but no detail. |
| sub fps ≤ main fps | Same reason. `fps=20` on a 15 fps camera would duplicate frames. |
| sub gop ≠ 2 × sub fps → **warning** | Same 2 s convention as main. |
| partial `sub:` with no `defaults.sub` → error | E.g. `cam-quay: sub is missing width, height, bitrate`. |
| `osd` must be a real YAML bool | `StrictBool`: pydantic's default would accept the string `"yes"` silently. |
| `version: 1` rejected | The meaning of the file changed (v1 cameras never had sub streams). |

---

## 4. The OSD: a clock burned into the pixels

`drawtext` renders text with FreeType onto every frame. Each camera gets two text items, like most IP
camera OSDs (date/time top-left, channel name bottom-right):

```
 ┌──────────────────────────────────────────────┐
 │▓2026-10-09 12:28:32 UTC▓                     │   %{gmtime\:%Y-%m-%d %T} UTC, x=8, y=8
 │                                              │
 │                    (scene)                   │
 │                                              │
 │                                   ▓cam-yard▓ │   camera id, x=w-tw-8, y=h-th-8
 └──────────────────────────────────────────────┘   ▓ = box=1:boxcolor=black@0.5 (readable on sky)
```

- **`%{gmtime\:FMT}`** is a drawtext expansion, evaluated **per frame, when the frame passes through
  the filter**, using the container's clock (shared with the host, so no NTP is needed in the
  container). We use UTC on purpose: all cameras and all later services (MQTT events, twin) compare
  times without time-zone conversion.
- **Font size = height / 24:** 30 px on the main stream, 15 px on the sub. Each branch draws its own
  OSD. Drawing once before `split` and then scaling would also work, but the text would come out
  blurry from scaling.
- **Font:** DejaVu Sans Mono, explicitly installed in `sim/camera/Dockerfile` (`fonts-dejavu-core`).
  It was already there as an indirect dependency of the Debian ffmpeg package, but depending on that
  by accident would break the day the dependency changes. A monospace font keeps the clock from
  jittering sideways as digits change.
  - Problem: digits have different widths -> A monospace font gives every character the same width. The digit 1 occupies the same horizontal space as 8.

### Why the OSD clock is not the same as PTS

```
 PTS (presentation timestamp in the RTP/RTSP stream)    OSD clock (pixels)
 ──────────────────────────────────────────────────     ──────────────────────────────
 counts from 0 when the stream (or reader) starts       absolute UTC wall-clock time
 lost/rebased on reconnect, remux, export               survives recording, export, screenshots
 machines use it for sync and pacing                    humans (and OCR) use it as evidence
```

A real camera sets its OSD from NTP-synced time. When an operator exports a clip of a truck at the
gate, the burned-in time is what goes into the incident report. Without it, a recording only knows
"12.4 s into segment 2026-10-09T12-28-00.mp4", which depends on the NVR clock and the segment naming.
How machines get absolute time *from the stream itself* (RTCP sender reports) is still the open
question from Session 2.

### Escaping: three parsers read this string

**The clock text is not read by just one parser. It passes through multiple layers of interpretation, and each layer gives certain characters a special meaning.** A colon :, for example, can separate FFmpeg options, but it can also be part of a time format. If FFmpeg mistakes a colon inside the clock text for an option separator, it may misinterpret the entire filter configuration and fail to start the video pipeline.

#### First, what is FFmpeg trying to do?

FFmpeg is a multimedia tool. In your camera setup, it can receive video, transform it, and send the result to another component.

The drawtext video filter draws text directly onto video frames. This is often called an OSD (on-screen display).
A simplified FFmpeg filter could look like this:

```drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf:fontsize=30:text='CAM-QUAY'```

This tells FFmpeg to use the `drawtext` *filter*, select a font, set its size, and draw the specified text.
Your clock is more complicated because its contents change dynamically. Instead of passing a fixed string, it asks FFmpeg to calculate a timestamp while rendering the video.

```%{gmtime\:%Y-%m-%d %T}```

Each part has a different job, and some characters have special meanings to different parsers.

#### What does the entire text expression mean?
Here is the exact expression again:
```%{gmtime\:%Y-%m-%d %T} UTC```

It is composed of several pieces:
```
The whole expression
%{gmtime\:%Y-%m-%d %T} UTC

%{ ... } — dynamic expansion
Tells FFmpeg to evaluate a special expression rather than display those characters literally.

gmtime — current UTC time
Asks FFmpeg for the time at which the filter is running, expressed in UTC.

\: — escaped colon
Separates the function name from its argument without allowing the colon to be mistaken for a delimiter.

%Y-%m-%d %T — date/time format
Specifies the desired layout: year, month, day, then time.

UTC — literal text
Appends the letters UTC to the formatted timestamp.
```

The output might look like:
```
2026-10-09 17:12:30 UTC
```

That is an illustrative timestamp, not a reading from your camera.

The %T directive is equivalent to %H:%M:%S in the strftime date/time formatting convention. 

#### Why are there three parsers?
A parser is a component that reads a string according to a set of rules and decides what its pieces mean.

**Parser 1: the filtergraph parser:** This parser understands the overall arrangement of filters.
```
scale=1280:720,drawtext=fontsize=30:text='CAM-QUAY'
```
Conceptually, the comma separates two filters: `scale` and `drawtext`

The filtergraph grammar gives special meaning to characters including:
- , — separates filters in a chain.
- ; — separates filter chains.
- [ and ] — delimit filter input/output labels.

**Parser 2: the filter-option parser:** The drawtext filter accepts options such as:

```drawtext=fontfile=...:fontsize=30:fontcolor=white:text=...```

The colon separates option assignments:

```
fontfile=...  -> Select the font file

fontsize=30   -> Set font size

text='...'    -> Supply the text to draw
```

Here is the key conflict. Your clock needs colons in its output: `17:12:30`. But FFmpeg uses colons to separate options: `fontsize=30:text=...`

**Parser 3: the drawtext expansion parser:** After the filter and its options have been parsed, drawtext evaluates the text expression.
In normal expansion mode, a pattern such as: `%{function:argument}` means "evaluate this function using this argument."

The function name is gmtime, and the argument is the date-format string %Y-%m-%d.


```
 drawtext=fontfile=…:fontsize=30:…:text='%{gmtime\:%Y-%m-%d %T} UTC'
 │                                     │                  │
 │ 1. filtergraph parser: , ; [ ] split filters/chains    │ ← '…' protects the text from 1 and 2
 │ 2. option parser: : separates key=value                │
 │ 3. drawtext expansion: %{name:arg}  →  \: needed       │
```

`%T` (= `%H:%M:%S`) keeps more colons out of the string. The generator passes the command in exec
form (a list, no shell), so there is no 4th level of shell quoting. Session 5 explains why that
matters.

**What breaks if the escaping is wrong:** without the quotes and `\:`, the option parser splits at
the first `:` inside the text. The leftover pieces become positional options of drawtext, and ffmpeg
6.1 fails with:

```
$ ffmpeg -f lavfi -i testsrc -vf "drawtext=fontfile=…:text=%{gmtime:%Y-%m-%d %H:%M:%S}" -f null -
[Parsed_drawtext_0 @ 0x…] Both text and text file provided. Please provide only one
```

Inside a camera container that means the container restarts every 2 s forever, and the RTSP path
never comes up. A unit test (`test_osd_clock_and_id_on_both_streams`) pins the exact string.

---

## 5. Snapshot: `sim/scripts/snapshot.sh`

Real cameras serve a JPEG at an HTTP URL (vendor-specific, e.g. `/snapshot.jpg` or `/ISAPI/…`). NVRs
use it for thumbnails, and some detectors poll it instead of decoding video. We simulate it from the
RTSP side:

```bash
sim/scripts/snapshot.sh rtsp://localhost:8554/cam-yard yard.jpg
```

```
before: 2026-10-09 12:28:31.235 UTC
after:  2026-10-09 12:28:35.081 UTC
wrote yard.jpg                                → OSD in the image: 2026-10-09 12:28:32 UTC  ✔ in window
```

Why it takes about 4 s: ffmpeg connects (RTSP `DESCRIBE`/`SETUP`/`PLAY`), probes the stream (inspecting incoming media to learn how to process it.), and can
only decode from the **next IDR**. With a 2 s GOP that wait is 0–2 s:

```
 t=31.2   connect + probe ──────────┐
                                    ▼
 stream   … P P P P [I] P P …   first decodable frame = next keyframe (≤ 2 s away)
                     │
                     └── decoded, OSD shows 12:28:32 → JPEG written, ffmpeg exits at 35.1
```

That is also the main reason real cameras serve snapshots from the encoder directly: an RTSP grab
costs a connection plus up to one GOP of latency. If Shinobi later needs a real HTTP snapshot URL
(Session 9), we will add one; until then the script is a verification tool.

---

## 6. Verification (real output)

### streamprobe: 8 streams, configured resolution, 2 s GOP, no B-frames

```bash
for c in gate quay yard waterway; do for s in "" -sub; do
  uv run python -m tools.streamprobe rtsp://localhost:8554/cam-$c$s --seconds 10
done; done
```

| Stream | Resolution | fps | GOP frames | GOP s | B-frames |
|---|---|---|---|---|---|
| cam-gate | 1280×720 | 15 | 30 / 30 / 30 | 2 | no |
| cam-gate-sub | 640×360 | 10 | 20 / 20 / 20 | 2 | no |
| cam-quay | 1280×720 | 15 | 30 / 30 / 30 | 2 | no |
| cam-quay-sub | 640×360 | 10 | 20 / 20 / 20 | 2 | no |
| cam-yard | 1280×720 | 10 | 20 / 20 / 20 | 2 | no |
| cam-yard-sub | 640×360 | 10 | 20 / 20 / 20 | 2 | no |
| cam-waterway | 1280×720 | 10 | 20 / 20 / 20 | 2* | no |
| cam-waterway-sub | 640×360 | 10 | 20 / 20 / 20 | 2 | no |

\* The first probe of `cam-waterway`, right after the fleet restarted, printed
`GOP secs: min n/a`. At least one frame came without a usable timestamp, which can happen in the
first moments of an RTSP session (see the Session 2 notes on missing PTS). Three probes a minute later
all showed `2 / 2 / 2`. Keep this in mind for Session 7 health checks: probe after a warm-up, or
retry once.

#### Why can this happen at the start of an RTSP session?
When a probe first connects to cam-waterway, several things happen:
1. FFmpeg establishes the RTSP session and starts receiving video.
2. It initializes the stream and gathers codec and timing information.
3. It receives frames and examines their timestamps.
4. The probe uses the available information to estimate the GOP duration.
During startup, the stream may not yet have supplied enough consistent timestamp information for the calculation. Depending on the camera, stream, and probing method, the first measurement can therefore be unavailable.
Here, n/a means the probe could not calculate a valid result—not necessarily that the stream has no GOP or that the camera is broken.
#### What do the results tell us?

First probe — immediately after restart
GOP secs: min n/a -> At least one frame lacked usable timing information for the probe's calculation, so the minimum GOP duration could not be determined reliably.

### OSD clock vs wall clock

| Snapshot | Host `before` | OSD | Host `after` |
|---|---|---|---|
| cam-yard (main) | 12:28:31.235 | 12:28:32 | 12:28:35.081 |
| cam-waterway-sub | 12:28:35.088 | 12:28:35 | 12:28:38.665 |

The OSD time always falls inside the window, so it matches wall-clock time to within a second. The
OSD is drawn when the frame is filtered, and `-re` + `-tune zerolatency` keep that within a frame or
two of when the frame is sent.

#### “The OSD is drawn when the frame is filtered”

FFmpeg processes video through a sequence of stages:
1. Receive the video frame: Frame arrives from the RTSP stream
2. Decode the frame: FFmpeg reconstructs the video image
3. Apply the drawtext filter: The OSD timestamp is rendered onto the image
4. Encode or write the snapshot: The resulting image is saved as a JPEG

The key point is that the timestamp is added during frame processing, rather than being added later by the snapshot script after the JPEG has been saved.
If drawtext uses FFmpeg's current-time expansion, such as %{gmtime...}, the timestamp represents the time at which the filter evaluates it. That is close to the frame's processing time, but it isn't necessarily the exact instant the camera captured the frame.

#### Why -re matters

Imagine a stream containing frames from several seconds of video. Without real-time pacing, FFmpeg may process buffered frames much faster than they would normally play.
If the OSD uses the current system time, those frames could receive timestamps based on when FFmpeg processes them, rather than the times the frames originally represent.
-re helps by pacing input processing in real time. But it doesn't guarantee that every frame is processed immediately after it was captured.

#### Why -tune zerolatency matters
When FFmpeg uses an encoder such as libx264, this tuning reduces encoder-side delay, for example by avoiding certain frame-reordering and buffering behaviors.
That can help the processed output stay close to real time. But the option only helps if it's applied to a compatible encoder; it doesn't speed up every stage of the pipeline.
### Cost of the sub stream

```
$ docker stats --no-stream
shinobi-cam-gate-1       96.38%   144.3MiB      Session 5: 66.60%
shinobi-cam-quay-1      129.05%   143.8MiB                 96.02%
shinobi-cam-waterway-1   82.56%   144.9MiB                 80.01%
shinobi-cam-yard-1       74.62%   145.6MiB                 55.06%
shinobi-mediamtx-1        9.03%    26.1MiB                  4.86%   twice the paths
```

≈ 3.8 cores in total instead of ≈ 3.0 (single samples, so ±10 %). The extra cost is the second x264
encoder plus four drawtext passes. Decoding is shared, which is the payoff of one process with
`split`. MediaMTX doubles because it now forwards 8 paths.

The `RTP packets are too big (1460 > 1440)` log line now appears for sub paths too. It is still
harmless (see the Session 5 notes).

---

## 7. Try it yourself

```bash
scripts/sim-up.sh                                              # rebuilds the image (font), 4 cameras, 8 paths
ffplay -rtsp_transport tcp rtsp://localhost:8554/cam-gate      # main: clock top-left, id bottom-right
ffplay -rtsp_transport tcp rtsp://localhost:8554/cam-gate-sub  # same frame, smaller
date -u                                                        # compare with the OSD
uv run python -m tools.streamprobe rtsp://localhost:8554/cam-gate-sub --seconds 10
sim/scripts/snapshot.sh rtsp://localhost:8554/cam-quay quay.jpg
```

Experiments that show what breaks:

- Set `sub: {fps: 30}` on `cam-gate` → `gen_compose: invalid …: cam-gate: sub fps 30 higher than main fps 15`.
- Set `sub: null` on `cam-yard`, run `scripts/sim-up.sh` → the `cam-yard-sub` path no longer
  exists in MediaMTX (ffplay fails to open it), and the main stream is unchanged.
- Set `defaults.osd: false` → the OSD is gone, and CPU drops a little.

## Glossary

| Term | Meaning |
|---|---|
| Main stream | Full-quality stream a camera offers for recording. |
| Sub stream | Second, low-resolution / low-fps stream from the same camera for detection and live grids. |
| ISP | Image signal processor: turns raw sensor data into frames before encoding. |
| OSD | On-screen display: text (time, name) burned into the pixels by the camera. |
| CBR / VBR | Constant bitrate / variable bitrate. VBR with a max cap is what we emulate. |
| Filter graph | Network of ffmpeg filters with named pads; needed for more than one output branch. |
| `split` | Filter that duplicates a stream of frames to N outputs, without copying pixels. |
| `drawtext` | ffmpeg filter that renders text with FreeType; `%{gmtime}` expands to the current UTC time. |
| Snapshot | Single JPEG from a camera, usually over HTTP; here grabbed from RTSP with `-frames:v 1`. |
