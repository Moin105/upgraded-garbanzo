# karst enterprise

Enterprise mode turns karst from a **single-developer local tool** into a
**team / org context platform** — without giving up the things that make karst
different (curated packs, determinism, on-prem privacy).

> **Open-core.** The `karst` package on PyPI (Apache-2.0) stays the free,
> self-hosted core. This `enterprise/` folder is the commercial layer that sits
> **on top** of it and is **not** shipped in the PyPI package. (Licensing for
> this folder is a deliberate decision — see [Licensing](#licensing).)

---

## The wedge (why this, vs competitors)

Competitors (Code Context Engine, claude-context, graph tools) push dashboards,
team sync, and big token-savings benchmarks. karst's durable edge is **curated,
reusable packs + deterministic indexing + a real local/air-gapped story.**
Enterprise mode monetizes that edge for teams who need:

- **Shared context, not per-dev silos** — one team publishes packs, everyone
  pulls them. Curation effort is spent once.
- **Governance** — per-team **repo + tool access control** (a key/JWT scoped to
  `acme-app` can't read another team's repo — enforced at the call level), plus
  usage + audit metering of who called what, on-prem. See [DEPLOY.md](DEPLOY.md).
- **A single endpoint** — one authenticated MCP gateway the whole org points
  their AI tools at, instead of every dev running their own server.

## Architecture

```
   Free OSS core (PyPI: karst)          Enterprise layer (this folder)
   ┌───────────────────────────┐        ┌──────────────────────────────────┐
   │ index · graph · packs     │        │ gateway/                         │
   │ karst-mcp (stdio + http)  │◀──────▶│  • API keys (per team/user)      │
   │ search_code/find_impact/… │  wraps │  • usage metering + audit log    │
   └───────────────────────────┘        │  • RBAC / scopes                 │
                                         │  • team pack registry (shared)   │
   Admin (existing Next.js dashboard) ──▶│  • keys/usage/audit management   │
                                         └──────────────────────────────────┘
```

The gateway **reuses** the OSS Streamable-HTTP MCP server (`karst-mcp --http`)
and adds the multi-tenant layer around it: instead of one shared bearer token,
every request carries a **per-team API key**, every call is **metered and
audited**, and access is **scoped**.

## Roadmap (phased)

1. **Gateway core — auth + usage** ✅ *(this commit)*
   `gateway/keys.py` (per-team API keys, hashed, revocable) +
   `gateway/usage.py` (metering + audit log) + `gateway/middleware.py`
   (ASGI auth + usage logging that wraps the MCP app). Tested.
2. **Serve** ✅ — `gateway/serve.py` composes the gateway in front of karst's
   real MCP app (`mcp.streamable_http_app()` wrapped by `GatewayAuth`);
   `python -m enterprise.gateway.cli serve`.
3. **Team pack libraries** ✅ — `gateway/packs.py`: publish/pull **versioned**
   shared pack definitions per team, so curation is done once, not per-dev.
4. **SSO + RBAC** ✅ — **OIDC/SSO** (`gateway/oidc.py`, enable with
   `KARST_OIDC_ISSUER`) accepts enterprise-IdP JWTs alongside API keys, and
   **per-team repo + per-tool access is enforced** at the call level
   (`gateway/middleware.py` gates every `tools/call`). SCIM provisioning is next.
5. **Self-hosted deploy** ✅ — Docker image + Compose (gateway + Postgres),
   `DATABASE_URL` Postgres backend, env-config. See [DEPLOY.md](DEPLOY.md).
6. **Admin UI** — extend the existing dashboard with key management, usage
   dashboards, and audit export.

## Status

- `gateway/db.py` — storage adapter: **sqlite** (dev/air-gapped) or **PostgreSQL**
  (production, via `DATABASE_URL`). Verified against live Postgres 16. ✅
- `gateway/keys.py` — API-key store (hashed, revocable, scoped). ✅
- `gateway/usage.py` — usage metering + audit log. ✅
- `gateway/oidc.py` — optional **OIDC/SSO** JWT auth (PyJWT). ✅
- `gateway/middleware.py` — ASGI auth (API key **or** SSO JWT) + usage logging. ✅
- `gateway/cli.py` — admin CLI: create/list/revoke keys, usage, packs, serve. ✅
- `Dockerfile` + `docker-compose.yml` + [DEPLOY.md](DEPLOY.md) — one-command deploy. ✅
- Tests: `tests/test_gateway.py` (15, incl. Postgres translation + OIDC). ✅
- Core (keys/usage/packs) is pure stdlib + sqlite; production adds `psycopg`
  (Postgres) and `PyJWT` (SSO) — see [requirements.txt](requirements.txt).

## Try it

```bash
# 1. create a team API key (shown once)
python -m enterprise.gateway.cli keys add --team acme --label "CI bot"

# 2. curate a shared pack once; the whole team can pull it
python -m enterprise.gateway.cli packs publish --team acme --name auth \
    --glob 'src/auth/**' --glob 'src/login/**' --desc "auth core"
python -m enterprise.gateway.cli packs list --team acme
python -m enterprise.gateway.cli packs pull --team acme --name auth   # prints the local recreate cmd

# 3. run the authenticated, metered MCP gateway for the org
python -m enterprise.gateway.cli serve --host 0.0.0.0 --port 8080

# 4. see what the team spent
python -m enterprise.gateway.cli usage --team acme
```

## Licensing

This folder is **not** Apache-2.0 by default just because it lives in the same
repo — that's a decision to make explicitly. Common open-core options:

- A separate **commercial / BSL** license for `enterprise/**` (keep the core
  Apache-2.0), or
- Keep it private (move to a separate private repo before any public release).

**TODO (owner decision):** pick the license model before this folder ships
publicly. Until then, treat `enterprise/**` as source-available internal code.
