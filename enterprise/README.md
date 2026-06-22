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
- **Governance** — who can see which repos/packs, who called what, how many
  tokens it cost. Audit-grade, on-prem.
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
4. **RBAC + SSO** — scopes per key today; SAML/OIDC + per-repo access policy
   next.
5. **Self-hosted deploy** — Docker / compose, Postgres backend, on-prem.
6. **Admin UI** — extend the existing dashboard with key management, usage
   dashboards, and audit export.

## Status

- `gateway/keys.py` — API-key store (sqlite; Postgres-portable schema). ✅
- `gateway/usage.py` — usage metering + audit log. ✅
- `gateway/middleware.py` — ASGI per-key auth + usage logging. ✅
- `gateway/cli.py` — admin CLI: create/list/revoke keys, usage summary. ✅
- Tests: `tests/test_gateway.py`. ✅
- Pure stdlib so far (sqlite3, hashlib, secrets) — no new runtime deps.

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
