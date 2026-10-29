# Session 12 — Monitors as code

How a Shinobi monitor is stored, how the one Add/Edit/Delete endpoint really works, what
Shinobi does to the JSON you send, and why "idempotent provisioning" means *reaching a fixed
point* rather than "sending the same thing every time". Result: `scripts/provision_monitors.py`
recreates all four monitors from `sim/cameras.yaml` + `shinobi-config/`, byte-identical.

Captured on 2026-10-29, Shinobi `dev` (digest in `.env`). Source references are files in the
container under `/home/Shinobi/`. Keys, group key and user id are written as `EDIT_API_KEY`,
`GROUP_KEY`, `USER_ID`.

```
 sim/cameras.yaml ───────┐  id, fps, width, height
 sim/gen_compose.py      │  RTSP_BASE = rtsp://mediamtx:8554   (the URL the cameras publish to)
   RTSP_BASE ────────────┼──▶ render (pure) ──▶ desired[mid] ──▶ shinobi-config/monitors/*.json
 shinobi-config/         │                           │               (generated, committed)
   monitor.template.json ┘                           │
                                                     ▼
 Shinobi  GET /EDIT_API_KEY/monitor/GROUP_KEY ──▶ actual[mid] ──▶ plan: create | update
                                                                      | unchanged | extra
                       POST configureMonitor/GROUP_KEY/<mid> ◀── apply: only create/update
```

---

## 1. Terms

| Term | Meaning |
|---|---|
| **Monitor config** | The stored part of a monitor: one row of `ccio.Monitors` (§2). Everything else in a `GET /monitor` answer (status, code, streams, viewers, CPU) is runtime state of the running FFmpeg, built per request. |
| **Desired state** | What the repo says a monitor should be: template + camera entry. |
| **Actual state** | What Shinobi has stored now. |
| **Drift** | Desired ≠ actual, e.g. somebody clicked in the UI, or two monitors were created in different ways. |
| **Reconcile** | Compute the difference and change only what differs (Kubernetes controllers, Terraform `plan`/`apply`, Ansible). |
| **Idempotent** | Running it again changes nothing. Formally: `apply(apply(x)) == apply(x)`; here also "causes no side effects", because a save restarts the camera (§4). |
| **Fixed point** | A state where `render()` == what Shinobi returns. Only from there is a second run a no-op. §5 shows two ways to never reach it. |

## 2. The monitor JSON: 15 columns + one blob

```
 ccio.Monitors
 ┌─────────────────────────────────────────── top-level columns ──────────────────────────────┐
 │ ke         varchar(50)   group key (tenant)       ┐ (ke, mid) is the real key
 │ mid        varchar(100)  monitor id               ┘ only [A-Za-z0-9_] survive (cam-gate → camgate)
 │ name       varchar(100)  display name             "cam-gate"
 │ type       varchar(25)   input type               "h264" (any FFmpeg network input)
 │ ext        varchar(10)   recording container      "mp4"
 │ protocol / host / port / path                     rtsp / mediamtx / 8554 / /cam-gate
 │                           → FFmpeg -i  protocol://[user:pass@]host:port path (buildMonitorUrl)
 │ fps / width / height     descriptive with `copy`; used when Shinobi encodes/decodes (S14)
 │ mode       varchar(15)   stop | start (Watch-Only) | record | idle
 │ saveDir / tags           NULL here
 │ details    longtext      JSON, 212 keys ──────────────────────────────────┐
 └───────────────────────────────────────────────────────────────────────────┼────────────────┘
   { "vcodec": "copy", "cutoff": 15, "stream_type": "hls", "rtsp_transport": "tcp",
     "auto_host": "rtsp://mediamtx:8554/cam-gate", "auto_host_enable": 1,
     "wall_clock_timestamp_ignore": 1, "snap": 0, "detector": 0, "detector_filters": {},
     "probesize": 1000000, "aduration": 1000000, "tv_channel_id": "cam_gate", … }
```

Why a blob: Shinobi adds settings every release; one JSON column needs no schema migration.
Consequence: `details` can't be updated partially. A save **replaces the whole blob**. A key
you leave out is gone afterwards.

Which columns a save keeps: `cleanMonitorObjectForDatabase()` (libs/monitor.js) has a list of
16 (the 15 above + `ke`). Any other top-level key you send is dropped without a message. The
template loader therefore refuses unknown top-level keys instead of letting them vanish.

## 3. One endpoint for Add, Edit and Delete (libs/webServerAdminPaths.js)

