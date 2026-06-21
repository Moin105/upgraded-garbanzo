# karst MCP server

Connect karst to any MCP host — **Claude Desktop, Cursor, Continue, Cline, or a
custom agent** — and your AI tool gets scoped, cited code context instead of
reading your repo blind.

The server returns *context*, not answers: it never calls an LLM, so **you don't
need to give karst an API key**. Your host (Claude Desktop / Cursor) already has
the model; karst just feeds it the right slice of the repo.

---

## 1. Install

```bash
uv tool install karst      # recommended — fast, handles PATH for you
# or
pipx install karst         # isolated, also handles PATH
# or
pip install karst          # fallback (see PATH note below)
```

(From a clone of this repo for development: `pip install -e .`)

This installs two console commands:

- `karst` — the CLI (`index`, `ask`, `impact`, `packs`, `review`)
- `karst-mcp` — the MCP server (this doc)

> **PATH note.** `uv tool install` / `pipx` put these commands on your PATH for
> you. Plain `pip install --user` (notably Microsoft Store Python) drops the
> scripts in a `Scripts\` folder that often isn't on PATH — then `karst` /
> `karst-mcp` won't be found. Two PATH-free options that always work:
> `python -m karst …` (CLI) and `python -m karst.mcp_server` (server). The MCP
> configs below include launchers that need no PATH at all.

## 2. Index a repo (one time)

The MCP tools read a prebuilt index. Build it once per repo:

```bash
karst index /path/to/your-repo
# optional but recommended — enables find_impact and pack scoping:
karst graph-index /path/to/your-repo
karst packs --storage ~/.karst/indexes/your-repo \
  suggest /path/to/your-repo --apply --retag
```

> The `--storage` folder is the **basename of the repo path**: indexing
> `/path/to/myapp` stores it at `~/.karst/indexes/myapp` (the two must match).
> Simpler: run `karst quickstart /path/to/your-repo`, which does all three steps
> and prints the exact storage path.

(You can also do this from inside the host by calling the `index_repository`
tool — handy for small repos. For large repos prefer the CLI so you don't block
the host on a long call.)

## 3. Wire it into your IDE

### Claude Desktop

Edit `claude_desktop_config.json`:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

Use whichever launcher you have (all three are equivalent):

```json
{
  "mcpServers": {
    "karst": { "command": "uvx", "args": ["--from", "karst", "karst-mcp"] }
  }
}
```

- **`uvx` (recommended)** — needs nothing pre-installed; fetches and runs karst
  on demand. Requires [`uv`](https://docs.astral.sh/uv/).
- **Installed already?** `{ "command": "karst-mcp" }` (works if it's on PATH —
  it is after `uv tool install` / `pipx`).
- **No PATH at all?** `{ "command": "python", "args": ["-m", "karst.mcp_server"] }`
  — the most universal option (just needs `python` on PATH).

Restart Claude Desktop. You'll see a 🔌 / tools icon — `karst` and its 5 tools
should be listed.

### Cursor

Create `.cursor/mcp.json` in your project root (or `~/.cursor/mcp.json` for all
projects):

```json
{
  "mcpServers": {
    "karst": { "command": "uvx", "args": ["--from", "karst", "karst-mcp"] }
  }
}
```

(Same three launcher options as above — swap in `karst-mcp` or
`python -m karst.mcp_server` if you prefer.) Reload Cursor. Settings → MCP should
show `karst` as connected.

### Continue / Cline / other MCP hosts

Any host that speaks MCP over stdio works. Point it at the command `karst-mcp`
(or `python -m karst.mcp_server`). No args, no env vars required.

## 4. Use it

Once connected, just ask your IDE's model normally. It will call karst's tools
when useful. Examples that trigger them:

- *"Using karst, how does checkout charge the user in /path/to/repo?"*
  → `search_code` returns the relevant functions with `file:line` citations.
- *"What breaks if I change the `login` function in this repo?"*
  → `find_impact` returns the blast radius from the call graph.
- *"What context packs exist for this repo?"* → `list_packs`.

You can always pass the repo's absolute path; the tools resolve the index from
`~/.karst/indexes/<repo-name>`.

---

## Tools

| Tool | What it does | Needs |
|---|---|---|
| `search_code(query, repo_path, packs?, limit?)` | Ranked code chunks for a question, each cited to `file:line`. Scope with `packs` to cut tokens. | vector index |
| `find_impact(symbol, repo_path, max_depth?)` | Blast radius of changing a symbol — what depends on it, ranked. | graph (`graph-index`) |
| `list_packs(repo_path)` | Named context packs available for the repo. | packs (suggest+apply) |
| `index_status(repo_path)` | Whether a repo is indexed and how big the index is. | — |
| `index_repository(repo_path, reset?)` | Build/refresh the vector index **and** the graph. Slow first run; instant after. | — |

## Why this design

Most "code context" integrations dump files into the model and hope. karst
instead:

1. **Scopes** — pack-filtered retrieval reads ~200 chunks, not 5,000.
2. **Cites** — every chunk carries an exact `file:line`, so the model (and you)
   can verify, not trust.
3. **Predicts** — `find_impact` answers "what else breaks?" from a real call
   graph, which embeddings alone can't do.

Net effect on a real 246-file repo (Byfoods): ~60% fewer input tokens per
question, and answers grounded in citations.

## Remote / hosted mode (claude.ai, ChatGPT, shared servers)

By default karst-mcp speaks **stdio** — for local hosts that launch it as a
subprocess. To connect a **browser/cloud host** (claude.ai, ChatGPT) or share
one server with a team, run it over **Streamable HTTP** instead:

```bash
# on a machine that has the repos indexed (it reads ~/.karst/indexes locally)
export KARST_MCP_TOKEN="a-long-random-secret"
karst-mcp --http                  # host 0.0.0.0, port $PORT or 8080
```

- Endpoint: `https://your-host/mcp`  ·  health check: `GET /healthz` (open).
- **Auth:** every request needs `Authorization: Bearer $KARST_MCP_TOKEN`. If you
  don't set the token it runs unauthenticated and warns loudly — don't do that
  off localhost.
- **Put it behind HTTPS** (your platform's TLS, or a reverse proxy). `$PORT` is
  honored, so it deploys as-is to Fly / Render / Railway.

**Important:** the server reads indexes from its **own disk**
(`~/.karst/indexes/<repo>`). A hosted server can't see your laptop's files — so
index the repos **on the server** (run `karst index` / `karst quickstart` there,
or mount a volume that has them).

**Connecting clients:**
- Clients that support a remote MCP URL + custom headers → point them at
  `https://your-host/mcp` with the `Authorization: Bearer …` header.
- stdio-only clients (e.g. Claude Desktop) can bridge to it:
  `npx mcp-remote https://your-host/mcp --header "Authorization: Bearer $TOKEN"`.
- claude.ai / ChatGPT's built-in connector UIs currently expect **OAuth**; the
  bearer-token server works today for header-capable clients and the
  `mcp-remote` bridge — native OAuth is the next step on the roadmap.

## Troubleshooting

- **"This repo isn't indexed yet."** Run `karst index <path>` (and
  `graph-index` for impact), or call the `index_repository` tool.
- **`karst-mcp` not found.** Use `python -m karst.mcp_server` in the
  config, or add the pip Scripts dir to PATH.
- **Host shows no tools.** Fully quit and reopen the host after editing its
  config — most hosts only read MCP config at startup.
- **First `search_code` is slow.** The embedding model downloads + loads on
  first use (~once), then it's fast.
