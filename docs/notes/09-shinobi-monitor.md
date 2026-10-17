# Session 9 — First monitor: what Shinobi does with a camera

`cam-gate` is now a Shinobi **monitor**. This note follows it from "Save" to a running FFmpeg
process, annotates every flag of the command Shinobi generated, and shows what changes with
other stream types and modes, plus what happens when the camera goes away. Everything below was
captured from this repo on 2026-10-17: Shinobi `dev` @ `63755122` (image digest in `.env`),
FFmpeg 4.3.9 inside the container, MediaMTX 1.21.1. Source references are to files inside the
container under `/home/Shinobi/`.

Placeholders: `<ke>` = group key, `<auth>` = session or API key. Both are secrets for this repo
(CLAUDE.md) and never written down.

```
 cam-gate container            mediamtx                shinobi container
 ──────────────────            ────────                ──────────────────────────────────────────────────────────────
 ffmpeg (x264, OSD) ─RTSP/TCP─▶ /cam-gate ◀─RTSP/TCP── ffmpeg  (one per monitor, spawned via node singleCamera.js)
      publish                   :8554       pull        │ -c:v copy (no decode)
                                                        ├─▶ HLS  /dev/shm/streams/<ke>/cam_gate/s.m3u8 + sN.ts  (tmpfs)
                                                        │        │ GET /<auth>/hls/<ke>/cam_gate/s.m3u8
                                                        │        ▼
                                                        │   camera.js HTTP :8080 ──▶ browser (hls.js → <video>)
                                                        ├─▶ (mode=record) MP4 segments /home/Shinobi/videos/<ke>/cam_gate/
                                                        └─▶ -progress pipe:5 ──▶ watchdog in camera.js ("is it alive?")
```

---

## 1. Vocabulary

| Term | Meaning |
|---|---|
| **Monitor** | Shinobi's word for one configured video input: where to pull from, what to do with it. One row in table `ccio.Monitors` (top-level columns + a big JSON `details` column, 229 keys). |
| **Monitor ID (`mid`)** | Primary key within the group. Shinobi strips everything except `[A-Za-z0-9_ ]`, so `cam-gate` would become `camgate`. We use **`cam_gate`** (hyphen → underscore) and keep the name `cam-gate`. |
| **Group key (`ke`)** | The account's group (Session 8 §7). Every path and URL contains it. |
| **Mode** | What the monitor does: Disabled (`stop`), Watch-Only (`start`), Record (`record`), Idle (`idle`). See §8. |
| **Input type (`type`)** | How FFmpeg reads the source: `h264` (any FFmpeg-readable network stream; the name is historic, H.265 works too), `mjpeg`, `jpeg` (snapshot polling), `hls`, `mp4`, `local`, `rtmp`, `socket`/`dashcam` (pushed from a browser). Ours: `h264`. |
| **Stream type (`details.stream_type`)** | The **live-view output**: how the picture gets to a browser. HLS, FLV, MJPEG, Poseidon (fragmented MP4), Base64-over-WebSocket, JPEG, "use substream". See §6–7. |
| **Live view** | Watching the camera in the browser *now*. Browsers can't speak RTSP, so the NVR has to re-package the stream into something they can play. |
| **Status code** | Runtime state the API reports: `0` Disabled, `1` Starting, `2` Watching, `3` Recording, `4` Restarting, `5` Stopped, `6` Idle, `7` Died, `8` Stopping, `9` Started (`definitions/monitorStatusCodes.js`). |

**Limit of an unactivated install:** `GET /activationStatus` (only answers from inside the
container) reports `"activated": false, "maxCameras": 15`. Above 15 monitors, *Add* fails with
"You must activate your installation". 4 cameras (8 with sub streams) fit.

## 2. Adding the monitor

The UI way: dashboard → add monitor → fill in the fields below → Save. Under the hood the Save
button runs `$.post(<auth>/configureMonitor/<ke>/<mid>, {data: JSON})`
(`web/assets/js/bs5.monitorsUtils.js:1282`), merging what you typed into the defaults of
`generateDefaultMonitorSettings()` (`web/assets/js/bs5.monitorSettings.js:28`). We did exactly
that from a script: login (Session 8 §8) → defaults → overrides → POST. Same request, no clicking.
Session 12 turns this into `scripts/provision_monitors.py`; until then the monitor lives in the
`shinobi-db` volume and survives `sim-down`/`sim-up`.

