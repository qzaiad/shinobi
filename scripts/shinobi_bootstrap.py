"""Shinobi superuser, admin account and API keys from the repo + .env, provisioned idempotently.

    uv run python scripts/shinobi_bootstrap.py plan             # dry run; exit 2 = changes pending
    uv run python scripts/shinobi_bootstrap.py apply [--prune]  # change only what differs
    uv run python scripts/shinobi_bootstrap.py apply --wait 120 # first wait for Shinobi to answer

What Session 12 left out: a wiped `shinobi-db` volume (or a fresh clone) also loses the account
and its API keys, which were made by hand in the UI. This script rebuilds them, then
scripts/provision_monitors.py rebuilds the monitors (docs/notes/13-shinobi-cheatsheet.md).

    .env  SHINOBI_SUPER_EMAIL/PASSWORD ─┐
          SHINOBI_ADMIN_EMAIL/PASSWORD ─┤
          SHINOBI_GROUP_KEY (wanted ke) ─┤
    shinobi-config/account.yaml ────────┤  (account details, key permissions; no secrets)
                                        ▼
     1 superuser   POST /super?json=true            .env creds, else the image's default creds
                   └ default works → POST /super/<sk>/accounts/saveSettings  (writes super.json)
     2 account     GET  /super/<sk>/accounts/list
                   ├ missing → POST /super/<sk>/accounts/registerAdmin   ke = SHINOBI_GROUP_KEY
                   └ details drift → POST /super/<sk>/accounts/editAdmin (merges details)
     3 admin login POST /?json=true                 → auth_token, ke, uid
     4 API keys    GET  /<auth_token>/api/<ke>/list
                   ├ .env code missing in Shinobi → POST /<auth_token>/api/<ke>/add  (no code)
                   └ permissions/ip drift        → POST /<auth_token>/api/<ke>/add  (code=...)
     5 .env        SHINOBI_GROUP_KEY, SHINOBI_UID, one variable per key (rewritten in place)

Why .env is written back: Shinobi always generates `uid` (s.gid()) and every API key code
(s.gid(30)); an unknown `code` sent to api/add is ignored and a new random one is created
(libs/user/apiKeys.js editApiKey). Only the group key `ke` can be chosen. After a rebuild the
old codes are gone, so the new ones must land where the other tools read them.

Login failures count towards Shinobi's lock (5 per client IP -> 15 min), so each login is tried
once: the .env superuser, then (only if that fails) the image default, then the .env admin.
Secrets never reach stdout or exception messages: errors name the step, never the URL.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, Self

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))  # run as a plain script: make `services` importable

from services.common.envfile import load_env, update_env  # noqa: E402

DEFAULT_SPEC = REPO / "shinobi-config" / "account.yaml"
DEFAULT_SUPER = ("admin@shinobi.video", "admin")  # super.sample.json in the image (md5 of admin)

# Every flag Shinobi stores on an API key (api/list answer; libs/user/apiKeys.js
# availablePermissions + the account-level flags the UI also sends). Unlisted = "0".
KEY_PERMISSIONS = (
    "auth_socket",
    "create_api_keys",
    "edit_user",
    "edit_permissions",
    "get_monitors",
    "edit_monitors",
    "control_monitors",
    "get_logs",
    "watch_stream",
    "watch_snapshot",
    "watch_videos",
    "delete_videos",
    "get_alarms",
    "edit_alarms",
)
# The rest of a key's details as the UI saves it: an unrestricted key of the account itself.
KEY_DETAILS_BASE: Mapping[str, Any] = {
    "treatAsSub": "0",
    "permissionSet": "",
    "monitorsRestricted": "0",
    "monitorPermissions": {},
}


class ConfigError(Exception):
    """account.yaml or .env cannot be provisioned as written."""


class BootstrapError(Exception):
    """Shinobi refused or answered unexpectedly. Messages never contain secrets."""


# -- spec (account.yaml) ------------------------------------------------------------------------


class _KeyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    env: str
    ip: str = "0.0.0.0"
    permissions: list[str]

    @field_validator("env")
    @classmethod
    def _env_name(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", v):
            raise ValueError(f"{v!r} is not an environment variable name")
        return v

    @field_validator("permissions")
    @classmethod
    def _known(cls, v: list[str]) -> list[str]:
        unknown = sorted(set(v) - set(KEY_PERMISSIONS))
        if unknown:
            raise ValueError(f"unknown permissions {unknown}; known: {list(KEY_PERMISSIONS)}")
        return v


class _AccountModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    details: dict[str, str] = {}


class _SpecModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1]
    account: _AccountModel
    api_keys: list[_KeyModel]


@dataclass(frozen=True, slots=True)
class KeySpec:
    env: str
    ip: str
    permissions: frozenset[str]

    def details(self) -> dict[str, Any]:
        """The full details object Shinobi stores for this key: every flag "1" or "0"."""
        flags = {p: "1" if p in self.permissions else "0" for p in KEY_PERMISSIONS}
        return dict(KEY_DETAILS_BASE) | flags


@dataclass(frozen=True, slots=True)
class Spec:
    account_details: Mapping[str, str]
    api_keys: tuple[KeySpec, ...]


def load_spec(path: Path) -> Spec:
    try:
        m = _SpecModel.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except ValidationError as exc:
        raise ConfigError(f"{path}: {exc}") from None
    names = [k.env for k in m.api_keys]
    if len(set(names)) != len(names):
        raise ConfigError(f"{path}: two api_keys write the same variable: {names}")
    reserved = {"SHINOBI_GROUP_KEY", "SHINOBI_UID"} & set(names)
    if reserved:
        raise ConfigError(f"{path}: {sorted(reserved)} is written by the bootstrap itself")
    keys = tuple(KeySpec(k.env, k.ip, frozenset(k.permissions)) for k in m.api_keys)
    return Spec(dict(m.account.details), keys)


# -- credentials (.env) -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Credentials:
    super_mail: str
    super_pass: str
    admin_mail: str
    admin_pass: str
    group_key: str  # wanted ke for a NEW account; "" = let Shinobi generate one

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Self:
        names = ("SHINOBI_SUPER_EMAIL", "SHINOBI_SUPER_PASSWORD")
        names += ("SHINOBI_ADMIN_EMAIL", "SHINOBI_ADMIN_PASSWORD")
        missing = [n for n in names if not env.get(n)]
        if missing:
            raise ConfigError(f"set {', '.join(missing)} in .env")
        creds = cls(*(env[n] for n in names), env.get("SHINOBI_GROUP_KEY", ""))
        if (creds.super_mail, creds.super_pass) == DEFAULT_SUPER or creds.super_pass == "admin":
            raise ConfigError("SHINOBI_SUPER_PASSWORD must not be the image default")
        # registerAdmin deletes punctuation from ke (incl. - and _): refuse instead of drifting.
        if creds.group_key and not re.fullmatch(r"[A-Za-z0-9]+", creds.group_key):
            raise ConfigError("SHINOBI_GROUP_KEY: letters and digits only (Shinobi strips others)")
        return creds


# -- Shinobi HTTP layer -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Account:
    ke: str
    uid: str
    mail: str
    details: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Session:
    """A logged-in admin: auth_token works like an API key with every permission."""

    auth_token: str = field(repr=False)
    ke: str
    uid: str


@dataclass(frozen=True, slots=True)
class ApiKey:
    code: str = field(repr=False)
    ip: str
    details: Mapping[str, Any]


class BootstrapApi(Protocol):
    """What run() needs from Shinobi (ShinobiAdminApi; tests pass a fake)."""

    def super_login(self, mail: str, password: str) -> str | None: ...
    def super_set_credentials(self, sk: str, mail: str, password: str) -> None: ...
    def accounts(self, sk: str) -> list[Account]: ...
    def register_admin(
        self, sk: str, mail: str, password: str, ke: str, details: Mapping[str, str]
    ) -> None: ...
    def edit_admin_details(self, sk: str, account: Account, details: Mapping[str, str]) -> None: ...
    def login(self, mail: str, password: str) -> Session | None: ...
    def api_keys(self, s: Session) -> list[ApiKey]: ...
    def save_api_key(
        self, s: Session, code: str | None, ip: str, details: Mapping[str, Any]
    ) -> str: ...
    def delete_api_key(self, s: Session, code: str) -> None: ...


class ShinobiAdminApi:
    """Superuser + account endpoints over httpx. Session keys and tokens sit in URL paths, so
    exceptions are re-raised with the step name only (`from None` drops the httpx chain)."""

    def __init__(self, base_url: str, *, timeout: float = 15, transport: Any = None) -> None:
        self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self._http.close()

    def _call(self, step: str, method: str, path: str, **kw: Any) -> Any:
        try:
            r = self._http.request(method, path, **kw)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as exc:
            raise BootstrapError(f"{step}: HTTP {exc.response.status_code}") from None
        except (httpx.HTTPError, ValueError) as exc:
            raise BootstrapError(f"{step}: {type(exc).__name__}") from None

    def _ok(self, step: str, body: Any) -> Any:
        if not isinstance(body, dict) or body.get("ok") is not True:
            msg = body.get("msg") if isinstance(body, dict) else None
            raise BootstrapError(f"{step}: Shinobi refused ({msg or 'ok is not true'})")
        return body

    def wait_until_up(self, seconds: float, log: Callable[[str], None] = print) -> None:
        deadline = time.monotonic() + seconds
        while True:
            try:
                if self._http.get("/").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                raise BootstrapError(f"Shinobi did not answer within {seconds:.0f} s")
            log("waiting for Shinobi ...")
            time.sleep(3)

    def super_login(self, mail: str, password: str) -> str | None:
        body = self._call(
            "superuser login", "POST", "/super", params={"json": "true"},
            data={"mail": mail, "pass": password, "function": "super"},
        )  # fmt: skip
        user = body.get("$user") if isinstance(body, dict) else None
        if body.get("ok") is True and isinstance(user, dict) and user.get("sessionKey"):
            return str(user["sessionKey"])
        return None

    def super_set_credentials(self, sk: str, mail: str, password: str) -> None:
        form = {"mail": mail, "pass": password, "pass_again": password}
        body = self._call(
            "superuser saveSettings", "POST", f"/super/{sk}/accounts/saveSettings",
            data={"data": json.dumps(form)},
        )  # fmt: skip
        self._ok("superuser saveSettings", body)

    def accounts(self, sk: str) -> list[Account]:
        body = self._ok(
            "accounts/list", self._call("accounts/list", "GET", f"/super/{sk}/accounts/list")
        )
        return [
            Account(u["ke"], u["uid"], u["mail"], json.loads(u.get("details") or "{}"))
            for u in body.get("users", [])
        ]

    def register_admin(
        self, sk: str, mail: str, password: str, ke: str, details: Mapping[str, str]
    ) -> None:
        form: dict[str, Any] = {"mail": mail, "pass": password, "pass_again": password}
        form |= {"details": dict(details)} | ({"ke": ke} if ke else {})
        body = self._call(
            "registerAdmin", "POST", f"/super/{sk}/accounts/registerAdmin",
            data={"data": json.dumps(form)},
        )  # fmt: skip
        self._ok("registerAdmin", body)

    def edit_admin_details(self, sk: str, account: Account, details: Mapping[str, str]) -> None:
        # Only `details`: editAdmin passes every form field into UPDATE Users (mass assignment).
        target = {"mail": account.mail, "ke": account.ke, "uid": account.uid}
        body = self._call(
            "editAdmin", "POST", f"/super/{sk}/accounts/editAdmin",
            data={"data": json.dumps({"details": dict(details)}), "account": json.dumps(target)},
        )  # fmt: skip
        self._ok("editAdmin", body)

    def login(self, mail: str, password: str) -> Session | None:
        body = self._call(
            "admin login", "POST", "/", params={"json": "true"},
            data={"mail": mail, "pass": password, "function": "dash"},
        )  # fmt: skip
        user = body.get("$user") if isinstance(body, dict) else None
        if body.get("ok") is True and isinstance(user, dict) and user.get("auth_token"):
            return Session(str(user["auth_token"]), str(user["ke"]), str(user["uid"]))
        return None

    def api_keys(self, s: Session) -> list[ApiKey]:
        body = self._ok(
            "api/list", self._call("api/list", "GET", f"/{s.auth_token}/api/{s.ke}/list")
        )
        return [ApiKey(k["code"], k.get("ip") or "", k.get("details") or {}) for k in body["keys"]]

    def save_api_key(
        self, s: Session, code: str | None, ip: str, details: Mapping[str, Any]
    ) -> str:
        form = {"ip": ip, "details": dict(details)} | ({"code": code} if code else {})
        body = self._call(
            "api/add", "POST", f"/{s.auth_token}/api/{s.ke}/add",
            data={"data": json.dumps(form)},
        )  # fmt: skip
        api = self._ok("api/add", body).get("api")
        if not isinstance(api, dict) or not api.get("code"):
            raise BootstrapError("api/add: answer has no key code")
        return str(api["code"])

    def delete_api_key(self, s: Session, code: str) -> None:
        body = self._call(
            "api/delete", "POST", f"/{s.auth_token}/api/{s.ke}/delete",
            data={"data": json.dumps({"code": code})},
        )  # fmt: skip
        self._ok("api/delete", body)


# -- plan / apply -------------------------------------------------------------------------------

Kind = Literal["ok", "create", "update", "extra", "env"]


@dataclass(frozen=True, slots=True)
class Step:
    what: str
    kind: Kind
    changes: tuple[str, ...] = ()


def detail_changes(desired: Mapping[str, Any], actual: Mapping[str, Any]) -> list[str]:
    """Only the keys we manage; Shinobi's own extra keys are left alone."""
    out = []
    for k, want in desired.items():
        have = actual.get(k, "(missing)")
        if json.dumps(have, sort_keys=True) != json.dumps(want, sort_keys=True):
            out.append(f"{k}: {json.dumps(have)} -> {json.dumps(want)}")
    return out


