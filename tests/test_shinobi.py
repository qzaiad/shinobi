"""Shinobi is up and the accounts from .env survive `docker compose down && up` (e2e).

    scripts/sim-up.sh && uv run pytest -m e2e tests/test_shinobi.py

Login is Shinobi's own form handler, `POST /?json=true` (dashboard) or `POST /super?json=true`
(superuser) with `mail`, `pass`, `function` (libs/webServerPaths.js). Every failed login counts
towards Shinobi's brute-force lock (5 failures per client IP -> 15 min lock-out), so the tests
only ever send the credentials from .env and never probe wrong ones.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.e2e

REPO = Path(__file__).resolve().parents[1]


def _env() -> dict[str, str]:
    """KEY=VALUE pairs from .env, overridden by the process environment."""
    values: dict[str, str] = {}
    path = REPO / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.lstrip().startswith("#"):
                values[key.strip()] = value.strip()
    values.update(os.environ)
    return values


ENV = _env()
BASE = (ENV.get("SHINOBI_URL") or "http://localhost:8080").rstrip("/")


def _creds(prefix: str) -> tuple[str, str]:
    mail, password = ENV.get(f"{prefix}_EMAIL", ""), ENV.get(f"{prefix}_PASSWORD", "")
    if not (mail and password):
        pytest.skip(f"set {prefix}_EMAIL and {prefix}_PASSWORD in .env")
    return mail, password


def _login(path: str, function: str, mail: str, password: str) -> dict:
    r = httpx.post(
        f"{BASE}{path}",
        params={"json": "true"},
        data={"mail": mail, "pass": password, "function": function},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def test_web_server_up() -> None:
    r = httpx.get(f"{BASE}/", timeout=10)
    assert r.status_code == 200


def test_superuser_password_changed() -> None:
    body = _login("/super", "super", *_creds("SHINOBI_SUPER"))
    assert body["ok"] is True, "superuser login failed"
    assert body["$user"]["usingDefaultPassword"] is False


def test_admin_account_persists() -> None:
    mail, password = _creds("SHINOBI_ADMIN")
    body = _login("/", "dashboard", mail, password)
    assert body["ok"] is True, "admin login failed (account missing from the database?)"
    user = body["$user"]
    assert user["mail"] == mail
    assert user["ke"], "no group key"
    assert user["auth_token"], "no session token"
