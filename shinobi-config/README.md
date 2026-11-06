# shinobi-config

Shinobi monitors as code. Provisioned by `scripts/provision_monitors.py`; explained in
[docs/notes/12-monitors-as-code.md](../docs/notes/12-monitors-as-code.md) and, for motion detection and
the string form of `details`, [docs/notes/14-motion-detection.md](../docs/notes/14-motion-detection.md).

| File | Edited by | Content |
|---|---|---|
| `monitor.template.json` | hand | settings shared by every monitor, in Shinobi's stored form (`0`, not `"0"`) |
| `detection.yaml` | hand | motion detection per camera: detector input (sub/main), regions, thresholds ([note 14](../docs/notes/14-motion-detection.md)) |
| `monitors/<mid>.json` | **generated** (`render`), do not edit | desired config per camera = template + `sim/cameras.yaml` + `detection.yaml`, top-level `details` values as strings (Shinobi's runtime form) |

Per-camera values (`mid`, `name`, `protocol`, `host`, `port`, `path`, `fps`, `width`, `height`,
`details.auto_host`, `details.tv_channel_id`) come only from `sim/cameras.yaml` and
`sim/gen_compose.py` `RTSP_BASE`; the template may not contain them.

```bash
uv run python scripts/provision_monitors.py render            # after editing template or cameras.yaml
uv run python scripts/provision_monitors.py plan              # what would change (exit 2 = changes)
uv run python scripts/provision_monitors.py apply [--prune]   # save only differing monitors
uv run python scripts/provision_monitors.py export && git diff shinobi-config/   # live drift
```

Changed something in the UI? `export` writes the live configs over `monitors/`, `git diff` shows
what changed; move it into the template (or `cameras.yaml`), `render`, and the diff disappears.
No secrets here: `render`/`export` refuse configs with camera credentials.
