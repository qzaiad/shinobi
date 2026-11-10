"""Typed client for Shinobi's HTTP API.

    uv run python -m services.common.shinobi_client monitors
    uv run python -m services.common.shinobi_client videos [MONITOR_ID] [--limit N]
    uv run python -m services.common.shinobi_client events [MONITOR_ID] [--limit N]
    uv run python -m services.common.shinobi_client snapshot MONITOR_ID out.jpg

Every endpoint is GET /<api key>/<endpoint>/<group key>[/<monitor id>] on Shinobi's web port
(routes in libs/webServerPaths.js and libs/webServerStreamPaths.js, auth in libs/auth.js).
Writes (add/edit/delete monitor) go through configureMonitor (libs/webServerAdminPaths.js) and
need a key with "Can Edit Monitors". Explained in docs/notes/11-shinobi-api.md and
docs/notes/12-monitors-as-code.md. The traps this module exists to hide:

    1. A wrong key or a missing permission is HTTP 200 with {"ok": false, "msg": ...}, so the
       status code says nothing; the body is checked on every call (ShinobiAuthError).
    2. Success shapes differ per endpoint: monitor/logs/events return a bare JSON array,
       videos an object {"ok": true, "videos": [...]}.
    3. The snapshot URL answers 200 image/jpeg with a stock placeholder (web/libs/img/bg.jpg)
       when the monitor has no snapshot output (SnapshotUnavailable).
    4. The API key sits in the URL path, so it ends up in httpx's exception messages and in
       URLs Shinobi returns. Errors raised here carry the path with the key replaced.
       (httpx itself logs full URLs at INFO level on the "httpx" logger: keep that logger at
       WARNING in services.)
"""

from __future__ import annotations

import argparse
import enum
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self

import httpx

from services.common.envfile import load_env

JPEG_MAGIC = b"\xff\xd8"  # every JPEG starts with the SOI marker FF D8

# The columns of the Monitors table that a save stores (libs/monitor.js
# cleanMonitorObjectForDatabase, minus the group key `ke`). Everything else in a GET /monitor
# answer (status, code, streams, currentlyWatching, ...) is runtime state built per request.
MONITOR_COLUMNS = (
    "mid", "name", "type", "ext", "protocol", "host", "path", "port",
    "fps", "mode", "saveDir", "tags", "width", "height", "details",
)  # fmt: skip


class MonitorCode(enum.IntEnum):
    """Runtime state of a monitor's FFmpeg process (definitions/monitorStatusCodes.js).

    Shinobi sends it as a string ("3") next to a translated label ("Recording"); the code is
    the stable value, the label depends on the account language.
    """

    DISABLED = 0
    STARTING = 1
    WATCHING = 2
    RECORDING = 3
    RESTARTING = 4
    STOPPED = 5
    IDLE = 6
    DIED = 7
    STOPPING = 8
    STARTED = 9


class ShinobiError(Exception):
    """Any failure talking to Shinobi (transport, HTTP status, or {"ok": false})."""


class ShinobiAuthError(ShinobiError):
    """{"ok": false, "msg": "Not Authorized"}: wrong key/group key or permission missing."""


class SnapshotUnavailable(ShinobiError):
    """The monitor produces no snapshot; Shinobi would serve its placeholder image instead."""


@dataclass(frozen=True, slots=True)
class ShinobiConfig:
    url: str  # e.g. http://localhost:8080
    api_key: str = field(repr=False)  # never in repr(), logs or tracebacks
    group_key: str
    uid: str = ""  # only needed for the WebSocket login

    @classmethod
    def from_env(cls, env: Mapping[str, str], *, key_var: str = "SHINOBI_API_KEY") -> Self:
        """key_var picks the key: SHINOBI_API_KEY (read-only) or SHINOBI_EDIT_API_KEY."""
        names = ("SHINOBI_URL", key_var, "SHINOBI_GROUP_KEY")
        missing = [n for n in names if not env.get(n)]
        if missing:
            raise ShinobiError(f"missing configuration: {', '.join(missing)}")
        return cls(
            url=env["SHINOBI_URL"].rstrip("/"),
            api_key=env[key_var],
            group_key=env["SHINOBI_GROUP_KEY"],
            uid=env.get("SHINOBI_UID", ""),
        )


