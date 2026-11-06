# Session 13 — Shinobi operations cheatsheet

Everything needed to run, inspect, back up, rebuild and debug the Shinobi side of this repo, in
one place. Sessions 8–12 explain *why*; this page says *what to type* and points back to them.
New in this session: the account and its API keys as code (`scripts/shinobi_bootstrap.py`), so a
wiped database or a fresh clone is rebuilt from the repo + `.env` alone.

Captured on 2026-10-31, Shinobi `dev` (digest in `.env`). Source references are files in the
container under `/home/Shinobi/`. Secrets are written as `API_KEY`, `GROUP_KEY`, `USER_ID`,
`SESSION_KEY`, `AUTH_TOKEN`.

```
 repo (git)                         .env (host, 0600)               Docker state
 ─────────────────────────────      ──────────────────────────      ───────────────────────────────
 docker-compose.yml                 SHINOBI_SUPER_EMAIL/PASSWORD    data/shinobi/super.json (host)
 sim/cameras.yaml                   SHINOBI_ADMIN_EMAIL/PASSWORD    data/shinobi/conf.json  (host)
 shinobi-config/account.yaml   ─┐   SHINOBI_GROUP_KEY  (chosen)     volume shinobi-db:
 shinobi-config/monitor.*.json  │   SHINOBI_UID        (generated)    Users API Monitors Videos Logs
                                │   SHINOBI_API_KEY    (generated)  volume shinobi-videos: *.mp4
                                │   SHINOBI_EDIT_API_KEY (generated)
                                ▼
   shinobi_bootstrap.py apply ──▶ super.json, Users, API  ──▶ writes generated values to .env
   provision_monitors.py apply ─▶ Monitors
```

---

## 1. Map: ports, processes, paths

```
 host 127.0.0.1                     compose network "shinobi_default"
 ──────────────                     ─────────────────────────────────────────────────────────────
 :8554  ──▶ mediamtx:8554   RTSP    ◀── cam-* publishers (FFmpeg, RTSP/TCP ANNOUNCE+RECORD)
 :8080  ──▶ shinobi:8080    HTTP + socket.io (UI, /super, API, WebSocket)
                              │
                              ├─ PM2 ─ node camera.js            web server, API, DB client, cron
                              │          ├─ node cameraThread/singleCamera.js ─ ffmpeg   (1 per monitor)
                              │          ├─ ffmpeg (thumbnail, 1 per finished segment)
                              │          └─ node libs/cron/worker.js   (hourly age purge)
                              └─ mysqld (MariaDB 10.5, built in; socket only, no TCP for others)
```

| Path in container | Host / volume | Holds | Lost by |
|---|---|---|---|
| `/home/Shinobi/conf.json` | `data/shinobi/conf.json` (bind, file) | server settings; `init.sh` rewrites `db ssl pluginKeys subscriptionId databaseType cpuUsageMarker` on every start | fresh clone (re-seeded from the image sample, identical after one start, §5.4) |
| `/home/Shinobi/super.json` | `data/shinobi/super.json` (bind, file) | superuser mail + sha256 hash (md5 accepted for legacy) | fresh clone (sample = `admin@shinobi.video` / `admin`) |
| `/var/lib/mysql` | volume `shinobi-db` | `ccio` DB: Users, API, Monitors, Videos, Logs, Events … | `down -v`, `volume rm`, new machine |
| `/home/Shinobi/videos/<ke>/<mid>/` | volume `shinobi-videos` | `YYYY-MM-DDTHH-MM-SS.mp4` segments, `<mid>_timelapse/` | `down -v`, `volume rm` |
| `/dev/shm/streams/<ke>/<mid>/` | tmpfs 512 MB | live HLS segments (`s.m3u8`, `*.ts`) | every restart (by design) |
| `/home/Shinobi/libs/` | image | the source: read it, `docker compose exec shinobi grep -rn …` | — |

Both host files are bind-mounted **as single files**. That works because Shinobi saves them with
`fs.writeFile` (truncate + write, same inode). A save by "write temp + rename" would fail with
`EBUSY`, and a missing source file makes Docker create a *directory* in its place (S8).
Trap in the source: with `thisIsDocker: true` in conf.json, superuser saves go to
`/config/super.json` (not mounted) and vanish on restart (`webServerSuperPaths.js:312`).

