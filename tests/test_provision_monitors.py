"""Monitors as code (scripts/provision_monitors.py): render, Shinobi's stored form, plan, apply.

    uv run pytest tests/test_provision_monitors.py               # unit, no stack
    uv run pytest -m e2e tests/test_provision_monitors.py        # live == rendered (edit key)

The central property is idempotency: apply on a drifted Shinobi saves the drifted monitors,
a second apply saves nothing (each save would restart a camera). FakeShinobiStore below keeps
monitors the way Shinobi does (strings JSON-parsed, empty columns NULL on insert), so the test
fails if render() and Shinobi's storage disagree about any value.
"""

from __future__ import annotations

import copy
import itertools
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from scripts import provision_monitors as pm
from services.common.envfile import load_env
from services.common.shinobi_client import ShinobiClient, ShinobiConfig
from sim import gen_compose

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "shinobi"


@pytest.fixture(scope="module")
def template() -> dict[str, Any]:
    return pm.load_template(pm.DEFAULT_TEMPLATE)


@pytest.fixture(scope="module")
def fleet() -> gen_compose.Fleet:
    return gen_compose.load(pm.DEFAULT_CONFIG)


@pytest.fixture
def desired(fleet: gen_compose.Fleet, template: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return pm.render(fleet, template)


# -- Shinobi's stored form --------------------------------------------------------------------


def test_stored_form_parses_json_strings_like_shinobi() -> None:
    sent = {"detector": "0", "cords": "[]", "cutoff": "15", "primary_input": "0:0", "x": "true"}
    assert pm.stored_form(sent) == {
        "detector": 0,  # the Session 9 finding: "0" is stored as 0
        "cords": [],
        "cutoff": 15,
        "primary_input": "0:0",  # not JSON: stays a string
        "x": True,
    }


def test_stored_form_follows_javascript_numbers_and_json() -> None:
    assert pm.stored_form({"a": 1.0, "b": "1e3", "c": 0.5}) == {"a": 1, "b": 1000, "c": 0.5}
    # Python's json accepts NaN/Infinity, JSON.parse does not: they stay (whitelisted) strings.
    assert pm.stored_form({"a": "NaN", "b": "Infinity"}) == {"a": "NaN", "b": "Infinity"}


@pytest.mark.parametrize("value", ["a;b", "x<y", "rtsp://u:p~w@cam/1", "back\\slash"])
def test_stored_form_refuses_values_shinobi_would_alter(value: str) -> None:
    # Stored != sent would make every plan show a change and every apply restart the camera.
    with pytest.raises(pm.ConfigError, match=r"details\.notes"):
        pm.stored_form({"details": {"notes": value}})


def test_stored_form_allows_comparisons_inside_detector_filters() -> None:
    filters = {"f1": {"where": [{"p2": ">", "p3": "0.5"}]}}
    assert pm.stored_form({"detector_filters": filters}) == {
        "detector_filters": {"f1": {"where": [{"p2": ">", "p3": 0.5}]}}
    }


def test_template_is_already_in_stored_form(template: dict[str, Any]) -> None:
    # Exported from a stored monitor; must stay so, or the diff against Shinobi never settles.
    assert pm.stored_form(template) == template


# -- template + render ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"width": 1920}, "set per camera"),
        ({"details": {"auto_host": "rtsp://x/y"}}, "set per camera"),
        ({"details": {"tv_channel_id": ""}}, "set per camera"),
        ({"status": "Recording"}, "not monitor columns"),
        ({"details": None}, "needs a `details` object"),
    ],
)
def test_template_validation(tmp_path: Path, patch: dict[str, Any], message: str) -> None:
    raw = json.loads(pm.DEFAULT_TEMPLATE.read_text(encoding="utf-8"))
    if isinstance(patch.get("details"), dict):
        raw["details"] |= patch["details"]
    else:
        raw |= patch
    path = tmp_path / "t.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(pm.ConfigError, match=message):
        pm.load_template(path)


def test_monitor_id() -> None:
    assert pm.monitor_id("cam-gate") == "cam_gate"
    with pytest.raises(pm.ConfigError, match="would alter"):
        pm.monitor_id("cam.gate")


