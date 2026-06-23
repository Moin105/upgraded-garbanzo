"""Tests for the enterprise gateway: API keys, usage metering, and the ASGI
auth middleware. Pure stdlib except the middleware test (needs starlette, which
ships with mcp/fastmcp)."""
from __future__ import annotations

import asyncio
import json

import pytest

from enterprise.gateway.keys import KEY_PREFIX, KeyStore
from enterprise.gateway.usage import UsageLog


# ---- keys ------------------------------------------------------------------

def test_create_and_verify(tmp_path):
    store = KeyStore(tmp_path / "g.db")
    raw, kid = store.create_key("acme", label="CI", scopes=("search_code", "find_impact"))
    assert raw.startswith(KEY_PREFIX)
    p = store.verify(raw)
    assert p is not None and p.team_id == "acme" and p.key_id == kid
    assert p.may("search_code") and not p.may("list_packs")
    assert store.verify("kst_sk_not-a-real-key") is None
    assert store.verify("") is None and store.verify(None) is None


def test_revoke(tmp_path):
    store = KeyStore(tmp_path / "g.db")
    raw, kid = store.create_key("acme")
    assert store.verify(raw) is not None
    assert store.revoke(kid) is True
    assert store.verify(raw) is None          # revoked key never authenticates
    assert store.revoke(kid) is False          # already revoked


def test_list_keys_never_leaks_secret(tmp_path):
    store = KeyStore(tmp_path / "g.db")
    store.create_key("acme", label="a")
    store.create_key("acme", label="b")
    store.create_key("other", label="c")
    acme = store.list_keys("acme")
    assert len(acme) == 2
    # KeyInfo carries only a short display prefix, never the full secret.
    for k in acme:
        assert k.prefix.startswith(KEY_PREFIX) and len(k.prefix) < 20
        assert not hasattr(k, "key_hash")


def test_wildcard_scope(tmp_path):
    store = KeyStore(tmp_path / "g.db")
    raw, _ = store.create_key("acme", scopes=("*",))
    p = store.verify(raw)
    assert p.may("anything") and p.may("search_code")


def test_may_access_repo():
    from enterprise.gateway.keys import Principal

    p = Principal(0, "acme", "x", ("search_code",), repos=("acme-app", "billing"))
    assert p.may_access_repo("/srv/repos/acme-app")       # by full path's basename
    assert p.may_access_repo("acme-app")                  # by bare name
    assert p.may_access_repo("C:\\repos\\billing")        # windows path
    assert not p.may_access_repo("/srv/repos/other-team")  # not in the allowlist
    assert not p.may_access_repo(None)                     # scoped key must name a repo

    star = Principal(0, "acme", "x", ("*",), repos=("*",))
    assert star.may_access_repo("/anything") and star.may_access_repo(None)


def test_repos_round_trip(tmp_path):
    store = KeyStore(tmp_path / "g.db")
    raw, _ = store.create_key("acme", repos=("acme-app", "acme-api"))
    assert store.verify(raw).repos == ("acme-app", "acme-api")
    assert store.list_keys("acme")[0].repos == ("acme-app", "acme-api")
    raw2, _ = store.create_key("ops")                      # default = all
    assert store.verify(raw2).repos == ("*",)


# ---- usage -----------------------------------------------------------------

def test_usage_summary_and_recent(tmp_path):
    log = UsageLog(tmp_path / "u.db")
    log.log(key_id=1, team_id="acme", tool="search_code", tokens_in=100, tokens_out=20, ok=True)
    log.log(key_id=1, team_id="acme", tool="find_impact", tokens_in=50, tokens_out=10, ok=True)
    log.log(key_id=2, team_id="acme", tool="search_code", ok=False)
    log.log(key_id=9, team_id="other", tool="search_code", tokens_in=999, ok=True)

    s = log.summary(team_id="acme")
    assert s["calls"] == 3
    assert s["tokens_in"] == 150 and s["tokens_out"] == 30
    assert s["errors"] == 1

    assert log.summary()["calls"] == 4           # all teams
    assert len(log.recent(team_id="acme")) == 3
    assert log.recent(limit=1)[0]["team_id"] in {"acme", "other"}


# ---- middleware ------------------------------------------------------------

def _run_asgi(app, scope):
    """Drive an ASGI app once and return the list of sent messages."""
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        sent.append(msg)

    asyncio.run(app(scope, receive, send))
    return sent


