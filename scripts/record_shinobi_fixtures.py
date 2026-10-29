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
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

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