def test_render_one_monitor_per_camera(
    fleet: gen_compose.Fleet, desired: dict[str, dict[str, Any]]
) -> None:
    assert list(desired) == [pm.monitor_id(c.id) for c in fleet.cameras]
    for cam in fleet.cameras:
        m = desired[pm.monitor_id(cam.id)]
        assert (m["name"], m["fps"], m["width"], m["height"]) == (
            cam.id,
            cam.fps,
            cam.width,
            cam.height,
        )
        url = f"{m['protocol']}://{m['host']}:{m['port']}{m['path']}"
        assert url == m["details"]["auto_host"]
        # Shinobi pulls exactly the URL the simulated camera publishes its main stream to.
        assert url in gen_compose.ffmpeg_command(cam, fleet.defaults)
        assert m["details"]["tv_channel_id"] == pm.monitor_id(cam.id)  # never empty, see above
        assert m["mode"] == "record" and m["details"]["vcodec"] == "copy"
        assert "tags" not in m and "ke" not in m


def test_committed_monitor_files_are_up_to_date() -> None:
    assert pm.main(["render", "--check"]) == 0, "run scripts/provision_monitors.py render"


def test_credentials_are_refused_for_files(desired: dict[str, dict[str, Any]]) -> None:
    m = copy.deepcopy(desired["cam_gate"])
    m["details"]["auto_host"] = "rtsp://admin:secret@cam/1"
    with pytest.raises(pm.ConfigError, match="credentials"):
        pm.check_no_credentials(m)
    m = copy.deepcopy(desired["cam_gate"])
    m["details"]["mpass"] = "secret"
    with pytest.raises(pm.ConfigError, match="mpass"):
        pm.check_no_credentials(m)


# -- plan ---------------------------------------------------------------------------------------


def test_plan_kinds(desired: dict[str, dict[str, Any]]) -> None:
    actual = copy.deepcopy({k: v for k, v in desired.items() if k != "cam_yard"})
    actual["cam_quay"]["width"], actual["cam_quay"]["height"] = 640, 480
    actual["cam_quay"]["details"]["probesize"] = 1000000000
    actual["cam_gate"]["tags"] = None  # NULL from INSERT == absent
    actual["cam_old"] = copy.deepcopy(desired["cam_gate"]) | {"mid": "cam_old"}
    got = {a.mid: a for a in pm.plan(desired, actual)}
    assert {mid: a.kind for mid, a in got.items()} == {
        "cam_gate": "unchanged",
        "cam_quay": "update",
        "cam_yard": "create",
        "cam_waterway": "unchanged",
        "cam_old": "extra",
    }
    assert got["cam_quay"].changes == (
        "details.probesize: 1000000000 -> 1000000",
        "height: 480 -> 720",
        "width: 640 -> 1280",
    )


def test_plan_sees_type_changes() -> None:
    # True == 1 in Python, but Shinobi compares strings ("1"): a type change is a change.
    assert pm.changes({"a": True}, {"a": 1}) == ["a: 1 -> true"]
    assert pm.changes({"a": {"b": 1}}, {"a": {}}) == ["a.b: (missing) -> 1"]


# -- apply ----------------------------------------------------------------------------------------