```
 POST /<key>/configureMonitor/<ke>/<mid>              body: data=<monitor JSON>  (form or JSON)
        row (ke, mid) missing → INSERT   log "Monitor Added"    msg "Monitor Added by user. : USER_ID"
        row exists            → UPDATE   log "Monitor Updated"  msg "Monitor Updated by user. : USER_ID"
 GET  /<key>/configureMonitor/<ke>/<mid>/delete               stop FFmpeg, DELETE row; videos KEPT
 GET  /<key>/configureMonitor/<ke>/<mid>/delete?deleteFiles=true    + Videos rows, files, timelapse, fileBin
 GET  /<key>/configureMonitor/<ke>/<mid>/restart              stop + start, config unchanged
```

- `app.all(...)`: any HTTP method works; the action comes from the optional `:f` path segment.
- Add vs Edit is decided **by the server** from whether the row exists. The POST is an
  **upsert**, which makes provisioning simple: the same call for create and update.
- **Permission**: an API key needs *Can Edit Monitors* (`edit_monitors: "1"`); otherwise
  `200 {"ok": false, "msg": "Not Authorized"}` (`s.checkPermission`, libs/monitor.js). That is
  why there is a second key now (`SHINOBI_EDIT_API_KEY`: Get + Edit Monitors only). The
  read-only key stays read-only.
- **Reading with the edit key** returns the stored config unredacted. With the read-only key,
  `removeSenstiveInfoFromMonitorConfig()` blanks `protocol/host/path/port`, `auto_host`,
  credentials, custom FFmpeg fields and `tv_channel_id`. A provisioner reading that would
  "fix" the blanks and overwrite the camera address. `monitor_configs()` refuses such an
  answer (`ShinobiAuthError`).