def test_middleware_auth_and_metering(tmp_path):
    pytest.importorskip("starlette")
    from enterprise.gateway.middleware import GatewayAuth

    keys = KeyStore(tmp_path / "g.db")
    usage = UsageLog(tmp_path / "u.db")
    raw, _ = keys.create_key("acme", label="bot")

    calls = {"n": 0}

    async def inner(scope, receive, send):
        calls["n"] += 1
        assert scope.get("karst_principal") is not None  # principal handed down
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    gw = GatewayAuth(inner, keys=keys, usage=usage)

    # 1) no key -> 401, inner NOT called, no usage event
    sent = _run_asgi(gw, {"type": "http", "method": "POST", "path": "/mcp", "headers": []})
    assert sent[0]["status"] == 401
    assert calls["n"] == 0
    assert usage.summary()["calls"] == 0

    # 2) valid key -> inner called, one usage event for the team
    scope = {"type": "http", "method": "POST", "path": "/mcp",
             "headers": [(b"authorization", f"Bearer {raw}".encode())]}
    sent = _run_asgi(gw, scope)
    assert sent[0]["status"] == 200
    assert calls["n"] == 1
    s = usage.summary(team_id="acme")
    assert s["calls"] == 1 and s["errors"] == 0

    # 3) open health path -> 200 without a key
    sent = _run_asgi(gw, {"type": "http", "method": "GET", "path": "/healthz", "headers": []})
    assert sent[0]["status"] == 200


# ---- team pack library -----------------------------------------------------

def test_pack_registry_versions(tmp_path):
    from enterprise.gateway.packs import PackRegistry

    reg = PackRegistry(tmp_path / "p.db")
    v1 = reg.publish("acme", "auth", ["src/auth/**"], description="auth core")
    v2 = reg.publish("acme", "auth", ["src/auth/**", "src/login/**"], description="auth + login")
    assert v1.version == 1 and v2.version == 2          # auto-incrementing, non-destructive
    assert reg.get("acme", "auth").version == 2          # latest by default
    assert reg.get("acme", "auth", version=1).globs == ("src/auth/**",)
    assert [p.version for p in reg.history("acme", "auth")] == [2, 1]


def test_pack_registry_list_is_latest_per_name_and_team_scoped(tmp_path):
    from enterprise.gateway.packs import PackRegistry

    reg = PackRegistry(tmp_path / "p.db")
    reg.publish("acme", "auth", ["a/**"])
    reg.publish("acme", "auth", ["a/**", "b/**"])   # v2
    reg.publish("acme", "billing", ["pay/**"])
    reg.publish("other", "secret", ["x/**"])         # different team

    acme = reg.list_packs("acme")
    assert {p.name for p in acme} == {"auth", "billing"}
    assert next(p for p in acme if p.name == "auth").version == 2   # only the latest
    assert reg.list_packs("other") and reg.list_packs("other")[0].name == "secret"


def test_pack_registry_validation(tmp_path):
    from enterprise.gateway.packs import PackRegistry

    reg = PackRegistry(tmp_path / "p.db")
    with pytest.raises(ValueError):
        reg.publish("acme", "", ["a/**"])
    with pytest.raises(ValueError):
        reg.publish("acme", "auth", [])


def test_serve_build_app_smoke(tmp_path):
    # The gateway wraps karst's real MCP app — needs the MCP/HTTP deps.
    pytest.importorskip("mcp")
    pytest.importorskip("starlette")
    from enterprise.gateway.serve import build_app

    app, keys, usage = build_app(tmp_path / "g.db")
    assert callable(app)                # ASGI app (GatewayAuth instance)
    assert keys.list_keys() == []       # fresh db, no keys yet
    keys.close()
    usage.close()


# ---- db backend (sqlite <-> postgres translation) --------------------------

def test_db_postgres_translation():
    from enterprise.gateway.db import Db, is_postgres

    assert is_postgres("postgresql://u:p@h/db") and is_postgres("postgres://h/db")
    assert not is_postgres("/tmp/gateway.db")

    db = Db.__new__(Db)          # no connection — just exercise the translators
    db.is_pg = True
    assert db._q("WHERE a = ? AND b = ?") == "WHERE a = %s AND b = %s"
    ddl = db._ddl("id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL")
    assert "GENERATED BY DEFAULT AS IDENTITY" in ddl
    assert "DOUBLE PRECISION" in ddl and "REAL" not in ddl

    db.is_pg = False             # sqlite is pass-through
    assert db._q("a = ?") == "a = ?"
    assert db._ddl("ts REAL") == "ts REAL"