What we set (everything else = UI defaults):

| UI field | JSON | Value | Why |
|---|---|---|---|
| Name | `name` | `cam-gate` | matches `sim/cameras.yaml` |
| Monitor ID | `mid` | `cam_gate` | `-` is stripped (§1) |
| Mode | `mode` | `start` (Watch-Only) | recording is Session 10 |
| Input Type | `type` | `h264` | RTSP H.264 source |
| Full URL / Automatic | `details.auto_host_enable` `"1"`, `details.auto_host` | `rtsp://mediamtx:8554/cam-gate` | the UI's "full URL" field; FFmpeg's URL is assembled by `s.buildMonitorUrl()` from `protocol` `host` `port` `path`, which we set to `rtsp` `mediamtx` `8554` `/cam-gate` |
| RTSP Transport | `details.rtsp_transport` | `tcp` | project rule: RTSP over TCP (also the UI default) |
| Width / Height / FPS | `width` `height` `fps` | 1280 / 720 / 15 | descriptive only: no output of this command uses them (§5.4) |
| Stream Type | `details.stream_type` | `hls` (default) | §6 |
| Stream video codec | `details.stream_vcodec` | `copy` (default) | no re-encode |
| Detector | `details.detector` | `"0"` (default) | Session 14 |

The host is `mediamtx`, the compose service name: Shinobi resolves it through Docker's DNS on
the `shinobi_default` network (it resolved to 172.19.0.3). `localhost` would be the Shinobi
container itself.

```
$ … POST /<auth>/configureMonitor/<ke>/cam_gate   data=<json>
{ "ok": true, "msg": "Monitor Added by user. : <uid>" }
```

## 3. What happens on Save

```
 browser / script            camera.js (Node.js)                                         kernel / MariaDB
 ────────────────            ───────────────────                                         ────────────────
 POST configureMonitor ─────▶ s.auth(): <auth> is a live session of group <ke>?
                              cleanStringsInObject(): strip odd characters, JSON.parse every string (§9)
                              getWarningChangesForMonitor(): ffprobe the URL, suggest fixes (none here)
                              INSERT/UPDATE ccio.Monitors ──────────────────────────────▶ row
                              Logs: "Monitor Added" / "Monitor Updated"
                              edit of a running monitor → s.camera('stop') first (full restart, ~3 s)
                              s.camera('start') → launchMonitorProcesses()
                                 ├ status 1 "Starting"
                                 ├ TCP connect test mediamtx:8554, 2 s timeout (only "is the port open?")
                                 ├ s.ffmpeg(): build the command string (libs/ffmpeg/builders.js)
                                 ├ write /dev/shm/streams/<ke>/cam_gate/cmd_<token>.txt
                                 ├ spawn: node libs/cameraThread/singleCamera.js /usr/bin/ffmpeg cmd_<token>.txt
                                 │          └ spawn: /usr/bin/ffmpeg <args>  ── TCP ──────────────▶ mediamtx:8554
                                 ├ Logs: "Process Started" {cmd: …}
                                 └ status 2 "Watching"
```

`cmd_<token>.txt` (~33 KB) carries the argument array, the monitor config **and the whole
`conf.json`** to the helper; it sits in tmpfs and is deleted when the process closes.

### Why a Node helper between camera.js and FFmpeg?

`singleCamera.js` (the "camera thread") owns the FFmpeg child: it opens the numbered pipes,
forwards them, hosts the built-in motion detector (Session 14) when enabled, and on SIGTERM kills
FFmpeg's whole process group (`process.kill(-pid)`). If FFmpeg exits, the helper exits too, and
camera.js sees the exit.

## 4. Process tree and file descriptors

