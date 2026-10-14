# Session 8 — Shinobi in Docker

How Shinobi is built inside its container, what happens on every start, where its state lives,
and why this repo mounts it the way it does. All outputs are real, captured from this repo on
2026-10-14 with image `registry.gitlab.com/shinobi-systems/shinobi@sha256:aa5cd832…`
(built 2026-09-02, Shinobi commit `63755122`). Decision record: [ADR 0001](../adr/0001-shinobi-image-and-database.md).

```
 host (127.0.0.1 only)                 compose network shinobi_default
 ─────────────────────                 ───────────────────────────────────────────────────────────
                                        ┌─ cam-gate … cam-waterway ─┐  RTSP/TCP   ┌─ mediamtx ─────┐
 browser / tests ── :8554 ─────────────▶│ ffmpeg publishers          │───publish──▶│ :8554          │
                                        └────────────────────────────┘             └──────┬─────────┘
                                                                                          │ RTSP pull
 browser / tests ── :8080 ──┐                                                     (Session 9)
                            ▼                                                             ▼
                     ┌─ shinobi ──────────────────────────────────────────────────────────────────┐
                     │ camera.js (Node.js) ── HTTP :8080: UI, /super, REST API, WebSocket         │
                     │      │ spawns one ffmpeg per monitor (from Session 9)                       │
                     │      ▼ SQL over 127.0.0.1:3306                                             │
                     │ mariadbd (built in)                                                         │
                     └─────────────────────────────────────────────────────────────────────────────┘
                       volumes: shinobi-db  shinobi-videos  data/shinobi/*.json  tmpfs /dev/shm/streams
```

---

## 1. What an NVR is, and where Shinobi fits

An **NVR** (network video recorder) is the server side of a CCTV system. IP cameras only *encode
and send* video; the NVR does everything else:

