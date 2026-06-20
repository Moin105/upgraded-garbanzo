# karst — deployment runbook

End-to-end steps to ship karst to production. Read top-to-bottom the first time; skim later.

## 1. Domain & DNS

Register **`karst.dev`** (Porkbun, Cloudflare Registrar, Namecheap — any TLD-supporting registrar).

DNS records:

| Type | Host | Value | Purpose |
|---|---|---|---|
| `A` | `@` (apex) | Vercel anycast IP `76.76.21.21` | Landing site at `karst.dev`. |
| `CNAME` | `www` | `cname.vercel-dns.com` | `www.karst.dev` -> landing. |
| `CNAME` | `admin` | `karst-dashboard.fly.dev` | `admin.karst.dev` -> Fly dashboard. |

TTL 300 while setting up; bump to 3600 once stable.

## 2. Splitting the landing page out of the main repo

The landing site needs to be public (so Vercel can build it for free, and so anyone can view source). The dashboard and CLI stay in the private monorepo. Extract `landing/` into its own public repo.

### Option A — `git filter-repo` (clean history, recommended)

```bash
# from a fresh clone of the private repo
git clone git@github.com:you/karst.git karst-landing-extract
cd karst-landing-extract

# install: pipx install git-filter-repo  (or: pip install git-filter-repo)
git filter-repo --path landing --path-rename landing/:

# push to a new empty public repo
git remote add origin git@github.com:you/karst-landing.git
git branch -M main
git push -u origin main
```

Result: a repo whose root is what used to be `landing/`, with only the commits that touched those files.

### Option B — `git subtree split` (simpler, keeps merge commits)

```bash
# from the private repo, on main
git subtree split --prefix=landing -b landing-main

# push that branch to a new public repo
git push git@github.com:you/karst-landing.git landing-main:main
```

Faster, no extra tooling, but history is less tidy. Fine for a landing page.

After either option, delete `landing/` from the private repo in a follow-up commit so it doesn't drift.

## 3. Deploying landing to Vercel

1. Vercel dashboard -> **Add New -> Project** -> import `karst-landing`.
2. Framework preset: auto-detect (Next.js / Astro / whatever the landing uses).
3. **Build command**: leave default. No env vars needed if it's static.
4. Deploy.
5. **Settings -> Domains -> Add** `karst.dev` and `www.karst.dev`. Vercel will show the A/CNAME records it expects — they match the table in section 1.
6. Wait for the cert (auto, ~1 min).

The waitlist form on landing posts to `https://admin.karst.dev/api/waitlist` (CORS allowlist that origin on the dashboard).

## 4. Deploying dashboard to Fly.io

```bash
# install flyctl
curl -L https://fly.io/install.sh | sh
fly auth login

# from dashboard/ — fly.toml is already in the repo
cd dashboard
fly launch --no-deploy   # confirm settings, do NOT let it generate a new toml

# create the persistent volume for SQLite
fly volumes create karst_data --region iad --size 1

# set secrets (do NOT commit these)
fly secrets set \
  KARST_SESSION_SECRET=$(openssl rand -hex 32) \
  KARST_ADMIN_EMAIL=you@example.com \
  KARST_ADMIN_PASSWORD_HASH='salt:hash-from-scrypt-one-liner' \
  KARST_INGEST_TOKEN=$(openssl rand -hex 24)

# ship it
fly deploy
```

Verify: `fly status`, then `curl -I https://karst-dashboard.fly.dev` -> 200.

Attach the custom hostname:

```bash
fly certs add admin.karst.dev
```

Wait for the cert to issue (Fly polls the CNAME).

## 5. First-time admin login

1. Visit `https://admin.karst.dev/login`.
2. Email = `KARST_ADMIN_EMAIL`, password = the plaintext you fed the scrypt one-liner.
3. Cookie set, redirected to `/` (Overview).
4. Smoke-test the main routes:
   - `/`           — Overview (KPIs, recent activity, pipeline)
   - `/signups`    — waitlist emails (the landing form posts here)
   - `/partners`   — design-partner CRM (kanban by status)
   - `/installs`   — anonymized MCP installs
   - `/feedback`   — feedback inbox
   - `/analytics`  — query volume + cost over time
   - `/content`    — blog post drafts
   - `/settings`   — env + ingest URLs

   Every page should render with an empty state on a fresh deploy. Run
   `pnpm db:seed` locally first if you want sample data.

If login fails, the most common cause is the `salt:hash` format being wrong — re-run the one-liner and re-`fly secrets set`.

## 6. Pointing the CLI at the admin

The CLI defaults to `https://admin.karst.dev`. Override during local testing:

```bash
export KARST_INGEST_URL=https://admin.karst.dev
# or for staging:
export KARST_INGEST_URL=https://karst-dashboard.fly.dev
```

If `KARST_INGEST_TOKEN` is set on the dashboard, the CLI must send the same value as `Authorization: Bearer <token>`. Bake the public-prod token into the CLI release build, or fetch it from a public config endpoint.

To verify the pipe is live:

```bash
karst --version   # triggers /api/ingest/install on first run
# then in the dashboard: /installs should show one new row
```

## 7. Year-1 cost breakdown

| Item | Cost |
|---|---|
| Domain (`karst.dev`, 1 year) | $12 |
| Fly.io dashboard (shared-cpu-1x, 1GB volume, ~$5/mo) | $60 |
| Vercel landing (Hobby tier) | $0 |
| PyPI (CLI distribution) | $0 |
| GitHub (private repo on free tier) | $0 |
| **Total** | **~$72/year** |

Add ~$1/mo if you turn on S3 backups (negligible storage, a few cents in egress).
