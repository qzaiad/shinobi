# Session 11 — The Shinobi API

How a program (not a browser) talks to Shinobi: the URL scheme and its three identifiers, how a
key is checked, what each read endpoint really returns, the traps where Shinobi says "200 OK" for
a failure, and the WebSocket that pushes state changes. Result: `services/common/shinobi_client.py`
(typed, `httpx`) with tests on recorded payloads.

Captured on 2026-10-22: Shinobi `dev` (digest in `.env`), 4 monitors recording (Session 10).
Source references are files in the container under `/home/Shinobi/`. In all output below the
API key, group key and user id are replaced by `API_KEY`, `GROUP_KEY`, `USER_ID`.

```
 pytest / CLI / later: evaluator, twin-state
        │  services.common.shinobi_client  (httpx.Client, connection pool)
        │  GET /<API_KEY>/<endpoint>/<GROUP_KEY>[/<mid>]          127.0.0.1:8080
        ▼                                                         (docker forward → 172.19.0.1)
 shinobi: camera.js = Express + socket.io on ONE port
        ├─ s.auth()  RAM cache s.api{} ── miss ──▶ MariaDB ccio.API / ccio.Users
        ├─ monitor/  ── Monitors table  + RAM state (status, code) of each ffmpeg
        ├─ videos/   ── Videos table    (rows of FINISHED segments only)
        ├─ logs/     ── Logs table
        ├─ events/   ── Events table    (detections, Session 14+)
        ├─ jpeg/     ── /dev/shm/streams/<ke>/<mid>/s.jpg   or placeholder bg.jpg (!)
        └─ /socket.io  push: monitor_status, video_build_success, log, detector_trigger, ...
```

---

## 1. Two styles: request/response and push

The dashboard is not special: it is a JavaScript page calling the same endpoints we call.

| | HTTP API | WebSocket (socket.io) |
|---|---|---|
| who starts | client asks, one answer | server sends when something happens |
| use for | "which monitors", "videos 10:00–11:00", snapshot | "cam X died", "segment finished", detections |
| without it | — | polling; anything shorter than the poll interval is missed |

This session builds the HTTP client. The WebSocket was explored with a throwaway script (§7);
the real event consumer belongs to Sessions 17–18 (webhook vs MQTT vs WebSocket ADR).

## 2. URL anatomy

```
 http://localhost:8080 / <30 chars> / videos / <10 chars> / cam_gate ? start=…&limit=10
 └──── base URL ─────┘   └─ :auth ─┘  endpoint └── :ke ───┘  └─ :id ──┘  └── query ───┘
                         who are you   what     which group  which monitor  filters
```

Express route patterns, e.g. `app.get(apiPrefix+':auth/videos/:ke/:id', …)`; `:name` segments
become `req.params.name`; `apiPrefix` is `/`.

- **group key `ke`**: tenant id. Shinobi is multi-tenant; every row of Monitors, Videos, Events,
  Logs, API has a `ke` column and nearly every query starts `WHERE ke = ?`.
- **monitor id `mid`**: unique only inside a group, so `(ke, mid)` is the real key.
  Convention here: camera `cam-gate` (cameras.yaml) → monitor `cam_gate`.
- **`:auth`**: either a **session token** (32 chars, `auth_token` from the login form, changes
  every login: for browsers) or an **API key** (30 chars, `s.gid(30)`, long-lived, own
  permissions: for services).

The key sits in the path, not in an `Authorization` header, so the same URLs work in
`<img src>`, `<video src>`, VLC, ffplay. Price: the key ends up in proxy logs, shell history,
`Referer` headers, httpx's INFO log lines (logger `httpx`) and **in Shinobi's own answers**
(§5). Treat every URL from this API as a secret.

### The `API` table

```
 ke       varchar(50)   group
 uid      varchar(50)   user who created it; the key acts as that user
 ip       varchar(255)  allowed IPs, comma separated; 0.0.0.0 = any
 code     varchar(100)  THE KEY, stored in plain text (a DB dump leaks every key)
 details  text JSON     permissions {"get_monitors":"1", ...}
 time     timestamp
```

