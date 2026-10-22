# Session 10 — Recording, storage, CPU

All four cameras now record continuously in Shinobi. This note follows a recording from the
FFmpeg output that writes it, through the database row that indexes it, to the two mechanisms
that delete it, and measures what recording costs (copy vs transcode, main vs sub stream).
Everything was captured on 2026-10-22: Shinobi `dev` @ `63755122`, FFmpeg 4.3.9 in the container,
4 simulated cameras from `sim/cameras.yaml` v2. Source references are files in the container
under `/home/Shinobi/`. `<ke>` = group key, `<auth>` = session/API key (secrets, never written down).

```
 cam-x (encode) ─RTSP─▶ mediamtx ─RTSP/TCP─▶ shinobi: ONE ffmpeg per monitor
                                             │ -c:v copy
                                             ├─▶ HLS live view   /dev/shm/streams/<ke>/<mid>/s.m3u8   (tmpfs)
                                             └─▶ segment muxer   /home/Shinobi/videos/<ke>/<mid>/YYYY-MM-DDTHH-MM-SS.mp4
                                                   │ -segment_list pipe:8  ("file X is finished")
                                                   ▼
                                             camera.js ── INSERT ──▶ MariaDB ccio.Videos  ◀── retention deletes rows + files
                                                   └─ spawns ffmpeg: frame at 7 s ─▶ <mid>_timelapse/<date>/*.jpg
```

Final configuration (applied through the UI's own API calls; monitors as code is Session 12):

| Setting | Where | Value | Why |
|---|---|---|---|
| Mode | monitor `mode` | `record` (all 4) | continuous recording |
| Record video codec | `details.vcodec` | `copy` | §4: ~0.2 % CPU per camera instead of 40–140 % |
| Segment length | `details.cutoff` | `15` (min, max allowed) | §2.4: fewer files, rows and thumbnail runs |
| Wall-clock timestamps | `details.wall_clock_timestamp_ignore` | `1` (= use camera timestamps) | §6: decision of the Session 2/9 open question |
| Transcode fallback | `details.crf` / `preset_record` | `23` / `veryfast` | ignored with copy; replaces the CRF 1 trap (§4.2) |
| Monitor fps | `fps` | 15 / 15 / 10 / 10 | was `1`/empty (UI default); used when transcoding (§4.2) |
| Videos to keep | account `details.days` | `1` | §5 |
| Max storage | account `details.size` | `20000` (MB) | threshold ≈ 16 200 MiB ≈ 3.6 h of all 4 cameras |

---

## 1. Record mode = one more output on the same FFmpeg

Session 9 showed one FFmpeg process per monitor. Record mode doesn't add a process. It adds a
second output to the same command (`buildMainRecording()` in `libs/ffmpeg/builders.js`).
Captured from `cam_quay` (cutoff 1 min during the experiments):

```
/usr/bin/ffmpeg -progress pipe:5 -use_wallclock_as_timestamps 1 -analyzeduration 1000000
  -probesize 1000000 -fflags +igndts -rtsp_transport tcp -loglevel warning
  -i "rtsp://mediamtx:8554/cam-quay"
  ─── output #0: live view (Session 9) ───────────────────────────────────────────────
  -strict -2 -fflags +genpts+igndts -muxdelay 0 -muxpreload 0 -an -c:v copy
  -f hls -hls_time 2 -hls_list_size 3 -start_number 0 -hls_allow_cache 0
  -hls_flags +delete_segments+omit_endlist+discont_start "/dev/shm/streams/<ke>/cam_quay/s.m3u8"
  ─── output #1: recording (new) ─────────────────────────────────────────────────────
  -an -vcodec copy -strict -2 -segment_format_options movflags=faststart
  -fflags +genpts+igndts -muxdelay 0 -muxpreload 0
  -f segment -segment_atclocktime 1 -reset_timestamps 1 -strftime 1 -segment_list pipe:8
  -segment_time 60 "/home/Shinobi/videos/<ke>/cam_quay/%Y-%m-%dT%H-%M-%S.mp4"
```

Consequences:

- **One RTSP pull per camera** for live view and recording. Real cameras often allow only 2–4
  RTSP sessions, so this matters.
- **Shared fate.** If the FFmpeg dies, live view and recording stop together. The 10-minute
  back-off of Session 9 §10 is also a 10-minute recording gap.
- **Every monitor edit restarts it**: ~4 s recording gap per edit (`cam_quay` file closed
  11:10:22, next one started 11:10:26); a crash restart costs 7–9 s (§3.4).

## 2. The segment muxer

`-f segment` is a meta-muxer: it opens a new inner MP4 every `segment_time` seconds.

| Flag | Meaning | What breaks without it |
|---|---|---|
| `-segment_time 900` | target length; = `cutoff` × 60, clamped to 1–15 min | one endless file: unplayable until closed, lost entirely on a crash |
| `-segment_atclocktime 1` | cut at wall-clock multiples (`:00 :15 :30 :45`) | cuts at "start + n × 900 s": files of different cameras don't line up |
| `-strftime 1` + `%Y-%m-%dT%H-%M-%S` | file name = server clock when the file starts | Shinobi gets a video's start time from the name (`s.nameToTime()`) |
| `-reset_timestamps 1` | each file's PTS starts at 0 | (files would carry the stream's running time) |
| `-segment_list pipe:8` | write each **finished** file name to fd 8 | Shinobi would never learn that a file is done (§3) |
| `movflags=faststart` | move the MP4 index (`moov`) to the front at close | browser must download the whole file before playing |

