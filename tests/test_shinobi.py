"""Shinobi is up and the accounts from .env survive `docker compose down && up` (e2e).

    scripts/sim-up.sh && uv run pytest -m e2e tests/test_shinobi.py

Login is Shinobi's own form handler, `POST /?json=true` (dashboard) or `POST /super?json=true`
(superuser) with `mail`, `pass`, `function` (libs/webServerPaths.js). Every failed login counts
towards Shinobi's brute-force lock (5 failures per client IP -> 15 min lock-out), so the tests
only ever send the credentials from .env and never probe wrong ones.

What the three tests prove
==========================

    test_web_server_up               camera.js runs and its HTTP server answers on :8080
    test_superuser_password_changed  super.json (bind mount) kept the NEW superuser password
    test_admin_account_persists      the admin account (row in ccio.Users, volume shinobi-db)
                                     survived the container being removed and recreated

Data flow of one test
=====================

    pytest process (host)                          shinobi container
    ─────────────────────                          ──────────────────────────────────────────
    _env()  reads .env  ─▶ mail, password
    httpx.post(...)
      │ 1. builds an HTTP request (bytes)
      │ 2. opens a TCP connection to 127.0.0.1:8080 ── docker port forward ──▶ camera.js :8080
      │ 3. writes the request bytes                                          │ Express router
      │                                                                      │ login handler
      │                                                                      ├─▶ super.json (super)
      │                                                                      └─▶ MariaDB (admin)
      │ 4. reads the response bytes  ◀──────────────── HTTP/1.1 200 + JSON ──┘
      │ 5. closes the connection
      ▼
    httpx.Response ─▶ .raise_for_status() ─▶ .json() ─▶ dict ─▶ assert ...

httpx in one page
=================

httpx is an HTTP client library: it turns a Python call into an HTTP request on a TCP socket and
the bytes coming back into a `Response` object. Same idea as `requests` (and as libcurl in C/C++),
with a nearly identical API, plus type hints, async support and HTTP/2. It is the project's
standard HTTP client (CLAUDE.md), so the Shinobi API client in Session 11 uses it too.

Two ways to use it:

    httpx.get(url) / httpx.post(url)   one-shot: creates a temporary Client, sends ONE request,
                                       closes the connection. Simple; fine for a few test calls.
    with httpx.Client(base_url=...) as c:
        c.get("/a"); c.post("/b")      a Client keeps a connection POOL: TCP connections are
                                       reused between requests (HTTP keep-alive), and shared
                                       settings (base_url, headers, timeout, cookies) live in
                                       one place. Use this in services and for many requests.

What `httpx.post(url, params=..., data=..., timeout=10)` sends. Captured with
`httpx.Client().build_request(...)` (dummy credentials; httpx 0.28.1):

    POST /super?json=true HTTP/1.1              ◀ method, path + query string (from params=)
    host: localhost:8080                        ◀ from the URL
    accept: */*                                 ◀ httpx default headers ...
    accept-encoding: gzip, deflate              ◀   (server may compress; httpx decompresses)
    connection: keep-alive
    user-agent: python-httpx/0.28.1
    content-length: 54                          ◀ length of the body in bytes
    content-type: application/x-www-form-urlencoded   ◀ because we passed data=
                                                ◀ blank line ends the headers
    mail=you%40example.com&pass=s3cret+word&function=super    ◀ the body

The keyword arguments, one by one:

    params={"json": "true"}   -> appended to the URL as ?json=true, values URL-encoded.
                                 Shinobi checks req.query.json == 'true' to answer JSON instead of
                                 rendering an HTML page.
    data={...}                -> request BODY, form-encoded: key=value joined by '&', special
                                 characters percent-encoded ('@' -> %40, ' ' -> '+'). That is
                                 exactly what a browser sends when you submit the login <form>,
                                 so Shinobi's handler reads it as req.body.mail etc.
                                 (json={...} would send a JSON body instead: a different
                                 content-type the handler is not documented to expect.)
    timeout=10                -> httpx.Timeout(10): 10 s limit for EACH phase: connect, write,
                                 read (between bytes received) and waiting for a pooled
                                 connection. Not a total deadline. httpx's default is 5 s; there
                                 is no "wait forever" unless you pass timeout=None.

What comes back, from the real server (`httpx.get("http://localhost:8080/")`):

    r.http_version   'HTTP/1.1'
    r.status_code    200                 ◀ 2xx success, 3xx redirect, 4xx client error, 5xx server
    r.reason_phrase  'OK'
    r.elapsed        0:00:00.014611      ◀ time from sending to receiving the response headers
    r.headers        content-type: text/html; charset=utf-8, content-length: 10068,
                     keep-alive: timeout=5, ...   (case-insensitive dict)
    r.text / r.content / r.json()        body as str / bytes / parsed JSON (dict or list)

Three ways an HTTP call can fail, and which line catches each:

    1. no answer at all       stack down -> httpx.ConnectError "[Errno 111] Connection refused"
                              (kernel answered the SYN with RST: nothing listens on :8080).
                              Too slow -> httpx.ConnectTimeout / ReadTimeout.
                              Both are httpx.TransportError -> httpx.RequestError -> HTTPError.
                              They are RAISED by httpx.post() itself: the test errors out.
    2. HTTP error status      4xx/5xx -> r.raise_for_status() raises httpx.HTTPStatusError.
                              httpx does NOT raise on its own for these; you must ask.
    3. "successful" failure   wrong password -> Shinobi still answers 200 OK with
                              {"ok": false}. Status code says nothing here, which is why every
                              login test also asserts body["ok"] is True.

(Also unlike `requests`: httpx does not follow redirects unless follow_redirects=True. A 302 to
the login page would come back as a 302, not silently as the HTML of the page it points to.)
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

# Marks every test in this module as e2e. Combined with `addopts = ["-m", "not e2e"]` in
# pyproject.toml, a plain `uv run pytest` then skips these tests (they need the running stack);
# `uv run pytest -m e2e` selects them.
pytestmark = pytest.mark.e2e

REPO = Path(__file__).resolve().parents[1]  # tests/test_shinobi.py -> repo root


def _env() -> dict[str, str]:
    """KEY=VALUE pairs from .env, overridden by the process environment."""
    # `uv run` does not load .env by itself, so parse it here. Minimal on purpose: no quotes,
    # no `export`, no ${VAR} expansion (our .env needs none). Real env vars win, so
    # `SHINOBI_URL=http://other:8080 uv run pytest ...` works without editing .env.
    values: dict[str, str] = {}
    path = REPO / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            # partition: split at the FIRST '=' only, so values may contain '=' themselves.
            # sep is '' when the line has no '=' (blank lines), which we skip.
            key, sep, value = line.partition("=")
            if sep and not key.lstrip().startswith("#"):
                values[key.strip()] = value.strip()
    values.update(os.environ)
    return values


# Read once at import time (= when pytest collects this file), shared by all tests.
ENV = _env()
# `or`: an empty SHINOBI_URL= line in .env also falls back to the default.
# rstrip("/"): so f"{BASE}{path}" never produces "//super".
BASE = (ENV.get("SHINOBI_URL") or "http://localhost:8080").rstrip("/")


def _creds(prefix: str) -> tuple[str, str]:
    """(email, password) from <prefix>_EMAIL / <prefix>_PASSWORD, or skip the calling test."""
    mail, password = ENV.get(f"{prefix}_EMAIL", ""), ENV.get(f"{prefix}_PASSWORD", "")
    if not (mail and password):
        # pytest.skip() raises an exception that ends the test right here and reports it as
        # "skipped" (s) instead of failed: missing local setup is not a Shinobi bug. It also
        # means we never send an empty password, which would count as a failed login.
        pytest.skip(f"set {prefix}_EMAIL and {prefix}_PASSWORD in .env")
    return mail, password


def _login(path: str, function: str, mail: str, password: str) -> dict:
    """POST Shinobi's login form and return the parsed JSON answer.

    path      "/" (dashboard) or "/super" (superuser panel): both hit the same handler, which
              uses the path to pick the page it would render.
    function  "dashboard" or "super": tells the handler which account store to check
              (MariaDB Users table vs super.json).
    """
    # One-shot request (see module docstring): connect, send, receive, close.
    r = httpx.post(
        f"{BASE}{path}",  # e.g. http://localhost:8080/super
        params={"json": "true"},  # -> ?json=true : answer JSON, not an HTML page
        data={"mail": mail, "pass": password, "function": function},  # form-encoded body
        timeout=10,  # per phase (connect/read/write/pool), not total
    )
    # Turn 4xx/5xx into an exception (httpx.HTTPStatusError, message has status and URL).
    # Without it, a 500 with an HTML error page would only fail later in r.json() with a
    # confusing JSONDecodeError. A wrong password is NOT caught here (it is a 200).
    r.raise_for_status()
    # Parse the body as JSON -> dict. The superuser answer contains the password HASH and a
    # session key, the admin answer an auth_token: don't print or log it whole.
    return r.json()


def test_web_server_up() -> None:
    # Plain GET of the login page. Proves: container running, camera.js started, its web
    # server listening, Docker port forward 127.0.0.1:8080 -> container :8080 working.
    # Fails with httpx.ConnectError (an error, not an assert) if nothing listens.
    r = httpx.get(f"{BASE}/", timeout=10)
    assert r.status_code == 200


def test_superuser_password_changed() -> None:
    # *_creds(...) unpacks the (mail, password) tuple into two positional arguments.
    body = _login("/super", "super", *_creds("SHINOBI_SUPER"))
    # Login itself worked: the password in .env matches the hash in super.json.
    assert body["ok"] is True, "superuser login failed"
    # Shinobi computes this flag itself (compares with the default "admin" hash). False means
    # the bind-mounted super.json still holds the password you set in /super after restarts.
    assert body["$user"]["usingDefaultPassword"] is False
    # print(f'\n{body=}\n')
    # print(f'\n{body["ok"]=}\n')
    # print(f'\n{body["$user"]=}\n')
    # print(f'\n{body["$user"]["usingDefaultPassword"]=}\n')


def test_admin_account_persists() -> None:
    """