def test_db_resolve_url(monkeypatch, tmp_path):
    from enterprise.gateway.db import resolve_url

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("KARST_GATEWAY_DATABASE_URL", raising=False)
    assert resolve_url(tmp_path / "g.db") == str(tmp_path / "g.db")
    monkeypatch.setenv("DATABASE_URL", "postgresql://h/db")
    assert resolve_url(tmp_path / "g.db") == "postgresql://h/db"   # env wins


# ---- OIDC / SSO ------------------------------------------------------------

def test_oidc_principal_mapping():
    from enterprise.gateway.oidc import OidcConfig, OidcVerifier

    v = OidcVerifier(OidcConfig(issuer="https://idp", team_claim="org", scopes_claim="scope"))
    p = v.principal_from_claims(
        {"org": "acme", "scope": "search_code find_impact", "email": "dev@acme.com"}
    )
    assert p is not None and p.team_id == "acme" and p.label == "dev@acme.com"
    assert p.key_id == 0                       # SSO principal marker
    assert p.may("search_code") and not p.may("list_packs")

    assert v.principal_from_claims({"org": "acme", "scope": "*"}).may("anything")
    assert v.principal_from_claims({"sub": "no-team"}) is None   # no team claim

    # missing scope claim -> NO scopes (fail closed), not the default toolset
    p3 = v.principal_from_claims({"org": "acme"})
    assert p3 is not None and p3.scopes == () and not p3.may("search_code")


def test_oidc_from_env(monkeypatch):
    from enterprise.gateway.oidc import OidcVerifier

    monkeypatch.delenv("KARST_OIDC_ISSUER", raising=False)
    monkeypatch.delenv("KARST_OIDC_AUDIENCE", raising=False)
    assert OidcVerifier.from_env() is None                       # disabled by default

    # issuer set but no audience -> fail closed (would otherwise accept tokens
    # minted for a different relying party in the same IdP)
    monkeypatch.setenv("KARST_OIDC_ISSUER", "https://idp.example.com/")
    with pytest.raises(ValueError):
        OidcVerifier.from_env()

    monkeypatch.setenv("KARST_OIDC_AUDIENCE", "karst-gateway")
    monkeypatch.setenv("KARST_OIDC_TEAM_CLAIM", "org")
    v = OidcVerifier.from_env()
    assert v is not None and v.config.audience == "karst-gateway"
    assert v.config.team_claim == "org"


def test_middleware_accepts_oidc_token(tmp_path):
    pytest.importorskip("starlette")
    from enterprise.gateway.keys import KeyStore, Principal
    from enterprise.gateway.middleware import GatewayAuth
    from enterprise.gateway.usage import UsageLog

    keys = KeyStore(tmp_path / "g.db")
    usage = UsageLog(tmp_path / "u.db")

    class FakeOidc:
        def verify(self, token):
            if token == "good-jwt":
                return Principal(key_id=0, team_id="sso-team", label="sso@x", scopes=("search_code",))
            return None

    async def inner(scope, receive, send):
        assert scope["karst_principal"].team_id == "sso-team"
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    gw = GatewayAuth(inner, keys=keys, usage=usage, oidc=FakeOidc())

    # valid SSO JWT -> 200 + metered under the team from the token's claims
    scope = {"type": "http", "method": "POST", "path": "/mcp",
             "headers": [(b"authorization", b"Bearer good-jwt")]}
    sent = _run_asgi(gw, scope)
    assert sent[0]["status"] == 200
    assert usage.summary(team_id="sso-team")["calls"] == 1

    # neither a valid JWT nor a valid key -> 401
    bad = {"type": "http", "method": "POST", "path": "/mcp",
           "headers": [(b"authorization", b"Bearer nope")]}
    assert _run_asgi(gw, bad)[0]["status"] == 401


# ---- repo + scope enforcement (multi-tenant isolation) ---------------------

