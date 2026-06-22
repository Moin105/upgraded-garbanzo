# karst — architecture

karst is three pieces that talk over HTTPS. None of them share a database directly; the dashboard is the system of record.

## System diagram

```
                              karst.dev
                              (public)
   +-----------+   GET    +------------------+
   |  Browser  |--------->|  Vercel landing  |
   +-----------+          +--------+---------+
                                   |
                                   | POST /api/waitlist  (email, source)
                                   v
                       +-------------------------+
                       |   Fly.io dashboard      |
                       |   admin.karst.dev       |
                       |                         |
   +-----------+ POST  |   /api/waitlist         |     +--------------+
   | User CLI  |------>|   /api/ingest/install   |---->| SQLite       |
   |  (karst)  |       |   /api/ingest/query     |     | karst.db     |
   +-----------+       |   /api/ingest/error     |     | (Fly volume) |
                       |                         |     +--------------+
                       |   /dashboard (UI)       |
                       +-----------+-------------+
                                   ^
                                   | HTTPS + iron-session cookie
                                   |
                              +----+----+
                              |  Admin  |
                              +---------+
```

## The three systems

### 1. Landing (`karst.dev`)

- **Where**: Vercel, free Hobby tier, public repo.
- **What**: Static-ish marketing site. Hero, features, install snippet, waitlist form.
- **State**: None. The waitlist form POSTs cross-origin to the dashboard.
- **Why split out**: Free hosting on Vercel, public source code is fine, decouples marketing churn from the private app.

### 2. Dashboard (`admin.karst.dev`)

- **Where**: Fly.io, single Machine in `iad`, 1GB persistent volume.
- **What**: Next.js 15 admin app. Waitlist viewer, design-partner CRM, install/query analytics, feedback inbox, content (blog), settings.
- **State**: SQLite at `/app/data/karst.db`. System of record for everything user-visible to the team.
- **Auth**: iron-session cookie, single admin from env. No public registration.
- **Ingest**: `POST /api/waitlist`, `POST /api/ingest/install`, `POST /api/ingest/query`, `POST /api/ingest/error`. CORS allowlists `karst.dev` for the waitlist endpoint; ingest endpoints accept any origin but optionally require `Authorization: Bearer $KARST_INGEST_TOKEN`.

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

Emails enter the system **only** on explicit user action: waitlist signup on the landing page, or feedback submission from inside the CLI / docs site. Those tables (`waitlist`, `feedback`) are the only PII in `karst.db`.

Today the CLI's only network activity is the one-time embedding-model download
and whichever LLM you explicitly opt into — nothing else. If opt-out telemetry
is ever added, `KARST_TELEMETRY=0` will disable it; until then there is nothing
to disable. The engine runs fully offline aside from that one model download
(see [SELF-HOSTED.md](SELF-HOSTED.md)).