```
$ docker compose exec shinobi ps -eo pid,ppid,rss,pcpu,args --forest
 PID PPID    RSS %CPU COMMAND
   7    1  56524  0.8 node /usr/local/bin/pm2-docker /usr/local/share/shinobi/pm2.yml
 181    7 186796  5.0  \_ node /home/Shinobi/camera.js
 350  181  52064  2.4      \_ node /home/Shinobi/libs/cameraThread/singleCamera.js /usr/bin/ffmpeg /dev/shm/streams/<ke>/cam_gate/cmd_….txt
 361  350  46516  3.1          \_ /usr/bin/ffmpeg -progress pipe:5 -use_wallclock_as_timestamps 1 … /dev/shm/streams/<ke>/cam_gate/s.m3u8
```

Cost of one copy-mode monitor: ~100 MB RSS (two processes) and a few % of one core. FFmpeg's
open descriptors:

```
$ docker compose exec shinobi ls -l /proc/361/fd
0 1 2 3 4 5 6 7 8 9 -> socket:[…]      ← 10 pipes created by Shinobi (config.pipeAddition = 10)
10 -> socket:[24183844]                ← opened by FFmpeg itself: the RTSP connection
```

fd 10 in `/proc/361/net/tcp`: `020013AC:D09A → 030013AC:216A` = 172.19.0.2:53402 → 172.19.0.3:**8554**
(the IPv4 address is printed as a little-endian number, so its bytes appear reversed; the port is plain hex). That is the pull from MediaMTX.

FFmpeg addresses descriptors as `pipe:N` (= "write to fd N"). Shinobi's fixed numbering
(`libs/ffmpeg/builders.js`):

```
 fd  FFmpeg flag that writes there          used for
 ──  ─────────────────────────────          ──────────────────────────────────────────────
 0   (stdin)                                a pipe from the helper into FFmpeg
 1   pipe:1                                 live view for FLV / MJPEG / Poseidon / Base64 (§7)
 2   (stderr)                               FFmpeg's log (-loglevel warning)
 3   pipe:3                                 grey PAM frames for the built-in motion detector (Session 14)
 4   pipe:4                                 JPEG frames for object-detection plugins (Session 15)
 5   -progress pipe:5                       heartbeat → watchdog (§10)
 6   pipe:6                                 PCM audio for the audio detector
 7   pipe:7                                 timelapse JPEGs
 8   -segment_list pipe:8                   "a recording segment is finished" → Videos table (Session 10)
 9   (unused here)
 10+                                        extra stream channels (one pipe each), then FFmpeg's own sockets
```

## 5. The generated command, flag by flag

