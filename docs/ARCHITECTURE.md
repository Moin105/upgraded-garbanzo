# karst — architecture

karst is three pieces that talk over HTTPS. None of them share a database directly; the dashboard is the system of record.

## System diagram

```
                              karst.dev
                              (public, Vercel)
   +-----------+   GET    +------------------+
   |  Browser  |--------->|  Vercel landing  |
   +-----------+          +--------+---------+
                                   |
                                   | POST /api/waitlist  (email, source)
                                   | POST /api/ingest/feedback
                                   v
                       +---------------------------+
                       |   Vercel dashboard        |
                       |   (Next.js 15, serverless)|
                       |   *.vercel.app            |
                       |   [planned: admin.karst.  |
                       |    dev — not yet wired]   |
   +-----------+ POST  |                           |     +---------------+
   | User CLI  |------>|   /api/waitlist           |     | Neon Postgres |
   |  (karst)  |       |   /api/ingest/install     | SQL | (managed,     |
   +-----------+       |   /api/ingest/query       |---->|  serverless)  |
                       |   /api/ingest/feedback    |     | DATABASE_URL  |
                       |                           |     +---------------+
                       |   /dashboard (UI)         |
                       +-----------+---------------+
                                   ^
                                   | HTTPS + iron-session cookie
                                   |
                              +----+----+
                              |  Admin  |
                              +---------+
```

## The three systems

### 1. Landing (`karst.dev`)

- **Where**: Vercel, free Hobby tier. Served from the private monorepo (live at `karst.dev` / `www.karst.dev`).
- **What**: Static-ish marketing site. Hero, features, install snippet, waitlist + feedback forms.
- **State**: None. The forms POST cross-origin to the dashboard.
- **Why Vercel**: Free static hosting, instant deploys on push, decouples marketing churn from the app's data layer.

### 2. Dashboard (Vercel; planned `admin.karst.dev`)

- **Where**: Vercel serverless (Next.js 15) — no always-on machine. Live at `https://upgraded-garbanzo-x2e8.vercel.app`; `admin.karst.dev` is the intended custom domain but is **not yet wired up**.
- **What**: Next.js 15 admin app. Waitlist viewer, design-partner CRM, install/query analytics, feedback inbox, content (blog), settings.
- **State**: **Neon Postgres** — managed, serverless Postgres reached over `DATABASE_URL` via a `pg` connection pool (`lib/db.ts`). System of record for everything user-visible to the team. (SQLite on a Fly volume is gone; serverless functions can't share a SQLite file.)
- **Auth**: iron-session cookie, single admin from env. No public registration.
- **Ingest**: `POST /api/waitlist`, `POST /api/ingest/install`, `POST /api/ingest/query`, `POST /api/ingest/feedback`. CORS allowlists the landing origins (`KARST_ALLOWED_ORIGINS`) for the public forms; ingest endpoints optionally require `Authorization: Bearer $KARST_INGEST_TOKEN`.

### 3. Engine / CLI (`pip install karst`)

- **Where**: Installed locally on each user's machine. Distributed via PyPI.
- **What**: The actual product. Runs the user's queries, calls the model, returns results.
- **Phone-home**: Anonymized events to `/api/ingest/*` on each command. Disabled by `KARST_TELEMETRY=0`.
- **No back-channel**: The dashboard never initiates connections to user machines. All traffic is CLI -> dashboard.

## Privacy

> **Status: not implemented in the shipped CLI.** As of the current release the
> karst package sends **no telemetry at all** — it imports no HTTP client and
> makes zero `/api/ingest/*` calls (the dashboard merely *exposes* those
> endpoints for if/when telemetry is added). The table below is the
> privacy-by-design spec telemetry **would** follow if it's ever shipped:
> anonymous, opt-out, and never touching your code.

What telemetry would collect, if it were enabled:

| Endpoint | Data | Notes |
|---|---|---|
| `/api/ingest/install` | anonymous install id (UUID stored in `~/.karst/id`), OS, arch, CLI version | One row per install, ever. |
| `/api/ingest/query` | install id, command name (e.g. `analyze`), latency ms, success bool, model id, token counts | **No prompt text. No source code. No file paths.** Only the command shape. |
| `/api/ingest/error` | install id, error class, stack trace with file paths scrubbed | **Opt-in only**, off by default. |

What we **do not** collect:

- Source code, file contents, or file paths in queries (paths are scrubbed before send).
- Prompt content or model outputs.
- IP addresses (we strip them at the dashboard before insert).
- Anything tying an install id to a person, unless the user voluntarily signs up for the waitlist or sends feedback with an email.

Emails enter the system **only** on explicit user action: waitlist signup on the landing page, or feedback submission from inside the CLI / docs site. Those tables (`signups`, `feedback`) are the only PII in the dashboard's Neon Postgres database.

Today the CLI's only network activity is the one-time embedding-model download
and whichever LLM you explicitly opt into — nothing else. If opt-out telemetry
is ever added, `KARST_TELEMETRY=0` will disable it; until then there is nothing
to disable. The engine runs fully offline aside from that one model download
(see [SELF-HOSTED.md](SELF-HOSTED.md)).