| NVR job | What it means | In Shinobi |
|---|---|---|
| Ingest | Pull each camera's stream (RTSP) and keep reconnecting when it drops | one FFmpeg process per **monitor** |
| Record | Write the stream to disk in segments (e.g. 15-min MP4 files), delete old ones | `videos/` + `Videos` table |
| Live view | Re-package the stream for browsers (they can't play RTSP) | HLS / MJPEG / FLV, segments in `/dev/shm/streams` |
| Detect | Motion detection, object detection via plugins | Session 14–16 |
| Events out | Tell other systems "something happened" | webhooks, MQTT, API (Session 17–18) |
| Users | Accounts, permissions, API keys | `Users`, `API` tables |

A **monitor** is Shinobi's word for "one configured camera input": URL, mode (watch / record),
output settings, detector settings. Monitors are rows in the database, not files.

## 2. Inside the container: process tree

Captured with `docker compose exec shinobi ps -eo pid,ppid,rss,args`:

```
 PID PPID    RSS  COMMAND
   1    0   1268  /usr/bin/tini -- /usr/local/bin/shinobi-init.sh pm2-docker /usr/local/share/shinobi/pm2.yml
   7    1  53068  node /usr/local/bin/pm2-docker /usr/local/share/shinobi/pm2.yml
  10    7   1632  /bin/sh /usr/bin/mysqld_safe --user=mysql
 124   10  86140  /usr/sbin/mariadbd --basedir=/usr --datadir=/var/lib/mysql ...
 125   10   2564  logger -t mysqld -p daemon error
 183    7 219784  node /home/Shinobi/camera.js
```

As a tree, with the role of each piece:

```
 tini (PID 1)                  tiny init: forwards SIGTERM, reaps zombie children
  └─ shinobi-init.sh           entrypoint script; at the end `exec "$@"` REPLACES itself with:
     = pm2-docker (PID 7)      PM2 process manager in foreground ("no daemon") mode
        ├─ mysqld_safe          started by init.sh in the background (&), so PM2 inherited it
        │   └─ mariadbd         the database server (~86 MB)
        └─ node camera.js       Shinobi itself (~220 MB): web server, monitor manager
            │   (thread)        libs/cron.js: a worker_threads thread, not a process (so not in ps):
            │                   housekeeping, deletes old videos/logs ("CRON.js LOG … Worker Processing!")
            └─ ffmpeg × N       one per active monitor (none yet; Session 9)
```

Terms:

- **PID 1 problem.** In a container the first process is PID 1, the role `init`/`systemd` plays on
  a normal system. The kernel gives PID 1 two special duties: it receives orphaned children (and
  must `wait()` for them, or they stay as `<defunct>` *zombies*), and signals without a handler
  are ignored for it (so a plain `docker stop` SIGTERM would do nothing). **tini** is a ~1 MB init
  that does exactly these two things. Upstream added it because ffmpeg processes left behind when
  Shinobi force-kills a camera were piling up as zombies (issue #461 in the Dockerfile comment).
- **PM2** is a Node.js process manager: it starts `camera.js`, restarts it when it crashes, and
  collects its logs. `pm2-docker` (= `pm2-runtime`) runs it in the foreground so the container
  lives exactly as long as PM2. `pm2.yml` sets `kill_timeout: 5000`: on stop, PM2 waits 5 s for
  `camera.js` to exit before SIGKILL. Our compose file gives the container `stop_grace_period: 30s`
  so Docker doesn't SIGKILL everything (MariaDB included) after the default 10 s.
- **`camera.js`** is the whole application in one Node.js process: HTTP/WebSocket server, the
  monitor manager that spawns and watches ffmpeg, the detector-plugin socket, the event logic.

What breaks without these: no tini → zombie ffmpeg processes accumulate over days; no PM2 → a
crash of `camera.js` ends the container (Docker's `restart:` would restart it, but also MariaDB).

## 3. Startup timeline (`init.sh`)

From `docker compose logs --timestamps shinobi`, first start on an empty volume:

```
 t (s)   log line                                              what happens
 ─────   ──────────────────────────────────────────────────    ──────────────────────────────────────────
  0.0    MariaDB Directory ... ibdata1 mysql ...               init.sh lists /var/lib/mysql: ALREADY initialised (see §5)
  0.0    Starting MariaDB ...                                  mysqld_safe & (background)
  5.0    Wait for MySQL server ... / mysqld is alive           fixed `sleep 5s`, then mysqladmin ping loop
  5.1    Create database user if it does not exists ...        source sql/user.sql (errors ignored: `|| true`)
  5.9    db: {"host": "localhost","user": "majesticflame",...} tools/modifyConfiguration.js REWRITES conf.json
  6.0    Default Superuser : admin@shinobi.video / admin       informational only
  6.0    Starting Shinobi ...                                  exec pm2-docker
  8.1    PM2 log: App [camera:0] online
 11.9    Available Hardware Acceleration Methods : vdpau, vaapi, qsv, drm, opencl
 25.9    FFmpeg version : 4.3.9-0+deb11u2 / Node.js v20.20.2    (~14 s of capability probing)
 27.0    Shinobi : Web Server Listening on 8080
 27.5    Creating Table "Logs" … "Custom Settings"             18 tables, ONLY on first start
 30.5    This Install of Shinobi is NOT Activated               no paid licence: fine for us (see below)
 30.6    Shinobi is ready.
```

Second start (after `down && up`): 0 `Creating Table` lines, HTTP up after ~22 s.

Things to know from this:

- **`conf.json` is rewritten on every start.** `modifyConfiguration.js` merges `cpuUsageMarker`,
  `subscriptionId`, `pluginKeys`, `databaseType`, `db`, `ssl` from env vars into it. So for these
  keys the env vars win; anything else (e.g. `mqttClient` in Session 18) you edit in the file and
  it survives. That's why `conf.json` is a gitignored bind mount and not committed: it would show
  up modified after each start.
- **"NOT Activated"** refers to Shinobi's paid subscription (`SUBSCRIPTION_ID`, link in the log).
  We run unactivated; what that restricts is not checked yet. Watch for it from Session 9 on, and
  don't fill in `SUBSCRIPTION_ID` with anything made up.
- Startup is slow (~25 s before the web server listens). A test or a dependent service must poll
  until `:8080` answers, not assume it's there right after `docker compose up`.

## 4. Where state lives

```
 ┌────────────────── shinobi container ──────────────────────┐
 │ /home/Shinobi/            ← application code: from the IMAGE (not persisted, replaced on upgrade)
 │   camera.js libs/ web/ plugins/ node_modules/ ...
 │   conf.json   ◀──────── bind ── data/shinobi/conf.json    server config (port, db, mqtt…)
 │   super.json  ◀──────── bind ── data/shinobi/super.json   superuser e-mail + password hash
 │   videos/     ◀──────── named volume shinobi-videos       recordings (Session 10)
 │ /var/lib/mysql/ ◀────── named volume shinobi-db           ALL accounts, monitors, settings,
 │                                                           API keys, video/event index
 │ /dev/shm/streams/ ◀──── tmpfs 512 MB (RAM)                live-view HLS segments, throwaway
 └───────────────────────────────────────────────────────────┘
```

| What you do in the UI | Where it is stored | Survives `down && up` because |
|---|---|---|
| Change superuser password in `/super` | `super.json` (`pass` becomes a 64-hex sha256; the sample was a 32-hex md5 of `admin`) | bind mount |
| Create admin account | `ccio.Users` | volume `shinobi-db` |
| Add a monitor (Session 9) | `ccio.Monitors` | volume `shinobi-db` |
| Create API key (Session 11) | `ccio.API` | volume `shinobi-db` |
| Recording (Session 10) | file in `videos/` + row in `ccio.Videos` | both volumes, must stay in sync |

**Named volume vs bind mount.** A *bind mount* maps a host path into the container
(`./data/shinobi/conf.json`); you can see and edit it directly. A *named volume* (`shinobi-db`) is
a directory Docker manages (`/var/lib/docker/volumes/shinobi_shinobi-db/_data`, root-owned); it
lives until `docker volume rm` or `docker compose down -v`. Two behaviours matter here:

1. **An empty named volume is pre-filled from the image** at that path on first use. A bind mount
   never is: it simply *hides* whatever the image had there.
2. **Bind-mounting a file that doesn't exist on the host makes Docker create a *directory*** with
   that name. `camera.js` then fails to read `conf.json`. That's why `scripts/shinobi-seed.sh`
   copies the samples out of the image first (`docker run --rm --entrypoint cat … conf.sample.json`)
   and refuses to continue if it finds a directory there.

Shinobi writes both files with `fs.writeFile` (truncate + write, same inode), which is why a
single-file bind mount works. A tool that saves via "write temp file + rename" would break the
mount, because the rename replaces the inode the container sees with one it doesn't.

## 5. Why not mount all of `/home/Shinobi` (the upstream way)

Upstream documents `-v "$HOME/Shinobi":/home/Shinobi`. Because `/home/Shinobi` is where the
*application* lives, `init.sh` seeds an empty mount with a full copy of the code:

```
 image v1 ──first start──▶ volume gets camera.js v1 ─▶ runs v1
 image v2 (docker pull) ──▶ volume already has camera.js ─▶ seeding skipped ─▶ STILL runs v1
```

You pull an update and nothing changes, silently. Mounting only the data paths keeps the code in
the image: changing `SHINOBI_DIGEST` in `.env` really upgrades.

**Pinning.** `dev` is a moving tag (rebuilt on every push to Shinobi's `dev` branch). A *digest*
(`@sha256:…`) names one exact image forever. Same reason we pin `MTX_TAG`. To upgrade on purpose:

```bash
docker buildx imagetools inspect registry.gitlab.com/shinobi-systems/shinobi:dev   # → Digest: sha256:…
# put it into .env as SHINOBI_DIGEST=sha256:…, then scripts/sim-up.sh
```

## 6. The built-in database

The image installs MariaDB 10.5 and `init.sh` starts it unconditionally. Pointing `DB_HOST` at a
separate DB container would still leave this one running, which is why we use it
([ADR 0001](../adr/0001-shinobi-image-and-database.md)).

A surprise found while checking access: the image ships `/var/lib/mysql` **already initialised**
(files dated 2026-09-02, image build time), and Docker copies that into the empty `shinobi-db`
volume. So `init.sh`'s `if [ ! -f /var/lib/mysql/ibdata1 ]` block, which would create
`root@'%'` and `${DB_USER}@'%'` (with `${DB_PASS}`, a variable that is never set, while `conf.json`
gets `${DB_PASSWORD}`), **never runs**. The real accounts:

```
$ docker compose exec -T shinobi mysql -e "select user,host from mysql.user"
mariadb.sys    localhost
root           localhost        ← unix socket only; `docker compose exec … mysql` works without password
mysql          localhost
majesticflame  127.0.0.1        ← what camera.js uses, empty password
```

MariaDB listens on all interfaces (`bind_address` is empty, `:::3306` in `/proc/net/tcp6`), but
the host part of every account rejects other machines. Tested from a throwaway container on the
same compose network:

```
$ docker run --rm --network shinobi_default --entrypoint mysql <shinobi image> -h shinobi -u root -e 'select 1'
ERROR 1130 (HY000): Host 'condescending_hypatia.shinobi_default' is not allowed to connect to this MariaDB server
```

Consequence: keep `DB_PASSWORD` empty (the default). Setting it would make `conf.json` disagree
with the actual `majesticflame` account and Shinobi could no longer log in to its own database.

Useful for looking around (read-only habits; don't edit rows by hand, Shinobi caches them):

```bash
docker compose exec shinobi mysql ccio -e 'show tables'
docker compose exec shinobi mysql ccio -e 'select ke,uid,mail from Users'   # ke = group key
```

## 7. Superuser vs admin vs group key

```
 superuser (super.json)          one per Shinobi install; only /super panel: create/delete accounts,
   │                             server-wide settings, logs. Cannot see cameras.
   │ creates
   ▼
 admin account (Users row)       owns a GROUP, identified by the group key `ke` (random, 10 chars here).
   │                             Monitors, videos, API keys all belong to a group: every API URL is
   │                             /<API_KEY>/<endpoint>/<GROUP_KEY>/<MONITOR_ID>  (Session 11)
   └─ sub-accounts (optional)    same group, restricted permissions
```

The admin's group key is what goes into `SHINOBI_GROUP_KEY` in Session 11.

## 8. Logging in from code (`tests/test_shinobi.py`)

The UI login form posts to the same handler the tests use (`libs/webServerPaths.js`,
"API : Login handler"); `?json=true` makes it answer JSON instead of an HTML page:

```
POST /super?json=true   mail=…&pass=…&function=super      → {"ok": true, "$user": {mail, pass, sessionKey, usingDefaultPassword}, …}
POST /?json=true        mail=…&pass=…&function=dashboard  → {"ok": true, "$user": {ke, uid, mail, auth_token, details, …}, …}
```

- `usingDefaultPassword` is computed by Shinobi; the test asserts it is `false`, i.e. the default
  `admin` password really was changed.
- **Brute-force lock:** 5 failed logins from one client IP → that IP gets `{"ok": false}` for
  15 min, even with correct credentials. All requests from the host arrive with the same IP, so
  the tests never try wrong passwords on purpose.
- The login response includes the superuser's password *hash*; never log the whole body.

```
$ uv run pytest -m e2e tests/test_shinobi.py -v
tests/test_shinobi.py::test_web_server_up PASSED
tests/test_shinobi.py::test_superuser_password_changed PASSED
tests/test_shinobi.py::test_admin_account_persists PASSED
```

## 9. The "done when" check

```bash
scripts/sim-down.sh                       # containers + network removed; volumes stay
docker volume ls --filter name=shinobi    # shinobi_shinobi-db, shinobi_shinobi-videos
scripts/sim-up.sh
uv run pytest -m e2e tests/test_shinobi.py   # 3 passed: superuser + admin survived
```

`docker compose down -v` (with `-v`) would delete both volumes: accounts, monitors and recordings
are gone, and the next start looks like a fresh install (`super.json` survives, it's on the host).

## 10. Resource use (idle, no monitors)

```
$ docker stats --no-stream
shinobi-shinobi-1    1.5 %    210 MiB
```

The cameras use ~2.5–3 cores each for live x264 encoding; Shinobi itself is idle until it gets
monitors in Session 9. Each monitor adds one ffmpeg process, which is what Session 10 measures.

## Gotchas collected

| Symptom | Cause | Fix |
|---|---|---|
| Shinobi exits "FAILED TO OPEN CONFIGURATION FILE", `data/shinobi/conf.json/` is a directory | bind mount source didn't exist, Docker created a dir | `rmdir`, run `scripts/shinobi-seed.sh` |
| Pulled a new image, version unchanged | full `/home/Shinobi` mount kept the old code | mount only data (this repo's layout) |
| Shinobi can't reach its DB after setting `DB_PASSWORD` | `init.sh` creates users with `DB_PASS` (and only on first init); conf gets `DB_PASSWORD` | leave it empty |
| All logins fail, even correct ones | brute-force lock after 5 failures, 15 min | wait, or `docker compose restart shinobi` (lock is in memory) |
| `curl localhost:8080` refused right after `up` | ~25 s startup (capability probing) | poll until it answers |
