"""ASGI middleware: authenticate every gateway request with a per-team API key
and record it in the usage/audit log. Wraps the OSS karst Streamable-HTTP MCP
app (the single shared-token model in `karst.mcp_server` becomes multi-tenant
here). ``GET /healthz`` stays open for liveness probes.

This is the seam where the free core and the enterprise layer meet: the inner
``app`` is exactly the OSS MCP server; everything tenant-aware lives out here.
"""
from __future__ import annotations

import json
import time
from typing import Awaitable, Callable

from .keys import KeyStore, Principal
from .oidc import OidcVerifier
from .usage import UsageLog

Scope = dict
Receive = Callable[[], Awaitable[dict]]
Send = Callable[[dict], Awaitable[None]]


def _parse_call(body: bytes) -> tuple[str | None, str | None, str | None, object]:
    """Pull (method, tool, repo_path, rpc_id) from a JSON-RPC MCP request body.
    Returns Nones for anything we don't recognise (let the inner app handle it)."""
    try:
        msg = json.loads(body or b"{}")
    except Exception:
        return (None, None, None, None)
    if not isinstance(msg, dict):
        return (None, None, None, None)
    method = msg.get("method")
    rpc_id = msg.get("id")
    if method != "tools/call":
        return (method, None, None, rpc_id)
    params = msg.get("params") or {}
    args = params.get("arguments") or {}
    return (method, params.get("name"), args.get("repo_path"), rpc_id)


class GatewayAuth:
    def __init__(
        self,
        app,
        *,
        keys: KeyStore,
        usage: UsageLog,
        oidc: OidcVerifier | None = None,
        open_paths: tuple[str, ...] = ("/healthz", "/health"),
    ) -> None:
        self._app = app
        self._keys = keys
        self._usage = usage
        self._oidc = oidc
        self._open = set(open_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            # websockets/lifespan pass straight through.
            await self._app(scope, receive, send)
            return

        from starlette.responses import JSONResponse, PlainTextResponse

        path = scope.get("path", "")
        if scope.get("method") == "GET" and path in self._open:
            await PlainTextResponse("ok")(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode("latin-1")
        token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        # Accept an enterprise SSO (OIDC JWT) token if configured, else fall
        # back to a static per-team API key. Either resolves to a Principal.
        principal: Principal | None = None
        if self._oidc is not None:
            principal = self._oidc.verify(token)
        if principal is None:
            principal = self._keys.verify(token)

        if principal is None:
            await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
            return

        # Buffer the request body so we can (a) authorize the call against the
        # principal and (b) replay it unchanged to the inner MCP app.
        buffered: list[dict] = []
        body = b""
        while True:
            m = await receive()
            buffered.append(m)
            if m.get("type") == "http.request":
                body += m.get("body", b"")
                if not m.get("more_body", False):
                    break
            else:
                break  # http.disconnect, etc.

        method, tool, repo, rpc_id = _parse_call(body)

        # Authorization: gate tools/call by scope AND repo. Protocol methods
        # (initialize, tools/list, ping, notifications/*) pass through.
        denial: str | None = None
        if method == "tools/call":
            if tool and not principal.may(tool):
                denial = f"key not permitted to call tool '{tool}'"
            elif not principal.may_access_repo(repo):
                denial = f"key not permitted to access repo '{repo}'"

        scope = dict(scope)
        scope["karst_principal"] = principal
        start = time.monotonic()

        if denial is not None:
            await JSONResponse(
                {"jsonrpc": "2.0", "id": rpc_id,
                 "error": {"code": -32001, "message": f"forbidden: {denial}"}},
                status_code=403,
            )(scope, receive, send)
            self._usage.log(
                key_id=principal.key_id, team_id=principal.team_id,
                tool=tool or path, repo=repo,
                latency_ms=int((time.monotonic() - start) * 1000), ok=False,
            )
            return

        # Replay the buffered body to the inner app, then defer to live receive.
        idx = 0

        async def replay() -> dict:
            nonlocal idx
            if idx < len(buffered):
                msg = buffered[idx]
                idx += 1
                return msg
            return await receive()

        ok = True
        try:
            await self._app(scope, replay, send)
        except Exception:
            ok = False
            raise
        finally:
            self._usage.log(
                key_id=principal.key_id, team_id=principal.team_id,
                tool=tool or path, repo=repo,
                latency_ms=int((time.monotonic() - start) * 1000), ok=ok,
            )
