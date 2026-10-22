"""Shinobi API client: unit tests on recorded payloads + one live e2e test.

    uv run pytest tests/test_shinobi_client.py                 # unit (fixtures, no stack)
    uv run pytest -m e2e tests/test_shinobi_client.py          # live, needs stack + .env key

How the unit tests work
=======================

    ShinobiClient ── httpx.Client ── MockTransport(handler) ── tests/fixtures/shinobi/*.json
                     (real request building,      (instead of a TCP socket: a Python function
                      params, URL encoding)        that answers from recorded Shinobi payloads)

The fixtures were recorded from the live instance by scripts/record_shinobi_fixtures.py with the
API key / group key replaced by the literal strings API_KEY / GROUP_KEY. The tests use exactly
those strings as credentials, so the replayed answers are what Shinobi sends for that key.
Re-record after a Shinobi upgrade and let these tests show what changed.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import yaml

from services.common.envfile import load_env
from services.common.shinobi_client import (
    MonitorCode,
    ShinobiAuthError,
    ShinobiClient,
    ShinobiConfig,
    ShinobiError,
    SnapshotUnavailable,
)

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "shinobi"
CONFIG = ShinobiConfig(url="http://shinobi.test", api_key="API_KEY", group_key="GROUP_KEY")
CAMERA_MONITORS = {"cam_gate", "cam_quay", "cam_yard", "cam_waterway"}


def fixture(name: str) -> bytes:
    return (FIXTURES / f"{name}.json").read_bytes()


# URL path -> recorded answer. Query strings are ignored here; tests that care inspect requests.
ROUTES: dict[str, bytes] = {
    "/API_KEY/monitor/GROUP_KEY": fixture("monitors"),
    "/API_KEY/monitor/GROUP_KEY/cam_gate": fixture("monitor_cam_gate"),
    "/API_KEY/monitor/GROUP_KEY/no_such_monitor": fixture("monitor_unknown"),
    "/API_KEY/videos/GROUP_KEY/cam_gate": fixture("videos_cam_gate"),
    "/API_KEY/logs/GROUP_KEY/cam_gate": fixture("logs_cam_gate"),
}


class FakeShinobi:
    """Callable for httpx.MockTransport; remembers every request it got."""

    def __init__(self, routes: dict[str, bytes] | None = None) -> None:
        self.routes = ROUTES if routes is None else routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = self.routes.get(request.url.path)
        if body is None:  # what Shinobi does for a wrong key or group key: 200 + ok=false
            body = fixture("not_authorized")
        ctype = "image/jpeg" if request.url.path.endswith(".jpg") else "application/json"
        return httpx.Response(200, content=body, headers={"content-type": ctype})


@pytest.fixture
def fake() -> FakeShinobi:
    return FakeShinobi()


@pytest.fixture
def client(fake: FakeShinobi) -> ShinobiClient:
    return ShinobiClient(CONFIG, transport=httpx.MockTransport(fake))


def client_with(handler: Callable[[httpx.Request], httpx.Response], **cfg: str) -> ShinobiClient:
    return ShinobiClient(replace(CONFIG, **cfg), transport=httpx.MockTransport(handler))


# -- monitors -------------------------------------------------------------------------------------


def test_monitors_parses_all_four_cameras(client: ShinobiClient) -> None:
    monitors = {m.mid: m for m in client.monitors()}
    assert set(monitors) == CAMERA_MONITORS
    gate = monitors["cam_gate"]
    assert gate.mode == "record"
    # "3" on the wire -> enum; the label is for display only (language dependent)
    assert gate.code is MonitorCode.RECORDING
    assert gate.status == "Recording"
    assert (gate.width, gate.height, gate.fps) == (1280, 720, 15)
    assert gate.stream_types == ("hls",)
    assert gate.details["vcodec"] == "copy"  # Session 10 setting, visible through the API


def test_read_only_key_gets_redacted_camera_address(client: ShinobiClient) -> None:
    # The recording key lacks "Can Edit Monitors": Shinobi blanks host/path/credentials
    # (removeSenstiveInfoFromMonitorConfig). Session 12 needs a key that can edit.
    for m in client.monitors():
        assert m.host == ""
        assert m.details["muser"] == "" and m.details["mpass"] == ""


def test_single_monitor(client: ShinobiClient, fake: FakeShinobi) -> None:
    assert client.monitor("cam_gate").mid == "cam_gate"
    assert fake.requests[-1].url.path == "/API_KEY/monitor/GROUP_KEY/cam_gate"


def test_unknown_monitor_is_an_error_not_an_empty_answer(client: ShinobiClient) -> None:
    with pytest.raises(ShinobiError, match="no_such_monitor"):
        client.monitor("no_such_monitor")


def test_wrong_key_raises_although_http_status_is_200() -> None:
    fake = FakeShinobi(routes={})  # every path answers {"ok": false, "msg": "Not Authorized"}
    with pytest.raises(ShinobiAuthError, match="Not Authorized"):
        client_with(fake).monitors()


# -- videos ---------------------------------------------------------------------------------------


def test_videos_parse_times_as_utc(client: ShinobiClient) -> None:
    videos = client.videos("cam_gate", limit=3)
    assert videos, "fixture has videos"
    for v in videos:
        assert v.mid == "cam_gate"
        assert v.time.tzinfo is not None and v.time.utcoffset() == timedelta(0)
        assert v.end >= v.time
        assert v.size > 0
        assert v.filename == v.time.strftime("%Y-%m-%dT%H-%M-%S.mp4")  # name = start time
    assert videos == sorted(videos, key=lambda v: v.time, reverse=True)  # newest first


def test_videos_query_parameters(client: ShinobiClient, fake: FakeShinobi) -> None:
    cest = timezone(timedelta(hours=2))
    client.videos(
        "cam_gate",
        start=datetime(2026, 10, 22, 14, 40, tzinfo=cest),  # sent as UTC: 12:40Z
        end=datetime(2026, 10, 22, 13, 30, tzinfo=UTC),
        started_in_window=True,
        limit=3,
    )
    params = fake.requests[-1].url.params
    assert params["start"] == "2026-10-22T12:40:00Z"
    assert params["end"] == "2026-10-22T13:30:00Z"
    assert params["endIsStartTo"] == "1"
    assert params["limit"] == "3"


def test_naive_datetime_is_rejected(client: ShinobiClient) -> None:
    # Shinobi would read it in the container's local zone; refuse instead of guessing.
    with pytest.raises(ValueError, match="naive"):
        client.videos("cam_gate", start=datetime(2026, 10, 22, 12, 40))


def test_video_url(client: ShinobiClient) -> None:
    v = client.videos("cam_gate")[0]
    raw = json.loads(fixture("videos_cam_gate"))["videos"][0]
    # same URL Shinobi itself returns as href
    assert client.video_url(v) == CONFIG.url + raw["href"]


# -- logs, streams --------------------------------------------------------------------------------


def test_logs(client: ShinobiClient, fake: FakeShinobi) -> None:
    entries = client.logs("cam_gate", limit=2)
    assert [e.type for e in entries] == ["Process Started", "Process Started"]
    assert "-rtsp_transport tcp" in entries[0].msg["cmd"]  # the generated FFmpeg command
    assert fake.requests[-1].url.params["limit"] == "2"


def test_stream_url_matches_what_shinobi_returns(client: ShinobiClient) -> None:
    raw = json.loads(fixture("monitor_cam_gate"))[0]
    assert client.stream_url("cam_gate", "hls") == CONFIG.url + raw["streams"][0]
    with pytest.raises(ValueError):
        client.stream_url("cam_gate", "rtsp")


# -- snapshot -------------------------------------------------------------------------------------


def test_snapshot_refused_when_monitor_has_no_snapshot_output(
    client: ShinobiClient, fake: FakeShinobi
) -> None:
    # Shinobi would answer 200 image/jpeg with its placeholder (web/libs/img/bg.jpg).
    with pytest.raises(SnapshotUnavailable):
        client.snapshot("cam_gate")
    assert not any(r.url.path.endswith(".jpg") for r in fake.requests)


def _snap_enabled_routes(jpeg_body: bytes) -> dict[str, bytes]:
    monitor = json.loads(fixture("monitor_cam_gate"))
    monitor[0]["details"]["snap"] = "1"
    return {
        "/API_KEY/monitor/GROUP_KEY/cam_gate": json.dumps(monitor).encode(),
        "/API_KEY/jpeg/GROUP_KEY/cam_gate/s.jpg": jpeg_body,
    }


def test_snapshot_returns_jpeg_bytes() -> None:
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 16
    assert client_with(FakeShinobi(_snap_enabled_routes(jpeg))).snapshot("cam_gate") == jpeg


def test_snapshot_json_failure_body_is_detected() -> None:
    # 200 + image path, but the body is the 46-byte {"ok":false} JSON
    fake = FakeShinobi(_snap_enabled_routes(fixture("not_authorized")))
    with pytest.raises(ShinobiAuthError):
        client_with(fake).snapshot("cam_gate")


# -- errors never leak the key --------------------------------------------------------------------

SECRET = "s3cr3tKEYs3cr3tKEYs3cr3tKEY123"


def test_http_error_message_hides_api_key() -> None:
    client = client_with(lambda r: httpx.Response(500), api_key=SECRET)
    with pytest.raises(ShinobiError) as info:
        client.monitors()
    assert "HTTP 500" in str(info.value)
    assert SECRET not in str(info.value)


def test_connect_error_message_hides_api_key() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"refused: {request.url}", request=request)

    with pytest.raises(ShinobiError) as info:
        client_with(refuse, api_key=SECRET).monitors()
    assert SECRET not in str(info.value)


def test_config_repr_hides_api_key() -> None:
    config = ShinobiConfig(url="http://x", api_key=SECRET, group_key="g")
    assert SECRET not in repr(config)


def test_config_from_env_names_missing_variables() -> None:
    with pytest.raises(ShinobiError, match="SHINOBI_API_KEY"):
        ShinobiConfig.from_env({"SHINOBI_URL": "http://x", "SHINOBI_GROUP_KEY": "g"})


# -- live -----------------------------------------------------------------------------------------


@pytest.mark.e2e
def test_live_monitors_match_camera_fleet() -> None:
    """Every camera in sim/cameras.yaml is a running Shinobi monitor (id cam-x -> cam_x)."""
    env = load_env(REPO / ".env")
    if not env.get("SHINOBI_API_KEY"):
        pytest.skip("set SHINOBI_URL, SHINOBI_API_KEY, SHINOBI_GROUP_KEY in .env")
    fleet = yaml.safe_load((REPO / "sim" / "cameras.yaml").read_text(encoding="utf-8"))
    expected = {cam["id"].replace("-", "_") for cam in fleet["cameras"]}
    with ShinobiClient(ShinobiConfig.from_env(env)) as shinobi:
        monitors = {m.mid: m for m in shinobi.monitors()}
    assert set(monitors) == expected
    for m in monitors.values():
        assert m.code is MonitorCode.RECORDING, f"{m.mid}: {m.status}"