Key used by this repo (`SHINOBI_API_KEY`, created in the UI, *API Keys → Add New*):
Allowed IPs `0.0.0.0`; permissions *Authenticate Websocket, Get Monitors, Get Logs, View
Streams, View Snapshots, View Videos*. Nothing that edits, controls or deletes; Session 12 gets a
separate key that can edit monitors.

## 3. How a key is checked: `s.auth()` (libs/auth.js)

```
 GET /<auth>/monitor/<ke>
  ① s.group[ke].users[auth]       open dashboard socket with this token → OK (no IP check)
  ② s.api[auth] and .ke == ke     RAM cache of keys seen before          → IP check
  ③ username/password params      (not reachable through these routes)
  ④ SELECT … FROM API  WHERE code=auth AND ke=ke    first use of a key   → cache in s.api, IP check
  ⑤ SELECT … FROM Users WHERE auth=auth AND ke=ke   a login session token → cache, IP check
  otherwise: {"ok": false, "msg": "Not Authorized"}
```

- Only the first request of a key hits MariaDB; afterwards it lives in `s.api` (Node RAM).
  `editApiKey` evicts it from the cache, `deleteApiKey` does not: a deleted key may keep
  working until Shinobi restarts (read from source, not tested).
- IP check: `ip = cf-connecting-ip || x-forwarded-for || remoteAddress`, then
  `requestIp.indexOf(allowedIp) > -1`. Client-supplied header, substring match: **not a
  security boundary**. (Substring because Node reports `::ffff:172.19.0.1`, IPv4-mapped IPv6.)
  Our requests arrive from the Docker bridge gateway `172.19.0.1`, so a key limited to
  `127.0.0.1` would reject our own client.
- `s.auth` failures don't count toward the 5-failed-logins lock (that is the login form only).

### Failure is HTTP 200

```
$ curl -i localhost:8080/BADKEY/monitor/BADGROUP
HTTP/1.1 200 OK                       ◀ not 401/403
Content-Type: application/json
{ "ok": false, "msg": "Not Authorized" }
```

| failure | status | caught by |
|---|---|---|
| Shinobi down | — | `httpx.ConnectError` |
| crash / proxy | 5xx | status check |
| wrong key, wrong group, no permission | **200** | only `body["ok"] is False` |
| permission missing on a list endpoint | **200** | nothing: answer is `[]` |
| unknown monitor id | **200** | nothing: answer is `[]` |

## 4. Read endpoints, as measured

| endpoint | success shape | notes |
|---|---|---|
| `monitor/<ke>` | **bare array** of monitors | 41 KB for 4 monitors, ~212 `details` keys each |
| `monitor/<ke>/<mid>` | array with 1 element, `[]` if unknown | |
| `videos/<ke>[/<mid>]` | **object** `{"ok":true,"limit","videos":[…],"endIsStartTo"}` | newest first |
| `logs/<ke>[/<mid>]?limit=` | bare array, `info` parsed | default limit 50 |
| `events/<ke>[/<mid>]` | bare array | 2 dashboard test events from 2026-10-18 |
| `jpeg/<ke>/<mid>/s.jpg` | `image/jpeg` | placeholder if no snapshot output (§6) |
| `hls/…/s.m3u8`, `flv/…/s.flv`, `mp4/…/s.mp4`, `mjpeg/…` | media | live view |
| `videos/<ke>/<mid>/<file>` | the MP4 | |
| `monitor/<ke>/<mid>/<mode>` | changes state | Session 12, not used here |

### A monitor object = config + runtime

```
 from MariaDB (config)                       from RAM (running ffmpeg)
 mid, name, mode "record", type "h264",      status "Recording"   ◀ TRANSLATED label
 fps 15, width/height, ext "mp4",            code   "3"           ◀ stable value, a STRING
 protocol/host/path/port  ""  ◀ redacted     currentlyWatching, subStreamActive
 details {vcodec:"copy", cutoff:15, …}       + built per request: streams[], streamsSortedByType
```