No, it isn't HTML. body is a Python dict. Because of params={"json": "true"}, Shinobi answers with JSON (content-type: application/json), and r.json() in _login() parses it into a dict. Without ?json=true, the same request returns the HTML dashboard page.

What it contains: Shinobi's login handler builds one data object for rendering the dashboard page. With json=true it deletes a few server-internal fields (config, __dirname, customAutoLoad, fs) and sends you the rest. That's why the response is huge. Here it is from your real admin login, with values hidden:


HTTP 200, application/json, 739 612 bytes   (indented JSON; raw data ≈ 405 KB)

body
├─ ok          bool        true                 ← did the login succeed (what the test checks first)
├─ $user       dict 6 keys   794 B              ← the account that logged in
│   ├─ ok          true
│   ├─ auth_token  32 chars   session token, new on every login (stored in Users.auth)
│   ├─ ke          10 chars   group key → SHINOBI_GROUP_KEY in Session 11
│   ├─ uid         10 chars   user id inside the group
│   ├─ mail        your admin e-mail
│   └─ details     32 keys    account limits/permissions: size, days, event_days, log_days,
│                             max_camera, permissions, use_aws_s3, use_ldap, monitorOrder, …
├─ lang        dict 2476 keys  147 KB           ← every UI text string ("Log": "Log", …), for the page
├─ define      dict 39 keys    258 KB           ← UI form definitions: "Monitor Settings",
│                                                 "Account Settings", "API Keys", … (Session 9 useful!)
├─ addStorage  list            [{"name": "second", "path": "/home/Shinobi/videos2"}]  from conf.json
└─ timezone    str  "UTC"
What matters for us:

