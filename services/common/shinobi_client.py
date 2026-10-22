"""Typed client for Shinobi's HTTP API (read-only part).

    uv run python -m services.common.shinobi_client monitors
    uv run python -m services.common.shinobi_client videos [MONITOR_ID] [--limit N]
    uv run python -m services.common.shinobi_client snapshot MONITOR_ID out.jpg

Every endpoint is GET /<api key>/<endpoint>/<group key>[/<monitor id>] on Shinobi's web port
(routes in libs/webServerPaths.js and libs/webServerStreamPaths.js, auth in libs/auth.js).
Explained in docs/notes/11-shinobi-api.md. The traps this module exists to hide:

    1. A wrong key or a missing permission is HTTP 200 with {"ok": false, "msg": ...}, so the
       status code says nothing; the body is checked on every call (ShinobiAuthError).
    2. Success shapes differ per endpoint: monitor/logs return a bare JSON array, videos an
       object {"ok": true, "videos": [...]}.
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
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self

import httpx

from services.common.envfile import load_env

JPEG_MAGIC = b"\xff\xd8"  # every JPEG starts with the SOI marker FF D8


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
    def from_env(cls, env: Mapping[str, str]) -> Self:
        names = ("SHINOBI_URL", "SHINOBI_API_KEY", "SHINOBI_GROUP_KEY")
        missing = [n for n in names if not env.get(n)]
        if missing:
            raise ShinobiError(f"missing configuration: {', '.join(missing)}")
        return cls(
            url=env["SHINOBI_URL"].rstrip("/"),
            api_key=env["SHINOBI_API_KEY"],
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


class ShinobiClient:
    """Read-only Shinobi API client; one pooled HTTP connection set per instance.

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

    def _get(self, path: str, params: Mapping[str, str] | None = None) -> httpx.Response:
        try:
            r = self._http.get(path, params=params)
        except httpx.HTTPError as exc:  # connect/read errors; message may contain the URL
            raise ShinobiError(f"{type(exc).__name__}: {self._redact(str(exc))}") from None
        if r.is_error:
            raise ShinobiError(f"HTTP {r.status_code} for GET {self._redact(path)}")
        return r

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

    def logs(self, mid: str | None = None, *, limit: int = 50) -> list[LogEntry]:
        """Newest log entries (Shinobi's default limit is 50). Needs "Can Get Logs".

        Note: entries can contain full FFmpeg command lines, i.e. camera URLs with credentials.
        """
        path = self._path("logs", mid) if mid else self._path("logs")
        return [LogEntry.from_api(e) for e in self._get_json(path, {"limit": str(limit)})]

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m services.common.shinobi_client")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("monitors", help="list monitors with their runtime state")
    p_vid = sub.add_parser("videos", help="list finished recordings, newest first")
    p_vid.add_argument("mid", nargs="?")
    p_vid.add_argument("--limit", type=int, default=10)
    p_snap = sub.add_parser("snapshot", help="save the latest JPEG of a monitor")
    p_snap.add_argument("mid")
    p_snap.add_argument("out", type=Path)
    args = parser.parse_args(argv)

    try:
        with ShinobiClient(ShinobiConfig.from_env(load_env())) as shinobi:
            match args.command:
                case "monitors":
                    _print_monitors(shinobi.monitors())
                case "videos":
                    _print_videos(shinobi.videos(args.mid, limit=args.limit))
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