Where to read it: the `Logs` table row `Process Started` (also in the UI's log view), or
`/proc/<pid>/cmdline`. The Logs version still has quotes; `splitForFFMPEG()` removes them
before `spawn()`, which does no shell parsing.

```
docker compose exec shinobi mysql ccio -N -B --raw -e \
  "select time, json_value(info,'$.msg.cmd') from Logs where mid='cam_gate' order by time desc limit 1"
```

```
/usr/bin/ffmpeg
  -progress pipe:5                                     ┐
  -use_wallclock_as_timestamps 1                       │
  -analyzeduration 1000000000 -probesize 1000000000    │ input options
  -fflags +igndts                                      │ (apply to the next -i)
  -rtsp_transport tcp                                  │
  -loglevel warning                                    ┘ (global)
  -i rtsp://mediamtx:8554/cam-gate                       input #0
  -strict -2                                           ┐
  -fflags +genpts+igndts -muxdelay 0 -muxpreload 0     │ output options
  -an -c:v copy                                        │ (apply to the next output file)
  -f hls -hls_time 2 -hls_list_size 3 -start_number 0  │
  -hls_allow_cache 0                                   │
  -hls_flags +delete_segments+omit_endlist+discont_start┘
  /dev/shm/streams/<ke>/cam_gate/s.m3u8                  output #0
```

FFmpeg's command-line grammar: options **before** `-i` belong to that input, options before an
output filename belong to that output, and the pattern repeats. One input can feed many outputs
(§8 shows a second output appearing). Shinobi builds the string from pieces:
`buildMainInput()` + `buildMainStream()` + `buildJpegApiOutput()` + `buildMainRecording()` +
`buildAudioDetector()` + `buildMainDetector()` + `buildEventRecordingOutput()` +
`buildTimelapseOutput()`; empty pieces are left out.

### 5.1 Input side

**`-progress pipe:5`**: FFmpeg writes a block of `key=value` lines about twice a second, each
ending with `progress=continue` (`progress=end` when it finishes). Real sample (local FFmpeg 6.1,
same format):

```
bitrate=N/A
total_size=N/A
out_time_us=1933333
out_time=00:00:01.933333
dup_frames=0
drop_frames=0
speed=…
progress=continue
```

Shinobi doesn't parse the content. Any data on fd 5 resets a 60 s timer, and that timer is the
"camera is not streaming" watchdog (§10). *Without it*: an FFmpeg process that is still alive
but receives nothing (frozen camera, Session 7) would keep running forever and look healthy.

**`-use_wallclock_as_timestamps 1`**: replaces every packet's timestamp with the **arrival time**
on Shinobi's clock, throwing away the camera's own PTS. Added for input type `h264` unless the UI
option "wall_clock_timestamp_ignore" is `1`. Measured on the HLS output (§6):

```
                                    PTS step between frames (nominal 1/15 s = 66.67 ms = 6000 ticks @ 90 kHz)
 source RTSP (camera clock)         min 66.7  max 66.7  stdev  0.0 ms   59/59 exactly 6000
 Shinobi HLS, wall clock ON         min 39.2  max 89.7  stdev 16.8 ms    0/29 exactly 6000
 Shinobi HLS, wall clock OFF        min 66.7  max 66.7  stdev  0.0 ms   29/29 exactly 6000
```

```
 camera PTS:   |----|----|----|----|----|        perfectly regular (encoder clock)
 arrival:      |--|------|---|-----|--|---       network + scheduling jitter becomes the timeline
```

Why upstream does it anyway: real cameras often send broken timestamps (jumping back on reboot,
wrong clock, restart at 0 after a reconnect). Arrival time is monotonic and already in server
time, so recordings and events line up with the NVR clock. The price is jitter. It shows up as
slightly uneven playback, and it fooled ffprobe's frame-rate guess: streamprobe on the HLS
URL said **"@ 30 fps"** while it counted 151 frames in 10 s (= 15 fps). Each segment on its own
reports `r_frame_rate=15/1`; the 30 comes from the guess over the jittered timeline. *Without the
flag* a camera with bad timestamps gives "Non-monotonous DTS" errors and broken recordings.
Our simulated cameras have perfect timestamps. This feeds the open question from Session 2
(absolute timestamps: ingest-time stamping vs RTCP sender reports). **Decide in Session 10**,
when recordings depend on it; kept at the upstream default for now.

**`-analyzeduration 1000000000 -probesize 1000000000`**: before decoding/copying, FFmpeg *probes*
the input to learn each stream's codec, size, etc. These are the upper limits: 1000 s (the unit
is µs) and 1 GB. Probing stops as soon as all streams are known; for RTSP the SPS/PPS come in
the SDP (Session 2), so that's quick. Measured time to the first frame, 5 runs each:
defaults 2.1–4.1 s, these values 4.0 s. No difference beyond keyframe-join luck. *What
breaks*: if the SDP announces a stream that never sends data (e.g. an audio track a camera
advertises but mutes), FFmpeg keeps probing up to these limits, so the monitor "starts" but shows
nothing for a long time.

**`-fflags +igndts`** (input): "ignore DTS if PTS is set": the demuxer drops the decode timestamp
and lets FFmpeg derive it. Protects against cameras with bogus DTS. Our streams have no B-frames,
so DTS = PTS anyway (Session 2): harmless here.

**`-rtsp_transport tcp`**: interleave RTP inside the RTSP TCP connection (Session 2/4) instead
of separate UDP ports. Added only for `h264` + `rtsp://` + a transport not set to `no`. *Without
it*: FFmpeg tries UDP first; through Docker NAT/firewalls the UDP packets often never arrive, and
UDP loss tears frames into grey smears (Session 7).

**`-loglevel warning`**: stderr gets warnings and errors only (`details.loglevel`).

**`-i rtsp://mediamtx:8554/cam-gate`**: built by `s.buildMonitorUrl()` from
`protocol://[user:pass@]host:port/path`. Credentials (none here) are removed from the logged copy
by `sanitizedFfmpegCommand()`.

