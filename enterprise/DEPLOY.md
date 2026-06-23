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
| `KARST_OIDC_REPOS_CLAIM` | JWT claim holding allowed repo names (`*` = all) | `repos` |

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
  revocable, **tool- and repo-scoped**; or
- an **SSO JWT** from your configured OIDC issuer (signature, issuer, **audience**
  and expiry are all verified; `KARST_OIDC_AUDIENCE` is required).

**Tenant isolation is enforced at the call level.** The gateway parses each
`tools/call` and rejects it (HTTP 403) unless the principal's **scopes** allow the
tool *and* its **repos** allow the requested `repo_path`. So a key scoped to
`acme-app` cannot read `other-team-app` on a shared host:

```bash
# this key can only call search_code/list_packs, and only against acme's repos
docker compose exec gateway python -m enterprise.gateway.cli keys add \
  --team acme --label "acme bot" --scopes search_code,list_packs --repos acme-app,acme-api
```

Keys default to `--repos '*'` (all repos) for single-team/host convenience — set
`--repos` for per-team isolation. For SSO, a `repos` (and `scope`) claim on the JWT
carries the same limits; absent a `repos` claim a token gets all repos for its team
(scopes still gate the tools, and SSO tokens need an explicit `scope` claim).

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
- **Enforced today:** per-team **repo access control** + per-tool **scope**
  enforcement (the gateway parses each `tools/call` and 403s anything outside the
  principal's repos/scopes), and the usage/audit log records the real tool + repo.
- **Not yet built (roadmap):** SCIM auto-provisioning, and a cryptographically
  **signed, SIEM-exportable** audit log (the usage log is exportable from Postgres
  today, but not tamper-evident). If your review requires these, raise it before
  deploying.