### 2.1 Cuts happen only at keyframes

With `copy`, a file must start with an IDR frame (a keyframe that decodes on its own), so "cut at
:45:00" means "cut at the first keyframe **at or after** :45:00":

```
wall clock    12:44:58      12:45:00      12:45:02      12:45:04
GOP (2 s)     |I·····P·····|I·····P·····|I·····P·····|I·····
                                ▲ boundary
                                      ▲ first I after it → new file (≤ 1 GOP late)
files         …T12-38-43.mp4 ][ T12-45-01.mp4 ……
```

Real files from 2026-10-18 (cutoff 15): `12:38:43→12:45:01` (first file short: started
mid-quarter), `12:45:01→13:00:02`, `13:00:02→13:11:31` (monitor stopped). The container clock is
UTC (host CEST), so file names are UTC, the same as the camera OSD.

With our fixed 2 s GOP every 60 s file of the copy cameras had exactly 900 (15 fps) or 600 (10 fps)
frames. **What breaks with a long GOP**: §4.3 shows a 16.7 s GOP giving 50 s and 67 s files
instead of 60 s.

### 2.2 The MP4 index and why a crash loses the whole segment

```
while recording:   [ftyp][mdat ██████████ growing …]                ← no index yet
clean close:       [ftyp][mdat ████████████████][moov]
after faststart:   [ftyp][moov][mdat ████████████████]               ← streamable
killed hard:       [ftyp][mdat ████████████]   → "moov atom not found"
```

Seen live: `ffprobe` on a file still being written → `moov atom not found … Invalid data found
when processing input`. Crash tests in §3.4.

### 2.3 `-reset_timestamps 1`: absolute time lives only in the file name

Inside each file the first frame has PTS 0. "Something happened 37.4 s into
`2026-10-22T11-13-00.mp4`" = 11:13:00 + 37.4 s. The name has 1 s resolution, and the cut can be up
to one GOP after the boundary, so that's the accuracy of absolute time from recordings.
Precise event times have to come from the events themselves (Sessions 17–20).

### 2.4 Choosing the segment length

| Short (1 min) | Long (15 min) |
|---|---|
| finished file in the index within ~1 min | up to 15 min until a recording appears in `Videos`/API |
| crash loses ≤ 1 min | crash loses ≤ 15 min (§3.4) |
| 4 × 96 files/day per camera, 1 row each | 96 files/day per camera |
| thumbnail ffmpeg every minute: +4.5 % CPU for 4 cameras (§3.2) | +0.3 % |
| size pruning in 1-min steps | in 15-min steps |

We use 15 (the maximum Shinobi allows). The crash risk is better handled by fragmented MP4
(Session 25) than by short segments.

## 3. From finished file to database row

