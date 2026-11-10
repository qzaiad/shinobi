"""Browser labeller for labels.yaml: stdlib HTTP server on 127.0.0.1, one vanilla-JS page.

    browser (labeler.html)                    this server                 files
      GET /                       ─────────▶  the page
      GET /api/labels             ─────────▶  labels.yaml as JSON  ◀────  tests/fixtures/detection/
      GET /frames/<cam>/<id>.jpg  ─────────▶  only ids in labels   ◀────  data/detection/frames/
      PUT /api/frames/<cam>/<id>  ─────────▶  validate (pydantic) ─────▶  labels.yaml (atomic)

It never shows model output: labels made while looking at the detector's boxes drift towards
the detector (anchoring), and the measurement would partly measure itself.
"""

from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from pydantic import ValidationError

from tools.detect_eval import frames as fr
from tools.detect_eval import labels as lb

PAGE = Path(__file__).with_name("labeler.html")


class Store:
    """labels.yaml in memory; every accepted change is written through."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.labels = lb.load(path)
        self.lock = threading.Lock()

    def as_json(self) -> dict[str, Any]:
        with self.lock:
            return self.labels.model_dump(mode="json")

    def update(self, cam: str, frame_id: str, body: dict[str, Any]) -> lb.Frame:
        with self.lock:
            frame = self.labels.frame(cam, frame_id)
            new = lb.Frame.model_validate(
                {"id": frame.id, "index": frame.index, "done": body.get("done", frame.done),
                 "boxes": body.get("boxes", [])}
            )  # fmt: skip
            candidate = self.labels.model_copy(deep=True)
            frames = candidate.cameras[cam].frames
            frames[[f.id for f in frames].index(frame_id)] = new
            candidate = lb.LabelSet.model_validate(candidate.model_dump())  # classes, bounds
            lb.save(candidate, self.path)
            self.labels = candidate
            return new


def make_handler(store: Store) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # quiet: one line per save only
            pass

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj: Any) -> None:
            self._send(status, json.dumps(obj).encode(), "application/json")

        def _parts(self) -> list[str]:
            return [unquote(p) for p in self.path.split("?")[0].strip("/").split("/")]

        def do_GET(self) -> None:  # noqa: N802
            parts = self._parts()
            if parts == [""]:
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if parts == ["api", "labels"]:
                return self._json(200, store.as_json())
            if len(parts) == 3 and parts[0] == "frames" and parts[2].endswith(".jpg"):
                cam, frame_id = parts[1], parts[2].removesuffix(".jpg")
                try:
                    store.labels.frame(cam, frame_id)  # only known ids: no path traversal
                except KeyError:
                    return self._json(404, {"error": "unknown frame"})
                path = fr.frame_path(cam, frame_id)
                if not path.exists():
                    return self._json(404, {"error": "run: python -m tools.detect_eval frames"})
                return self._send(200, path.read_bytes(), "image/jpeg")
            self._json(404, {"error": "not found"})

        def do_PUT(self) -> None:  # noqa: N802
            parts = self._parts()
            if len(parts) != 4 or parts[:2] != ["api", "frames"]:
                return self._json(404, {"error": "not found"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                frame = store.update(parts[2], parts[3], body)
            except KeyError:
                return self._json(404, {"error": "unknown frame"})
            except ValidationError as e:
                msg = "; ".join(err["msg"].removeprefix("Value error, ") for err in e.errors())
                return self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": msg})
            except ValueError as e:  # body is not JSON
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
            print(f"saved {frame.id}: {len(frame.boxes)} boxes, done={frame.done}", flush=True)
            self._json(200, frame.model_dump(mode="json"))

    return Handler


def serve(path: Path, port: int) -> int:
    store = Store(path)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(store))
    print(f"labeller on http://127.0.0.1:{port}  (writes {path}; Ctrl-C ends)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
