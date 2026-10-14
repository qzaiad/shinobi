# ADR 0001 — Shinobi image and database

- Status: accepted
- Date: 2026-10-14 (Session 8)

## Context

Shinobi needs a MySQL-compatible database (accounts, monitors, settings, the index of videos and
events). Upstream offers two Docker routes:

1. **`registry.gitlab.com/shinobi-systems/shinobi:dev`**, built by the Shinobi repo's CI from its
   own `Dockerfile` (Debian 11, Node 20, FFmpeg 4.3, **MariaDB 10.5 built in**). The entrypoint
   `/usr/local/bin/shinobi-init.sh` *always* starts this built-in MariaDB, whatever `DB_HOST` says.
2. **ShinobiDocker** (gitlab.com/Shinobi-Systems/ShinobiDocker): no published image; `docker
   compose build` clones the `dev` branch at build time, plus a separate `mysql:5.7` container.

The `*-no-db` tags of the registry image are stale (built 2024-05, Node 18).

## Decision

Use the registry image **with its built-in MariaDB**, pinned by digest (`SHINOBI_DIGEST` in
`.env`), and persist only data:

| Mount | Kind | Holds |
|---|---|---|
| `/var/lib/mysql` | named volume `shinobi-db` | the database |
| `/home/Shinobi/videos` | named volume `shinobi-videos` | recordings |
| `/home/Shinobi/conf.json`, `super.json` | bind mounts from `data/shinobi/` (gitignored), seeded by `scripts/shinobi-seed.sh` | server config, superuser |
| `/dev/shm/streams` | tmpfs 512 MB | live-view segments |

## Reasons

- A separate DB container would not replace the built-in one: `init.sh` starts it anyway, so we
  would run two databases. Changing that means overriding the image's entrypoint, i.e. coupling to
  its internals.
- ShinobiDocker builds from a moving branch at build time: not reproducible, no digest to pin.
- The project contract says services never read another service's internals; nothing but
  Shinobi will ever talk to this database (we use the REST API, webhooks and MQTT), so where the
  DB runs is Shinobi's private detail.
- The upstream docs mount all of `/home/Shinobi`. That copies the application *into* the volume,
  and a newer image then changes nothing. Mounting only data keeps the code in the image, so a new
  digest really upgrades.

## Consequences

- The DB password stays empty. `init.sh` creates users with `${DB_PASS}` but writes
  `${DB_PASSWORD}` into `conf.json` (upstream mismatch); and with a named volume the user-creation
  block never runs, because Docker copies the image's already-initialised datadir into the empty
  volume. Accepted because the DB only allows `majesticflame@127.0.0.1` and `root@localhost`;
  a connection from another container is refused (`ERROR 1130 Host … is not allowed`).
- Scaling (Session 34) to several Shinobi instances or an external DB would need revisiting this.
- `docker compose down -v` deletes accounts and recordings.