`libs/monitor/utils.js:1542` reads fd 8. The list has no `.m3u8`/`.csv` extension, so FFmpeg
writes the "flat" format: one file name per line, written when the file is closed.

```
ffmpeg                         camera.js                                              MariaDB / disk
closes T11-13-00.mp4
"2026-10-22T11-13-00.mp4\n" ─fd 8─▶ regex /T\d\d-\d\d-\d\d./
                               s.insertCompletedVideo()          (libs/videos.js:104)
                                 ├ stat → size, mtime (= "end")
                                 ├ websocket "video_build_success" to open dashboards
                                 ├ s.purgeDiskForGroup()          ← SIZE retention (§5.2)
                                 ├ usedSpace += size
                                 ├ INSERT Videos(ke, mid, time=<from name>, end, size, status=1) ─▶ row
                                 └ postProcessCompletedMp4Video() → frame at 7 s → *_timelapse/*.jpg
                               log "Video Finished"
                               resetRecordingCheck(): timer = 1.3 × cutoff
                                 └ no finished segment in time → "Camera is not recording" → restart
```

`Videos` columns: `ke, mid, ext, size, status, archive, objects, saveDir, time, end, details`.
`status` 1 = unread, 2 = read (UI "mark as read" → `…/videos/<ke>/<mid>/<file>/status/2`). It
says nothing about completeness. `archive = 1` protects a video from both retention mechanisms.

### 3.1 Two watchdogs

| Watchdog | Trigger | Catches |
|---|---|---|
| `-progress pipe:5` (Session 9) | no progress data for 60 s | hung input (frozen camera) |
| `resetRecordingCheck` | no finished segment for 1.3 × cutoff (19.5 min) | a recording output that stalls while the input still runs |

### 3.2 Hidden cost: a thumbnail FFmpeg per segment

`postProcessCompletedMp4Video()` → `saveVideoFrameToTimelapse(video, 7)` →
`getVideoFrameAsJpeg()` spawns, for **every** finished file:

```
ffmpeg -loglevel warning -ss 7 -i <file> -frames:v 1 -q:v 2 -f image2pipe -vcodec mjpeg pipe:1
```

That's where the `<mid>_timelapse/<date>/*.jpg` folders come from (row in table `Timelapse Frames`,
retention `timelapseFrames_days`, default 60). Timed by hand on a `cam_quay` file:

```
0.31 s wall   0.57 s user   0.11 s sys      ← ≈ 0.68 s CPU per segment (decode is multithreaded)
startup only (ffmpeg -version): 0.12 s user 0.03 s sys
```

`-ss 7` before `-i` seeks to the keyframe at or before 7 s (6 s with a 2 s GOP), then decodes up to
7 s. With 1-min segments: 4 cameras × 0.68 s per 60 s = **4.5 %**, exactly the gap between the
per-process sum and the container total in §7. With 15-min segments: 0.3 %. Short-lived processes
are invisible to `ps`/`top`, but the cgroup counter catches them.

### 3.3 Orphan scan

At startup Shinobi logs `Orphaned Videos Found and Inserted {"<ke>":{"cam_gate":2,…}}`: rows for
files on disk without a row. It also picked up **files still being written at that moment**, which
caused duplicates:

```
cam_gate  2026-10-22 11:28:00  end 11:28:02  size    524336   ← orphan row, inserted at startup mid-write
cam_gate  2026-10-22 11:28:00  end 11:29:00  size  14995169   ← the real row when the file closed
```

Same file, two rows: the API lists it twice and Shinobi's used-space counter is too high by the
partial size.

### 3.4 Crash safety (measured)

| How the recording ended | Partial file | `Videos` row | Gap |
|---|---|---|---|
| Camera gone (`chaos.sh stop cam-yard`): FFmpeg input error, exits by itself | **plays**: 288 frames, 28.8 s | yes | 11:19:29 → 11:19:36 (7 s) |
| `kill -9` on the FFmpeg (OOM killer, power loss) | **unplayable**: `moov atom not found` (6.3 MB) | **yes, pointing to the broken file** | 11:19:25 → 11:19:34 (9 s) |
| Monitor edit (Shinobi stops FFmpeg itself) | plays (yard 11:27:00, 377 frames) | yes | ~4 s (quay 11:10:22 → 11:10:26) |
| `docker compose restart shinobi` | **unplayable** for 3 of 4 monitors (the 4th had just been restarted by an edit) | yes (orphan scan) | ~25 s (container start) |