def _parse_time(value: str) -> datetime:
    # Shinobi sends UTC as "2026-10-22T12:45:00Z" (videos) or "...00.000Z" (logs, events).
    return datetime.fromisoformat(value)


def _format_time(value: datetime) -> str:
    # Shinobi parses query times with JavaScript `new Date(...)`: a string without a zone is
    # taken in the container's local time zone. Always send explicit UTC.
    if value.tzinfo is None:
        raise ValueError("naive datetime: pass a timezone-aware value (e.g. tzinfo=UTC)")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _int_or_none(value: Any) -> int | None:
    return None if value in (None, "") else int(value)


@dataclass(frozen=True, slots=True)
class Monitor:
    """One row of Shinobi's Monitors table plus the runtime fields of the running process."""

    # configuration (Monitors table)
    mid: str
    name: str
    mode: str  # "stop" | "start" (Watch-Only) | "record"
    type: str  # input type, e.g. "h264"
    fps: int | None
    width: int | None
    height: int | None
    host: str  # "" when the key may not edit monitors (Shinobi redacts it)
    details: Mapping[str, Any]  # the ~200 keys of the monitor settings, as sent
    # runtime (process state in Shinobi's memory); None/empty when the monitor isn't running
    code: MonitorCode | None
    status: str | None  # translated label of `code`, for display only
    stream_types: tuple[str, ...]  # live outputs, e.g. ("hls",)
    sub_stream_active: bool

    @classmethod
    def from_api(cls, raw: Mapping[str, Any]) -> Self:
        details = raw.get("details") or {}
        code = raw.get("code")
        return cls(
            mid=raw["mid"],
            name=raw.get("name", ""),
            mode=raw.get("mode", ""),
            type=raw.get("type", ""),
            fps=_int_or_none(raw.get("fps")),
            width=_int_or_none(raw.get("width")),
            height=_int_or_none(raw.get("height")),
            host=raw.get("host") or "",
            details=details,
            code=None if code in (None, "") else MonitorCode(int(code)),
            status=raw.get("status"),
            stream_types=tuple(raw.get("streamsSortedByType") or {}),
            sub_stream_active=bool(raw.get("subStreamActive")),
        )

    @property
    def snapshot_enabled(self) -> bool:
        # Stored as "1"/"0" or 1/0 depending on how it was saved (Session 9: DB keeps 0 for "0").
        return str(self.details.get("snap")) == "1"


@dataclass(frozen=True, slots=True)
class Video:
    """One finished recording segment (a row of the Videos table; Session 10)."""

    mid: str
    filename: str  # e.g. 2026-10-22T12-45-00.mp4, the name under videos/<ke>/<mid>/
    time: datetime  # start, UTC
    end: datetime  # end, UTC
    size: int  # bytes
    status: int  # 1 = unread, 2 = read (the dashboard's "watched" flag)
    archived: bool  # archived videos are skipped by retention

    @classmethod
    def from_api(cls, raw: Mapping[str, Any]) -> Self:
        return cls(
            mid=raw["mid"],
            filename=raw["filename"],
            time=_parse_time(raw["time"]),
            end=_parse_time(raw["end"]),
            size=int(raw["size"]),
            status=int(raw["status"]),
            archived=bool(raw.get("archive")),
        )

    @property
    def duration(self) -> timedelta:
        return self.end - self.time


@dataclass(frozen=True, slots=True)
class LogEntry:
    mid: str  # monitor id, or "$USER"/"$WEBSERVER"-style pseudo ids for non-monitor entries
    time: datetime
    type: str  # e.g. "Process Started"
    msg: Any  # free-form: string or object, depends on the type

    @classmethod
    def from_api(cls, raw: Mapping[str, Any]) -> Self:
        info = raw.get("info") or {}
        return cls(
            mid=raw["mid"],
            time=_parse_time(raw["time"]),
            type=info.get("type", ""),
            msg=info.get("msg"),
        )