---

## 2. Three kinds of credentials

```
  WHO            WHERE STORED        LOGIN                              TOKEN              LIVES IN
  ───────────── ─────────────────── ────────────────────────────────── ────────────────── ──────────────
  superuser      super.json (host)   POST /super?json=true               $user.sessionKey   memory (s.superUsersApi)
                                       mail, pass, function=super
  admin account  ccio.Users          POST /?json=true                    $user.auth_token   memory (s.api)
                                       mail, pass, function=dash          + ke, uid
  API key        ccio.API            (none: created by the admin)        code (30 chars)    DB, cached in s.api
```

| Rule | Detail | Source |
|---|---|---|
| Lock-out | 5 failed logins per client IP → 15 min lock. Locked answers are `{"ok": false}`, the **same** as a wrong password. A successful login resets the counter. Container restart clears it (in memory). | `webServerPaths.js:354-420` |
| All host tools are one IP | Docker NAT: curl, pytest and scripts all reach Shinobi from the bridge gateway, so they share the 5 tries | S8 |
| `uid` and key codes are generated | `uid = s.gid()`, `code = s.gid(30)`. `api/add` with an unknown `code` creates a **new random** key. Only `ke` can be chosen (punctuation incl. `-` `_` stripped). | `user/utils.js:651`, `user/apiKeys.js:61` |
| Key changes act at once | Permission update and delete via the API drop the cache entry `s.api[code]`: measured, next request already `[]` / `Not Authorized`. A row deleted by SQL would keep working from the cache. | `apiKeys.js editApiKey`, `webPaths/apiKeys.js` |
| Missing permission ≠ error | `200 []` for a list endpoint, `200 {"ok":false}` for a bad key (S11) | S11 |
| Login answers carry secrets | super login returns `$user.pass` (**the stored hash**) and `tokens`; admin login `auth_token`. Plus 400 kB of UI tables (`lang`, `define`). Never print them. | §9 |

Our two API keys (`shinobi-config/account.yaml`):

| `.env` variable | Permissions | Used by |
|---|---|---|
| `SHINOBI_API_KEY` | `auth_socket get_monitors get_logs watch_stream watch_snapshot watch_videos` | client CLI, services, tests, WebSocket |
| `SHINOBI_EDIT_API_KEY` | `get_monitors edit_monitors` | `provision_monitors.py` (unredacted configs, saves) |

The UI-made read key also had `edit_user` and `get_alarms` (UI defaults); the first apply removed them.

---

## 3. URL patterns

```
 /super/<SESSION_KEY>/<endpoint>                       superuser API     (prefix webPaths.superApiPrefix)
 /<AUTH_TOKEN or API_KEY>/<endpoint>/<GROUP_KEY>[/<mid>] account API      (prefix webPaths.apiPrefix "/")
 /admin/<…>                                            same handlers, adminApiPrefix
 POST bodies: form field  data=<JSON>   (s.getPostData reads req.query.data, else req.body.data)
```

| Purpose | Request | Notes |
|---|---|---|
| superuser login | `POST /super?json=true` `mail pass function=super` | `$user.sessionKey` |
| change superuser | `POST /super/SK/accounts/saveSettings` `data={mail,pass,pass_again}` | writes super.json |
| list accounts | `GET /super/SK/accounts/list[/admin\|/sub]` | `details` is a JSON **string** here |
| create account | `POST /super/SK/accounts/registerAdmin` `data={mail,pass,pass_again,ke?,details?}` | details merged over defaults |
| edit account | `POST /super/SK/accounts/editAdmin` `data={details}` `account={mail,ke,uid}` | merges details; every field in `data` goes into `UPDATE Users` (send only `details`) |
| export tables | `POST /super/SK/export/system` `Users=1 Monitors=1 API=1 …` | §5.2 |
| import tables | `POST /super/SK/import/system` `data={database:{…}}` | insert-if-absent, §5.2 |
| admin login | `POST /?json=true` `mail pass function=dash` | `$user.{auth_token,ke,uid,details}` |
| API keys | `GET /AT/api/KE/list`, `POST /AT/api/KE/add` `data={code?,ip,details}`, `POST …/delete` `data={code}` | |
| monitors | `GET /KEY/monitor/KE[/MID]`, `POST /KEY/configureMonitor/KE/MID` `data=<json>`, `…?action=delete` | S11, S12 |
| videos / logs | `GET /KEY/videos/KE/MID?start&end&limit`, `GET /KEY/logs/KE/MID?limit` | S11 |
| snapshot | `GET /KEY/jpeg/KE/MID/s.jpg` | placeholder `bg.jpg` if `snap` is off (S11) |
| live HLS | `GET /KEY/hls/KE/MID/s.m3u8` | |