### 5.2 Output side (live view, HLS)

**`-strict -2`**: allow "experimental" codecs. Historic: FFmpeg's native AAC encoder used to be
experimental. No effect with `copy` and `-an`.

**`-fflags +genpts+igndts`** (output): as far as I can tell from FFmpeg's source, `genpts` and
`igndts` are only read on the demuxing side, so on an output they do nothing. Shinobi adds them to
every output (constant `ignoreDtsAndGenPts`). Not verified by experiment; listed so it isn't a mystery.

**`-muxdelay 0 -muxpreload 0`**: MPEG-TS (the container inside HLS segments) normally delays
the first timestamps by 0.7 s so a hardware decoder's buffer can fill. Zero means no added
start-up delay, which helps live view.

**`-an`**: no audio output (`stream_acodec` = `no`). Our cameras have no audio anyway.

**`-c:v copy`**: **stream copy**: the H.264 packets go from RTSP into MPEG-TS untouched, no
decode, no encode. That's why it costs ~3 % CPU, why the output is bit-identical to what the
camera encoded (streamprobe on the HLS: Main, 1280×720, yuv420p, GOP 30 frames = 2 s fixed, no
B-frames), and why the camera's encoder settings (Session 6) matter so much: the NVR passes them on.

**`-f hls -hls_time 2`**: write HLS (HTTP Live Streaming): a playlist (`.m3u8`) plus short
MPEG-TS segment files. Target segment length 2 s. With `copy`, FFmpeg can only cut **at a
keyframe** (a segment must be decodable on its own). Our GOP is exactly 2 s, so every
segment is ~2.0 s:

```
 GOP:      |I.............|I.............|I.............|      I = keyframe every 30 frames (2 s @ 15 fps)
 segments: [   s3.ts 2.0s ][   s4.ts 2.0s ][   s5.ts 2.0s ]
```

*What breaks with an irregular GOP*: the raw Santos clip had a 4.17 s GOP (Session 3). HLS
would cut at 4.17 s instead of 2 s, latency roughly doubles, and with scene-cut keyframes
(`-sc_threshold` not 0) the segment lengths vary and `#EXT-X-TARGETDURATION` has to grow.

**`-hls_list_size 3`**: the playlist lists only the newest 3 segments (UI default; the builder's
own fallback is 2). **`-start_number 0`**: first segment is `s0.ts`.
**`-hls_allow_cache 0`**: writes `#EXT-X-ALLOW-CACHE:NO` (a tag newer HLS versions dropped;
Shinobi also sends no-cache HTTP headers).
**`-hls_flags`**: `delete_segments`: delete files that fell out of the playlist (one extra is
kept, so a client still downloading it doesn't get a 404, which is why 4 `.ts` files exist for
3 entries); `omit_endlist`: never write `#EXT-X-ENDLIST`, so even after FFmpeg exits a player
treats the stream as live and keeps polling instead of "the video ended"; `discont_start`: put
`#EXT-X-DISCONTINUITY` before the first segment, telling the player "timestamps restart here"
(it disappears once `s0` leaves the list).

**`/dev/shm/streams/<ke>/cam_gate/s.m3u8`**: the tmpfs from Session 8 (512 MB RAM). Live
segments are disposable: RAM saves disk writes and is wiped on restart.

### 5.3 What a recording adds (mode Record, §8)

```
  -an -vcodec copy -strict -2 -segment_format_options movflags=faststart
  -fflags +genpts+igndts -muxdelay 0 -muxpreload 0
  -f segment -segment_atclocktime 1 -reset_timestamps 1 -strftime 1 -segment_list pipe:8
  -segment_time 900 "/home/Shinobi/videos/<ke>/cam_gate/%Y-%m-%dT%H-%M-%S.mp4"
```

`segment` muxer: start a new MP4 every 900 s (`cutoff` = 15 min, clamped to 1–15), cut at
wall-clock multiples (`:00 :15 :30 :45`, `segment_atclocktime`), each file's timestamps start at
0, the filename comes from `strftime`, finished files are announced on fd 8 so Shinobi can index
them, and `movflags=faststart` moves the MP4 index (`moov`) to the front so a browser can start
playing before downloading everything. Again `copy`: recording costs almost nothing extra.
Details in Session 10.