@dataclass(frozen=True, slots=True)
class EventBox:
    """One detection box of an event ("matrix"), in detector pixels (imgWidth x imgHeight).

    Built-in motion: one box per connected blob of changed pixels, tag = region name,
    confidence = the blob's size in % of its region (pixel-change, Session 14).
    Object plugin: one box per detected object, tag = class label ("truck"), confidence = the
    model's score 0..1 (Session 15).
    """

    tag: str
    x: float
    y: float
    width: float
    height: float
    confidence: float

    @classmethod
    def from_api(cls, raw: Mapping[str, Any]) -> Self:
        return cls(
            tag=str(raw.get("tag", "")),
            x=float(raw.get("x", 0)),
            y=float(raw.get("y", 0)),
            width=float(raw.get("width", 0)),
            height=float(raw.get("height", 0)),
            confidence=float(raw.get("confidence", 0)),
        )


@dataclass(frozen=True, slots=True)
class Event:
    """One row of the Events table: an accepted detector trigger (Session 14).

    `name` is "multipleRegions" when conf.json detectorMergePamRegionTriggers is true: all
    regions that fired on one frame become one event with the *average* confidence, so the
    regions that fired are only visible in `boxes[].tag` (see `regions`).
    """

    mid: str
    time: datetime  # UTC, whole seconds (DATETIME column)
    plug: str  # "built-in" for the pam-diff motion detector, else the plugin name
    reason: str  # "motion", "object", ...
    name: str
    confidence: float  # motion: changed pixels in % of the region (merged: average)
    boxes: tuple[EventBox, ...]
    image_size: tuple[int, int] | None  # detector frame (width, height) the boxes refer to
    archived: bool
    # Object plugins: ms from the plugin receiving the frame to its answer ("time"). Includes
    # queueing inside the plugin; `time` above is when Shinobi accepted the trigger, so the
    # frame itself is about processing_ms older (Session 15).
    processing_ms: int | None = None

    @classmethod
    def from_api(cls, raw: Mapping[str, Any]) -> Self:
        d = raw.get("details") or {}
        w, h = _int_or_none(d.get("imgWidth")), _int_or_none(d.get("imgHeight"))
        return cls(
            mid=raw["mid"],
            time=_parse_time(raw["time"]),
            plug=str(d.get("plug", "")),
            reason=str(d.get("reason", "")),
            name=str(d.get("name", "")),
            confidence=float(d.get("confidence") or 0),
            boxes=tuple(EventBox.from_api(m) for m in d.get("matrices") or () if m),
            image_size=(w, h) if w and h else None,
            archived=bool(raw.get("archive")),
            processing_ms=_int_or_none(d.get("time")) if d.get("reason") == "object" else None,
        )

    @property
    def regions(self) -> tuple[str, ...]:
        """Regions that fired (box tags), sorted; falls back to `name` without boxes."""
        tags = sorted({b.tag for b in self.boxes if b.tag})
        return tuple(tags) if tags else (self.name,)

    @property
    def labels(self) -> tuple[str, ...]:
        """Object classes in this event (box tags), sorted, unique; () for no boxes."""
        return tuple(sorted({b.tag for b in self.boxes if b.tag}))


@dataclass(frozen=True, slots=True)
class TriggerResult:
    """Answer of GET /<key>/motion/<ke>/<mid>?data=...: did Shinobi accept the injected event?

    triggered=False with a reason is NOT an error: the event went through triggerEvent() and was
    dropped on purpose (event filter, motion lock, region check; libs/events/utils.js).
    """

    triggered: bool
    reason: str = ""  # e.g. "Ignored by Event Filters", "Ignored : Motion Lock"
    msg: str = ""


