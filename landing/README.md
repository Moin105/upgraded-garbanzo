# karst — landing page

The public marketing site for [karst](https://github.com/Moin105/karst), the MCP-native
code-context tool. Self-contained, no build step, deploys as a static site on Vercel's free tier.

> **Site origin.** The absolute URLs in each page's `<head>` (canonical, `og:url`,
> `og:image`, `twitter:image`) currently point at the live Vercel origin, because
> `karst.dev` is **not owned**. Social crawlers won't resolve a relative
> `og:image`, and a canonical aimed at a domain we don't control tells search
> engines the real copy lives elsewhere — so these must always match wherever the
> site is actually served. If the domain is acquired later, update them together
> along with `robots.txt` and `sitemap.xml`.

## What it is

A single-page dark-theme site (`index.html`) that:

- Explains karst in one screen: tagline, the three headline numbers (343s -> 2.3s incremental
  reindex, $0.019 average cost per Sonnet 4.6 query, 60% fewer tokens), the three-step Index /
  Pack / Serve flow, and a copy-pasteable install snippet.
- Collects waitlist signups via a single `fetch` POST to the admin dashboard.
- Has no framework, no build, no `node_modules`. Tailwind is loaded from the CDN, fonts from
  Google Fonts.

Files in this folder:

| File              | Purpose                                                            |
|-------------------|--------------------------------------------------------------------|
| `index.html`      | The whole page. Inline SVG logo, inline JS for the waitlist form.  |
| `og-image.png`    | 1200x630 OG/Twitter card image (the one referenced in meta tags).  |
| `og-image.svg`    | Editable source for `og-image.png`. Re-export to PNG after edits.  |
| `robots.txt`      | Allow all crawlers.                                                |
| `vercel.json`     | Clean URLs + security headers (X-Frame-Options, Referrer-Policy).  |
| `package.json`    | Marks the folder as a project so Vercel auto-detects it. No deps.  |
| `.gitignore`      | Standard ignores (`.vercel/`, `.env`, `node_modules/`).            |

## Deploy on Vercel (free tier)

Vercel's free Hobby tier does **not** deploy from private repos. That's why this folder is meant
to live in its own **public** GitHub repo, separate from the main karst codebase.

1. Create a new public repo on GitHub, e.g. `Moin105/karst-landing`.
2. Copy the contents of this folder to the **root** of that repo (not a subdirectory) and push:
   ```bash
   cd landing/
   git init
   git remote add origin git@github.com:Moin105/karst-landing.git
   git add .
   git commit -m "Initial landing page"
   git branch -M main
   git push -u origin main
   ```
3. On [vercel.com/new](https://vercel.com/new), click **Import Project** and pick the new repo.
4. Framework preset: **Other**. Build command: leave blank (or `npm run build` — it just echoes).
   Output directory: leave blank (Vercel serves the repo root).
5. Deploy. The first deploy takes ~10 seconds.

## Custom domain

In Vercel: **Project Settings → Domains → Add**. Add both `karst.dev` and `www.karst.dev`.

At your DNS registrar, point:

| Type    | Name        | Value                       |
|---------|-------------|-----------------------------|
| `A`     | `@` (apex)  | `76.76.21.21`               |
| `CNAME` | `www`       | `cname.vercel-dns.com.`     |

(Vercel will show the exact values in the dashboard — use those, the table above is the typical
case.) DNS propagation usually finishes within a few minutes; certificates are issued
automatically.

## Pointing the waitlist form at your admin dashboard

The form in `index.html` POSTs `{ email, source: "landing" }` to a single endpoint, resolved in
this order:

1. `process.env.NEXT_PUBLIC_API_URL` (in case this is ever embedded in a Next.js build).
2. `window.KARST_API_URL` set by an inline `<script>`.
3. The default: `https://admin.karst.dev/api/waitlist`.

To override without editing `index.html`, add a script tag in `<head>` *before* the main inline
script:

```html
<script>window.KARST_API_URL = "https://your-admin.example.com/api/waitlist";</script>
```

Or just edit the `API_URL` constant at the bottom of `index.html` and commit. The endpoint must
accept JSON POSTs and respond with any 2xx status on success.

## How this folder was split out of the main private repo

This folder originally lived as `landing/` inside the private `Moin105/upgraded-garbanzo` monorepo. To
publish it as its own public repo while keeping the history:

### Option A — `git subtree split` (recommended, lossless history)

From the root of the private monorepo:

```bash
git subtree split --prefix=landing -b landing-only
git push git@github.com:Moin105/karst-landing.git landing-only:main
```

`landing-only` is a temporary branch containing only the commits that touched `landing/`,
rewritten so that `landing/` becomes the repo root. The public repo will have **only** those
commits — nothing from the private monorepo leaks.

### Option B — `git filter-branch` (older, slower, same idea)

```bash
git clone --no-local karst karst-landing
cd karst-landing
git filter-branch --prune-empty --subdirectory-filter landing main
git remote remove origin
git remote add origin git@github.com:Moin105/karst-landing.git
git push -u origin main
```

### Option C — fresh start

If you don't care about history (the landing page is small and self-contained), just `cp -r
landing/ ../karst-landing/`, `git init`, and push. Simplest and avoids any chance of leaking
private monorepo metadata.

After splitting, keep the two repos in sync by either:

- Editing the landing page directly in the public repo (preferred — it's small), or
- Editing in the monorepo and re-running `git subtree split` + force-pushing the public branch.