### 5.4 What is *not* in the command

- `width`/`height`/`fps` of the monitor: in `builders.js` only `e.fps` is used, and only when
  recording **with** re-encoding. The `*_scale_x/_y` fields resize, and only when encoding.
- Detector outputs (`pipe:3`, `pipe:4`): `details.detector` is `0`.
- A JPEG snapshot output: needs `details.snap` = `1` **and** `liveJpegApiEnabled` in conf.json.

## 6. Live view over HLS: checked

```
$ curl -s http://localhost:8080/<auth>/hls/<ke>/cam_gate/s.m3u8
#EXTM3U
#EXT-X-VERSION:3
#EXT-X-ALLOW-CACHE:NO
#EXT-X-TARGETDURATION:2          ← longest segment, rounded: 2 s (= our GOP)
#EXT-X-MEDIA-SEQUENCE:41         ← number of the first listed segment; grows by 1 every 2 s
#EXTINF:1.992689,
s41.ts
#EXTINF:2.000000,
s42.ts
…
$ uv run python -m tools.streamprobe http://localhost:8080/<auth>/hls/<ke>/cam_gate/s.m3u8 --seconds 10
stream:     h264 Main 1280x720 yuv420p @ 30 fps   ← misleading guess, see §5.1 (15 fps measured)
GOP frames: min 30 / max 30 / mean 30
GOP secs:   min 1.99 / max 2.01 / mean 2
fixed GOP:  yes
B-frames:   no
```

The URL comes from the API (`GET /<auth>/monitor/<ke>/cam_gate` → `"streams": [...]`), served by
`libs/webServerStreamPaths.js` from the tmpfs folder. In the dashboard the live grid puts a
`<video>` element on the page and feeds it with **hls.js** (a JS library that downloads the
playlist and segments and pushes them into the browser's Media Source Extensions, because not
every browser plays HLS natively).

**Latency.** A frame grabbed from Shinobi's HLS showed the camera OSD time 18:54:**33** while
the host clock said 18:54:**40**: about **7 s** behind. HLS latency is built in:

```
 camera ──▶ segment s(n) being written (up to 2 s) ──▶ listed ──▶ player starts at the OLDEST listed segment
            ≈ hls_time                                            ≈ (hls_list_size − 1) × hls_time = 4 s  + download/buffer
```

Fine for an operator glancing at a gate; too slow for anything interactive. Lower it with
`hls_list_size 2`, or use FLV/Poseidon (§7), or WebRTC from MediaMTX outside Shinobi.

## 7. The other stream types (same monitor, only `stream_type` changed)

Output part of the generated command, captured for each:

```
 hls     -an -c:v copy -f hls … s.m3u8                                                   ~3 %  CPU, 46 MB
 flv     -an -c:v copy -f flv pipe:1                                                      ~3 %  CPU, 46 MB
 mp4     -an -c:v copy -f mp4 -movflags +frag_keyframe+empty_moov+default_base_moof       ~5 %  CPU, 47 MB
         -metadata title="Poseidon Stream from Shinobi" -reset_timestamps 1 pipe:1
 mjpeg   -an -q:v 15 -vf "fps=2" -an -c:v mjpeg -f mpjpeg -boundary_tag shinobi pipe:1   ~17 % CPU, 138 MB
```

(CPU/RSS from `ps` a few seconds after start: rough, Session 10 measures properly.)

```
                         ┌─ files in tmpfs ─▶ GET /hls/…/s.m3u8, sN.ts (HTTP polling) ─▶ hls.js ─▶ <video>   HLS
 ffmpeg ─┤ copy ├────────┤
                         └─ pipe:1 ─▶ camera.js (EventEmitter, fan-out to N viewers)
                                        ├─ GET /flv/…/s.flv  or WebSocket ─▶ flv.js ─▶ <video>               FLV
                                        └─ GET /mp4/…/s.mp4 (fragmented MP4) ─▶ <video> via MSE              Poseidon
 ffmpeg ─┤ DECODE → fps=2 → JPEG encode ├─ pipe:1 ─▶ GET /mjpeg/… (multipart/x-mixed-replace) ─▶ <iframe>   MJPEG
```