Our tools wrap these: `services/common/shinobi_client.py` (API key), `scripts/provision_monitors.py`
(edit key), `scripts/shinobi_bootstrap.py` (superuser + admin session).

---

## 4. Logs: four places

```
                      ┌──────────────────────────── shinobi container ──────────────────────────────┐
 ffmpeg stderr ──────▶│ cameraFilterFfmpegLog()  (§4.2: some lines dropped, some force-saved)        │
 monitor lifecycle ──▶│ s.userLog(monitor, {type,msg}, forceSave)                                    │
                      │    ├── socket.io 'log' to the group ─────────────────────────────────────────┼─▶ ① UI log panel / WebSocket (live only)
                      │    └── INSERT Logs(ke, mid, info)  iff forceSave or details.sqllog === '1' ──┼─▶ ② DB, per monitor
 server events ──────▶│ s.systemLog(type,msg) → Logs(ke '$', mid '$SYSTEM'), conf systemLog (on) ───┼─▶ ③ DB, superuser view
 logins ─────────────▶│ failed/ok logins → Logs(ke '$', mid '$USER')                                 │
 everything else ────▶│ console.log: CRON.js, crashes, debugLog (conf, off), PM2 ────────────────────┼─▶ ④ docker compose logs
                      └──────────────────────────────────────────────────────────────────────────────┘
```

```bash
# ④ process output: crashes, cron purge errors, startup
docker compose logs --since 1h -t shinobi | grep -v "^\s*$" | tail -50
# ② per-monitor DB log: the ffmpeg command, exits, stops
docker compose exec shinobi mysql ccio -N -B --raw -e "select time, json_value(info,'$.type'), left(json_value(info,'$.msg'),120) from Logs where mid='cam_gate' order by time desc limit 10"
# ③ system + logins
docker compose exec shinobi mysql ccio -N -B -e "select time, mid, json_value(info,'$.type') from Logs where ke='\$' order by time desc limit 10"
# ① live, through the API (read key, needs get_logs)
uv run python -m services.common.shinobi_client …   # logs() method; or the UI's log panel
# processes and their command lines
docker compose exec shinobi ps -eo pid,etimes,pcpu,args --sort=-pcpu | cut -c1-200 | head
```

### 4.1 What reaches the DB log (②)

Force-saved, so always in the DB: `Process Started` (with the full ffmpeg command), `Monitor
Stopped`, `Process Unexpected Exit`, `Fatal Error`, `Ping Failed`, `Spawn Error`, `Monitor
Idling`, `Error While Decoding`, `Incorrect Settings Chosen`, `Event Webhook Error`, and
`FFMPEG STDERR` lines matching §4.2. Everything else only when `details.sqllog === '1'`.

