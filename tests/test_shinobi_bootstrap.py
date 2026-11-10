"""Superuser, account and API keys as code (scripts/shinobi_bootstrap.py) + .env rewriting.

    uv run pytest tests/test_shinobi_bootstrap.py            # unit, no stack
    uv run pytest -m e2e tests/test_shinobi_bootstrap.py     # live plan == nothing to do

FakeShinobi keeps state the way the Shinobi source does (libs/user/utils.js, apiKeys.js,
webServerSuperPaths.js): uid and key codes are random, an unknown `code` sent to api/add creates
a NEW random key, editAdmin merges details, failed logins are counted. So the tests fail if
the bootstrap relied on choosing a value Shinobi in fact generates.
"""

from __future__ import annotations

import json
import secrets
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts import shinobi_bootstrap as sb
from services.common.envfile import load_env, update_env

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "shinobi"

ENV = {
    "SHINOBI_URL": "http://shinobi.test",
    "SHINOBI_SUPER_EMAIL": "super@example.com",
    "SHINOBI_SUPER_PASSWORD": "super-secret",
    "SHINOBI_ADMIN_EMAIL": "admin@example.com",
    "SHINOBI_ADMIN_PASSWORD": "admin-secret",
    "SHINOBI_GROUP_KEY": "GroupKey01",
}


class FakeShinobi:
    """In-memory Shinobi: one super.json entry, Users and API tables."""

    def __init__(self) -> None:
        self.super = sb.DEFAULT_SUPER
        self.users: list[dict[str, Any]] = []
        self.keys: dict[str, dict[str, Any]] = {}  # code -> {uid, ip, details}
        self.failed_logins = 0
        self.writes: list[str] = []

    @staticmethod
    def gid(n: int = 10) -> str:
        return secrets.token_hex(n)[:n]

    def super_login(self, mail: str, password: str) -> str | None:
        if (mail.lower(), password) == (self.super[0].lower(), self.super[1]):
            return "sk"
        self.failed_logins += 1
        return None

    def super_set_credentials(self, sk: str, mail: str, password: str) -> None:
        self.writes.append("super.json")
        self.super = (mail, password)

    def accounts(self, sk: str) -> list[sb.Account]:
        return [sb.Account(u["ke"], u["uid"], u["mail"], dict(u["details"])) for u in self.users]

    def register_admin(
        self, sk: str, mail: str, password: str, ke: str, details: Mapping[str, str]
    ) -> None:
        self.writes.append("registerAdmin")
        defaults = {"size": "", "days": "", "use_remote_access": "1", "use_plugin_manager": "1"}
        self.users.append(
            {
                "ke": ke or self.gid(),
                "uid": self.gid(),
                "mail": mail,
                "pass": password,
                "details": defaults | dict(details),
            }  # fmt: skip
        )

    def edit_admin_details(self, sk: str, account: sb.Account, details: Mapping[str, str]) -> None:
        self.writes.append("editAdmin")
        user = next(u for u in self.users if u["mail"] == account.mail)
        user["details"] |= dict(details)  # Object.assign(details, form.details)

    def login(self, mail: str, password: str) -> sb.Session | None:
        for u in self.users:
            if u["mail"].lower() == mail.lower() and u["pass"] == password:
                return sb.Session("token", u["ke"], u["uid"])
        self.failed_logins += 1
        return None

    def api_keys(self, s: sb.Session) -> list[sb.ApiKey]:
        return [
            sb.ApiKey(code, k["ip"], json.loads(json.dumps(k["details"])))
            for code, k in self.keys.items()
            if k["uid"] == s.uid
        ]

    def save_api_key(
        self, s: sb.Session, code: str | None, ip: str, details: Mapping[str, Any]
    ) -> str:
        self.writes.append("api/add")
        if code not in self.keys:  # editApiKey: unknown or missing code -> createApiKey, random
            code = self.gid(30)
        self.keys[code] = {"uid": s.uid, "ip": ip, "details": dict(details)}
        return code

    def delete_api_key(self, s: sb.Session, code: str) -> None:
        self.writes.append("api/delete")
        del self.keys[code]


@pytest.fixture
def spec() -> sb.Spec:
    return sb.load_spec(sb.DEFAULT_SPEC)


@pytest.fixture
def creds() -> sb.Credentials:
    return sb.Credentials.from_env(ENV)