class FakeShinobiStore:
    """Keeps monitors like Shinobi's addOrEditMonitor: JSON-parsed strings, columns filtered,
    empty top-level strings NULL on insert, kept on update. Answers like getMonitors: an empty
    details.tv_channel_id comes back as a new random "temp_XXXXX" on every read."""

    def __init__(self, monitors: Mapping[str, dict[str, Any]] | None = None) -> None:
        self.rows: dict[str, dict[str, Any]] = copy.deepcopy(dict(monitors or {}))
        self.saved: list[str] = []
        self.deleted: list[tuple[str, bool]] = []
        self._ids = itertools.count()

    def monitor_configs(self) -> dict[str, dict[str, Any]]:
        rows = copy.deepcopy(self.rows)
        for row in rows.values():
            if not row["details"].get("tv_channel_id"):
                row["details"]["tv_channel_id"] = f"temp_{next(self._ids):05d}"
        return rows

    def save_monitor(self, config: Mapping[str, Any]) -> str:
        cleaned = pm.stored_form(json.loads(json.dumps(config)))
        row = {c: cleaned.get(c) for c in pm.MONITOR_COLUMNS if c in cleaned}
        mid = row["mid"]
        self.saved.append(mid)
        if mid not in self.rows:  # INSERT: falsy values are skipped -> NULL
            self.rows[mid] = {c: (v or None) if isinstance(v, str) else v for c, v in row.items()}
            return "Monitor Added by user. : USER_ID"
        # UPDATE: null/false are skipped, i.e. the column keeps its old value
        self.rows[mid] |= {c: v for c, v in row.items() if v is not None and v is not False}
        return "Monitor Updated by user. : USER_ID"

    def delete_monitor(self, mid: str, *, delete_files: bool = False) -> None:
        del self.rows[mid]
        self.deleted.append((mid, delete_files))


def test_apply_is_idempotent(desired: dict[str, dict[str, Any]]) -> None:
    drifted = copy.deepcopy(desired)
    for mid in ("cam_quay", "cam_yard"):
        drifted[mid]["width"], drifted[mid]["height"] = 640, 480
    del drifted["cam_waterway"]
    store = FakeShinobiStore(drifted)
    lines: list[str] = []

    pm.apply(store, desired, log=lines.append)
    assert store.saved == ["cam_quay", "cam_yard", "cam_waterway"]
    assert lines[-1].startswith("done: 1 create, 2 update, 1 unchanged, 0 extra")

    store.saved.clear()
    second = pm.apply(store, desired, log=lines.append)
    assert store.saved == [], "second apply must not save (a save restarts the camera)"
    assert {a.kind for a in second} == {"unchanged"}


def test_apply_after_wipe_recreates_identical_monitors(desired: dict[str, dict[str, Any]]) -> None:
    store = FakeShinobiStore()
    pm.apply(store, desired, log=lambda _: None)
    assert {mid: pm.comparable(row) for mid, row in store.rows.items()} == desired


def test_plan_saves_nothing(desired: dict[str, dict[str, Any]]) -> None:
    store = FakeShinobiStore()
    actions = pm.apply(store, desired, dry_run=True, log=lambda _: None)
    assert [a.kind for a in actions] == ["create"] * 4 and store.saved == []


def test_extra_monitor_deleted_only_with_prune_and_never_its_files(
    desired: dict[str, dict[str, Any]],
) -> None:
    store = FakeShinobiStore(desired | {"cam_old": desired["cam_gate"] | {"mid": "cam_old"}})
    pm.apply(store, desired, log=lambda _: None)
    assert "cam_old" in store.rows
    pm.apply(store, desired, prune=True, log=lambda _: None)
    assert store.deleted == [("cam_old", False)]


def test_plan_against_recorded_live_configs(desired: dict[str, dict[str, Any]]) -> None:
    """The real GET /monitor answer (edit key) parses into exactly the stored columns."""
    raw = json.loads((FIXTURES / "monitors_edit_key.json").read_text(encoding="utf-8"))
    actual = {m["mid"]: {c: m.get(c) for c in pm.MONITOR_COLUMNS} for m in raw}
    for action in pm.plan(desired, actual):
        assert action.kind in ("update", "unchanged")
        for change in action.changes:  # no runtime field may leak into the comparison
            assert change.split(":")[0].split(".")[0] in pm.MONITOR_COLUMNS


# -- live -----------------------------------------------------------------------------------------


@pytest.mark.e2e
def test_live_monitors_equal_rendered(desired: dict[str, dict[str, Any]]) -> None:
    env = load_env(REPO / ".env")
    if not env.get("SHINOBI_EDIT_API_KEY"):
        pytest.skip("set SHINOBI_EDIT_API_KEY in .env (Get Monitors + Edit Monitors)")
    with ShinobiClient(ShinobiConfig.from_env(env, key_var="SHINOBI_EDIT_API_KEY")) as shinobi:
        actions = pm.plan(desired, shinobi.monitor_configs())
    assert [a for a in actions if a.kind != "unchanged"] == []