| Type | Container | Re-encode? | Latency (expected) | Notes |
|---|---|---|---|---|
| HLS | MPEG-TS segments + playlist | no | several s (§6) | files, so HTTP caches/CDNs work; default |
| FLV | Flash Video stream | no | ~1 s | one long HTTP/WS response per viewer, needs flv.js |
| Poseidon | fragmented MP4 (`moof`+`mdat` per keyframe) | no | ~1 GOP | `empty_moov`: header without sample table, so playback can start immediately |
| MJPEG | multipart JPEGs | **yes, decode + JPEG encode** | low, but 2 fps here | works everywhere (even `<img>`), most CPU, `-q:v 15` = JPEG quality scale 2 (best)…31 |

*What breaks with MJPEG at scale*: it is the only type that decodes. With 4 cameras that's four
H.264 decoders running all the time, even when nobody watches. Live view should stay `copy`; the
sub stream (`/cam-x-sub`, Session 6) is the cheap source when something does have to decode.

## 8. Modes

| Mode (`mode`) | UI | Process | Status | Generated command |
|---|---|---|---|---|
| `stop` | Disabled | none | `5 Stopped` | none |
| `start` | Watch-Only | ffmpeg | `2 Watching` | input + live-view output |
| `record` | Record | **the same single** ffmpeg | `3 Recording` | input + live view **+ segment output** (§5.3) |
| `idle` | Idle | none | `6 Idle` | none |

Shinobi's help text: Disabled = "no process will be created"; Watch-Only = "will only stream, no
recording will occur unless otherwise ordered by API or Detector" (that's how motion-triggered
recording works later: a Watch-Only monitor that starts recording on events). In the code
(`libs/monitor.js` `case'idle':case'stop'`), Idle is the stop path plus a different status and
log line ("Monitor Idling"); I found no other behavioural difference in `libs/*.js` and
`libs/monitor/*.js`.

Record mode made a file within seconds:
`videos/<ke>/cam_gate/2026-10-17T18-53-59.mp4` (1.5 MB after a few seconds). It stays in the
`shinobi-videos` volume; Session 10 handles retention.

**Every edit restarts the monitor.** Changing any field logged `Monitor Stopped` → `Process
Started` 3 s later, with new PIDs, and live-view viewers see a gap. (Code exception: detection
regions/thresholds can be pushed to a running camera thread; Session 14.)

## 9. Quirk: strings become numbers in the database

Shinobi stores numeric-looking strings as **numbers**: sent `"detector": "0"`, the
`Monitors.details` column has `0`; `"cords": "[]"` became `[]`. 82 of 229 keys changed type, no
values changed. Cause: `cleanStringsInObject()` (`libs/basic/utils.js:246`) sanitises input by
calling `JSON.parse()` on every string, and `JSON.parse("0")` is the number `0`. The API's
`GET /monitor` returns them as numbers too.

The command builder compares with strings (`details.wall_clock_timestamp_ignore !== '1'`), yet
setting it to `"1"` really removed `-use_wallclock_as_timestamps`, so at build time the values
are strings again. Consequence for Session 12 (monitors as code): an exported monitor will show
numbers, and an idempotency check must compare `"0"` and `0` as equal.

## 10. When the camera goes away: watchdog and back-off

Two mechanisms (`libs/monitor/utils.js`):

1. **Process exit** → `fatalError()` → status `7 Died` → relaunch after **5 s**, but from the
   **3rd failure on, after 10 minutes** (`errorFatalCount >= 3 ? 600000 : 5000`). The counter
   resets to 0 only if 60 s pass after a launch without a new failure. `fatal_max` (`0` =
   unlimited) can stop retrying altogether.
2. **No progress for 60 s** while the process lives (the `-progress pipe:5` timer) → "Camera is
   not streaming" → forced restart. That is the case of a frozen camera (Session 7 `freeze`).

Before each launch a TCP connect test hits `mediamtx:8554` (2 s timeout). It only proves the
*server* is up: MediaMTX listens even when the camera isn't publishing, so the test passes and
FFmpeg then fails with 404.