def _run_asgi_with_body(app, scope, body: bytes):
    """Drive an ASGI app once, delivering `body` as the request payload."""
    sent: list[dict] = []
    state = {"done": False}

    async def receive():
        if not state["done"]:
            state["done"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        sent.append(msg)

    asyncio.run(app(scope, receive, send))
    return sent


def _tools_call(raw, tool, repo):
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": tool, "arguments": {"repo_path": repo}},
    }).encode()
    scope = {"type": "http", "method": "POST", "path": "/mcp",
             "headers": [(b"authorization", f"Bearer {raw}".encode())]}
    return scope, body


def test_repo_and_scope_enforcement(tmp_path):
    pytest.importorskip("starlette")
    from enterprise.gateway.middleware import GatewayAuth

    keys = KeyStore(tmp_path / "g.db")
    usage = UsageLog(tmp_path / "u.db")
    # key scoped to ONE repo and only two tools
    raw, _ = keys.create_key("acme", scopes=("search_code", "list_packs"), repos=("acme-app",))

    seen = {"n": 0, "body": None}

    async def inner(scope, receive, send):
        seen["n"] += 1
        m = await receive()
        seen["body"] = m["body"]
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    gw = GatewayAuth(inner, keys=keys, usage=usage)

    # 1) allowed repo + allowed tool -> 200, inner called, body replayed intact
    scope, body = _tools_call(raw, "search_code", "/srv/repos/acme-app")
    sent = _run_asgi_with_body(gw, scope, body)
    assert sent[0]["status"] == 200 and seen["n"] == 1
    assert seen["body"] == body                                  # replayed unchanged

    # 2) DIFFERENT team's repo -> 403, inner NOT called (the isolation guarantee)
    scope, body = _tools_call(raw, "search_code", "/srv/repos/other-team")
    sent = _run_asgi_with_body(gw, scope, body)
    assert sent[0]["status"] == 403 and seen["n"] == 1

    # 3) tool outside the key's scopes -> 403
    scope, body = _tools_call(raw, "find_impact", "/srv/repos/acme-app")
    sent = _run_asgi_with_body(gw, scope, body)
    assert sent[0]["status"] == 403 and seen["n"] == 1

    # 4) a wildcard key can reach any repo/tool
    raw2, _ = keys.create_key("ops", scopes=("*",), repos=("*",))
    scope, body = _tools_call(raw2, "find_impact", "/srv/repos/anything")
    sent = _run_asgi_with_body(gw, scope, body)
    assert sent[0]["status"] == 200 and seen["n"] == 2

    # denied calls are recorded as errors in the audit log, with the real tool
    assert usage.summary(team_id="acme")["errors"] == 2


def test_served_tokens_are_metered(tmp_path):
    pytest.importorskip("starlette")
    from enterprise.gateway.middleware import GatewayAuth

    keys = KeyStore(tmp_path / "g.db")
    usage = UsageLog(tmp_path / "u.db")
    raw, _ = keys.create_key("acme", scopes=("*",), repos=("*",))

    payload = b"x" * 4000   # the retrieved-context the gateway serves back

    async def inner(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": payload})

    gw = GatewayAuth(inner, keys=keys, usage=usage)
    scope, body = _tools_call(raw, "search_code", "/srv/repos/acme-app")
    sent = _run_asgi_with_body(gw, scope, body)
    assert sent[0]["status"] == 200

    s = usage.summary(team_id="acme")
    # tokens_out ~= served-bytes/4; no longer the old always-zero. tokens_in
    # reflects the request arguments. This is the per-team billing signal.
    assert s["tokens_out"] == len(payload) // 4 == 1000
    assert s["tokens_in"] == len(body) // 4 > 0


def test_protocol_methods_pass_through(tmp_path):
    pytest.importorskip("starlette")
    from enterprise.gateway.middleware import GatewayAuth

    keys = KeyStore(tmp_path / "g.db")
    usage = UsageLog(tmp_path / "u.db")
    raw, _ = keys.create_key("acme", scopes=("search_code",), repos=("acme-app",))

    async def inner(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    gw = GatewayAuth(inner, keys=keys, usage=usage)
    # tools/list is a protocol method, not a tools/call — must pass even for a
    # tightly-scoped key.
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
    scope = {"type": "http", "method": "POST", "path": "/mcp",
             "headers": [(b"authorization", f"Bearer {raw}".encode())]}
    sent = _run_asgi_with_body(gw, scope, body)
    assert sent[0]["status"] == 200
