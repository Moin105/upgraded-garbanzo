# karst — deployment runbook

End-to-end steps to ship karst to production. Read top-to-bottom the first time; skim later.

## 1. Domain & DNS

Register **`karst.dev`** (Porkbun, Cloudflare Registrar, Namecheap — any TLD-supporting registrar).

DNS records:

| Type | Host | Value | Purpose |
|---|---|---|---|
| `A` | `@` (apex) | Vercel anycast IP `76.76.21.21` | Landing site at `karst.dev`. |
| `CNAME` | `www` | `cname.vercel-dns.com` | `www.karst.dev` -> landing. |
| `CNAME` | `admin` | `cname.vercel-dns.com` | `admin.karst.dev` -> Vercel dashboard. **Not yet configured** — the dashboard currently runs at its `*.vercel.app` URL (see §4.3). |

TTL 300 while setting up; bump to 3600 once stable.

## 2. Splitting the landing page out of the main repo

> **Status: not done — optional.** Today the landing is deployed to Vercel
> straight from the private monorepo (no public repo, no split), and that works
> fine. This section is the plan for *if* you later want the landing's source to
> be public. Skip it otherwise — §3 and §4 below describe the actual setup.

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

1. Vercel dashboard -> **Add New -> Project** -> import the monorepo and set **Root Directory** to `landing` (or import the standalone `karst-landing` repo if you did the optional split in §2).
2. Framework preset: **Other** — the landing is static HTML (`index.html` + `vercel.json`), no build step.
3. **Build command**: leave default/empty. No env vars needed; it's static.
4. Deploy.
5. **Settings -> Domains -> Add** `karst.dev` and `www.karst.dev`. Vercel will show the A/CNAME records it expects — they match the table in section 1.
6. Wait for the cert (auto, ~1 min).

The landing's waitlist and feedback forms post **cross-origin** to the dashboard on Vercel — currently `https://upgraded-garbanzo-x2e8.vercel.app/api/waitlist` and `/api/ingest/feedback` (`https://admin.karst.dev/...` once that custom domain is attached, see §4.3). The dashboard must CORS-allowlist the landing origin via `KARST_ALLOWED_ORIGINS` (include `https://karst.dev` and `https://www.karst.dev`).

## 4. Deploying the dashboard to Vercel (Neon Postgres)

The dashboard is a Next.js 15 app that runs on **Vercel's serverless runtime** with a **Neon Postgres** database. (It previously ran on Fly.io with SQLite on a persistent volume; that's gone — serverless functions can't share a SQLite file across invocations, so the store is now managed Postgres.)

### 4.1 Provision the database (Neon)

1. Create a project at <https://neon.tech> (free tier is plenty to start).
2. Copy the **pooled** connection string — it looks like:
   `postgres://<user>:<pass>@<host>-pooler.<region>.aws.neon.tech/<db>?sslmode=require`
3. Apply the schema once, before the app serves traffic:

   ```bash
   cd dashboard
   DATABASE_URL='postgres://...neon.tech/...?sslmode=require' npm run db:migrate
   # -> "Migration complete — schema is up to date."
   ```

   `lib/db.ts` also creates the tables lazily on first request, but running the
   one-shot migration up front avoids concurrent-DDL races across cold-starting
   serverless instances.

### 4.2 Deploy to Vercel

1. Vercel dashboard -> **Add New -> Project** -> import the (private) monorepo.
2. **Root Directory**: `dashboard`. The framework preset auto-detects **Next.js** —
   leave the build command and output directory at their defaults.
3. **Environment Variables** (Project Settings -> Environment Variables): set the
   full list from `dashboard/.env.example`. At minimum:

   | Var | Notes |
   |---|---|
   | `DATABASE_URL` | Neon pooled string from §4.1. SSL is enabled automatically for non-local hosts. |
   | `KARST_SESSION_SECRET` | `openssl rand -hex 32`. |
   | `KARST_ADMIN_EMAIL` | login email. |
   | `KARST_ADMIN_PASSWORD_HASH` | from `npm run hash-password`. |
   | `KARST_ALLOWED_ORIGINS` | CORS allowlist for the landing forms — must include `https://karst.dev` and `https://www.karst.dev`. |
   | `SMTP_*`, `EMAIL_FROM` | optional; leave blank to disable email (signups still work). |
   | `KARST_INGEST_TOKEN` | optional shared secret required on `/api/ingest/*`. |

   `KARST_PUBLIC_URL` is optional on Vercel — `VERCEL_URL` is injected
   automatically and used to build safe password-reset links.
4. Deploy.

Verify:

```bash
curl -s https://<your-project>.vercel.app/api/health
# -> {"ok":true,"version":"0.1.0","db":"postgres","schema_ready":true}
```

`schema_ready:true` confirms the migration from §4.1 landed. The dashboard is
currently live at `https://upgraded-garbanzo-x2e8.vercel.app`.

### 4.3 (Optional) custom domain `admin.karst.dev`

`admin.karst.dev` is **not wired up yet** — the dashboard is reached at its
`*.vercel.app` URL. To attach the custom domain later: Vercel Project ->
**Settings -> Domains -> Add** `admin.karst.dev`, then create the
`CNAME admin -> cname.vercel-dns.com` record from §1. Vercel issues the
certificate automatically. Once it's live, update the landing form URLs (§3) and
`KARST_ALLOWED_ORIGINS` if needed.

## 5. First-time admin login

1. Visit the dashboard's `/login` — currently `https://upgraded-garbanzo-x2e8.vercel.app/login` (`https://admin.karst.dev/login` once the custom domain is attached).
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

If login fails, the most common cause is the `salt:hash` format being wrong — re-run `npm run hash-password` and update `KARST_ADMIN_PASSWORD_HASH` in the Vercel project's environment variables (then redeploy).

## 6. Pointing the CLI at the admin

The CLI defaults to `https://admin.karst.dev`. Override during local testing:

```bash
export KARST_INGEST_URL=https://admin.karst.dev
# or point at the live Vercel deployment directly:
export KARST_INGEST_URL=https://upgraded-garbanzo-x2e8.vercel.app
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
| Vercel (landing + dashboard, Hobby tier) | $0 |
| Neon Postgres (free tier) | $0 |
| PyPI (CLI distribution) | $0 |
| GitHub (private repo on free tier) | $0 |
| **Total** | **~$12/year** |

Both the landing and the dashboard run on Vercel's free **Hobby** tier, and the
database on Neon's free tier, so hosting is effectively $0 — the only hard cost
is the domain. (Neon handles its own backups, so the old SQLite-to-S3 cron is no
longer needed.) Note that Hobby is non-commercial: a real commercial launch
moves the dashboard to Vercel **Pro** (~$20/mo) and likely Neon's paid tier
(~$19/mo), i.e. roughly **$480/year** once you outgrow the free tiers.
