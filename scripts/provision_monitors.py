"""Shinobi monitors as code: cameras.yaml + a template -> monitors, provisioned idempotently.

    uv run python scripts/provision_monitors.py render [--check]   # write shinobi-config/monitors/
    uv run python scripts/provision_monitors.py plan               # dry run: what apply would do
    uv run python scripts/provision_monitors.py apply [--prune]    # save only what differs
    uv run python scripts/provision_monitors.py export [--out DIR] # live configs -> JSON files

Pipeline (docs/notes/12-monitors-as-code.md):

    sim/cameras.yaml ──┐ (id, fps, width, height; camera URL = gen_compose.RTSP_BASE/<id>)
    monitor.template ──┼─ render (pure) ─▶ desired[mid] ─┐
    detection.yaml ────┘ (motion regions, S14)           ├─ plan ─▶ create | update | unchanged
    Shinobi GET /monitor (edit key) ─────▶ actual[mid] ──┘          | extra (only --prune deletes)

Why "compare, then save": every save restarts the monitor's FFmpeg (a gap in the recording), so
re-sending everything on each run is not harmless. A second `apply` must do nothing.

Three forms of one config (Session 12, Session 14):

    template (numbers, readable) ─ stored_form ─ runtime_form ─▶ desired ─ wire_form ─▶ POST
                                                                    ▲                    │
                                                                    └── Shinobi parses ──┘
                                                                        (stored_form)

- stored_form(): Shinobi JSON.parses every string it receives (libs/basic/utils.js
  cleanStringsInObject), so "0" is stored as 0, and refuses values Shinobi would silently alter.
- runtime_form(): top-level `details` numbers as strings (1 -> "1"). That is what a *started*
  monitor holds in memory (libs/monitor.js s.camera -> convertNumbersToStrings), and Shinobi's
  code compares flags with === '1'. A save that changes only live-updatable detection values
  (regions, sensitivities) skips the restart and keeps the parsed form in memory: with numbers
  there, detector_save === '1' is false and no event is stored any more (measured, S14).
- wire_form(): a string Shinobi would JSON.parse is sent JSON-encoded ("1" -> "\"1\""), so it
  is stored as the string "1". stored_form(wire_form(desired)) == desired, tested.
If desired and actual differed in type, every run would see a change and restart every camera.

Needs SHINOBI_EDIT_API_KEY in .env (permissions: Get Monitors + Edit Monitors): with the
read-only key Shinobi redacts the camera address in GET answers and refuses saves.
"""

from __future__ import annotations

import argparse
import copy
import difflib
import json
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))  # run as a plain script: make `services` and `sim` importable

from scripts import detection as det  # noqa: E402
from services.common.envfile import load_env  # noqa: E402
from services.common.shinobi_client import (  # noqa: E402
    MONITOR_COLUMNS,
    ShinobiClient,
    ShinobiConfig,
    ShinobiError,
)
from sim import gen_compose  # noqa: E402

DEFAULT_CONFIG = REPO / "sim" / "cameras.yaml"
DEFAULT_TEMPLATE = REPO / "shinobi-config" / "monitor.template.json"
DEFAULT_DETECTION = det.DEFAULT_CONFIG
DEFAULT_OUT = REPO / "shinobi-config" / "monitors"

# Set per camera by render(); a template that sets them too would be a second source of truth.
PER_CAMERA = frozenset(
    {"mid", "name", "protocol", "host", "port", "path", "fps", "width", "height"}
)
# tv_channel_id: GET /monitor invents "temp_" + 5 random chars for an empty one on EVERY answer
# (libs/monitor/utils.js getMonitors; the DB keeps ""), so "" can never compare equal.
PER_CAMERA_DETAILS = frozenset({"auto_host", "tv_channel_id"})
# Shinobi stores an empty top-level string as NULL on INSERT but as "" on UPDATE (libs/monitor.js
# addOrEditMonitor), so a never-edited monitor has NULL where an edited one has "". Same meaning.
EMPTY_MEANS_UNSET = ("tags", "saveDir")
CREDENTIAL_DETAILS = ("muser", "mpass", "rtmp_key")

# Characters a non-JSON string keeps (cleanStringsInObject): everything else is deleted.
# JS uses [^\w\p{L}\p{N}...] with the u flag; Python's Unicode \w covers letters, digits and _.
_KEEP = r"\w\s.\-=+(){}\[\]*$@!`^%#:?/&,'|\""
_STRIP = re.compile(f"[^{_KEEP}]")
_STRIP_IN_FILTERS = re.compile(f"[^{_KEEP}<>]")  # detector_filters may hold comparisons


