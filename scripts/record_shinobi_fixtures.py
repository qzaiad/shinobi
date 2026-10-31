"""Record real Shinobi API answers into tests/fixtures/shinobi/ (secrets replaced).

    uv run python scripts/record_shinobi_fixtures.py        # needs the stack + .env

Why recorded payloads: the unit tests in tests/test_shinobi_client.py replay these files through
httpx.MockTransport, so they test the client against what Shinobi really sends (bare arrays vs
{"ok":..} objects, "3" as a string, times with and without milliseconds), not against what we
think it sends.

Why redaction is needed: Shinobi writes the caller's API key and group key into the answers
themselves (streams[], every video's href/actionUrl/links). The real values are replaced by the
placeholders API_KEY / GROUP_KEY / USER_ID, which are also the values the unit tests use as
"credentials", so a replayed answer is exactly what Shinobi would send for that key. Before
anything is written, every secret-looking value from .env is searched for in the output; one
hit aborts the whole run.

Plain httpx on purpose (not services.common.shinobi_client): fixtures must show the wire
format, independent of how the client parses it.

Session answers (scripts/shinobi_bootstrap.py, recorded when the superuser/admin credentials are
in .env) carry more secrets: the superuser session key and password HASH (`$user.pass` is sent
back on every /super login), the admin auth_token, each account's `auth` value and the e-mails.
They are replaced field by field (scrub_session), the e-mails by example addresses, and the
`lang`/`define` UI tables (~100 kB, unused) are dropped. Every replaced value also joins the leak
check.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))  # run as a plain script: make `services` importable

from services.common.envfile import load_env  # noqa: E402

OUT = REPO / "tests" / "fixtures" / "shinobi"

# fixture name -> path after /<key>/ ; {ke} is replaced by the group key. Small limits keep the
# files reviewable; the monitor list is complete because the tests check all four cameras.
REQUESTS: dict[str, str] = {
    "monitors": "monitor/{ke}",
    "monitor_cam_gate": "monitor/{ke}/cam_gate",
    "monitor_unknown": "monitor/{ke}/no_such_monitor",
    "videos_cam_gate": "videos/{ke}/cam_gate?limit=3",
    "logs_cam_gate": "logs/{ke}/cam_gate?limit=2",
}
# Recorded with SHINOBI_EDIT_API_KEY (Get + Edit Monitors): the unredacted stored configs that
# scripts/provision_monitors.py compares against. Skipped when that key is not set.
EDIT_KEY_REQUESTS: dict[str, str] = {
    "monitors_edit_key": "monitor/{ke}",
}
# Recorded with the superuser and admin logins from .env (see scrub_session).
SESSION_FIXTURES = ("super_login", "accounts_list", "admin_login", "api_list")
BAD_KEY = "not-a-valid-api-key"  # for the "Not Authorized" fixture (does not count as a login)


def secrets_from(env: dict[str, str]) -> dict[str, str]:
    """Real value -> placeholder for identifiers, plus every other credential-like .env value."""
    out = {
        env["SHINOBI_API_KEY"]: "API_KEY",
        env["SHINOBI_GROUP_KEY"]: "GROUP_KEY",
    }
    if env.get("SHINOBI_EDIT_API_KEY"):
        out[env["SHINOBI_EDIT_API_KEY"]] = "EDIT_API_KEY"
    if env.get("SHINOBI_UID"):
        out[env["SHINOBI_UID"]] = "USER_ID"
    if env.get("SHINOBI_ADMIN_EMAIL"):
        out[env["SHINOBI_ADMIN_EMAIL"]] = "admin@example.com"
    if env.get("SHINOBI_SUPER_EMAIL"):
        out[env["SHINOBI_SUPER_EMAIL"]] = "super@example.com"
    # Never expected in API answers, but if one shows up, abort instead of committing it.
    for name, value in env.items():
        if name.startswith("SHINOBI_") and name.endswith(("PASSWORD", "EMAIL")) and value:
            out.setdefault(value, "")
    return out


def redact(text: str, secrets: dict[str, str]) -> str:
    for real, placeholder in secrets.items():
        if placeholder:
            text = text.replace(real, placeholder)
    leaked = [p or "a password/e-mail from .env" for real, p in secrets.items() if real in text]
    if leaked:
        raise SystemExit(f"refusing to write fixtures: still contains {leaked}")
    return text


def scrub_session(body: dict[str, Any], secrets: dict[str, str]) -> dict[str, Any]:
    """Drop the UI tables, replace session secrets by placeholders (also added to `secrets`)."""
    body = {k: v for k, v in body.items() if k not in ("lang", "define")}
    placeholders = {"sessionKey": "SESSION_KEY", "auth_token": "AUTH_TOKEN",
                    "pass": "PASSWORD_HASH", "auth": "ACCOUNT_AUTH"}  # fmt: skip
    users = [body.get("$user")] + list(body.get("users") or [])
    for user in (u for u in users if isinstance(u, dict)):
        for name, placeholder in placeholders.items():
            if user.get(name):
                secrets.setdefault(str(user[name]), placeholder)
                user[name] = placeholder
        if user.get("tokens"):
            user["tokens"] = ["SUPER_TOKEN"] * len(user["tokens"])
    return body


def record_sessions(
    client: httpx.Client, env: dict[str, str], secrets: dict[str, str]
) -> dict[str, str]:
    """Answers the bootstrap parses; two logins with the .env credentials (never wrong ones)."""
    out: dict[str, Any] = {}
    out["super_login"] = client.post("/super", params={"json": "true"}, data={
        "mail": env["SHINOBI_SUPER_EMAIL"], "pass": env["SHINOBI_SUPER_PASSWORD"],
        "function": "super"}).json()  # fmt: skip
    sk = out["super_login"]["$user"]["sessionKey"]
    out["accounts_list"] = client.get(f"/super/{sk}/accounts/list").json()
    out["admin_login"] = client.post("/", params={"json": "true"}, data={
        "mail": env["SHINOBI_ADMIN_EMAIL"], "pass": env["SHINOBI_ADMIN_PASSWORD"],
        "function": "dash"}).json()  # fmt: skip
    user = out["admin_login"]["$user"]
    out["api_list"] = client.get(f"/{user['auth_token']}/api/{user['ke']}/list").json()
    return {name: json.dumps(scrub_session(body, secrets), indent=2) for name, body in out.items()}


def main() -> int:
    env = load_env(REPO / ".env")
    missing = [k for k in ("SHINOBI_URL", "SHINOBI_API_KEY", "SHINOBI_GROUP_KEY") if not env.get(k)]
    if missing:
        print(f"set {', '.join(missing)} in .env", file=sys.stderr)
        return 2
    key, ke = env["SHINOBI_API_KEY"], env["SHINOBI_GROUP_KEY"]
    secrets = secrets_from(env)

    texts: dict[str, str] = {}
    with httpx.Client(base_url=env["SHINOBI_URL"], timeout=10) as client:
        for name, path in REQUESTS.items():
            r = client.get(f"/{key}/{path.format(ke=ke)}")
            r.raise_for_status()
            texts[name] = r.text
        edit_key = env.get("SHINOBI_EDIT_API_KEY")
        for name, path in EDIT_KEY_REQUESTS.items() if edit_key else ():
            r = client.get(f"/{edit_key}/{path.format(ke=ke)}")
            r.raise_for_status()
            texts[name] = r.text
        r = client.get(f"/{BAD_KEY}/monitor/{ke}")
        r.raise_for_status()
        texts["not_authorized"] = r.text
        if all(
            env.get(f"SHINOBI_{w}")
            for w in ("SUPER_EMAIL", "SUPER_PASSWORD", "ADMIN_EMAIL", "ADMIN_PASSWORD")
        ):
            texts |= record_sessions(client, env, secrets)

    # Redact everything first, write only if ALL files are clean (no half-written set).
    clean = {name: redact(text, secrets) for name, text in texts.items()}
    OUT.mkdir(parents=True, exist_ok=True)
    for name, text in clean.items():
        json.loads(text)  # must still be valid JSON after substitution
        path = OUT / f"{name}.json"
        path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        print(f"{path.relative_to(REPO)}  {len(text)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