def apply(api: FakeShinobi, spec: sb.Spec, creds: sb.Credentials, env: dict[str, str], **kw: Any):
    steps, updates = sb.run(api, spec, creds, env, dry_run=False, log=lambda _: None, **kw)
    env |= updates
    return steps


def kinds(steps: list[sb.Step]) -> dict[str, str]:
    return {s.what: s.kind for s in steps}


# -- spec and credentials -----------------------------------------------------------------------


def test_committed_spec(spec: sb.Spec) -> None:
    read, edit, trigger = spec.api_keys
    assert read.env == "SHINOBI_API_KEY" and "edit_monitors" not in read.permissions
    assert "edit_user" not in read.permissions  # least privilege: read key cannot edit the account
    assert edit.permissions == {"get_monitors", "edit_monitors"}
    # S16: injects synthetic events only; cannot read configs or edit anything
    assert trigger.permissions == {"control_monitors"}
    assert spec.account_details["use_remote_access"] == "0"
    details = read.details()
    assert set(details) == set(sb.KEY_PERMISSIONS) | set(sb.KEY_DETAILS_BASE)
    assert all(details[p] in ("0", "1") for p in sb.KEY_PERMISSIONS)  # strings, as stored


@pytest.mark.parametrize(
    ("yaml_text", "error"),
    [
        (
            "version: 1\naccount: {}\napi_keys: [{env: K, permissions: [fly]}]",
            "unknown permissions",
        ),
        ("version: 1\naccount: {}\napi_keys: [{env: k, permissions: []}]", "not an environment"),
        ("version: 1\naccount: {}\napi_keys: [{env: SHINOBI_UID, permissions: []}]", "written by"),
        (
            "version: 1\naccount: {}\n"
            "api_keys: [{env: A, permissions: []}, {env: A, permissions: []}]",
            "same variable",
        ),
        ("version: 2\naccount: {}\napi_keys: []", "version"),
        ("version: 1\naccount: {pass: x}\napi_keys: []", "pass"),
    ],
)
def test_spec_refuses(tmp_path: Path, yaml_text: str, error: str) -> None:
    path = tmp_path / "account.yaml"
    path.write_text(yaml_text)
    with pytest.raises(sb.ConfigError, match=error):
        sb.load_spec(path)


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"SHINOBI_ADMIN_PASSWORD": ""}, "SHINOBI_ADMIN_PASSWORD"),
        ({"SHINOBI_SUPER_PASSWORD": "admin"}, "image default"),
        ({"SHINOBI_GROUP_KEY": "my-group"}, "letters and digits"),  # registerAdmin strips "-"
    ],
)
def test_credentials_refused(change: dict[str, str], error: str) -> None:
    with pytest.raises(sb.ConfigError, match=error):
        sb.Credentials.from_env(ENV | change)


# -- plan / apply on the fake -------------------------------------------------------------------


def test_fresh_install_is_built_and_env_rewritten(spec: sb.Spec, creds: sb.Credentials) -> None:
    api, env = FakeShinobi(), dict(ENV)
    steps = apply(api, spec, creds, env)
    assert kinds(steps)["superuser"] == "update" and api.super == (
        ENV["SHINOBI_SUPER_EMAIL"],
        ENV["SHINOBI_SUPER_PASSWORD"],
    )
    assert api.failed_logins == 1  # only the .env superuser try on a default install
    (user,) = api.users
    assert user["ke"] == ENV["SHINOBI_GROUP_KEY"]
    assert user["details"]["use_remote_access"] == "0" and user["details"]["size"] == "20000"
    assert env["SHINOBI_UID"] == user["uid"]
    for k in spec.api_keys:
        assert api.keys[env[k.env]]["details"] == k.details()


def test_second_apply_changes_nothing(spec: sb.Spec, creds: sb.Credentials) -> None:
    api, env = FakeShinobi(), dict(ENV)
    apply(api, spec, creds, env)
    api.writes.clear()
    steps = apply(api, spec, creds, env)
    assert {s.kind for s in steps} == {"ok"} and api.writes == [] and api.failed_logins == 1


def test_wiped_database_gets_new_codes_same_ke(spec: sb.Spec, creds: sb.Credentials) -> None:
    api, env = FakeShinobi(), dict(ENV)
    apply(api, spec, creds, env)
    old = dict(env)
    api.users.clear()
    api.keys.clear()  # docker volume rm shinobi_shinobi-db (super.json is on the host)
    steps = apply(api, spec, creds, env)
    assert kinds(steps)["api key SHINOBI_API_KEY"] == "create"
    assert env["SHINOBI_GROUP_KEY"] == old["SHINOBI_GROUP_KEY"]
    assert env["SHINOBI_UID"] != old["SHINOBI_UID"]  # uid is always generated
    assert env["SHINOBI_API_KEY"] != old["SHINOBI_API_KEY"]  # old code cannot be re-used