class ConfigError(Exception):
    """Template or camera config that cannot become a Shinobi monitor as written."""


def monitor_id(camera_id: str) -> str:
    """cam-gate -> cam_gate. Shinobi deletes every character outside [A-Za-z0-9_] from a mid."""
    mid = camera_id.replace("-", "_")
    if not re.fullmatch(r"[A-Za-z0-9_]+", mid):
        raise ConfigError(f"camera id {camera_id!r} -> monitor id {mid!r}: Shinobi would alter it")
    return mid


def _js_json_parse(text: str) -> tuple[bool, Any]:
    """JSON.parse semantics: (True, value) or (False, None). Python's json also accepts NaN and
    Infinity, JavaScript does not."""

    def no_constants(name: str) -> Any:
        raise ValueError(name)

    try:
        return True, json.loads(text, parse_constant=no_constants)
    except ValueError:
        return False, None


def stored_form(value: Any, path: str = "", in_filters: bool = False) -> Any:
    """What Shinobi stores when it receives `value` (cleanStringsInObject, then JSON.stringify).

    - a string that is valid JSON is replaced by the parsed value: "0" -> 0, "[]" -> [];
    - any other string loses the characters outside the whitelist: refused here with
      ConfigError instead, because the stored value would never match the desired one;
    - JavaScript has one number type: 1.0 is stored as 1.
    """
    if isinstance(value, Mapping):
        return {
            k: stored_form(v, f"{path}.{k}" if path else k, in_filters or k == "detector_filters")
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [stored_form(v, f"{path}[{i}]", in_filters) for i, v in enumerate(value)]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        ok, parsed = _js_json_parse(value)
        if ok:
            # cleanStringsInObject recurses into a parsed object; a parsed string stays as is.
            return parsed if isinstance(parsed, str) else stored_form(parsed, path, in_filters)
        cleaned = (_STRIP_IN_FILTERS if in_filters else _STRIP).sub("", value)
        if cleaned != value:
            raise ConfigError(f"{path}: Shinobi would store {value!r} as {cleaned!r}")
    return value


def _js_number_string(value: int | float, path: str) -> str:
    """Number.prototype.toString for the values monitor configs hold (no exponent forms)."""
    text = str(value) if isinstance(value, int) else repr(value)
    if not re.fullmatch(r"-?\d+(\.\d+)?", text):
        raise ConfigError(f"{path}: {value!r} has no plain JavaScript string form")
    return text


def runtime_form(config: Mapping[str, Any]) -> dict[str, Any]:
    """Top-level `details` numbers -> strings, like Shinobi's convertNumbersToStrings on start.
    Nested values (cords, input_maps, ...) stay as they are: Shinobi leaves them too."""
    out = dict(config)
    details = config.get("details")
    if isinstance(details, Mapping):
        out["details"] = {
            k: _js_number_string(v, f"details.{k}")
            if isinstance(v, int | float) and not isinstance(v, bool)
            else v
            for k, v in details.items()
        }
    return out


def wire_form(value: Any) -> Any:
    """What to send so that Shinobi stores `value`: every string JSON.parse would accept is sent
    JSON-encoded ("1" -> '"1"'), Shinobi parses one layer and keeps the string."""
    if isinstance(value, Mapping):
        return {k: wire_form(v) for k, v in value.items()}
    if isinstance(value, list):
        return [wire_form(v) for v in value]
    if isinstance(value, str) and _js_json_parse(value)[0]:
        return json.dumps(value, ensure_ascii=False)
    return value


def load_template(path: Path) -> dict[str, Any]:
    template = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(template.get("details"), dict):
        raise ConfigError(f"{path}: needs a `details` object")
    unknown = set(template) - set(MONITOR_COLUMNS)
    if unknown:  # Shinobi would drop them (cleanMonitorObjectForDatabase): a silent no-op
        raise ConfigError(f"{path}: not monitor columns: {sorted(unknown)}")
    per_camera = (set(template) & PER_CAMERA) | (set(template["details"]) & PER_CAMERA_DETAILS)
    if per_camera:
        raise ConfigError(f"{path}: set per camera from cameras.yaml, remove: {sorted(per_camera)}")
    return template


def render(
    fleet: gen_compose.Fleet,
    template: Mapping[str, Any],
    detection: det.Detection | None = None,
) -> dict[str, dict[str, Any]]:
    """Pure: cameras + template (+ detection.yaml) -> desired config per monitor id, in
    Shinobi's stored form.

    The camera URL is the one gen_compose publishes to (RTSP_BASE/<id>), so the simulator and
    Shinobi cannot disagree about it. Cameras listed in detection.yaml get motion detection on
    top of the template (scripts/detection.py). Order = cameras.yaml order.
    """
    if detection is not None:
        det.check_cameras(detection, fleet)
    base = urlsplit(gen_compose.RTSP_BASE)
    if base.hostname is None or base.port is None:
        raise ConfigError(f"RTSP_BASE {gen_compose.RTSP_BASE!r} needs host and port")
    monitors: dict[str, dict[str, Any]] = {}
    for cam in fleet.cameras:
        mid = monitor_id(cam.id)
        cfg = copy.deepcopy(dict(template))
        cfg |= {
            "mid": mid,
            "name": cam.id,
            "protocol": base.scheme,
            "host": base.hostname,
            "port": base.port,
            "path": f"/{cam.id}",
            # Descriptive for `copy` recording; used for scaling once Shinobi decodes (S14).
            "fps": cam.fps,
            "width": cam.width,
            "height": cam.height,
        }
        cfg["details"]["auto_host"] = f"{gen_compose.RTSP_BASE}/{cam.id}"
        cfg["details"]["tv_channel_id"] = mid  # name in Shinobi's TV-channel (M3U) list
        if detection is not None:
            cfg["details"] |= det.details_for(detection, cam, fleet.defaults)
        monitors[mid] = comparable(runtime_form(stored_form(cfg)))
    return monitors


def comparable(config: Mapping[str, Any]) -> dict[str, Any]:
    """The stored columns of a monitor, with NULL/"" of EMPTY_MEANS_UNSET dropped."""
    out = {c: config[c] for c in MONITOR_COLUMNS if c in config}
    for c in EMPTY_MEANS_UNSET:
        if out.get(c) in (None, ""):
            out.pop(c, None)
    return out


def check_no_credentials(config: Mapping[str, Any]) -> None:
    """Refuse configs that would put camera credentials into a committed file."""
    details = config.get("details") or {}
    filled = [k for k in CREDENTIAL_DETAILS if details.get(k)]
    if urlsplit(str(details.get("auto_host") or "")).password is not None:
        filled.append("auto_host (user:password@)")
    if filled:
        raise ConfigError(f"monitor {config.get('mid')!r} has credentials in {filled}")


def dump(config: Mapping[str, Any]) -> str:
    """One file per monitor: sorted keys, so git diffs show exactly the changed settings."""
    return json.dumps(config, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def changes(desired: Any, actual: Any, path: str = "") -> list[str]:
    """Human-readable differences, one per leaf ("details.cutoff: 10 -> 15")."""
    if isinstance(desired, Mapping) and isinstance(actual, Mapping):
        out: list[str] = []
        for k in sorted(set(desired) | set(actual), key=str):
            sub = f"{path}.{k}" if path else str(k)
            if k not in actual:
                out.append(f"{sub}: (missing) -> {json.dumps(desired[k])}")
            elif k not in desired:
                out.append(f"{sub}: {json.dumps(actual[k])} -> (not in desired)")
            else:
                out += changes(desired[k], actual[k], sub)
        return out
    # bool is an int in Python (True == 1): compare JSON text so true vs 1 counts as a change.
    if json.dumps(desired, sort_keys=True) == json.dumps(actual, sort_keys=True):
        return []
    return [f"{path}: {json.dumps(actual)} -> {json.dumps(desired)}"]


Kind = Literal["create", "update", "unchanged", "extra"]


@dataclass(frozen=True, slots=True)
class Action:
    mid: str
    kind: Kind
    changes: tuple[str, ...] = ()


def plan(
    desired: Mapping[str, Mapping[str, Any]], actual: Mapping[str, Mapping[str, Any]]
) -> list[Action]:
    """Desired order first, then monitors Shinobi has but the repo doesn't ("extra")."""
    actions: list[Action] = []
    for mid, want in desired.items():
        if mid not in actual:
            actions.append(Action(mid, "create"))
            continue
        diff = changes(want, comparable(actual[mid]))
        actions.append(Action(mid, "update", tuple(diff)) if diff else Action(mid, "unchanged"))
    actions += [Action(mid, "extra") for mid in sorted(set(actual) - set(desired))]
    return actions


class MonitorApi(Protocol):
    """The part of ShinobiClient that apply() uses (tests pass a fake)."""

    def monitor_configs(self) -> dict[str, dict[str, Any]]: ...
    def save_monitor(self, config: Mapping[str, Any]) -> str: ...
    def delete_monitor(self, mid: str, *, delete_files: bool = False) -> None: ...


def apply(
    api: MonitorApi,
    desired: Mapping[str, Mapping[str, Any]],
    *,
    prune: bool = False,
    dry_run: bool = False,
    log: Any = print,
) -> list[Action]:
    """Bring Shinobi to `desired`: save created/changed monitors, one at a time (each save
    restarts that monitor). Extra monitors are deleted only with prune, never their files."""
    actions = plan(desired, api.monitor_configs())
    for a in actions:
        log(f"{a.kind:<9} {a.mid}")
        for c in a.changes:
            log(f"            {c}")
        if dry_run:
            continue
        if a.kind in ("create", "update"):
            log(f"            -> {api.save_monitor(wire_form(desired[a.mid]))}")
        elif a.kind == "extra" and prune:
            api.delete_monitor(a.mid)
            log("            -> deleted (recordings kept)")
    counts = {k: sum(a.kind == k for a in actions) for k in ("create", "update", "unchanged")}
    extra = sum(a.kind == "extra" for a in actions)
    pruned = "deleted" if prune and not dry_run else "kept, use --prune to delete"
    log(
        f"{'plan' if dry_run else 'done'}: {counts['create']} create, {counts['update']} update, "
        f"{counts['unchanged']} unchanged, {extra} extra ({pruned})"
    )
    return actions


# -- command line -------------------------------------------------------------------------------


def _render_files(desired: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    for cfg in desired.values():
        check_no_credentials(cfg)
    return {f"{mid}.json": dump(cfg) for mid, cfg in desired.items()}


def _check_files(out: Path, files: Mapping[str, str]) -> int:
    stale = 0
    existing = {p.name for p in out.glob("*.json")} if out.is_dir() else set()
    for name in sorted(existing - set(files)):
        print(f"provision: {out / name} has no camera in cameras.yaml", file=sys.stderr)
        stale += 1
    for name, text in files.items():
        path = out / name
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if current != text:
            sys.stderr.writelines(
                difflib.unified_diff(
                    current.splitlines(keepends=True),
                    text.splitlines(keepends=True),
                    fromfile=f"{path} (committed)",
                    tofile=f"{path} (rendered)",
                )
            )
            stale += 1
    if stale:
        print("provision: shinobi-config/monitors is out of date; run `render`", file=sys.stderr)
    return 1 if stale else 0


def _write_files(out: Path, files: Mapping[str, str]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.json"):
        if old.name not in files:
            old.unlink()
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8")
    print(f"provision: wrote {len(files)} monitor files to {out}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    ap.add_argument("--detection", type=Path, default=DEFAULT_DETECTION)
    sub = ap.add_subparsers(dest="command", required=True)
    p_render = sub.add_parser("render", help="write desired configs to shinobi-config/monitors/")
    p_render.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p_render.add_argument("--check", action="store_true", help="exit 1 if files are out of date")
    sub.add_parser("plan", help="show what apply would change (no writes)")
    p_apply = sub.add_parser("apply", help="create/update monitors that differ")
    p_apply.add_argument("--prune", action="store_true", help="delete monitors not in the YAML")
    p_export = sub.add_parser("export", help="write live monitor configs as JSON files")
    p_export.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    try:
        desired = render(
            gen_compose.load(args.config), load_template(args.template), det.load(args.detection)
        )
        if args.command == "render":
            files = _render_files(desired)
            if args.check:
                return _check_files(args.out, files)
            _write_files(args.out, files)
            return 0
        env = load_env(REPO / ".env")
        with ShinobiClient(ShinobiConfig.from_env(env, key_var="SHINOBI_EDIT_API_KEY")) as api:
            match args.command:
                case "plan" | "apply":
                    actions = apply(api, desired, prune=getattr(args, "prune", False),
                                    dry_run=args.command == "plan")  # fmt: skip
                    if args.command == "plan" and any(a.kind != "unchanged" for a in actions):
                        return 2  # like `terraform plan -detailed-exitcode`: 2 = changes pending
                case "export":
                    live = {mid: comparable(cfg) for mid, cfg in api.monitor_configs().items()}
                    _write_files(args.out, _render_files(live))
    except (gen_compose.ConfigError, ConfigError, det.DetectionError, ShinobiError) as exc:
        print(f"provision: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