Experiment: `scripts/chaos.sh stop cam-gate`, start it again 30 s later, poll the API:

```
 18:55:31  chaos stop cam-gate
 18:55:31  Process Unexpected Exit   ← MediaMTX closed the reader session          fatal #1 → retry in 5 s
 18:55:37  Process Started / Process Unexpected Exit   ← no publisher → 404 (S7)   fatal #2 → retry in 5 s
 18:55:43  Process Started / Process Unexpected Exit                               fatal #3 → retry in 10 min
 18:56:02  chaos start cam-gate      ← camera is back and publishing
 18:56:17 … 18:57:35   status "Died" (7), no ffmpeg process
 19:05:44  Process Started → status "Watching" (2)   ← 10 min 1 s after fatal #3; camera was back for 9 min 42 s
```

```
 camera:   ████████░░░░░░(30 s down)░░░░░████████████████████████████████████████████████
 shinobi:  ████████ x   x   x ─────────────────── waiting 10 min ──────────────────── █████
                    5s  5s                                                        relaunch
```

**For a port this matters:** a 15-second camera reboot costs ~10 minutes without live view,
recording or detection, and nothing on the Shinobi side says "the camera is back". Options for
Session 25 (robustness): a watchdog service that polls monitor status and calls
`GET /<auth>/configureMonitor/<ke>/<mid>/restart` when the stream is up again, or make MediaMTX
keep the path alive (MediaMTX has a per-path fallback option; check its docs then) so FFmpeg never sees a 404. Manual fix for now:
re-save the monitor, or use the restart endpoint.

## 11. Cheat sheet

```bash
# generated command, newest first
docker compose exec shinobi mysql ccio -N -B --raw -e \
  "select time, json_value(info,'$.type'), json_value(info,'$.msg.cmd') from Logs where mid='cam_gate' order by time desc limit 5"
# process tree incl. ffmpeg
docker compose exec shinobi ps -eo pid,ppid,rss,pcpu,args --forest
# exact argv of the running ffmpeg
docker compose exec shinobi sh -c 'tr "\0" " " < /proc/$(pgrep -x ffmpeg)/cmdline; echo'
# live HLS files
docker compose exec shinobi sh -c 'ls -la /dev/shm/streams/*/cam_gate/; cat /dev/shm/streams/*/cam_gate/s.m3u8'
# stored monitor config (numbers, not strings: §9)
docker compose exec shinobi mysql ccio -N -B --raw -e "select details from Monitors where mid='cam_gate'"
# monitor limit of this install (inside the container only)
docker compose exec shinobi curl -s http://127.0.0.1:8080/activationStatus
```

## Gotchas collected

| Symptom | Cause | Fix |
|---|---|---|
| Monitor ID `cam-gate` saved as `camgate` | `mid` is stripped to `[A-Za-z0-9_ ]` | use `cam_gate` |
| URL with `localhost` → monitor dies at once | `localhost` is the Shinobi container | use the compose service name `mediamtx` |
| streamprobe on Shinobi's HLS says 30 fps | wall-clock timestamps jitter, ffprobe's rate guess | count frames; or turn wall clock off (Session 10 decision) |
| Live view ~7 s behind | HLS: player starts at oldest of 3 × 2 s segments | smaller `hls_list_size`, or FLV/Poseidon |
| Monitor stays "Died" after the camera is back | 10-minute back-off after 3 quick failures | restart the monitor; Session 25 watchdog |
| Every settings change interrupts live view ~3 s | edit = stop + start | change settings outside busy hours; provision in bulk |
| Exported config has `0` where the UI sent `"0"` | `cleanStringsInObject()` JSON-parses strings | compare loosely (Session 12) |
| "Add monitor" fails after 15 monitors | unactivated install, `maxCameras: 15` | stay ≤ 15 (we need 8 at most) |

## Open questions

- Wall-clock vs camera timestamps for recordings and event times: decide in Session 10 with
  real recordings (ties into the Session 2 question about RTCP sender reports).
- Does the `string → number` conversion (§9) break any `=== '1'` check on the *detector* path?
  Check in Session 14 when the detector is switched on (the builder path worked).
