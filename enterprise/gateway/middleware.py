"""ASGI middleware: authenticate every gateway request with a per-team API key
and record it in the usage/audit log. Wraps the OSS karst Streamable-HTTP MCP
app (the single shared-token model in `karst.mcp_server` becomes multi-tenant
here). ``GET /healthz`` stays open for liveness probes.

This is the seam where the free core and the enterprise layer meet: the inner
``app`` is exactly the OSS MCP server; everything tenant-aware lives out here.
"""
from __future__ import annotations

import time
from typing import Awaitable, Callable

from .keys import KeyStore, Principal
from .oidc import OidcVerifier
from .usage import UsageLog

Scope = dict
Receive = Callable[[], Awaitable[dict]]
Send = Callable[[dict], Awaitable[None]]


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

        # Hand the principal down to the app (so tool dispatch can enforce
        # scopes / pick the team's repos), and meter the call.
        scope = dict(scope)
        scope["karst_principal"] = principal
        start = time.monotonic()
        ok = True
        try:
            await self._app(scope, receive, send)
        except Exception:
            ok = False
            raise
        finally:
            self._usage.log(
                key_id=principal.key_id,
                team_id=principal.team_id,
                tool=path,
                latency_ms=int((time.monotonic() - start) * 1000),
                ok=ok,
            )