`sqllog` and the S9 open question (does `"0"`→`0` break `=== '1'` checks?): **yes, here** (refined in [14 §8](14-motion-detection.md#L264): a start converts back to `'1'`, a no-restart edit does not).
Measured on `cam_yard`:

| Sent in `configureMonitor` | Stored (`json_type`) | `=== '1'` |
|---|---|---|
| `"sqllog": "1"` | `INTEGER 1` | false: setting has no effect |
| `"sqllog": "\"1\""` (JSON string inside the string) | `STRING "1"` | true |

`cleanStringsInObject` JSON-parses one layer, so only the double-encoded form survives as a
string. The 20 s restart window produced only force-saved rows in both cases, so the logging
*effect* is still to be seen with a non-forced message (Session 14, motion events). We keep
`sqllog: 0`: the DB log stays an error/lifecycle log, chatter goes to ① only.

### 4.2 FFmpeg stderr: what Shinobi does with each line (`libs/monitor/utils.js:1599`)

| stderr contains | Shinobi does |
|---|---|
| `Not Enough Bandwidth` | flags critical error 453, no log |
| `No space left on device` | emergency purge of the group + DB log |
| `error while decoding`, `error parsing AU headers` | `Error While Decoding` (DB) |
| `Non-monotonous DTS`, `Non-monotonic DTS`, `RTP: missed`, `Past duration`, `bad cseq`, `[hls @`, `pkt->duration = 0`, `NULL @`, starts with `DTS`, `deprecated pixel format`, `Last message repeated` | **dropped**: not in the DB, not even live |
| `Could not find tag for vp8`, `Only VP8 or VP9`, `Could not write header` | `Incorrect Settings Chosen` (DB) |
| `Connection refused`, `Connection timed out`, `Immediate exit requested`, `mjpeg_decode_dc`, `bad vlc`, `error dc`, … | `FFMPEG STDERR` (DB) |
| `Could not find codec parameters`, `No route to host` | `FFMPEG STDERR` (DB) + fatal restart after 60 s |
| anything else | `FFMPEG STDERR`, live only |

The dropped group is exactly what Sessions 2, 4 and 7 measured as stream health: packet loss
(`RTP: missed`), timestamp breaks (`Non-monotonous DTS`), RTSP sequence errors. Shinobi never
shows them, so the netem loss of Session 7 is invisible in its logs. Stream health stays our
job: `tools/streamprobe`, `sim/scripts/check_stream.sh`, `tests/test_streams.py`.

---

## 5. Backup and migration

### 5.1 Three layers, three answers

```
 layer      examples                              lives in          backup = …               rebuild by
 ─────────  ────────────────────────────────────  ────────────────  ───────────────────────  ─────────────────────────
 config     monitors, key permissions, retention  git               git                      bootstrap + provision (§6, §7)
 secrets    passwords, ke, uid, API key codes     .env, super.json  your password manager    .env by hand; codes regenerated
 history    Videos rows, Logs, Events, *.mp4      volumes           mysqldump + volume copy  not rebuildable: restore or lose
```

The repo cannot hold the last two rows (CLAUDE.md: no secrets, no DB dumps, no media). So
"rebuild from the repo" means config + a fresh history.

### 5.2 Shinobi's own export/import (superuser)

```
 export/system  for each table in [Users, Monitors, API, Videos, Cloud Videos, Logs, Files, Presets]
                if form field <table>=1 → SELECT * → {"ok":true,"database":{"Users":[…],…}}
 import/system  per row: SELECT by identity columns (API: ke,code,uid · Users: ke,uid,mail ·
                Monitors: ke,host,protocol,port,path,mid · Videos: ke,time,mid · …)
                found → skip (rowsExistingAlready)   not found → INSERT the raw row
```

Measured export (Users, Monitors, API): 30.9 kB, 1 + 4 + 2 rows; `Users.pass` = 64-hex sha256,
`API.code` in plain text, `Monitors.host/path` unredacted. Consequences:

- **Insert-if-absent, not a sync.** Importing a backup over a changed monitor does nothing and
  still answers `ok: true`.
- It **preserves** generated values (uid, key codes, hashes): the right tool to *move* a live
  install to another host. The wrong tool for the repo: the file is a secret.

### 5.3 Our backup commands

```bash
mkdir -p data/backups && chmod 700 data/backups               # data/ and *.sql are gitignored
( umask 077; docker compose exec -T shinobi mysqldump --single-transaction ccio \
    > data/backups/ccio-$(date -u +%Y%m%dT%H%M%SZ).sql )       # 188 kB here; consistent (InnoDB)
# --- the two below are standard but NOT exercised in this session ---
# restore into a running, empty Shinobi (then restart it: Shinobi caches users/keys/monitors)
docker compose exec -T shinobi mysql ccio < data/backups/ccio-….sql && docker compose restart shinobi
# recordings: copy the volume (tar via a throwaway container)
docker run --rm -v shinobi_shinobi-videos:/v:ro -v "$PWD/data/backups:/b" debian:bookworm-slim \
    tar -C /v -cf /b/videos-$(date -u +%F).tar .
```

### 5.4 A fresh clone needs nothing from the old conf.json

Diff of our `conf.json` against `conf.sample.json` from the pinned image: only `db`, `ssl`,
`cpuUsageMarker`, `databaseType`, `subscriptionId`, all rewritten by `init.sh` on every start.
So `scripts/shinobi-seed.sh` + one start reproduce it. If conf.json is ever changed on purpose
(e.g. `mqttClient`, Session 18), it needs its own "as code" step.

---

## 6. The account as code: `scripts/shinobi_bootstrap.py`

```
 .env creds + shinobi-config/account.yaml
   │
   1 superuser   login(.env) ── fails ──▶ login(admin@shinobi.video / admin) ──▶ saveSettings(.env)
   2 account     accounts/list ── missing ──▶ registerAdmin(ke = SHINOBI_GROUP_KEY, details)
                                └ details differ ──▶ editAdmin(details)   (merge, only managed keys)
   3 admin login ──▶ auth_token, ke, uid
   4 API keys    api/list ── .env code unknown ──▶ api/add (no code) ──▶ new random code
                           └ ip/permissions differ ──▶ api/add (code) = update, code kept
                           └ codes not in account.yaml ──▶ "extra" (deleted only with --prune)
   5 .env        SHINOBI_GROUP_KEY, SHINOBI_UID, key codes   (in place, atomic, 0600)
```

- `plan` logs in and writes nothing; exit 2 = changes pending (like `provision_monitors.py plan`).
- Login budget: on a fresh install exactly one failed login (the `.env` superuser), cleared by
  the default login that follows. On a configured install none. No retries anywhere.
- Account `details` managed: `size 20000`, `days 1` (S10), and `use_remote_access`,
  `use_plugin_manager`, `use_system_control` = `"0"`: **new accounts default them to `"1"`**,
  so a naive rebuild would switch on Shinobi's cloud remote access.
- First live run (UI-made install): `superuser ok`, `account ok`, read key `update`
  (`edit_user "1" -> "0"`, `get_alarms "1" -> "0"`, same code), `.env` byte-identical; second
  plan: 4 ok, exit 0.

---

## 7. Rebuild runbook (done-when: < 10 min)

```bash
# fresh clone on a new machine
cp .env.example .env               # fill SHINOBI_URL, *_EMAIL, *_PASSWORD, SHINOBI_GROUP_KEY (any
                                   # letters+digits, or empty); leave UID and key codes empty
./scripts/fetch_media.sh           # footage (S3)
scripts/sim-up.sh                  # seeds data/shinobi/*.json, starts mediamtx, shinobi, cameras
uv run python scripts/shinobi_bootstrap.py apply --wait 180   # superuser, account, keys → .env
uv run python scripts/provision_monitors.py apply             # monitors
docker compose restart shinobi     # ONLY if old recordings exist: boot sweep re-indexes them (§7.2)
uv run pytest -m e2e tests/test_shinobi.py tests/test_shinobi_bootstrap.py \
                     tests/test_provision_monitors.py tests/test_shinobi_client.py
uv run python scripts/shinobi_bootstrap.py plan && uv run python scripts/provision_monitors.py plan
                                   # both exit 0 = nothing left to do
```

### 7.1 Drill (2026-10-31): wipe the DB volume + both host files, keep recordings

Backup first (§5.3), then `sim-down` → `docker volume rm shinobi_shinobi-db` → move
`data/shinobi/{conf,super}.json` aside → the runbook above.

```
 +0 s    sim-down                                   containers + network removed
 +1 s    volume rm, host files moved                (a fresh clone, as far as Shinobi can tell)
 +1 s    sim-up                                     seeded conf.json + super.json from the image
 +46 s   bootstrap apply   ✗ "unrecognized arguments: --wait 180"     ◀ bug, see below
 ── fixed, resumed on the same fresh install ──
 +0 s    bootstrap plan    update superuser (default -> .env) · create account (ke = SHINOBI_GROUP_KEY)
                           · create 2 keys · .env: GROUP_KEY, UID, key codes        exit 2
 +1 s    bootstrap apply   same steps; .env updated: SHINOBI_API_KEY, SHINOBI_UID,
                           SHINOBI_EDIT_API_KEY (GROUP_KEY unchanged: chosen value)
 +2 s    provision apply   4 create
 +21 s   done
         e2e: 6 passed (1 s) · bootstrap plan: 4 ok, exit 0 · provision plan: 4 unchanged, exit 0
         monitors: all four recording, 1280x720
         restart shinobi: up in 9 s, sweep done ~15 s later (§7.2)
```

Machine time: 46 s + 21 s + 1 s + ~25 s restart/sweep ≈ **1.5 min**, well under 10 min. A new
machine adds the image pull and `fetch_media.sh`.

The bug: `--wait` was defined on the main argparse parser, which only accepts it *before* the
subcommand (`--wait 180 apply`), while the docstring and runbook say `apply --wait 180`. The
unit tests called `run()`, never the argument parser. Now `--wait` sits on each subcommand
(parent parser) and `test_cli_accepts_documented_forms` parses the documented forms.

### 7.2 Old recordings after a DB rebuild: invisible until a restart

After the rebuild the files were still on disk (same `ke` → same directory
`videos/GROUP_KEY/<mid>/`, 15–19 per monitor), but `Videos` was **empty**: not in the API, not in
disk usage, never purged. The full orphan sweep only runs at boot:

```
 libs/startup.js checkForOrphanedVideos()      for each monitor LOADED AT BOOT:
   find videos/<ke>/<mid>/ -maxdepth 1 -type f | sort -r      checkMax 'unlimited'
   → INSERT a Videos row for each file without one
 libs/monitor/utils.js spawn_exit              after "Process Unexpected Exit": newest 2 files only
```

At our boot the DB had no monitors; they arrived 20 s later through the API, and nothing sweeps
then. Source comment: *"otherwise orphans older than the newest few are never adopted and go
missing from disk use forever"*. After `docker compose restart shinobi`:

```
$SYSTEM log  "Orphaned Videos Found and Inserted"
             {"GROUP_KEY":{"cam_gate":16,"cam_quay":17,"cam_yard":20,"cam_waterway":17}}
Videos       rows = files on disk, oldest 15:00 UTC, every (mid, time) once
```

So: **keep `ke` stable** (otherwise the old files sit in another directory that no monitor ever
sweeps) and **restart once after provisioning**. The restart cuts the open segments (S10).

Side effect, checked after the segments closed at 22:30: the sweep also adopts files **still
being written**, so each open segment got **two rows**:

```
mid       time      end       size
cam_gate  22:19:34  22:19:39    1048624   ◀ boot sweep: size/end of the file AT SCAN (5 s in)
cam_gate  22:19:34  22:30:01  156609898   ◀ normal row when the segment closed
(same for quay, waterway, yard: 4 of 4)
```

That is the mechanism behind the cron `ENOENT` of §9.7 and the "same video listed twice" of
S10/S11: the size purge deletes the file and one row, the age purge later finds the other row,
`unlink` fails, the row is removed anyway. Harmless for us (dedupe by `(mid, time)` and keep the
larger `size`), but the disk usage Shinobi sums from rows is too high until then.

---

## 8. Troubleshooting

| Symptom | Cause | Fix / where |
|---|---|---|
| Shinobi exits "FAILED TO OPEN CONFIGURATION FILE"; `data/shinobi/conf.json/` is a directory | bind source missing, Docker made a dir | `rmdir`, `scripts/shinobi-seed.sh` (S8) |
| New digest pulled, version unchanged | whole `/home/Shinobi` mounted | mount only data (S8) |
| All logins fail, even correct ones | lock after 5 failures/IP, 15 min; answer identical to wrong password | wait, or `docker compose restart shinobi` (S8, §2) |
| `curl :8080` refused right after `up` | ~25 s start-up | `bootstrap --wait`, poll (S8) |
| API answers `200 {"ok":false}` / `[]` | bad key / missing permission / unknown mid | check the body, not the status (S11) |
| Host/path/credentials blank in monitor GET | read-only key redacts | edit key (S12) |
| Deleted or narrowed key still works | only if removed by SQL (cache `s.api`) | change keys via API / bootstrap (§2) |
| `.env` keys stop working after a DB wipe | key codes and uid are generated | `shinobi_bootstrap.py apply` rewrites `.env` (§6) |
| Old recordings missing from the API after a DB rebuild, disk fills up | orphan sweep runs only at boot, for monitors that exist then | same `ke`, `docker compose restart shinobi` after provisioning (§7.2) |
| Rebuilt account has remote access / plugin manager on | new-account defaults `"1"` | `account.yaml` sets them `"0"` (§6) |
| Monitor ID `cam-gate` saved as `camgate` | `mid` stripped to `[A-Za-z0-9_]` | `cam_gate` (S9) |
| URL with `localhost` → monitor dies | `localhost` = Shinobi container | `mediamtx` (S9) |
| Monitor "Died" though camera is back | 10-min back-off after 3 fails | restart monitor; S25 watchdog (S9) |
| Every edit interrupts recording 4–7 s | save = ffprobe + 2 s + restart | provision only diffs (S12) |
| A `"1"` setting has no effect | stored as number 1, code checks `=== '1'` | send `"\"1\""` (§4.1); provisioner does it for all flags since S14 |
| Packet loss / DTS errors invisible in Shinobi | stderr lines dropped (§4.2) | streamprobe, check_stream.sh |
| Footage gone after ~7 h despite `days 1` | size cap (20000 MB / 4 cams) decides first | size vs days on purpose (S10) |
| Videos deleted, nothing logged | size purge doesn't log | own audit (S25) (S10) |
| `CRON.js … Video Delete Error … ENOENT` in `docker compose logs` | duplicate `Videos` row of a segment adopted while open (§7.2); purge deleted the file via the other row | harmless, row is removed anyway; dedupe by `(mid, time)` |
| `ERROR 1396 … CREATE USER failed for 'majesticflame'@'127.0.0.1'` on a fresh DB volume | image ships an initialised datadir, `init.sh` user creation runs anyway | harmless (S8) |
| `moov atom not found` | file open or FFmpeg killed | wait for close; fMP4 (S25) (S10) |
| Same video listed twice | orphan scan at start-up | dedupe by (mid, time) (S10, S11) |
| Transcoded recording huge / 1 fps / long GOP | x264 defaults, monitor fps 1 | copy, or CRF ~23 + GOP flags (S10) |
| "Add monitor" fails after 15 | unactivated, `maxCameras` | ≤ 14 monitors (S9, ADR 0002) |
| Saved superuser password gone after restart | `thisIsDocker: true` → `/config/super.json` | keep it unset (§1) |

---

## 9. Findings of this session

1. **Generated identifiers.** Only `ke` can be chosen; `uid` and API key codes are always
   random, so rebuilding means rewriting `.env` (§2, §6).
2. **Key revocation is immediate through the API** (measured: 4 monitors → `[]` after the
   permission update → `Not Authorized` after delete; second request of a key 32 → 17 ms = the
   `s.api` cache). Closes the Session 11/12 open item.
3. **Least privilege drift from the UI**: the "read-only" key could edit the account (`edit_user`).
4. **`=== '1'` vs stored numbers**: `sqllog` cannot be enabled with `"1"`; `"\"1\""` works (§4.1). S14: the real trap is the no-restart edit (events stop being saved), see [14 §8](14-motion-detection.md#L264).
5. **Shinobi drops stream-health stderr** (§4.2).
6. **Login answers leak**: superuser login returns the password hash; fixtures drop it
   (`scripts/record_shinobi_fixtures.py scrub_session`).
7. **Cron `ENOENT`**: on 2026-10-30 15:00 the hourly age purge failed to unlink exactly one file
   per monitor, the first segment after Session 12 deleted and re-created the monitors
   (13:48:29–34 → 14:00). Every other segment purged cleanly and the rows are gone. A `Videos`
   row survived its file: a duplicate row for one file, reproduced in the drill (§7.2).
8. **export/import** is insert-if-absent and holds secrets (§5.2).
9. **Rebuild drill passed**: wipe → working, idempotent install in ≈ 1.5 min of machine time,
   after fixing a CLI bug the unit tests could not see (§7.1).
10. **Orphan sweep only at boot**: old recordings stay unindexed (and unpurged) until Shinobi
    restarts with the monitors in place (§7.2).

## 10. Open items

- Session 14: a non-forced log message to see `sqllog` (`"\"1\""`) take effect; `snap`,
  `width/height` and the sub stream once Shinobi decodes.
- Session 18: `conf.json` changes (MQTT) need an "as code" step (§5.4).
- Session 25: graceful stop, fMP4, own purge audit and index ↔ disk reconciliation (§7.2).
- Session 32 (one-command stack): fold bootstrap + provision (+ the restart of §7.2 when
  recordings exist) into the start script.