ok and $user are the only useful parts. Over 99 % of the bytes (lang, define) exist only so the browser page can render its menus and forms.
$user is just a key name that starts with a dollar sign. In Python you read it with body["$user"], like any other key.
define["Monitor Settings"] describes every monitor field Shinobi knows. Session 9 can use it to look up real config keys instead of guessing them.
That's also why print(f'\n{body=}\n') floods the test output with ~700 KB. It also prints auth_token, which works as a login session. If you want a debug line, print only body["ok"] and the key names, e.g. print(sorted(body["$user"])).

    """

    mail, password = _creds("SHINOBI_ADMIN")
    # print(f'\n{mail=}  {password=}\n')
    body = _login("/", "dashboard", mail, password)
    # print(f'\n{body=}\n')
    # ok is only true if the account exists in ccio.Users, i.e. the shinobi-db volume
    # survived `docker compose down && up`.
    assert body["ok"] is True, "admin login failed (account missing from the database?)"
    user = body["$user"]
    # print(f'\n{body["$user"]=}\n')
    # Shinobi answered for the account we asked about.
    assert user["mail"] == mail
    # ke = group key: every monitor, video and API URL belongs to a group
    # (/<API_KEY>/<endpoint>/<GROUP_KEY>/...). Session 11 needs it for SHINOBI_GROUP_KEY.
    assert user["ke"], "no group key"
    # A fresh session token, generated on every successful login and stored in Users.auth.
    assert user["auth_token"], "no session token"