- **Delete keeps recordings by default.** The route passes `req.query.deleteFiles === 'true'`
  (the function's own default is `true`, but the route always passes a bool). Measured: after
  deleting all 4, `Videos` rows went 13 → **14** each. The stop is graceful, so the segment in
  progress was finished and indexed (unlike the `kill -9` in Session 10). When a monitor with
  the same `mid` is created again, it shows all old recordings.
- **Unactivated limit**: an INSERT beyond 15 monitors answers `ok:false` with
  `monitorEditFailedMaxReachedUnactivated` (libs/checker/utils.js `canAddMoreMonitors`).
  `save_monitor()` raises it as `ShinobiError`.

## 4. What happens to the JSON you send (libs/monitor.js `addOrEditMonitor`)

```
 POST data=<json>
  │ ① getPostData: req.query.data or req.body.data, JSON.parse
  │ ② mid, ke: delete every char outside [A-Za-z0-9_ ], then spaces
  │ ③ cleanStringsInObject(form)          libs/basic/utils.js:246
  │      every string: try JSON.parse →  "0" → 0   "[]" → []   "true" → true   "15" → 15
  │      not JSON     → char whitelist → "a;b" → "ab"   "x<y" → "xy"  (silently!)
  │      (detector_filters allow < > too: comparison operators)
  │ ④ getWarningChangesForMonitor: ffprobe the camera URL, then a FIXED 2 s wait
  │      → "auto-correct" partial for MJPEG sources, rtmp/http/https without port,
  │        rtsp without port (→ 554). None applies to us (h264, port 8554 always sent).
  │ ⑤ cleanMonitorObjectForDatabase: keep the 16 columns
  │ ⑥ INSERT: skip every falsy value ("" → column NULL)
  │   UPDATE: skip undefined/null/false/"false" (→ column keeps OLD value); "" IS written
  │ ⑦ restart: s.camera('stop') → s.camera(mode)        (only exception: an edit that
  │      changes only motion regions/thresholds is pushed into the running thread)
  ▼
 row in ccio.Monitors, FFmpeg restarted, socket.io "monitor_edit" to the group
```

What each step means for "as code":

| Step | Consequence | Handling in provision_monitors.py |
|---|---|---|
| ② | `cam-gate` would become `camgate` | `monitor_id()`: `-` → `_`, refuse anything else outside `[A-Za-z0-9_]` |
| ③ JSON.parse | sent `"0"`, stored `0`: a naive diff never settles | template written in **stored form**; `stored_form()` applies the same conversion (and JS number rules: `1.0` → `1`; JS rejects `NaN`, Python's json would accept it) |
| ③ whitelist | sent `a;b`, stored `ab`: a diff that can never settle | `stored_form()` **refuses** such a value (`ConfigError`) |
| ④ | each save ≥ 2 s + probe; the stored config may differ from what was sent | always send `port`; measured 4 saves = 24 s (~6 s each) |
| ⑥ | `tags` is NULL after INSERT, `""` after an UPDATE that sent `""`. Session 11 saw exactly that: `cam_yard` NULL, others `""`. An UPDATE can't set a column back to NULL. | `comparable()`: `tags`/`saveDir` NULL ≡ `""` ≡ absent; we never send them |
| ⑦ | **every save cuts the recording** | compare first, POST only on a difference (§6) |

### What a save costs, measured on cam_gate

```
 log "Monitor Updated"   last segment ends → next starts    gap
 14:12:58  apply #1      14:12:59 → 14:13:03                4 s
 14:13:37  apply #2      14:13:38 → 14:13:42                4 s   ◀ the restart loop of §5.2
 14:15:02  apply #3      14:15:01 → 14:15:03 (2 s stub) → 14:15:08   5 s + one 2 s file
```

4–7 s of footage lost per save, plus a short extra MP4. A provisioner that re-saves everything
on every run (cron, CI, a container start hook) loses that much footage on every camera on
every run.

## 5. Idempotency = a fixed point, and two ways to miss it

```
            ┌───────────── render() ─────────────┐
 repo ──▶ desired ──diff── actual ◀── GET ── Shinobi DB
             │       ≠                              ▲
             └──────── POST (restart) ──────────────┘
 Fixed point: desired == GET(DB after POST(desired)).  Then diff = ∅ and the loop stops.
```

### 5.1 Type conversion (anticipated)

```
 desired "0" ──POST──▶ stored 0 ──GET──▶ 0      "0" ≠ 0 → POST → … forever
 desired  0  ──POST──▶ stored 0 ──GET──▶ 0      equal → stop                    ✓
```

The template was exported from a stored monitor, so it is already in stored form. A unit test
keeps it that way: `stored_form(template) == template`.

### 5.2 The GET answer invents data (found live, the hard way)

First apply: 4 updates. Second apply: **4 updates again**, 7 more "Process Started" entries,
and in each monitor only one key differed:

```
update    cam_gate
            details.tv_channel_id: "temp_mQf69" -> ""
update    cam_quay
            details.tv_channel_id: "temp_coGKs" -> ""
```

The DB had `""`, exactly what was sent. Two GETs in a row returned `temp_WmBXR` and then
`temp_WpKya`. Source (libs/monitor/utils.js:2210, in `getMonitors`, i.e. the **read** path):

```js
if(!details.tv_channel_id||details.tv_channel_id==='')details.tv_channel_id = 'temp_'+s.gid(5)
```

So a GET answer is not a pure function of the stored state. Ignoring `temp_*` in the diff would
not be enough: `export` would still write a new random value into the repo each time. Fix:
`tv_channel_id` is never empty. It is now a per-camera value (= `mid`; it is the channel name
in Shinobi's TV-channel/M3U list) and no longer in the template. After that:

```
$ provision_monitors.py apply          # first run after the fix
update    cam_gate
            details.tv_channel_id: "temp_VYcm0" -> "cam_gate"
…
done: 0 create, 4 update, 0 unchanged, 0 extra (kept, use --prune to delete)
$ provision_monitors.py apply          # second run
unchanged cam_gate
unchanged cam_quay
unchanged cam_yard
unchanged cam_waterway
done: 0 create, 0 update, 4 unchanged, 0 extra (kept, use --prune to delete)
real 0m0.552s          "Process Started" count: 89 before, 89 after
```

Lesson for the unit tests: the first `FakeShinobiStore` returned what it stored, so
`test_apply_is_idempotent` passed while the real thing looped. The fake now also invents
`temp_XXXXX` for an empty `tv_channel_id` on every read. With the old template it reports
`['cam_gate', 'cam_quay', 'cam_yard', 'cam_waterway']` saved on the second run, which is the
live failure reproduced. A fake is only as good as the behaviour it copies, so copy it from
the source.

## 6. The provisioner

```
 scripts/provision_monitors.py
   load_template()   refuses: no `details`, unknown columns, per-camera keys
   render(fleet, template)   pure; per camera sets mid, name, protocol/host/port (from
                     gen_compose.RTSP_BASE), path /<id>, fps, width, height,
                     details.auto_host, details.tv_channel_id; then stored_form()
   comparable()      the 15 columns, tags/saveDir NULL ≡ "" ≡ absent
   changes()         leaf diff "details.probesize: 1000000000 -> 1000000"; compares JSON
                     text, so true vs 1 is a change (Python: True == 1)
   plan()            create | update(changes) | unchanged | extra
   apply()           saves create/update one at a time; extra deleted only with --prune,
                     never with files
   commands          render [--check] · plan (exit 2 = changes, like terraform
                     -detailed-exitcode) · apply [--prune] · export [--out DIR]
```

The camera URL is built from the same `RTSP_BASE` constant `gen_compose.py` uses to publish.
If MediaMTX moves, both sides change together. A test checks that every monitor's URL appears
in that camera's FFmpeg command.

**Generated files** `shinobi-config/monitors/<mid>.json`: the same pattern as
`compose/cameras.generated.yml`. They are reviewable in git ("what will this monitor be?"
without running anything), sorted keys for stable diffs, `render --check` in the tests. And
they are the target format of `export`:

```
export → overwrites shinobi-config/monitors/ with live configs → git diff = drift report
```

The first plan, against monitors created by hand in Sessions 9–10, listed the drift from
Session 11 and more. changes per monitor: cam_gate 44, cam_quay 5, cam_yard 8, cam_waterway 4 (61):

| Drift | Origin |
|---|---|
| `cam_gate`: ~40 keys `null` where the others have `0` | Session 9 posted the JS defaults function (`generateDefaultMonitorSettings`); the UI form turns every checkbox/select into `"0"` |
| `cam_gate`: `probesize`/`aduration` 10⁹ (1 GB / 1000 s) vs 10⁶ | same: function default vs form default |
| `detector_filters` with random ids `XXXXX`, `8Sjsq`, … | UI adds a default object filter with a random id; now `{}` (Session 15 decides) |
| `cam_quay`: `geolocation` `49.2578,-123.2634` (Vancouver) | a map default that got saved |
| 3× `width/height` 640×480 | UI default never changed (Session 11) |
| `detector_buffer_vcodec` auto/copy, `primary_input` 0 / `"0:0"` / null, `stream_fps` 2 / `""` | different UI paths / versions |

What it changed in FFmpeg (word diff of the "Process Started" commands before/after):

```
< -analyzeduration 1000000000 -probesize 1000000000      cam_gate only
> -analyzeduration 1000000 -probesize 1000000
```

Of 61 changed values, these two flags were the only ones that reached a command line. That
matches Session 9 §5.4: with `copy`, width/height/fps and the detector keys are unused. The
drift was mostly latent, but it was invisible until it was diffed, and it will matter as
soon as Shinobi decodes (Session 14).

## 7. Done-when: wipe and rebuild

"Wipe" = delete all monitors through the API (recordings kept). A full `shinobi-db` volume wipe
also removes the account and API keys; rebuilding those from the repo is Session 13.

```
videos before: cam_gate 13  cam_quay 13  cam_waterway 13  cam_yard 13
deleted cam_gate … cam_yard        left: {}     Monitors rows: 0     ffmpeg processes: 0
videos after delete: 14 14 14 14                ◀ graceful stop finished the open segment

$ provision_monitors.py apply
create    cam_gate      -> Monitor Added by user. : USER_ID
create    cam_quay      …
done: 4 create, 0 update, 0 unchanged, 0 extra        real 0m18.998s
$ shinobi_client monitors
cam_gate       cam-gate       record  recording   1280x720   15  hls
cam_quay       cam-quay       record  recording   1280x720   15  hls
cam_waterway   cam-waterway   record  recording   1280x720   10  hls
cam_yard       cam-yard       record  recording   1280x720   10  hls
$ provision_monitors.py export --out /tmp/x && diff -r shinobi-config/monitors /tmp/x
(no output)                                     ◀ byte-identical
$ provision_monitors.py apply
done: 0 create, 0 update, 4 unchanged, 0 extra
```

`tags`/`saveDir` are now NULL on all four (INSERT path), and old recordings are listed again
under the re-created `mid`s.

## 8. Tests

```
 tests/test_provision_monitors.py   (unit, no stack)
   stored_form: "0"→0, "[]"→[], "0:0" stays, 1.0→1, "1e3"→1000, NaN stays a string,
                refuses ";" "<" "~" "\" (detector_filters may use < >)
   template: already in stored form; refuses per-camera / unknown / missing keys
   render: one monitor per camera, URL == camera's publish URL, tv_channel_id == mid
   committed files up to date (render --check)
   plan kinds + exact change lines; True vs 1 is a change
   FakeShinobiStore (stores like addOrEditMonitor, reads like getMonitors incl. temp_ ids):
     apply on drift → saves drifted only; second apply → saves nothing
     apply on empty → stored == desired;  --prune deletes extra, never files
   recorded edit-key answer (fixtures/shinobi/monitors_edit_key.json, recorded BEFORE this
     session's apply, so it shows the drift): only stored columns enter the comparison
 tests/test_shinobi_client.py: from_env(key_var), monitor_configs (columns, redaction refused),
   save_monitor wire format (POST form data=<json>), ok:false raised, delete keeps files
 e2e: live plan == all unchanged
```

## 9. Open items

- Session 13: rebuild **account + API keys** too (full volume wipe), < 10 min.
- Session 14: `width/height` and `detector_scale_*` matter once Shinobi decodes; the sub stream
  (`/cam-x-sub`) for detection; `snap` decided there (kept 0 for now).
- Session 15: `detector_filters` (now `{}`).
- Session 11 leftover, still open: does a deleted API key keep working until restart?
- The fixed 2 s wait per save makes provisioning O(n). 14 monitors ≈ 1.5 min for a full create,
  which is fine. Parallel saves are possible but would restart several cameras at once.