def run(
    api: BootstrapApi,
    spec: Spec,
    creds: Credentials,
    env: Mapping[str, str],
    *,
    dry_run: bool,
    prune: bool = False,
    log: Callable[[str], None] = print,
) -> tuple[list[Step], dict[str, str]]:
    """Bring Shinobi to spec + creds. Returns the steps and the .env values to write (in a
    dry run: the values known without changing anything). Never logs a secret."""
    steps: list[Step] = []

    def step(s: Step) -> None:
        steps.append(s)
        log(f"{s.kind:<7} {s.what}")
        for c in s.changes:
            log(f"          {c}")

    # 1 superuser
    sk = api.super_login(creds.super_mail, creds.super_pass)
    if sk is None:
        sk = api.super_login(*DEFAULT_SUPER)
        if sk is None:
            raise BootstrapError(
                "superuser: neither the .env nor the default credentials work "
                "(changed by hand? fix .env or data/shinobi/super.json; or locked out after 5 "
                "failed logins: same {ok:false} answer, wait 15 min or restart Shinobi)"
            )
        step(Step("superuser", "update", ("credentials: image default -> .env",)))
        if not dry_run:
            api.super_set_credentials(sk, creds.super_mail, creds.super_pass)
            sk = api.super_login(creds.super_mail, creds.super_pass)
            if sk is None:
                raise BootstrapError("superuser: saved, but the new credentials do not log in")
    else:
        step(Step("superuser", "ok"))

    # 2 account
    mail = creds.admin_mail.lower()
    account = next((a for a in api.accounts(sk) if a.mail.lower() == mail), None)
    if account is None:
        ke_note = "ke = SHINOBI_GROUP_KEY" if creds.group_key else "ke generated by Shinobi"
        step(Step("account", "create", (ke_note,)))
        if dry_run:  # nothing to log in to yet: everything below would be created
            for k in spec.api_keys:
                step(Step(f"api key {k.env}", "create"))
            step(Step(".env", "env", ("SHINOBI_GROUP_KEY, SHINOBI_UID and key codes",)))
            return steps, {}
        api.register_admin(sk, creds.admin_mail, creds.admin_pass, creds.group_key,
                           spec.account_details)  # fmt: skip
    else:
        diff = detail_changes(spec.account_details, account.details)
        step(Step("account", "update" if diff else "ok", tuple(diff)))
        if diff and not dry_run:
            api.edit_admin_details(sk, account, spec.account_details)

    # 3 admin login
    session = api.login(creds.admin_mail, creds.admin_pass)
    if session is None:
        raise BootstrapError(
            "admin login failed: SHINOBI_ADMIN_PASSWORD differs from the account "
            "(or locked out after 5 failed logins)"
        )
    if creds.group_key and session.ke != creds.group_key:
        log("note    account exists with another ke than SHINOBI_GROUP_KEY; .env follows Shinobi")

    # 4 API keys
    existing = {k.code: k for k in api.api_keys(session)}
    updates = {"SHINOBI_GROUP_KEY": session.ke, "SHINOBI_UID": session.uid}
    wanted: set[str] = set()
    for k in spec.api_keys:
        code = env.get(k.env, "")
        have = existing.get(code) if code else None
        if have is None:
            step(Step(f"api key {k.env}", "create", () if not code else ("code in .env unknown",)))
            if not dry_run:
                updates[k.env] = api.save_api_key(session, None, k.ip, k.details())
            continue
        wanted.add(code)
        updates[k.env] = code
        diff = detail_changes(k.details(), have.details)
        if have.ip != k.ip:
            diff.insert(0, f"ip: {json.dumps(have.ip)} -> {json.dumps(k.ip)}")
        step(Step(f"api key {k.env}", "update" if diff else "ok", tuple(diff)))
        if diff and not dry_run:
            api.save_api_key(session, code, k.ip, k.details())
    for n, code in enumerate(c for c in existing if c not in wanted):
        perms = sorted(p for p in KEY_PERMISSIONS if existing[code].details.get(p) == "1")
        step(Step(f"api key #{n + 1} not in account.yaml", "extra", (f"permissions: {perms}",)))
        if prune and not dry_run:
            api.delete_api_key(session, code)
            log("          -> deleted")

    # 5 .env
    pending = sorted(n for n, v in updates.items() if env.get(n, "") != v)
    if dry_run:  # new key codes are unknown until created
        pending += [f"{k.env} (new)" for k in spec.api_keys if k.env not in updates]
    if pending:
        step(Step(".env", "env", (", ".join(pending),)))
    return steps, updates