Status codes (`definitions/monitorStatusCodes.js`): 0 Disabled, 1 Starting, 2 Watching,
3 Recording, 4 Restarting, 5 Stopped, 6 Idle, 7 Died, 8 Stopping, 9 Started. `status` is
`lang[...]` and changes with the account language; the client maps `code` to `MonitorCode`.

**Read-only key → redacted config.** Without *Can Edit Monitors*, `getMonitors()` passes every
monitor through `removeSenstiveInfoFromMonitorConfig()`: `protocol/host/path/port`, `muser`,
`mpass`, `auto_host`, custom ffmpeg fields are blanked. The **logs** endpoint does not redact:
"Process Started" entries carry the full ffmpeg command with the camera URL (with real cameras:
`rtsp://user:password@…`). *Can Get Logs* is effectively a "see camera credentials" permission.

**`width`/`height` are config, not measurement.** The CLI shows 640×480 for three cameras that
stream 1280×720: the UI default was never changed (only cam_gate was, in Session 9). Harmless
with `copy` recording; wrong scaling once Shinobi decodes (motion detection, Session 14).
→ Session 12 templates set them from `cameras.yaml`.

### Videos: window semantics and limits (libs/database/utils.js `getDatabaseRows`)

```
 default ORDER BY time DESC LIMIT 500        ← silent cap
 ?start=S&end=E                → time >= S AND end  <= E     (segment completely inside)
 ?start=S&end=E&endIsStartTo=1 → time >= S AND time <= E     (segment STARTED inside)
 ?limit=20,10                  → skip 20, take 10

            S━━━━━━━━━━━━━━━━━━━━━━━━━━━━E
   A   [──────]                                 excluded both ways
   B             [──────]                       included both ways
   C                               [──────]     only with endIsStartTo=1
```

- Times are parsed with JS `new Date(...)`: zones are honoured (`12:40+02:00` → 10:40Z), a
  string **without** a zone is read in the container's local zone. The client only sends
  `…Z` and rejects naive datetimes.
- Answers use `2026-10-22T12:45:00Z` (videos) and `…00.000Z` (logs, events).
- **A row exists only for finished segments** (inserted when `-segment_list pipe:8` reports
  the file). With `cutoff 15` the API lags the recording by up to 15 min; the WebSocket's
  `video_build_success` (§7) is immediate.
- **Duplicate rows.** After a Shinobi start, the orphan scan (Session 10) indexed the
  in-progress file; the real row came later:

  ```
  2026-10-22T12-31-43.mp4  12:31:43 → 12:45:02  305067455 B   ◀ real (= file on disk)
  2026-10-22T12-31-43.mp4  12:31:43 → 12:31:45     786480 B   ◀ stale, from the startup scan
  ```

  `(mid, filename)` is not unique in API answers. The client reports what Shinobi says;
  consumers deduplicate (keep the latest `end`).

## 5. "Get Streams": URLs with your key inside

There is no separate endpoint. `getMonitors()` builds the URLs per request with the caller's key:

```json
"streams": ["/API_KEY/hls/GROUP_KEY/cam_gate/s.m3u8"],
"streamsSortedByType": {"hls": ["/API_KEY/hls/GROUP_KEY/cam_gate/s.m3u8"]}
```

