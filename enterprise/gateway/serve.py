"""Run the enterprise gateway: the OSS karst MCP server, fronted by per-team
API-key auth + usage metering.

This is the seam between free core and enterprise layer — the inner ASGI app is
exactly `karst-mcp --http`; `GatewayAuth` makes it multi-tenant. One endpoint
the whole org points its AI tools at, with governance.

    python -m enterprise.gateway.cli serve --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

from pathlib import Path

from .db import resolve_url
from .keys import KeyStore
from .middleware import GatewayAuth
from .oidc import OidcVerifier
from .usage import UsageLog


def build_app(db: str | Path | None = None):
    """Build the gateway ASGI app: karst's MCP Streamable-HTTP app wrapped with
    per-key (and optional OIDC/SSO) auth + usage logging. Returns
    ``(app, keys, usage)``. ``db`` defaults to ``DATABASE_URL`` (env)."""
    # Import lazily so the rest of the package (keys/usage/packs + their tests)
    # has zero heavy deps; the MCP app needs mcp/starlette/uvicorn.
    from karst.mcp_server import mcp

    url = resolve_url(db)
    inner = mcp.streamable_http_app()
    keys = KeyStore(url)
    usage = UsageLog(url)
    oidc = OidcVerifier.from_env()  # None unless KARST_OIDC_ISSUER is set
    return GatewayAuth(inner, keys=keys, usage=usage, oidc=oidc), keys, usage


def serve(*, host: str = "0.0.0.0", port: int = 8080, db: str | Path | None = None) -> None:
    import sys

    import uvicorn

    from karst.mcp_server import mcp

    import os

    app, keys, _ = build_app(db)
    path = getattr(mcp.settings, "streamable_http_path", "/mcp")
    active = sum(1 for k in keys.list_keys() if k.revoked_at is None)
    backend = "PostgreSQL" if (resolve_url(db).startswith(("postgres://", "postgresql://"))) else "sqlite"
    oidc_on = "on" if os.environ.get("KARST_OIDC_ISSUER") else "off"
    print(
        f"[karst-enterprise] gateway on http://{host}:{port}{path}  (store: {backend}, SSO: {oidc_on})\n"
        f"[karst-enterprise] {active} active API key(s). GET /healthz is open; "
        f"everything else needs Authorization: Bearer <api-key-or-SSO-JWT>.",
        file=sys.stderr,
    )
    if active == 0 and oidc_on == "off":
        print(
            "[karst-enterprise] WARNING: no API keys yet — create one:\n"
            "    python -m enterprise.gateway.cli keys add --team <team> --label <label>",
            file=sys.stderr,
        )
    uvicorn.run(app, host=host, port=port, log_level="info")