def test_permission_drift_updates_same_code(spec: sb.Spec, creds: sb.Credentials) -> None:
    api, env = FakeShinobi(), dict(ENV)
    apply(api, spec, creds, env)
    code = env["SHINOBI_API_KEY"]
    api.keys[code]["details"]["edit_user"] = "1"  # the UI default found in Session 13
    plan, _ = sb.run(api, spec, creds, env, dry_run=True, log=lambda _: None)
    (step,) = [s for s in plan if s.what == "api key SHINOBI_API_KEY"]
    assert step.kind == "update" and step.changes == ('edit_user: "1" -> "0"',)
    apply(api, spec, creds, env)
    assert env["SHINOBI_API_KEY"] == code and api.keys[code]["details"]["edit_user"] == "0"


def test_account_detail_drift_is_merged(spec: sb.Spec, creds: sb.Credentials) -> None:
    api, env = FakeShinobi(), dict(ENV)
    apply(api, spec, creds, env)
    api.users[0]["details"] |= {"days": "5", "monitorOrder": {"x": 1}}
    steps = apply(api, spec, creds, env)
    assert kinds(steps)["account"] == "update"
    assert api.users[0]["details"]["days"] == "1"
    assert api.users[0]["details"]["monitorOrder"] == {"x": 1}  # unmanaged keys kept


def test_extra_key_kept_unless_prune(spec: sb.Spec, creds: sb.Credentials) -> None:
    api, env = FakeShinobi(), dict(ENV)
    apply(api, spec, creds, env)
    session = api.login(ENV["SHINOBI_ADMIN_EMAIL"], ENV["SHINOBI_ADMIN_PASSWORD"])
    assert session is not None
    extra = api.save_api_key(session, None, "0.0.0.0", {"get_logs": "1"})
    assert kinds(apply(api, spec, creds, env))["api key #1 not in account.yaml"] == "extra"
    assert extra in api.keys
    apply(api, spec, creds, env, prune=True)
    assert extra not in api.keys and len(api.keys) == len(spec.api_keys)


def test_dry_run_writes_nothing(spec: sb.Spec, creds: sb.Credentials) -> None:
    api = FakeShinobi()
    steps, updates = sb.run(api, spec, creds, dict(ENV), dry_run=True, log=lambda _: None)
    assert api.writes == [] and updates == {}
    creates = ["create"] * (1 + len(spec.api_keys))  # account + one per key
    assert [s.kind for s in steps] == ["update", *creates, "env"]


def test_wrong_admin_password_stops_after_one_try(spec: sb.Spec, creds: sb.Credentials) -> None:
    api, env = FakeShinobi(), dict(ENV)
    apply(api, spec, creds, env)
    wrong = sb.Credentials.from_env(ENV | {"SHINOBI_ADMIN_PASSWORD": "other"})
    with pytest.raises(sb.BootstrapError, match="SHINOBI_ADMIN_PASSWORD"):
        apply(api, spec, wrong, env)
    assert api.failed_logins == 2  # 1 from the first apply, 1 now: no retries


def test_log_never_contains_secrets(spec: sb.Spec, creds: sb.Credentials) -> None:
    api, env, lines = FakeShinobi(), dict(ENV), []
    sb.run(api, spec, creds, env, dry_run=False, log=lines.append)
    text = "\n".join(lines)
    for value in [*api.keys, api.users[0]["uid"], ENV["SHINOBI_SUPER_PASSWORD"]]:
        assert value not in text


# -- the HTTP layer against recorded answers ----------------------------------------------------


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _replay(request: httpx.Request) -> httpx.Response:
    path, method = request.url.path, request.method
    if method == "POST" and path == "/super":
        return httpx.Response(200, json=_fixture("super_login"))
    if method == "POST" and path == "/":
        return httpx.Response(200, json=_fixture("admin_login"))
    if path == "/super/SESSION_KEY/accounts/list":
        return httpx.Response(200, json=_fixture("accounts_list"))
    if path == "/AUTH_TOKEN/api/GROUP_KEY/list":
        return httpx.Response(200, json=_fixture("api_list"))
    if path == "/AUTH_TOKEN/api/GROUP_KEY/add":
        form = json.loads(httpx.QueryParams(request.content.decode())["data"])
        return httpx.Response(200, json={"ok": True, "api": {"code": form.get("code", "NEW")}})
    return httpx.Response(200, json={"ok": False, "msg": "Not Authorized"})


