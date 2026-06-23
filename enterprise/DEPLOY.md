# Deploying the karst enterprise gateway

One authenticated, metered MCP endpoint your whole org points its AI tools at —
so developers receive karst as approved infrastructure instead of installing it
themselves. Self-hosted; your code never leaves your perimeter.

```
   AI tools (Claude Code / Cursor / …)
            │  Authorization: Bearer <api-key | SSO JWT>
            ▼
   ┌──────────────────────────────┐        ┌───────────────┐
   │  karst gateway (this image)  │ ─────▶ │  PostgreSQL   │  keys · usage · packs
   │  • per-team key OR OIDC auth │        └───────────────┘
   │  • usage + audit metering    │
   │  • wraps karst-mcp (HTTP)    │   all on infrastructure you control
   └──────────────────────────────┘
```

## Option A — Docker Compose (fastest)

```bash
cd enterprise
export POSTGRES_PASSWORD='<a-strong-password>'
docker compose up -d                       # starts Postgres + the gateway

# mint a team key (shown once):
docker compose exec gateway \
  python -m enterprise.gateway.cli keys add --team acme --label "CI bot"
```

Point an AI tool at `http://<host>:8080/mcp` with header
`Authorization: Bearer <key>`. `GET /healthz` is open for load-balancer probes.

## Option B — your own Postgres / orchestrator

Build the image and run it with a `DATABASE_URL` pointing at your managed
Postgres (RDS, Cloud SQL, Neon, an internal cluster):

```bash
docker build -t karst-gateway -f enterprise/Dockerfile enterprise/
docker run -d -p 8080:8080 \
  -e DATABASE_URL='postgresql://user:pass@db-host:5432/karst_gateway' \
  karst-gateway
```

The schema is created automatically on first start. For Kubernetes, the same
image + a `DATABASE_URL` secret + a `Deployment`/`Service`/`Ingress` is all you
need; run `keys add` as a one-off `Job` or `kubectl exec`.

## Configuration (all via environment)

| Variable | Purpose | Default |
|----------|---------|---------|
| `DATABASE_URL` | `postgresql://…` for production (multi-instance). Omit for a local sqlite file. | sqlite `~/.karst/enterprise/gateway.db` |
| `KARST_OIDC_ISSUER` | Enterprise IdP issuer URL — **presence enables SSO** | unset (API keys only) |
| `KARST_OIDC_AUDIENCE` | Expected `aud` claim — **required when SSO is enabled** (the gateway refuses to start without it) | unset |
| `KARST_OIDC_JWKS_URL` | Explicit JWKS URL (else resolved via OIDC discovery) | discovery |
| `KARST_OIDC_TEAM_CLAIM` | JWT claim holding the team/org id | `team` |
| `KARST_OIDC_SCOPES_CLAIM` | JWT claim holding scopes/roles | `scope` |

## Enterprise SSO (OIDC)

Set `KARST_OIDC_ISSUER` (and ideally `KARST_OIDC_AUDIENCE`) and the gateway will
**also** accept JWTs minted by your IdP (Okta, Auth0, Entra ID, Keycloak, Google
Workspace). The token's signature is verified against the issuer's JWKS, and its
claims map to a team + scopes — so access follows your existing identity system,
with no separate key to distribute. Static API keys still work in parallel for
service accounts, CI, and air-gapped setups that can't reach the IdP.

Example (Compose): uncomment the `KARST_OIDC_*` block in `docker-compose.yml`.

## Auth model

Every non-health request must present `Authorization: Bearer <token>` where the
token is **either**:
- a **per-team API key** (`kst_sk_…`) created via `keys add` — hashed at rest,
  revocable, scoped; or
- an **SSO JWT** from your configured OIDC issuer (signature, issuer, **audience**
  and expiry are all verified; `KARST_OIDC_AUDIENCE` is required).

> ⚠️ **Tenant isolation is not yet enforced.** Authentication is real, but today
> *any* valid team key/JWT can call the tools against *any* repo that has been
> indexed on the gateway host — the gateway does **authentication + metering, not
> per-team repo data isolation**. Deploy one gateway per trust boundary (e.g. per
> team/project), or wait for repo-scoped access control (roadmap below). Don't
> rely on it to keep team A's code away from team B's key on a shared host.

Every call is recorded (team, tool path, latency, ok) for usage + audit:

```bash
docker compose exec gateway python -m enterprise.gateway.cli usage --team acme
```

## Air-gapped

The gateway needs no internet: use a Postgres inside your network and **API
keys** (not OIDC, which must reach your IdP — though an internal IdP works too).
Build the image from a base mirrored in your registry and install `karst` from
your internal PyPI mirror. See [`../docs/compliance/`](../docs/compliance/) for
the attestation + offline-install pack.

## Operational notes & honest gaps

- **Backups:** the Postgres database holds keys/usage/packs — back it up with
  your standard Postgres tooling (it's a normal DB).
- **TLS:** terminate TLS at your load balancer / ingress in front of the gateway.
- **Throughput:** Postgres uses a short-lived connection per operation today —
  correct under concurrency; wire `psycopg_pool` in `gateway/db.py` for very high
  request rates.
- **Not yet built (roadmap), in priority order:**
  1. **Per-team repo access control** — restrict which repos a team key/JWT may
     query (today there is none; see the Auth-model warning above). Needs the
     gateway to parse the MCP request body for the tool + `repo_path` and gate it
     against the principal. **This is the gap to close before relying on the
     gateway for multi-team isolation.**
  2. Per-MCP-tool **scope enforcement** (scopes are attached + metered but not yet
     gated at dispatch).
  3. SCIM provisioning and a SIEM-exportable, signed audit log.

  If your security review requires any of these today, raise it before deploying —
  or deploy one gateway per team as a clean interim isolation boundary.