class ShinobiClient:
    """Shinobi API client; one pooled HTTP connection set per instance.

    What it may do is decided by the key's permissions, not by this class: the read methods
    work with the read-only key, monitor_configs/save_monitor/delete_monitor need "Can Edit
    Monitors" (Shinobi answers {"ok": false, "msg": "Not Authorized"} otherwise).

    Use as a context manager (closes the connection pool):

        with ShinobiClient(ShinobiConfig.from_env(load_env())) as shinobi:
            for m in shinobi.monitors(): ...
    """

    def __init__(
        self,
        config: ShinobiConfig,
        *,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,  # tests pass httpx.MockTransport
    ) -> None:
        self.config = config
        self._http = httpx.Client(base_url=config.url, timeout=timeout, transport=transport)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # -- low level ----------------------------------------------------------------------------

    def _path(self, endpoint: str, *rest: str) -> str:
        return "/".join(("", self.config.api_key, endpoint, self.config.group_key, *rest))

    def _redact(self, text: str) -> str:
        return text.replace(self.config.api_key, "<api-key>")

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        data: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        extra: dict[str, Any] = {} if timeout is None else {"timeout": timeout}
        try:
            r = self._http.request(method, path, params=params, data=data, **extra)
        except httpx.HTTPError as exc:  # connect/read errors; message may contain the URL
            raise ShinobiError(f"{type(exc).__name__}: {self._redact(str(exc))}") from None
        if r.is_error:
            raise ShinobiError(f"HTTP {r.status_code} for {method} {self._redact(path)}")
        return r

    def _get(self, path: str, params: Mapping[str, str] | None = None) -> httpx.Response:
        return self._request("GET", path, params=params)

    @staticmethod
    def _check(body: Any) -> Any:
        """Raise on Shinobi's in-band failure object, return the body otherwise."""
        if isinstance(body, dict) and body.get("ok") is False:
            msg = str(body.get("msg", ""))
            cls = ShinobiAuthError if "Not Authorized" in msg else ShinobiError
            raise cls(msg or "Shinobi answered ok=false")
        return body

    def _get_json(self, path: str, params: Mapping[str, str] | None = None) -> Any:
        return self._check(self._get(path, params).json())

    # -- endpoints ----------------------------------------------------------------------------

    def monitors(self) -> list[Monitor]:
        """All monitors of the group. [] can also mean "key lacks Can Get Monitors"."""
        return [Monitor.from_api(m) for m in self._get_json(self._path("monitor"))]

    def monitor(self, mid: str) -> Monitor:
        rows = self._get_json(self._path("monitor", mid))
        if not rows:  # unknown id and "not permitted" look the same: []
            raise ShinobiError(f"monitor {mid!r} not found (or not visible to this key)")
        return Monitor.from_api(rows[0])

    def videos(
        self,
        mid: str | None = None,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        started_in_window: bool = False,
        limit: int | None = None,
    ) -> list[Video]:
        """Finished recordings, newest first.

        Window semantics (libs/database/utils.js getDatabaseRows): by default a video must lie
        completely inside [start, end] (time >= start AND end <= end). started_in_window=True
        asks for time <= end instead, which includes the segment crossing `end`. The segment
        being written has no row yet. Shinobi caps the answer at 500 rows unless limit is set.
        """
        params: dict[str, str] = {}
        if start is not None:
            params["start"] = _format_time(start)
        if end is not None:
            params["end"] = _format_time(end)
        if started_in_window:
            params["endIsStartTo"] = "1"
        if limit is not None:
            params["limit"] = str(limit)
        path = self._path("videos", mid) if mid else self._path("videos")
        body = self._get_json(path, params)
        return [Video.from_api(v) for v in body["videos"]]

    def events(
        self,
        mid: str | None = None,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> list[Event]:
        """Detector events (Events table), newest first. Needs "Can View Videos".

        Unlike videos, the answer is a bare JSON array. Window: time >= start, time <= end.
        Shinobi caps the answer at 500 rows unless limit is set.
        """
        params: dict[str, str] = {}
        if start is not None:
            params["start"] = _format_time(start)
        if end is not None:
            params["end"] = _format_time(end)
        if limit is not None:
            params["limit"] = str(limit)
        path = self._path("events", mid) if mid else self._path("events")
        return [Event.from_api(e) for e in self._get_json(path, params)]

    def trigger(self, mid: str, details: Mapping[str, Any]) -> TriggerResult:
        """Inject a synthetic detector event (needs "Can Control Monitors"), as if a detector had
        sent `details` ({plug, name, reason, confidence | matrices, imgWidth, imgHeight}).

        It runs the same triggerEvent() as a real detection: event filters, motion lock,
        object regions, then Events row / webhooks / recording. Blocked unless the monitor's
        detector_http_api allows it ("1" = always, the template default).
        """
        r = self._get(self._path("motion", mid), {"data": json.dumps(details)})
        body = r.json()
        if not isinstance(body, dict):
            raise ShinobiError(f"unexpected trigger answer: {str(body)[:200]}")
        if body.get("ok") is not False:
            return TriggerResult(triggered=True, msg=str(body.get("msg", "")))
        if "triggered" in body:  # went through triggerEvent() and was dropped there
            return TriggerResult(False, str(body.get("reason", "")), str(body.get("msg", "")))
        self._check(body)  # auth / unknown monitor / blocked: raises
        raise ShinobiError(f"trigger refused: {body.get('msg', '')}")  # pragma: no cover

    def connected_plugins(self) -> dict[str, dict[str, Any]]:
        """Detector plugins connected right now, by name (pluginKeys key), e.g.
        {"YoloV9": {"started": ..., "isClientPlugin": True, ...}}. Any valid key works
        (plain s.auth, libs/plugins.js); {} = none connected."""
        body = self._get_json(self._path("plugins", "connected"))
        return dict(body.get("plugins") or {})

    def logs(self, mid: str | None = None, *, limit: int = 50) -> list[LogEntry]:
        """Newest log entries (Shinobi's default limit is 50). Needs "Can Get Logs".

        Note: entries can contain full FFmpeg command lines, i.e. camera URLs with credentials.
        """
        path = self._path("logs", mid) if mid else self._path("logs")
        return [LogEntry.from_api(e) for e in self._get_json(path, {"limit": str(limit)})]

    def monitor_configs(self) -> dict[str, dict[str, Any]]:
        """Stored configuration of every monitor: mid -> {MONITOR_COLUMNS}, as Shinobi keeps it.

        Needs "Can Edit Monitors": without it Shinobi blanks host/path/port, auto_host and
        credentials (removeSenstiveInfoFromMonitorConfig), which this method refuses to return
        rather than hand out a config that looks like "camera address deleted".
        """
        configs: dict[str, dict[str, Any]] = {}
        for raw in self._get_json(self._path("monitor")):
            if raw.get("host") == "" and raw.get("protocol") == "":
                raise ShinobiAuthError(
                    f"monitor {raw['mid']!r} came back redacted: the key lacks Can Edit Monitors"
                )
            configs[raw["mid"]] = {c: raw.get(c) for c in MONITOR_COLUMNS}
        return configs

    def save_monitor(self, config: Mapping[str, Any]) -> str:
        """Add the monitor config["mid"] or replace its configuration (an upsert); returns
        Shinobi's message ("Monitor Added by user..." / "Monitor Updated by user...").

        Shinobi restarts the monitor's FFmpeg on every save (a gap of a few seconds in the
        recording) and ffprobes the camera first, so a save takes >= 2 s. Callers that want
        idempotency must compare before saving (scripts/provision_monitors.py).
        """
        path = self._path("configureMonitor", config["mid"])
        # Same wire format as the dashboard's Save button: form field `data` = the JSON text.
        r = self._request("POST", path, data={"data": json.dumps(config)}, timeout=30.0)
        body = self._check(r.json())
        if not body.get("ok"):  # e.g. max monitors reached: ok false without "Not Authorized"
            raise ShinobiError(str(body.get("msg") or "save failed"))
        return str(body.get("msg", ""))

    def delete_monitor(self, mid: str, *, delete_files: bool = False) -> None:
        """Stop and delete a monitor. Recordings (Videos rows + files) are kept unless
        delete_files is set; a monitor created again with the same mid sees them again."""
        params = {"deleteFiles": "true"} if delete_files else None
        r = self._request("GET", self._path("configureMonitor", mid, "delete"), params=params)
        self._check(r.json())

    def stream_url(self, mid: str, kind: str = "hls") -> str:
        """Absolute live-stream URL as Shinobi builds it (libs/monitor/utils.js getStreamUrl).

        Contains the API key: hand it to a player, don't log it.
        """
        tails = {
            "hls": "s.m3u8",
            "flv": "s.flv",
            "mp4": "s.mp4",
            "mjpeg": "",
            "h264": "",
        }
        if kind not in tails:
            raise ValueError(f"unknown stream type {kind!r}, expected one of {sorted(tails)}")
        parts = [mid, tails[kind]] if tails[kind] else [mid]
        return self.config.url + self._path(kind, *parts)

    def video_url(self, video: Video) -> str:
        """Absolute download URL of a recording (contains the API key)."""
        return self.config.url + self._path("videos", video.mid, video.filename)

    def snapshot(self, mid: str) -> bytes:
        """Latest JPEG frame of a monitor.

        Shinobi serves /dev/shm/streams/<ke>/<mid>/s.jpg if it exists, otherwise its stock
        background image, both as 200 image/jpeg. Only monitors with snapshots enabled
        (details.snap == "1") have the file, so check that first.
        """
        if not self.monitor(mid).snapshot_enabled:
            raise SnapshotUnavailable(f"monitor {mid!r} has snapshots disabled (details.snap)")
        r = self._get(self._path("jpeg", mid, "s.jpg"))
        if not r.content.startswith(JPEG_MAGIC):  # e.g. {"ok":false,...} JSON, also 200
            self._check(r.json())
            raise ShinobiError(f"snapshot of {mid!r} is not a JPEG")
        return r.content


# -- command line -------------------------------------------------------------------------------


def _print_monitors(monitors: list[Monitor]) -> None:
    print(f"{'MONITOR':<14} {'NAME':<14} {'MODE':<7} {'STATE':<11} {'SIZE':<9} {'FPS':>3}  LIVE")
    for m in sorted(monitors, key=lambda m: m.mid):
        state = m.code.name.lower() if m.code is not None else "not running"
        size = f"{m.width}x{m.height}" if m.width and m.height else "-"
        print(
            f"{m.mid:<14} {m.name:<14} {m.mode:<7} {state:<11} {size:<9} {m.fps or '-':>3}  "
            f"{','.join(m.stream_types) or '-'}"
        )


def _print_videos(videos: list[Video]) -> None:
    print(f"{'MONITOR':<14} {'START (UTC)':<20} {'DURATION':>8} {'SIZE MB':>8}  FILE")
    for v in videos:
        secs = int(v.duration.total_seconds())
        print(
            f"{v.mid:<14} {v.time:%Y-%m-%d %H:%M:%S} {secs // 60:>5}:{secs % 60:02} "
            f"{v.size / 1e6:>8.1f}  {v.filename}"
        )


def _print_events(events: list[Event]) -> None:
    # BOXES: motion = region + % of it changed, object = label + score [plugin ms]
    print(f"{'MONITOR':<14} {'TIME (UTC)':<19} {'REASON':<7} {'CONF':>5}  BOXES")
    for e in events:
        fmt = ".2f" if e.reason == "object" else ".1f"
        boxes = ", ".join(f"{b.tag} {b.confidence:{fmt}}" for b in e.boxes) or e.name
        late = f"  [{e.processing_ms} ms]" if e.processing_ms is not None else ""
        when = f"{e.time:%Y-%m-%d %H:%M:%S}"
        print(f"{e.mid:<14} {when} {e.reason:<7} {e.confidence:>5.1f}  {boxes}{late}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m services.common.shinobi_client")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("monitors", help="list monitors with their runtime state")
    sub.add_parser("plugins", help="list connected detector plugins")
    p_vid = sub.add_parser("videos", help="list finished recordings, newest first")
    p_vid.add_argument("mid", nargs="?")
    p_vid.add_argument("--limit", type=int, default=10)
    p_ev = sub.add_parser("events", help="list detector events, newest first")
    p_ev.add_argument("mid", nargs="?")
    p_ev.add_argument("--limit", type=int, default=10)
    p_snap = sub.add_parser("snapshot", help="save the latest JPEG of a monitor")
    p_snap.add_argument("mid")
    p_snap.add_argument("out", type=Path)
    args = parser.parse_args(argv)

    try:
        with ShinobiClient(ShinobiConfig.from_env(load_env())) as shinobi:
            match args.command:
                case "monitors":
                    _print_monitors(shinobi.monitors())
                case "plugins":
                    plugins = shinobi.connected_plugins()
                    for name, info in sorted(plugins.items()):
                        print(f"{name:<14} since {info.get('started', '?')}")
                    if not plugins:
                        print("no detector plugin connected")
                case "videos":
                    _print_videos(shinobi.videos(args.mid, limit=args.limit))
                case "events":
                    _print_events(shinobi.events(args.mid, limit=args.limit))
                case "snapshot":
                    data = shinobi.snapshot(args.mid)
                    args.out.write_bytes(data)
                    print(f"{args.out}  {len(data)} bytes")
    except ShinobiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