So a container stop is a hard kill for the recorders: the current segment of every camera is lost
(up to 15 min each). Each restart also costs 7–9 s: 5 s retry delay, FFmpeg start, waiting for
the next keyframe. → Session 25: fragmented MP4 (`movflags=+frag_keyframe+empty_moov`, as the
Poseidon live stream uses) stays playable up to the last fragment, and a client must not trust
`Videos` rows blindly (broken files, duplicates).

## 4. Copy vs transcode

```
copy:       RTP ─▶ depacketize ─▶ H.264 access units ─────────────────────────────────▶ MP4 mux
transcode:  RTP ─▶ depacketize ─▶ H.264 AU ─▶ DECODE ─▶ raw YUV (1.38 MB/frame @720p) ─▶ fps filter ─▶ ENCODE ─▶ MP4 mux
```

### 4.1 Measured on `cam_gate` (1280×720 @ 15 fps, 2 Mb/s source), other 3 cameras on copy as control

| Recording of cam_gate | ffmpeg CPU (100 % = 1 core) | Size | Profile |
|---|---|---|---|
| copy | **0.8 %** | 15 MiB/min (= source) | bit-identical: Main, GOP 2 s, no B |
| `libx264`, Shinobi defaults (CRF 1, preset medium) | **141 %** (3 runs: 141.1 / 140.9 / 140.3) | 34–35 MiB/min (**2.3× the source**) | **High, B-frames, GOP 250 = 16.7 s** |
| `libx264` CRF 23, `veryfast`, `-g 30 -keyint_min 30 -sc_threshold 0 -bf 0 -profile:v main` | **≈ 40 %** steady (39.1 / 39.7 / 39.9; first minutes 44–123) | 6.2–6.7 MiB/min (0.43×) | Main, GOP 2 s, no B |

Transcoding only makes sense to save storage, fix a broken camera stream or burn in an overlay. It
costs a full decode + encode per camera and loses quality (a second lossy generation). Our cameras
already deliver the profile we want → **copy**.

### 4.2 Default traps (only active when transcoding)

| Default | Source | Effect |
|---|---|---|
| `crf: "1"` | UI default + `e.details.crf ? … : '1'` in `buildMainRecording()` | near-lossless re-encode: bigger than the original, 1.4 cores |
| no `preset_record` | → x264 `medium` | slowest standard preset |
| monitor `fps: "1"` | UI default (yard/waterway had it) | transcoding adds `-vf fps=1` → **1-frame-per-second recording** |
| x264 defaults | `keyint=250`, scene-cut keyframes, `bframes=3`, High profile | breaks the camera profile (§4.3) |
| `details.cust_record` | appended to the recording output **even with copy** | don't leave encoder flags in it when switching back to copy |

`-crf` is placed before `-an -vcodec`: `… s.m3u8 -crf 1 -an -vcodec libx264 -vf fps=15 …`.
Output options may come in any order before the output file name.

### 4.3 What the default transcode did to the files

```
$ uv run python -m tools.streamprobe gate-x264-crf1.mp4 --seconds 70
stream:     h264 High 1280x720 yuv420p @ 15 fps
keyframes:  4
GOP frames: min 250 / max 250 / mean 250
GOP secs:   min 16.7 / max 16.7 / mean 16.7
B-frames:   yes
keyframes at PTS 0.0  16.7  33.3  50.0
segments:   37 s, 67 s, 50 s   ← cut only at the first keyframe after each 60 s boundary
```

## 5. Retention: two separate mechanisms

### 5.1 By age: cron worker (`libs/cron/worker.js`)

```
camera.js start ─▶ worker thread ─▶ doCronJobs() at start, then every cron.interval h (1 h)
                                     for each admin account:
                                       monitors with details.max_keep_days: delete their videos older than that
                                       all other monitors: delete videos older than account details.days (default 5)
                                       Logs: log_days (10) · Events: event_days (10) · Timelapse: timelapseFrames_days (60)
                                       rows with archive = 1 are skipped
```