def summary(steps: list[Step], dry_run: bool, prune: bool) -> str:
    counts = {k: sum(s.kind == k for s in steps) for k in ("create", "update", "ok", "extra")}
    extra = "deleted" if prune and not dry_run else "kept, use --prune to delete"
    return (
        f"{'plan' if dry_run else 'done'}: {counts['create']} create, {counts['update']} update, "
        f"{counts['ok']} ok, {counts['extra']} extra ({extra})"
    )


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    ap.add_argument("--env-file", type=Path, default=REPO / ".env")
    # On each subcommand (not on `ap`): argparse only accepts a main-parser option BEFORE the
    # subcommand, and `apply --wait 180` is how the runbook writes it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--wait", type=float, default=0, metavar="SECONDS",
                        help="wait up to SECONDS for Shinobi to answer first")  # fmt: skip
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("plan", parents=[common], help="show what apply would change (writes nothing)")
    p_apply = sub.add_parser(
        "apply", parents=[common], help="create/update what differs, rewrite .env"
    )
    p_apply.add_argument("--prune", action="store_true", help="delete keys not in account.yaml")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dry_run = args.command == "plan"
    prune = getattr(args, "prune", False)

    try:
        spec = load_spec(args.spec)
        env = load_env(args.env_file)
        creds = Credentials.from_env(env)
        if not env.get("SHINOBI_URL"):
            raise ConfigError("set SHINOBI_URL in .env")
        with ShinobiAdminApi(env["SHINOBI_URL"]) as api:
            if args.wait:
                api.wait_until_up(args.wait)
            steps, updates = run(api, spec, creds, env, dry_run=dry_run, prune=prune)
        if not dry_run:
            written = update_env(args.env_file, updates)
            print(f".env    {'updated: ' + ', '.join(written) if written else 'unchanged'}")
        print(summary(steps, dry_run, prune))
    except (ConfigError, BootstrapError) as exc:
        print(f"bootstrap: {exc}", file=sys.stderr)
        return 1
    if dry_run and any(s.kind != "ok" for s in steps):
        return 2  # like provision_monitors plan: 2 = changes pending
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