Videos get `href`, `actionUrl` and `links.deleteVideo` the same way. So recorded answers contain
the key many times (37 substitutions in this session's 6 fixtures):
`scripts/record_shinobi_fixtures.py` replaces key/group/uid with placeholders and refuses to
write if any secret value from `.env` (incl. passwords, e-mails) is still present.

## 6. The snapshot trap (libs/webServerStreamPaths.js)

```js
req.dir = s.dir.streams + ke + '/' + id + '/s.jpg'
res.writeHead(200, {'Content-Type': 'image/jpeg'})        // status before the check
if (fs.existsSync(req.dir)) stream(req.dir)
else                        stream(config.defaultMjpeg)  // web/libs/img/bg.jpg
```

Measured on cam_gate (snapshots off, `details.snap` unset): `200`, `image/jpeg`, 4652 bytes, a
valid 882×662 JPEG of solid beige, sha256 prefix `3f80974bdf89f7c4` = `web/libs/img/bg.jpg`.
Nothing in status or headers marks it. A detector would happily analyse a beige rectangle.

`ShinobiClient.snapshot()` checks `details.snap == "1"` first (`SnapshotUnavailable`), then
that the body starts with the JPEG marker `FF D8` (a bad key gives the 46-byte `{ok:false}` JSON,
also with 200). Enabling snapshots is a monitor setting → Session 12 (needs the edit key).

## 7. WebSocket: socket.io on the same port

```
 Shinobi messages   emit("f", {f:"init", ke, auth, uid})   ◀ ONE event name "f", type in d.f
 Socket.IO v4       namespaces, events, rooms        packets: 40 connect, 42["f",{…}] event
 Engine.IO v4       session, heartbeat               packets: 0 open, 2 ping, 3 pong, 4 message
 WebSocket          RFC 6455 frames (FIN, opcode, length, mask)
 HTTP/1.1 Upgrade   GET /socket.io/?EIO=4&transport=websocket → 101 Switching Protocols
 TCP :8080          same Express server as the REST API
```

Socket.IO is a protocol on top of WebSocket; a plain WebSocket client connects but then has to
parse `0{…}`/`42[…]` itself. Python: `python-socketio`.

Raw handshake with curl (no key needed):

```
HTTP/1.1 101 Switching Protocols
Upgrade: websocket
Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=   ◀ base64(SHA1(client key + RFC GUID))
81 6c                                   ◀ frame header: 0x81 = FIN + opcode 1 (text);
                                          0x6c = unmasked, payload 108 bytes
0{"sid":"…","upgrades":[],"pingInterval":60000,"pingTimeout":130000,"maxPayload":1000000}
 ▲ Engine.IO OPEN
```

- `transports: ['websocket']` (libs/webServer.js): HTTP long-polling, socket.io's usual first
  step, is disabled → `?transport=polling` answers `{"code":0,"message":"Transport unknown"}`.
  Clients must connect with `transports=["websocket"]`.
- Heartbeat 60 s / 130 s: a dead connection is noticed after up to ~130 s.
- Login (`libs/socketio.js`, `streamConnectionAuthentication`): `{f:"init", ke, auth, uid}`
  is checked against `Users WHERE ke, uid, auth` or `API WHERE ke, uid, code` **and**
  `details.auth_socket == "1"`. Hence `SHINOBI_UID` in `.env`.
- **Rooms** = server-side named groups of sockets (`GRP_<ke>`, `MON_<ke><mid>`,
  `DETECTOR_<ke><mid>`, …). `s.tx(msg, room)` sends to one room; you only receive what your
  socket joined. In-process only, like a private MQTT broker.

### Demo: socket login, watch cam_waterway, crash its camera

Throwaway script (`uv run --with "python-socketio[client]"`, not a project dependency): connect,
send `init`, then `{f:"monitor", ff:"watch_on", id:"cam_waterway"}`; 8 s later
`scripts/chaos.sh crash cam-waterway` (SIGKILL of the camera's ffmpeg, back in ~3 s).
Abridged, e-mail removed:

```
 0.01 s  connected (websocket)          -> f:init
 0.02 s  <- f:log  $USER "Websocket Connected" {ip: "::ffff:172.19.0.1"}
 0.02 s  <- f:users_online, f:user_status_change {…account details…}, f:cloudDiskUsed
 0.02 s  <- f:init_success              -> f:monitor ff:watch_on id:cam_waterway
 0.03 s  <- f:monitor_watch_on, f:viewer_count
 1.02 s  <- f:diskUsed {usedSpace 5518 MB, limit 20000}
13:06:15  chaos.sh crash cam-waterway
 6.51 s  <- f:video_build_success {filename 2026-10-22T13-00-02.mp4, end 13:06:16.308Z, size 140633466}
 6.52 s  <- f:log "Video Finished" / "FFMPEG STDERR: Process Closed" / "Process Unexpected Exit" (+cmd)
 6.54 s  <- f:monitor_status {code: 7, status: "Died"}
11.54 s  <- f:monitor_status {code: 1, status: "Starting"}
12.08 s  <- f:monitor_starting, f:log "Process Started"
12.11 s  <- f:monitor_status {code: 3, status: "Recording"}
```

- Shinobi's ffmpeg got EOF from MediaMTX and exited normally, so the MP4 was finalised (unlike
  Session 10's `kill -9` of Shinobi's own ffmpeg). Restart after ~5 s; a single failure does
  not trigger the 10-min back-off from Session 9. Recording gap ≈ 5 s, new segment `13-06-21`.
- `video_build_success` = the Videos row, pushed the moment it exists.
- `monitor_status.code` is the same code as the REST field → one `MonitorCode` for both.
- `user_status_change` broadcasts account `details` and e-mail to every socket of the group,
  even to an API-key socket that only watches one camera.

## 8. The client

`services/common/shinobi_client.py`:

```
 ShinobiConfig.from_env(env)  url, api_key (repr=False), group_key, uid
 ShinobiClient(config, timeout=10, transport=None)    context manager, one httpx.Client
   .monitors()                 -> list[Monitor]        bare array → dataclasses
   .monitor(mid)               -> Monitor              [] → ShinobiError
   .videos(mid, start=, end=, started_in_window=, limit=) -> list[Video]   UTC only
   .logs(mid, limit=50)        -> list[LogEntry]
   .snapshot(mid)              -> bytes                SnapshotUnavailable / JPEG check
   .stream_url(mid, "hls")     -> str                  same string Shinobi builds
   .video_url(video)           -> str
 errors: ShinobiError ⊃ ShinobiAuthError, SnapshotUnavailable
         messages never contain the key (redacted path; httpx exceptions re-raised redacted)
```

Every JSON answer goes through `_check()`: a dict with `ok is False` raises, whatever the
HTTP status. Dataclasses are `frozen=True, slots=True`; `details` stays a dict (≈ 212 keys,
modelled in Session 12 where they are written).

```
$ uv run python -m services.common.shinobi_client monitors
MONITOR        NAME           MODE    STATE       SIZE      FPS  LIVE
cam_gate       cam-gate       record  recording   1280x720   15  hls
cam_quay       cam-quay       record  recording   640x480    15  hls
cam_waterway   cam-waterway   record  recording   640x480    10  hls
cam_yard       cam-yard       record  recording   640x480    10  hls

$ uv run python -m services.common.shinobi_client snapshot cam_gate x.jpg
error: monitor 'cam_gate' has snapshots disabled (details.snap)        (exit 1)
```

### Tests

```
 ShinobiClient ── httpx.Client ── MockTransport(FakeShinobi) ── tests/fixtures/shinobi/*.json
                  real request building                      recorded, redacted answers
```

The tests use the placeholders `API_KEY`/`GROUP_KEY` as credentials, so replayed answers are
exactly what Shinobi sends for that key. 18 unit tests (parsing, the 200-failure, redaction by a
read-only key, window parameters, naive datetimes, snapshot refusal, no key in error messages)
plus one e2e test: live monitors = cameras in `sim/cameras.yaml`, all `RECORDING`.
Re-record after a Shinobi upgrade (`scripts/record_shinobi_fixtures.py`) and let the tests show
what changed.

## 9. Open items for Session 12

- Second API key with *Can Edit Monitors* (full, unredacted configs) for export/provisioning.
- Monitor `width`/`height` from `cameras.yaml` (3 of 4 still 640×480).
- Decide on snapshots (`details.snap`) per monitor; needed for `snapshot()`.
- Check: does a deleted API key keep working until restart (`s.api` cache)?