@pytest.fixture
def http_api() -> sb.ShinobiAdminApi:
    return sb.ShinobiAdminApi("http://shinobi.test", transport=httpx.MockTransport(_replay))


def test_parse_recorded_answers(http_api: sb.ShinobiAdminApi, spec: sb.Spec) -> None:
    sk = http_api.super_login("super@example.com", "x")
    assert sk == "SESSION_KEY"
    (account,) = http_api.accounts(sk)
    assert account.mail == "admin@example.com" and account.ke == "GROUP_KEY"
    assert isinstance(account.details, dict)  # accounts/list sends details as a JSON *string*
    assert sb.detail_changes(spec.account_details, account.details) == []
    session = http_api.login("admin@example.com", "x")
    assert session == sb.Session("AUTH_TOKEN", "GROUP_KEY", "USER_ID")
    keys = {k.code: k for k in http_api.api_keys(session)}
    assert set(keys) == {"API_KEY", "EDIT_API_KEY"}
    # Recorded before Session 13's apply: the UI-made read key had edit_user and get_alarms.
    read_spec = spec.api_keys[0]
    assert sb.detail_changes(read_spec.details(), keys["API_KEY"].details) == [
        'edit_user: "1" -> "0"',
        'get_alarms: "1" -> "0"',
    ]
    assert sb.detail_changes(spec.api_keys[1].details(), keys["EDIT_API_KEY"].details) == []


def test_api_add_wire_format(http_api: sb.ShinobiAdminApi) -> None:
    s = sb.Session("AUTH_TOKEN", "GROUP_KEY", "USER_ID")
    assert http_api.save_api_key(s, "API_KEY", "0.0.0.0", {"get_logs": "1"}) == "API_KEY"
    assert http_api.save_api_key(s, None, "0.0.0.0", {"get_logs": "1"}) == "NEW"


def test_refusal_and_transport_errors_hide_secrets() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused for " + str(request.url))

    api = sb.ShinobiAdminApi("http://shinobi.test", transport=httpx.MockTransport(boom))
    with pytest.raises(sb.BootstrapError) as exc:
        api.accounts("SECRET_SESSION")
    assert "SECRET_SESSION" not in str(exc.value) and exc.value.__cause__ is None
    refusing = sb.ShinobiAdminApi("http://shinobi.test", transport=httpx.MockTransport(_replay))
    with pytest.raises(sb.BootstrapError, match="Not Authorized"):
        refusing.accounts("wrong")


@pytest.mark.parametrize(
    "argv",
    [["apply", "--wait", "180"], ["plan", "--wait", "5"], ["apply", "--prune", "--wait", "1"]],
)
def test_cli_accepts_documented_forms(argv: list[str]) -> None:
    # The runbook writes `apply --wait 180`; first version only accepted `--wait 180 apply`.
    args = sb.build_parser().parse_args(argv)
    assert args.command == argv[0] and args.wait > 0


# -- .env rewriting -----------------------------------------------------------------------------


def test_update_env_keeps_everything_else(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    original = "# comment\nA=1\n\n# B=commented\nB=old=x\nC=3\n"
    path.write_text(original)
    path.chmod(0o644)
    assert update_env(path, {"B": "new", "D": "4", "A": "1"}) == ["B", "D"]
    assert path.read_text() == "# comment\nA=1\n\n# B=commented\nB=new\nC=3\nD=4\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load_env(path)["B"] == "new"
    assert update_env(path, {"B": "new"}) == []
    assert not list(tmp_path.glob(".*.tmp"))


def test_update_env_refuses_newlines(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        update_env(tmp_path / ".env", {"A": "x\nB=y"})


# -- live ---------------------------------------------------------------------------------------


@pytest.mark.e2e
def test_live_matches_spec(spec: sb.Spec) -> None:
    env = load_env(REPO / ".env")
    with sb.ShinobiAdminApi(env["SHINOBI_URL"]) as api:
        steps, updates = sb.run(
            api, spec, sb.Credentials.from_env(env), env, dry_run=True, log=lambda _: None
        )
    assert {s.what: s.kind for s in steps if s.kind != "ok"} == {}
    assert all(env.get(k) == v for k, v in updates.items())
