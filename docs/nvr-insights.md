# Building a Shinobi-like NVR — insights from the notes

Lessons from `docs/notes/` for our own NVR (network video recorder: pulls camera streams, records,
detects, publishes events). Each bullet points back to where it was observed.

## How to read and maintain this file

**References** `[09 §10 L446](notes/09-shinobi-monitor.md#L446)` = note 09, section 10, line 446.
Line numbers are from the commit that last updated the bullet; if a note was edited since, use the section.

| Tag | File | Tag | File |
|---|---|---|---|
| 01 | notes/01-repo-bootstrap.md | 08 | notes/08-shinobi-install.md |
| 02 | notes/02-video-fundamentals.md | 09 | notes/09-shinobi-monitor.md |
| 03 | notes/03-port-footage.md | 10 | notes/10-recording-storage.md |
| 04 | notes/04-first-rtsp-camera.md | 11 | notes/11-shinobi-api.md |
| 05 | notes/05-camera-fleet.md | 12 | notes/12-monitors-as-code.md |
| 06 | notes/06-realism.md | CP | camera-profile.md |
| 07 | notes/07-fault-injection.md | A1, A2 | adr/0001-…, adr/0002-… |
| LP | learning-plan.md | | |

**[+]** = my addition, not observed in a note (best practice or inference). Verify before relying on it.

**Updating.** When a new note lands in `docs/notes/`:
1. Read it, add bullets under the matching sections (new bullets at the end of a section).
2. Mark bullets a new finding contradicts or resolves (`~~old~~ → see [13 §x]`), don't delete them silently.
3. Add the note to the coverage table and a line to the changelog at the bottom.

**Coverage.** Ingested: 01, 02, 03, 04, 05, 06, 07, 08, 09, 10, 11, 12, CP, A1, A2, theory/.
Skipped on purpose: `*-explained.md` (long-form versions of the same sessions).

---

## 1. Use cases