- **Whole days only.** The cutoff is `date.setDate(date.getDate() - days)`, and JavaScript's
  `setDate()` truncates its argument, so 0.01 → 1 day back and 1.5 → 2 days back (proved in the
  container's Node). `parseFloat` accepts fractions, but they round up to whole days.
- Runs only at startup and hourly: age pruning can be up to ~1 h late.
- **No log line.** The code checks `affectedRows.length > 0`, but `affectedRows` is a number, so the
  "N SQL rows older than … deleted" message never fires (except with `debugLog`).

Test (planted copies of a real segment named with old dates, then restart; the orphan scan inserted
them at 11:29:31, cron ran at 11:29:33):

```
cam_yard 2026-10-20T12-00-00 (2 d)  yard max_keep_days 0.01 → really 1 day   DELETED
cam_gate 2026-10-16T12-00-00 (6 d)  account days unset → default 5           DELETED
cam_gate 2026-10-20T12-00-00 (2 d)  2 < 5                                     kept (goes with days = 1)
```

### 5.2 By size: after every finished segment (`libs/user/utils.js` `deleteMainVideos`)

```
threshold = size (MB, default 10000) × size_video_percent (default 90 %) × cron.deleteOverMaxOffset (0.9)
            MB here = MiB (Shinobi divides bytes by 1 048 576)

after each insertCompletedVideo:
  while usedSpaceVideos > threshold        (or the filesystem holding videos is ≥ 95 % full, max 10 rounds)
      delete the 3 OLDEST videos of the whole GROUP (all monitors), status ≠ 0, archive ≠ 1, no custom dir
```

- `usedSpaceVideos` is Shinobi's own counter (sum of `Videos.size` at startup, then ±). It matched
  `du` within the timelapse JPEGs (2736 MiB in `Videos`).
- The account save (`POST /<auth>/accounts/<ke>/edit`, form field `data` = JSON) **merges**
  `details` and updates the running limit (`s.group[ke].sizeLimit`) immediately.

Test: `size` 3000 → threshold 2430 MiB, with 2736 MiB stored. The next finished segment triggered:

```
batch 1:  cam_gate 10-17 18:53:59 · cam_gate 10-18 12:38:43 · cam_gate 10-18 12:45:01
batch 2:  cam_gate 10-18 13:00:02 · cam_gate 10-22 10:51:10 · cam_quay 10-22 10:51:13
→ 2374 MiB < 2430 → stop          (silent: no Logs row; only a websocket "video_delete")
```

Steady state a few minutes later:

```
            oldest kept   stored     bitrate
cam_gate    10:56:00      470 MiB    2   Mb/s
cam_quay    10:55:02      916 MiB    4   Mb/s
cam_waterway 10:55:01     686 MiB    3   Mb/s
cam_yard    10:55:00      343 MiB    1.5 Mb/s      total 2402–2427 MiB, sawtooth under 2430
```

**Same time depth for every camera, space in proportion to bitrate.** Different retention per
camera is only possible with `max_keep_days` (whole days).

### 5.3 Sizing for this fleet

```
camera         bitrate    per hour    per day
cam-gate       2   Mb/s   0.90 GB     21.6 GB    (10-18 file: 225 MB / 15 min ✓)
cam-quay       4   Mb/s   1.80 GB     43.2 GB
cam-yard       1.5 Mb/s   0.68 GB     16.2 GB
cam-waterway   3   Mb/s   1.35 GB     32.4 GB
total         10.5 Mb/s   4.7 GB/h   113  GB/day   ≈ 75 MiB/min

Shinobi defaults: days 5 → would need ~567 GB (disk has 297 GB free) ✗
                  size 10000 → threshold 8100 MiB → reached after ~1.8 h: the SIZE cap really decides
ours:             size 20000 → 16 200 MiB ≈ 3.6 h of all 4 cameras; days 1 as a second limit
```

Rule for a real port: compute bitrate × cameras × required days first, then set both limits on
purpose. Otherwise the size default silently decides retention, or (limit raised to "the disk") the
95 % disk-pressure check is the only brake.

## 6. Wall clock vs camera timestamps: decided

Open since Session 2 (absolute timestamps) and Session 9 (`-use_wallclock_as_timestamps 1` jitter).
Measured on 60 s copy recordings of `cam_quay` (15 fps, nominal step 66.67 ms):

```
                         frames  duration  PTS step ms (min / max / stdev)   exactly on time
wall clock ON  (default)  900    59.96 s    9.2 / 153.3 / 21.6                  3 / 899
camera timestamps         900    59.93 s   66.7 /  66.7 /  0.0                899 / 899
```

The MP4 stores a duration per frame (`stts` table), so wall-clock jitter becomes permanent judder
in the recording. **Decision: `wall_clock_timestamp_ignore = 1` (camera timestamps) for all our
monitors.** Our simulated cameras have perfect, continuous timestamps (Session 4). The switch is
per monitor, so a real camera with a broken clock can go back to wall clock. Absolute times come
from the file name (§2.3) and from event timestamps, not from PTS. Side effect: streamprobe on the
live HLS now reports the true 15 fps (Session 9: "30 fps").

Observation: the first full file after switching `cam_quay` had 870 frames and the next 930 (no
frame lost, the boundary landed one GOP early once). Every later file had 900. Keyframe arrival at
Shinobi was regular (95 keyframes in 190 s, all 2.0 ± 0.1 s apart), so the camera's loop boundary
(the quay clip is 9.36 s) is not the cause. Cause not determined; harmless.

## 7. CPU measurements

`scripts/shinobi-cpu.sh SECONDS RUNS`: CPU time per process (`/proc/<pid>/stat` utime + stime)
and per container (cgroup v2 `cpu.stat` `usage_usec`) between two snapshots. 100 % = one core.

**Conditions matter.** Intel Core Ultra 7 265U = 2 P-cores with HT (CPU 0–3, ≤ 5.4 GHz) + 8 E-cores
(CPU 4–11, ≤ 4.3 GHz) + 2 LP E-cores (CPU 12–13, ≤ 2.4 GHz). All measurements here were taken **on
battery, platform profile `quiet`**, cores at 1.1–1.9 GHz, threads spread over all 14 CPUs. The same
work costs a different number of CPU seconds depending on the core and clock: one window of the
record-copy baseline came out 2.5× higher across all processes. Hence 3 runs per state, and
comparisons within one window (control group). Absolute numbers are higher than on a server.

| State (4 monitors) | per ffmpeg | container total |
|---|---|---|
| Watch-Only, copy (HLS only) | gate 0.8 · quay 1.1–1.2 · waterway 0.9 · yard 0.6 % | **4.7 %** (3 runs: 4.7/4.7/4.8) |
| Record copy, 1-min segments | +0.1 each | 9.1–9.4 % (+4.5 % = thumbnail ffmpeg, §3.2) |
| Record copy, 15-min segments (final) | gate 0.9 · quay 1.4 · waterway 1.0 · yard 0.7 % | **5.4 %** (3 runs: 5.4/5.4/5.4), 349 MiB RAM |

Copy-mode cost tracks bitrate (yard 1.5 Mb/s → 0.6 %, quay 4 Mb/s → 1.2 %): it only moves bytes.
For comparison, the camera containers, which *encode*, use 0.7–1.2 cores each (`docker stats`).

### 7.1 Decode cost: main vs sub stream (what a detector pays)

FFmpeg in the Shinobi container, `-i rtsp://mediamtx:8554/<path> -t 30 -an -f null -`, main and
sub in parallel (same clock conditions), 2 runs each:

```
                    pixels/s   bitrate    CPU per 30 s        sub saves
cam-gate     main   13.8 M     2 Mb/s     3.53 s = 11.8 %  ┐
cam-gate-sub        2.3 M    384 kb/s     1.03 s =  3.4 %  ┘  3.4×
cam-quay     main   13.8 M     4 Mb/s     5.80 s = 19.3 %  ┐
cam-quay-sub        2.3 M    384 kb/s     1.09 s =  3.6 %  ┘  5.3×
```

- Decode cost depends on **pixels and bits**: same resolution, but quay (4 Mb/s) costs 1.65× gate.
  The entropy decoder (CABAC) works per bit.
- The sub stream saves 3.4–5.3×, not the 6× pixel ratio: fixed per-stream cost (RTSP, parsing,
  process).
- Decoding main (11–19 %) costs 15–20× more than copy-recording it. Detection on 4 cameras ≈ 60 %
  of a core on main vs ≈ 14 % on sub, before any detector runs → Sessions 14–15 detect on sub.
- `Missing reference picture` / `decode_slice_header error` at start = joining mid-GOP: MediaMTX
  starts a reader at the current packet, so P-frames arrive before the first IDR (≤ 2 s). Harmless.

How Shinobi would use the sub stream: **input maps** (`details.input_maps` + 
`input_map_choices.detector`) add a second `-i` to the same FFmpeg and route the detector outputs
(`pipe:3`/`pipe:4`) to it: record main, detect sub. `details.substream` is a different feature: a
separate on-demand FFmpeg for live view only.

## 8. Cheat sheet

```bash
# recordings per monitor (Shinobi's view)
docker compose exec shinobi mysql ccio -e \
  "select mid, count(*) n, round(sum(size)/1048576) MiB, min(time) oldest, max(time) newest from Videos group by mid"
# account retention settings
docker compose exec shinobi mysql ccio -N -B -e "select json_value(details,'$.days'), json_value(details,'$.size') from Users"
# files on disk
docker compose exec shinobi sh -c 'du -sm /home/Shinobi/videos/*/cam_*'
# is a recording playable? (frames, or "moov atom not found")
docker compose exec shinobi sh -c 'ffprobe -v error -select_streams v -count_packets \
  -show_entries stream=nb_read_packets -of csv=p=0 /home/Shinobi/videos/*/cam_gate/<file>.mp4'
# recording part of the running command
docker compose exec shinobi sh -c 'for p in $(pgrep -x ffmpeg); do tr "\0" " " < /proc/$p/cmdline | grep -o "\-f segment.*"; done'
# CPU per process + container
scripts/shinobi-cpu.sh 60 3
# cron (age retention) ran?
docker compose logs shinobi | grep "CRON.js LOG"
```

## Gotchas collected

| Symptom | Cause | Fix |
|---|---|---|
| Retention seems to be 5 days, but footage is gone after ~2 h | default `size` 10000 MB → size cap at 8100 MiB decides first | set `size` and `days` on purpose (§5.3) |
| `max_keep_days 0.5` keeps a whole day | `setDate()` truncates: whole days, rounded up | use whole days |
| Videos deleted, nothing in the logs | size purge doesn't log; cron's log check is broken (`affectedRows.length`) | own audit later (Session 25) |
| Transcode recording huge and 1.4 cores | default CRF 1, preset medium | CRF ~23, `veryfast`, or simply copy |
| Transcoded recording at 1 fps | monitor `fps` = 1 (UI default) → `-vf fps=1` | set the real fps |
| Transcoded files have 16.7 s GOP, B-frames, uneven segment lengths | x264 defaults | `cust_record` `-g 2×fps -keyint_min 2×fps -sc_threshold 0 -bf 0 -profile:v main` |
| `moov atom not found` | file still being written, or FFmpeg was killed | wait for close; for crash safety fragmented MP4 (Session 25) |
| `docker compose restart/stop` breaks the current recording of every camera | container stop kills FFmpeg hard | stop monitors first; fragmented MP4 |
| Same video listed twice | orphan scan at startup inserted a row for a file still being written | dedupe by (mid, time) in our client |
| `Videos` row for an unplayable file | row inserted after a hard kill | check playability before using a video |
| `ps`/`top` show less CPU than `docker stats` | short-lived thumbnail ffmpeg per segment | measure with cgroup counters |
| CPU numbers jump 2× between runs | laptop on battery, `quiet` profile, P/E-cores | same conditions, control group, several runs |

## Open questions

- Fragmented MP4 for recordings: does Shinobi's pipeline (thumbnail at 7 s, `faststart`, the
  browser player) accept `movflags=+frag_keyframe+empty_moov`? → Session 25.
- Cause of the one-time 870/930 frame split after switching to camera timestamps (§6).
- Graceful container shutdown: can `stop_grace_period` + Shinobi's own stop path finalize the
  segments? → Session 25.