- **Ingest:** pull each camera's RTSP stream and reconnect when it drops. [08 §1 L33-40](notes/08-shinobi-install.md#L33)
- **Continuous recording** in fixed-length segments using stream copy (no re-encoding). [09 §5.3 L309](notes/09-shinobi-monitor.md#L309), [10 §1 L36](notes/10-recording-storage.md#L36)
- **Event-triggered recording:** a watch-only monitor that records only while an event (motion, object) is active. [09 §8 L414](notes/09-shinobi-monitor.md#L414)
- **Live view in the browser.** Browsers can't play RTSP, so the stream must be re-packaged (HLS, FLV, fMP4, MJPEG). [09 §7 L371](notes/09-shinobi-monitor.md#L371)
- **Detection on the sub stream** (motion, objects). Recording uses the main stream. [06 §2 L48](notes/06-realism.md#L48), [10 §7.1 L389](notes/10-recording-storage.md#L389)
- **Events out** to other systems (webhook, MQTT, REST, WebSocket). The twin depends on this. [08 §1 L39](notes/08-shinobi-install.md#L39), [11 §1 L29](notes/11-shinobi-api.md#L29)
- **Search and playback by time range**, with explicit window semantics ("started in" vs "fully inside"). [11 §4 L160](notes/11-shinobi-api.md#L160)
- **Evidence export** with trustworthy absolute time (OSD, file name, event time). [06 §4 L184](notes/06-realism.md#L184), [10 §2.3 L112](notes/10-recording-storage.md#L112)
- **Snapshots and thumbnails** for grids, reports and polling detectors. [06 §5 L316](notes/06-realism.md#L316), [10 §3.2 L164](notes/10-recording-storage.md#L164)
- **Retention** by age and by size, plus "archive / protect this clip". [10 §5 L260](notes/10-recording-storage.md#L260), [10 §3 L155](notes/10-recording-storage.md#L155)
- **Camera health:** status per camera (starting / recording / died …) pushed to clients. [09 §1 L39](notes/09-shinobi-monitor.md#L39), [11 §7 L274](notes/11-shinobi-api.md#L274)
- **Telling "camera down" apart from "nothing happening"**, so downstream logic doesn't conclude an empty yard. [07 §1 L34](notes/07-fault-injection.md#L34)
- **Multi-tenant:** groups, users with permissions, service API keys. [08 §7 L229](notes/08-shinobi-install.md#L229), [11 §2 L53](notes/11-shinobi-api.md#L53)
- **Monitors as code:** declarative config, plan/apply, drift report. [12 §6 L213](notes/12-monitors-as-code.md#L213)
- **Timelapse** frames per camera. [09 §4 L147](notes/09-shinobi-monitor.md#L147)
- **[+]** Audit trail: who viewed, exported or deleted which footage (hinted by Shinobi's silent deletes, [10 L443](notes/10-recording-storage.md#L443)).
- **[+]** Clock/NTP health per camera and per server, since evidence depends on it.

## 2. Expected problems

### 2.1 Ingest and camera faults
- Two failure families: **close** (TEARDOWN/FIN, noticed at once) and **silence** (hang, power loss, cable cut: nothing arrives). Silence is only caught by a timeout. [07 §2 L38-51](notes/07-fault-injection.md#L38)
- Without a read timeout, a frozen camera looks "online" forever and the recorder writes an endless empty segment. [07 §2 L103](notes/07-fault-injection.md#L103)
- Liveness = "data keeps flowing" (a progress heartbeat that resets a 60 s timer), not "the process exists". [09 §5.1 L206](notes/09-shinobi-monitor.md#L206), [09 §10 L450](notes/09-shinobi-monitor.md#L450)
- **Back-off trap:** 3 quick failures → 10 min wait, even though the camera is back after 15 s. Use a capped back-off and keep probing. [09 §10 L446-480](notes/09-shinobi-monitor.md#L446)
- A TCP connect test proves only that the *server* is up (MediaMTX listens with no publisher → 404 later). Probe with `DESCRIBE` instead. [09 §10 L453](notes/09-shinobi-monitor.md#L453)
- Over TCP, loss turns into delay. 30 % loss or 200 ± 50 ms jitter cuts throughput to 0.3 Mb/s (cwnd collapse), so the stream falls behind and then times out. [07 §4 L160-205](notes/07-fault-injection.md#L160)
- A frame-count health check misses a constant backlog (latency). Compare the OSD clock with host time separately. [07 §5 L242](notes/07-fault-injection.md#L242)
- A reader joining mid-GOP can't decode up to 1 GOP of frames, so every start costs ≤ 1 GOP. Health windows need +1 GOP. [07 §5 L227](notes/07-fault-injection.md#L227), [10 L408](notes/10-recording-storage.md#L408)
- An SDP that announces a track which never sends data (muted audio) makes probing run to its limits, so the monitor "starts" but stays black. [09 §5.1 L237-244](notes/09-shinobi-monitor.md#L237)
- The first probe right after a (re)connect can lack timestamps. Retry once before raising an alarm. [06 §6 L370](notes/06-realism.md#L370)
- Real cameras allow only 2–4 RTSP sessions, so use one pull per camera and fan out internally. [10 §1 L59](notes/10-recording-storage.md#L59)
- Cameras may send B-frames, long GOPs or scene-cut keyframes. These break segment lengths and live latency, so check them when a camera is added. [02 L29-32](notes/02-video-fundamentals.md#L29), [09 §5.2 L291](notes/09-shinobi-monitor.md#L291), [CP L13-25](camera-profile.md#L13)
- Over UDP, one lost FU-A fragment drops the NAL, and the damage spreads until the next IDR (a loss in the IDR ruins the whole GOP). [02 L39-43](notes/02-video-fundamentals.md#L39)

### 2.2 Timestamps and time
- Wall-clock (arrival) timestamps jitter, and the jitter is baked into the MP4 frame durations. Camera PTS is exact, but cameras with broken clocks need wall clock. Make it a per-camera switch. [10 §6 L345-361](notes/10-recording-storage.md#L345), [09 §5.1 L226](notes/09-shinobi-monitor.md#L226)
- Jittered timestamps also fool frame-rate guesses (15 fps reported as 30). [09 §5.1 L229](notes/09-shinobi-monitor.md#L229)
- With `reset_timestamps`, absolute time lives only in the file name (1 s resolution, + ≤ 1 GOP cut delay). Precise times must come with the events. [10 §2.3 L112](notes/10-recording-storage.md#L112)
- RTCP sender-report time is the camera's clock (trustworthy only if NTP-synced) and FFmpeg mostly ignores it, so stamp at ingest on an NTP-synced host. [02 L45-48](notes/02-video-fundamentals.md#L45)
- The OSD clock ≠ PTS: PTS restarts on reconnect or remux, while the burned-in OSD survives export. [06 §4 L184-198](notes/06-realism.md#L184)
- Datetimes without a zone are read in the server's local zone. Accept UTC / explicit offsets only. [11 §4 L174](notes/11-shinobi-api.md#L174)

### 2.3 Recording
- With copy, a segment can only start at an IDR, so cuts land ≤ 1 GOP late. A long GOP (16.7 s) gives 37/67/50 s files instead of 60 s. [10 §2.1 L79-98](notes/10-recording-storage.md#L79), [10 §4.3 L249](notes/10-recording-storage.md#L249)
- A hard kill leaves a plain MP4 with no `moov` index, so the **whole segment is lost**. A container stop is a hard kill for every camera. [10 §2.2 L100](notes/10-recording-storage.md#L100), [10 §3.4 L200-213](notes/10-recording-storage.md#L200)
- The index points to broken files: the DB row is inserted even after a hard kill. [10 §3.4 L205](notes/10-recording-storage.md#L205)
- The startup orphan scan indexes files still being written, which creates duplicate rows and wrong used-space numbers. [10 §3.3 L186](notes/10-recording-storage.md#L186), [11 §4 L181](notes/11-shinobi-api.md#L181)
- The index lags by up to one segment length (rows exist only for finished files), so a push notification is needed. [11 §4 L178](notes/11-shinobi-api.md#L178)
- Segment length is a trade-off between visibility lag, crash loss, row count and CPU. [10 §2.4 L119](notes/10-recording-storage.md#L119)
- A per-segment thumbnail process is hidden CPU (+4.5 % with 1-min segments) that `ps`/`top` don't show. [10 §3.2 L164-184](notes/10-recording-storage.md#L164)
- A recorder can stall while its input still runs, which needs a second watchdog ("no finished segment in 1.3 × cutoff"). [10 §3.1 L157](notes/10-recording-storage.md#L157)
- Transcode defaults are traps: CRF 1 (2.3× size, 1.4 cores), `fps 1` → 1-fps recording, GOP 250 + B-frames. [10 §4.2 L234-245](notes/10-recording-storage.md#L234)
- Live view and recording share one process, so they share fate: a 10-min back-off is a 10-min recording gap. [10 §1 L61](notes/10-recording-storage.md#L61)

### 2.4 Retention and storage
- If nobody sets the size cap, it silently decides retention (≈ 1.8 h instead of 5 days). Calculate bitrate × cameras × days first. [10 §5.3 L326-343](notes/10-recording-storage.md#L326)
- Age retention in whole days only (`setDate` truncation), runs hourly → up to 1 h late. [10 §5.1 L273](notes/10-recording-storage.md#L273)
- Size purge deletes the oldest files across the whole group, giving equal time depth for every camera regardless of importance. [10 §5.2 L323](notes/10-recording-storage.md#L323)
- Deletions are silent (no log, a broken log check). [10 §5.1 L277](notes/10-recording-storage.md#L277), [10 §5.2 L310](notes/10-recording-storage.md#L310)
- The used-space counter is kept in RAM and drifts with duplicates. [10 §5.2 L300](notes/10-recording-storage.md#L300)

### 2.5 Live view
- HLS latency ≈ 7 s (player starts at the oldest of 3 × 2 s segments). Fixes: smaller list, FLV/fMP4, or WebRTC. [09 §6 L360-369](notes/09-shinobi-monitor.md#L360)
- MJPEG is the only live type that decodes (~17 % vs ~3 % CPU), and it runs even when nobody watches. [09 §7 L401](notes/09-shinobi-monitor.md#L401)
- A snapshot taken over RTSP costs connect + probe + ≤ 1 GOP (~4 s). [06 §5 L332-345](notes/06-realism.md#L332)

### 2.6 Configuration and API behaviour (Shinobi anti-patterns to avoid)
- **Every config edit restarts the camera** (4–7 s footage lost per save). [09 §8 L425](notes/09-shinobi-monitor.md#L425), [12 §4 L134-145](notes/12-monitors-as-code.md#L134)
- **Silent input rewriting:** strings JSON-parsed (`"0"` → `0`), characters stripped (`a;b` → `ab`), unknown keys dropped, ids changed (`cam-gate` → `camgate`). [09 §9 L429](notes/09-shinobi-monitor.md#L429), [12 §4 L101-131](notes/12-monitors-as-code.md#L101), [12 §2 L65](notes/12-monitors-as-code.md#L65)
- **Reads that invent data** (`temp_XXXXX` on each GET) make idempotent clients loop. [12 §5.2 L167-190](notes/12-monitors-as-code.md#L167)
- An update replaces the whole JSON blob, and INSERT/UPDATE treat `""`/NULL differently. [12 §2 L61](notes/12-monitors-as-code.md#L61), [12 §4 L131](notes/12-monitors-as-code.md#L131)
- **Failure answered with HTTP 200** (`{ok:false}`, `[]` for unknown id or missing permission). [11 §3 L104-119](notes/11-shinobi-api.md#L104)
- Silent `LIMIT 500` on list endpoints. [11 §4 L163](notes/11-shinobi-api.md#L163)
- A snapshot endpoint returns a beige placeholder JPEG with 200 when snapshots are off. [11 §6 L206-217](notes/11-shinobi-api.md#L206)
- Config fields that look like measurements (width/height/fps) are only descriptive and drift from reality. [11 §4 L155](notes/11-shinobi-api.md#L155), [09 §5.4 L325](notes/09-shinobi-monitor.md#L325)
- The status label is translated per language; only the numeric code is stable. [11 §4 L145](notes/11-shinobi-api.md#L145)
- Deleting a camera keeps its files by default, and re-creating the same id brings old footage back. [12 §3 L92](notes/12-monitors-as-code.md#L92)
- A fixed 2 s wait + ffprobe per save makes bulk provisioning O(n). [12 §4 L111](notes/12-monitors-as-code.md#L111), [12 §9 L323](notes/12-monitors-as-code.md#L323)
- Different UI paths store different defaults for the same field (`null` vs `0`, 10⁹ vs 10⁶ probesize), so drift stays invisible until it is diffed. [12 §6 L244-266](notes/12-monitors-as-code.md#L244)

### 2.7 Operations and processes
- PID 1: without an init, zombie ffmpeg processes pile up and SIGTERM is ignored. [08 §2 L75-89](notes/08-shinobi-install.md#L75), [05 §6 L476](notes/05-camera-fleet.md#L476)
- The shutdown grace period must cover all children (PM2 5 s, compose 30 s), or the DB and recorders get SIGKILLed. [08 §2 L83](notes/08-shinobi-install.md#L83)
- Slow startup (~25 s capability probing) means clients must poll readiness. [08 §3 L127](notes/08-shinobi-install.md#L127)
- `depends_on` orders startup, not readiness, so a retry loop or healthchecks are needed. [04 §7 L270-281](notes/04-first-rtsp-camera.md#L270)
- Bind-mounting a missing file creates a directory. Atomic temp+rename writes break single-file bind mounts. [08 §4 L158-167](notes/08-shinobi-install.md#L158)
- Config rewritten from env on every start, so file and env disagree. [08 §3 L119](notes/08-shinobi-install.md#L119)
- With the code in a data volume, `docker pull` silently doesn't upgrade. [08 §5 L169-180](notes/08-shinobi-install.md#L169)
- An in-memory brute-force lock (5 failures / IP / 15 min) blocks everyone behind one NAT/bridge IP. [08 §8 L256](notes/08-shinobi-install.md#L256)
- CPU numbers vary 2× with laptop power profile / P- vs E-cores. Use cgroup counters, control groups, ≥ 3 runs. [10 §7 L373-378](notes/10-recording-storage.md#L373)
- A killed ffprobe loses its buffered output. Let tools stop themselves (`-read_intervals`). [04 §6 L242-247](notes/04-first-rtsp-camera.md#L242)
- Docker DNS: `localhost` inside a container is the container itself, so use service names. [09 §2 L70](notes/09-shinobi-monitor.md#L70)

## 3. Security considerations

- **Keys in URL paths leak** (proxy logs, shell history, `Referer`, client debug logs, and in the server's own answers). Use an `Authorization` header plus short-lived signed URLs for `<video>`/`<img>`. [11 §2 L62](notes/11-shinobi-api.md#L62), [11 §5 L192-204](notes/11-shinobi-api.md#L192)
- **API keys stored in plain text** (a DB dump leaks every key). Store hashes. [11 §2 L72](notes/11-shinobi-api.md#L72)
- **IP allowlists must not trust `X-Forwarded-For` or match substrings.** Trust proxy headers only from known proxies. [11 §3 L97](notes/11-shinobi-api.md#L97)
- **Revocation must be immediate:** a deleted key may keep working from cache until restart. [11 §3 L95](notes/11-shinobi-api.md#L95)
- **Camera credentials leak through logs** (the full ffmpeg command with `rtsp://user:pass@`). Redact on every path, not only some. [11 §4 L149-153](notes/11-shinobi-api.md#L149), [09 §5.1 L257](notes/09-shinobi-monitor.md#L257)
- Don't write the whole server config into temp files for child processes (the 33 KB cmd file with `conf.json`). [09 §3 L101](notes/09-shinobi-monitor.md#L101)
- **WebSocket over-broadcasts:** account details and e-mail go to every socket of the group, even a single-camera API key. Authorize per message/room. [11 §7 L288](notes/11-shinobi-api.md#L288)
- The login response contains the password hash. Never return or log credential material. [08 §8 L259](notes/08-shinobi-install.md#L259)
- A default superuser `admin/admin` → force a change on first start (expose `usingDefaultPassword`-style check). [08 §3 L104](notes/08-shinobi-install.md#L104), [08 §8 L254](notes/08-shinobi-install.md#L254)
- DB with empty password, listening on all interfaces, protected only by account host rules. Use real credentials and a network policy. [08 §6 L210-221](notes/08-shinobi-install.md#L210), [A1 L46-50](adr/0001-shinobi-image-and-database.md#L46)
- Rate-limit logins per account + IP, persist the lock, and keep API-auth failures counted too (Shinobi doesn't count them). [08 §8 L256](notes/08-shinobi-install.md#L256), [11 §3 L102](notes/11-shinobi-api.md#L102)
- **Least privilege:** separate read and edit keys, scoped per tenant/camera; read access must not reveal camera addresses. [11 §2 L77](notes/11-shinobi-api.md#L77), [12 §3 L83-91](notes/12-monitors-as-code.md#L83)
- **Validate and reject input, never silently strip it.** Pass ffmpeg arguments as an argv list with no shell. [12 §4 L108](notes/12-monitors-as-code.md#L108), [05 §6 L426](notes/05-camera-fleet.md#L426), [09 §5 L155](notes/09-shinobi-monitor.md#L155)
- Bind published ports to `127.0.0.1` / internal networks. The RTSP server's default auth lets anyone publish or read. [04 §2 L62-78](notes/04-first-rtsp-camera.md#L62)
- Mount media read-only. Grant extra capabilities (NET_ADMIN) only to short-lived helpers. [04 §7 L263](notes/04-first-rtsp-camera.md#L263), [07 §3 L123-150](notes/07-fault-injection.md#L123)
- Secrets only in env/secret store, never in git, fixtures or error messages. Redact recorded fixtures and abort on any leak. [01 §5-6 L153-179](notes/01-repo-bootstrap.md#L153), [11 §5 L203](notes/11-shinobi-api.md#L203), [11 §8 L306](notes/11-shinobi-api.md#L306)
- **Recordings show people:** privacy/GDPR, access control, retention limits. [01 §5 L157](notes/01-repo-bootstrap.md#L157)
- **License / clean room:** Shinobi Pro is a source-available EULA (commercial use at ≥ 15 monitors, contributed code becomes theirs). Don't copy its code into our own NVR; learn behaviour from observation and docs. [A2 L13-30](adr/0002-shinobi-license.md#L13)
- Images can carry secrets (an upstream CI token in `git remote -v`). Never paste that output. [A2 L58](adr/0002-shinobi-license.md#L58)
- Pin images by digest, and pin downloaded assets by sha256 over https. [08 §5 L182](notes/08-shinobi-install.md#L182), [03 L7-15](notes/03-port-footage.md#L7)
- **[+]** TLS for UI/API and RTSPS/SRTP where cameras support it. Put cameras on an isolated VLAN.
- **[+]** Integrity of evidence: hash each finished segment, keep the hashes in an append-only log, and keep an export chain of custody.
- **[+]** Encryption at rest for recordings, plus an audit log of view, export and delete actions.

## 4. Implementation details

### 4.1 Architecture and languages
- **Keep the NVR replaceable behind the contract** (MQTT topics + JSON schemas). Downstream never knows which NVR produced an event. [A2 L42-46](adr/0002-shinobi-license.md#L42), [01 §1 L32](notes/01-repo-bootstrap.md#L32)
- **Media plane:** one worker per camera, **one input → many outputs** (live, record, detector frames, progress) from a single RTSP pull. [09 §5 L182-187](notes/09-shinobi-monitor.md#L182), [10 §1 L36](notes/10-recording-storage.md#L36)
- Start with an **FFmpeg subprocess per camera** (proven, isolates crashes). C++23 + libav pays off later in the detector/frame pipeline. [LP L274](learning-plan.md#L274), [LP L208](learning-plan.md#L208)
- **Side channels as fds/pipes:** progress heartbeat, "segment finished" list, detector frames (grey PAM for motion, JPEG for objects). [09 §4 L134-151](notes/09-shinobi-monitor.md#L134)
- Supervise children by **process group** (kill `-pid`) so no ffmpeg survives its worker. [09 §3 L106](notes/09-shinobi-monitor.md#L106)
- **Control plane** (API, config, retention, events) in Python/FastAPI, matching the repo conventions. **[+]** Go is a good alternative for a single static binary with many concurrent workers.
- **[+]** Consider delegating ingest/live view to MediaMTX (it proxies, records and serves WebRTC/HLS; see theory/MediaMTX.md) and building only the recording index, retention, detection and events.
- Detection reads the **sub stream** (3.4–5.3× cheaper to decode). The detector downscales to its input size anyway. [10 §7.1 L389-414](notes/10-recording-storage.md#L389), [06 §2 L72](notes/06-realism.md#L72)
- Prefer **push** (WebSocket/MQTT) for status and "segment finished". Polling misses short events. [11 §1 L29-37](notes/11-shinobi-api.md#L29)
- **[+]** Plain WebSocket + JSON (or MQTT over WS) rather than socket.io, whose layered protocol needs special clients. [11 §7 L223-235](notes/11-shinobi-api.md#L223)

### 4.2 Media settings
- Record with **stream copy** (0.8 % vs 40–141 % CPU per camera). Transcode only to save storage or fix a bad stream, and then with explicit CRF/preset/GOP. [10 §4 L215-246](notes/10-recording-storage.md#L215)
- **Fragmented MP4** (`+frag_keyframe+empty_moov`) so a crash loses at most one fragment. [10 §3.4 L210](notes/10-recording-storage.md#L210)
- Segment muxer: cut at wall-clock boundaries, strftime names in UTC, timestamps reset per file, announce on close. [10 §2 L66-77](notes/10-recording-storage.md#L66)
- Live segments in **tmpfs** (disposable, no disk wear). [09 §5.2 L306](notes/09-shinobi-monitor.md#L306), [A1 L21-29](adr/0001-shinobi-image-and-database.md#L21)
- Validate each camera on add: H.264 Main, yuv420p, no B-frames, fixed GOP ≈ 2 s. Warn, don't reject (real cameras vary). [CP L13-25](camera-profile.md#L13), [05 §4.5 L294](notes/05-camera-fleet.md#L294)
- RTSP over TCP by default. Offer UDP per camera. [09 §5.1 L250](notes/09-shinobi-monitor.md#L250), [07 Open L310](notes/07-fault-injection.md#L310)
- Bounded probing (`analyzeduration`/`probesize` ~1 s / 1 MB), not 1000 s / 1 GB. [09 §5.1 L237](notes/09-shinobi-monitor.md#L237), [12 §6 L259](notes/12-monitors-as-code.md#L259)

### 4.3 Data model and API
- Key everything by `(tenant, camera_id)`. Validate ids strictly (`^[a-z0-9_-]+$`) and reject anything else instead of rewriting it. [11 §2 L53-56](notes/11-shinobi-api.md#L53), [05 §3 L127](notes/05-camera-fleet.md#L127)
- Separate **config** (stored) from **runtime state** (status, viewers, streams) in the schema and the API. [11 §4 L135-143](notes/11-shinobi-api.md#L135), [12 §1 L30](notes/12-monitors-as-code.md#L30)
- Typed, versioned config schema with PATCH semantics, instead of a 212-key blob replaced on every save. [12 §2 L61](notes/12-monitors-as-code.md#L61), [06 §3 L152](notes/06-realism.md#L152)
- **Hot-apply** settings that don't need a new pull (retention, detector zones, labels). Restart only when input/output really change. [12 §4 L117](notes/12-monitors-as-code.md#L117)
- **Reads are pure:** GET returns exactly what is stored, so the config reaches a fixed point. [12 §5 L147-155](notes/12-monitors-as-code.md#L147)
- Correct HTTP status codes (401/403/404), explicit pagination, no silent caps. [11 §3 L113-119](notes/11-shinobi-api.md#L113)
- Stable numeric status enum. Labels are presentation only. [11 §4 L145](notes/11-shinobi-api.md#L145)
- Video index: unique `(camera, start)`, a state `writing|complete|broken`, size/duration from the closed file, a playability check before use. [10 §3 L153](notes/10-recording-storage.md#L153), [11 §4 L189](notes/11-shinobi-api.md#L189)
- Snapshot endpoint: 404/409 when unavailable, never a placeholder. [11 §6 L219](notes/11-shinobi-api.md#L219)
- Time: UTC everywhere, ISO 8601 with `Z`, reject naive times. [06 §4 L175](notes/06-realism.md#L175), [11 §4 L174](notes/11-shinobi-api.md#L174)
- Width/height/fps reported from the **measured** stream, not from config. [11 §4 L155](notes/11-shinobi-api.md#L155)

### 4.4 Retention
- One retention service: per-camera budgets (time and/or bytes), fractional days, archive flag, **every deletion logged/published**. [10 §5 L260-343](notes/10-recording-storage.md#L260)
- Compute used space from the filesystem/index, not a drifting RAM counter. [10 §5.2 L300](notes/10-recording-storage.md#L300)
- Delete-camera API: explicit `keep|delete` footage choice. [12 §3 L92](notes/12-monitors-as-code.md#L92)

### 4.5 Config as code
- One source of truth for the camera list, used by simulator, NVR provisioning, tests and twin. [05 §1 L41-49](notes/05-camera-fleet.md#L41)
- Pure `render()`, generated files committed, `--check` / golden tests. [05 §3 L113](notes/05-camera-fleet.md#L113), [05 §9 L543](notes/05-camera-fleet.md#L543)
- `plan` / `apply` / `export`, exit code 2 = changes pending, change only what differs. [12 §6 L213-241](notes/12-monitors-as-code.md#L213)
- Validation errors name the camera, not a list index. [05 §3 L150-163](notes/05-camera-fleet.md#L150)
- Strict types (`StrictBool`), schema `version` field, reject old versions explicitly. [06 §3 L151-152](notes/06-realism.md#L151)

### 4.6 Testing
- Three speeds: unit / integration (needs ffmpeg) / e2e (needs stack, opt-in so a missing stack fails loudly). [01 §7 L208-214](notes/01-repo-bootstrap.md#L208), [07 §5 L245](notes/07-fault-injection.md#L245)
- **Recorded real payloads** as fixtures (redacted). Fakes must reproduce real quirks or tests pass while production loops. [11 §8 L325-337](notes/11-shinobi-api.md#L325), [12 §5.2 L206-211](notes/12-monitors-as-code.md#L206)
- Camera **simulator** (looped clips, OSD clock, main + sub) plus **chaos** (stop, crash, freeze, flap, netem) as standard test inputs. [05](notes/05-camera-fleet.md), [06](notes/06-realism.md), [07 §6 L281-290](notes/07-fault-injection.md#L281)
- Loop-safe clips: no B-frames, no audio, first PTS 0, starts on IDR, CFR. [04 §4 L136-146](notes/04-first-rtsp-camera.md#L136), [CP L13-25](camera-profile.md#L13)
- Stream health test: frame count over window + 1 GOP, size/codec, wall-clock budget. [07 §5 L207-247](notes/07-fault-injection.md#L207)
- DTS continuity check across loop seams (strictly increasing, max gap). [04 §6 L208-238](notes/04-first-rtsp-camera.md#L208)
- Crash-safety tests: `kill -9` the recorder, restart the container, then check the files play and the index is correct. [10 §3.4 L200](notes/10-recording-storage.md#L200)
- Idempotency test: second apply → 0 changes, 0 restarts; export byte-identical. [12 §7 L268-294](notes/12-monitors-as-code.md#L268)
- Resource benchmarks with cgroup CPU counters, ≥ 3 runs, control group. [10 §7 L368-387](notes/10-recording-storage.md#L368)
- **[+]** Property tests for the index/retention logic (random segment streams, crashes, clock jumps).

### 4.7 Docker
- `init: true` (tini), signal trap in wrappers, `stop_grace_period` long enough to finalize segments. [05 §6 L403-481](notes/05-camera-fleet.md#L403), [08 §2 L83](notes/08-shinobi-install.md#L83)
- Code in the image, only data mounted. Pin by digest. [08 §5 L169-188](notes/08-shinobi-install.md#L169), [A1 L40-42](adr/0001-shinobi-image-and-database.md#L40)
- Named volumes for DB/recordings. Never `down -v`. Seed bind-mounted files before first start. [08 §4 L153-167](notes/08-shinobi-install.md#L153), [A1 L52](adr/0001-shinobi-image-and-database.md#L52)
- DB as its own service (Shinobi's built-in DB blocks scaling). [A1 L33-35](adr/0001-shinobi-image-and-database.md#L33), [A1 L51](adr/0001-shinobi-image-and-database.md#L51)
- Healthchecks + `depends_on: condition: service_healthy`. [04 §7 L281](notes/04-first-rtsp-camera.md#L281)
- No `container_name` (blocks `--scale`). Use labels for discovery, `--remove-orphans`. [05 §5 L383-399](notes/05-camera-fleet.md#L383), [05 §8 L528](notes/05-camera-fleet.md#L528)
- Exec-form commands (lists), no shell parsing. [05 §6 L426](notes/05-camera-fleet.md#L426)

### 4.8 Kubernetes and scaling
- Per-camera cost to budget: copy-record ≈ 1 %, ~100 MB RSS per camera (two processes); decode main 12–19 %, sub 3.4 %; transcode 40–141 %. [09 §4 L122](notes/09-shinobi-monitor.md#L122), [10 §7 L380-406](notes/10-recording-storage.md#L380)
- Storage sizing: 10.5 Mb/s for 4 cameras ≈ 113 GB/day. [10 §5.3 L328-339](notes/10-recording-storage.md#L328)
- The provisioner is a reconcile loop, the same pattern as a Kubernetes controller. [12 §1 L34](notes/12-monitors-as-code.md#L34)
- Port scale = hundreds of cameras (Shinobi license needs Enterprise > 150), so design for sharding from the start. [A2 L19-22](adr/0002-shinobi-license.md#L19), [07 §1 L22](notes/07-fault-injection.md#L22)
- **[+]** Camera workers as a sharded StatefulSet (or an operator with a `Camera` CRD), with consistent hashing of cameras to pods and rebalancing on pod loss.
- **[+]** Liveness probe = progress heartbeat; readiness = all workers started; live segments on `emptyDir: {medium: Memory}`.
- **[+]** Recordings on local PVs (high write rate), later tiered to object storage (S3/MinIO) for retention.
- **[+]** Detectors as a separate GPU-node Deployment fed from sub streams via a queue; backpressure drops frames, never blocks recording.
- **[+]** Metrics (Prometheus): per camera fps in, bytes written, reconnects, segment lag, index/disk mismatch.

## 5. Implementation plan (priorities)

P0 = must work before anything else; each step has a "done when".

| Prio | Step | Done when | Based on |
|---|---|---|---|
| P0 | Camera registry as code (schema v1, strict validation, plan/apply/export) | apply twice → 0 changes; ids rejected, not rewritten | [12](notes/12-monitors-as-code.md), [05](notes/05-camera-fleet.md) |
| P0 | Ingest worker: 1 pull per camera, ffmpeg copy, progress heartbeat, read timeout, capped back-off with DESCRIBE probe | all chaos faults recover ≤ 10 s after the camera is back | [07](notes/07-fault-injection.md), [09 §10](notes/09-shinobi-monitor.md#L442) |
| P0 | Segment recording in fMP4, UTC names, wall-clock cuts, per-camera timestamp mode | `kill -9` loses ≤ 1 fragment; segments = n × GOP | [10 §2-3, §6](notes/10-recording-storage.md#L66) |
| P0 | Video index (unique key, state, playability) + "segment finished" event | no duplicates after restart; broken files flagged | [10 §3](notes/10-recording-storage.md#L132), [11 §4](notes/11-shinobi-api.md#L181) |
| P0 | Auth: hashed keys in header, scoped permissions, real status codes, redaction everywhere | no secret in any URL, log or answer (test greps for it) | [11 §2-3](notes/11-shinobi-api.md#L42), [3](#3-security-considerations) |
| P1 | Retention service with per-camera budgets + deletion audit events | the test fills disk; oldest deleted, every deletion logged | [10 §5](notes/10-recording-storage.md#L260) |
| P1 | Status + events out (WebSocket push, MQTT per the contract) | camera died/recovered and segment-finished arrive < 1 s | [11 §7](notes/11-shinobi-api.md#L223), [A2](adr/0002-shinobi-license.md#L42) |
| P1 | Live view: low-latency (fMP4/WebRTC via MediaMTX), HLS fallback | latency < 2 s measured via OSD vs host clock | [09 §6-7](notes/09-shinobi-monitor.md#L332) |
| P1 | Snapshots from sub stream / camera HTTP, honest errors | no placeholder ever returned | [11 §6](notes/11-shinobi-api.md#L206), [06 §5](notes/06-realism.md#L316) |
| P2 | Motion detection on sub stream (zones, thresholds, hot-applied) | sane event rate on port clips, no restart on tuning | [06 §2](notes/06-realism.md#L48), [LP S14](learning-plan.md#L152) |
| P2 | Object detection via detector service (shared `Detection` schema) | labels + confidences on events | [LP S15, S23](learning-plan.md#L158) |
| P2 | Event-triggered recording with pre/post buffer | clip contains the 5 s before the event | [09 §8](notes/09-shinobi-monitor.md#L414) |
| P2 | Multi-tenant users/groups, RBAC, login rate limit, audit log | permission matrix tests pass | [08 §7-8](notes/08-shinobi-install.md#L229) |
| P3 | Scaling: sharded workers, separate DB, object storage, Prometheus | 16+ cameras, first bottleneck measured | [LP S34](learning-plan.md#L273), [4.8](#48-kubernetes-and-scaling) |
| P3 | Evidence export (hash chain, OSD, metadata), UI | exported clip verifiable offline | [06 §4](notes/06-realism.md#L184) |

## 6. Open questions carried from the notes

- Does fragmented MP4 work with thumbnails, faststart and the browser player? [10 Open L456](notes/10-recording-storage.md#L456)
- Can a graceful shutdown finalize all open segments? [10 Open L459](notes/10-recording-storage.md#L459)
- One-time 870/930 frame split after switching to camera timestamps: cause unknown. [10 §6 L363](notes/10-recording-storage.md#L363)
- Does a deleted key keep working until restart (cache)? [11 §9 L344](notes/11-shinobi-api.md#L344), [12 §9 L322](notes/12-monitors-as-code.md#L322)
- Should cameras optionally publish over UDP to test real picture corruption? [07 Open L310](notes/07-fault-injection.md#L310)

## Changelog

| Date | Notes ingested | Change |
|---|---|---|
| 2026-10-31 | 01–12, CP, A1, A2, theory/ | initial version |


```
Original Prompt:
I want to implement a shinobi-like app. Point all insights from docs/notes that I have to consider in my implementation in a separate md file. These insights should be short, meaningful and as bullet points und should reference the location in the note. Consider the folowing points:
- use cases
- expected problems
- security considerations
- implementation details, e.g. programming languages, testing, docker, kubernetes, ...
- implementation plan including priorities
- This list should be incremental und should be updated each time a new note is added
- you make complete this list with things you think they are important
```