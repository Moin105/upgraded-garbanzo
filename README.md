# karst

**Code context for AI dev tools.** karst sits between your repo and any AI
tool — Cursor, Claude Desktop, a custom agent — and feeds it the *right* slice
of the codebase: graph-grounded, pack-scoped, and cited to `file:line`. The
result is ~60% fewer input tokens per question, answers you can verify, and a
blast-radius check before you change anything.

It runs **locally**, returns **context (not answers)** over **MCP**, and never
calls an LLM itself — so you don't give karst an API key. Your IDE already has
the model; karst just makes what it reads sharp and cheap.

```bash
uv tool install karst      # recommended — fast, and puts `karst` on PATH for you
# or
pipx install karst         # isolated install, also handles PATH
# or
pip install karst          # if `karst` isn't found after, use `python -m karst …`
```

> [`uv`](https://docs.astral.sh/uv/) and `pipx` are the cleanest because they
> put the `karst` command on your PATH automatically. With plain `pip --user`
> (notably Microsoft Store Python) the command may not be on PATH — in that case
> `python -m karst …` always works, no PATH setup required.

## Why

Most "chat with your codebase" tools dump tens of thousands of vaguely-related
tokens into the model on every question. You can't see what was loaded, you
can't scope it, and the bill arrives at the end of the month. karst inverts
that:

- **Scopes** — pack-filtered retrieval reads ~200 chunks, not 5,000.
- **Cites** — every chunk carries an exact `file:line`. Verify, don't trust.
- **Predicts** — a real call/import graph answers "what else breaks if I change
  this?" — which embeddings alone can't.

Measured on a real 246-file NestJS + Next.js repo: 906 chunks indexed, re-index
**343s → 2.3s** incremental, **~$0.019** per question on Sonnet 4.6 (shown
*before* the call), **60%** fewer tokens with packs attached.

## Quickstart (CLI)

```bash
# 1. index a repo (incremental + cached after the first run)
karst index ./my-repo

# 2. build the call/import graph (enables impact analysis)
karst graph-index ./my-repo

# 3. auto-suggest context packs and tag the index
karst packs --storage ~/.karst/indexes/my-repo \
  suggest ./my-repo --apply --retag

# 4. ask — retrieval is pack-scoped and the token cost is printed
karst ask "How does checkout charge the user?" \
  --storage ~/.karst/indexes/my-repo

# what breaks if I change a function?
karst impact --target checkout \
  --graph-path ~/.karst/indexes/my-repo/graph.pkl

# review a diff with severity-tagged, cited findings
karst review --staged --storage ~/.karst/indexes/my-repo
```

`karst ask` needs an LLM key (`ANTHROPIC_API_KEY` or `OPENAI_API_KEY`), or pass
`--no-llm` to get the raw cited chunks. The **MCP server below needs no key** —
your IDE supplies the model.

## Use it from your IDE (MCP)

karst ships an MCP server (`karst-mcp`) exposing five tools — `search_code`,
`find_impact`, `list_packs`, `index_status`, `index_repository` — over stdio.

**Claude Desktop** (`claude_desktop_config.json`) or **Cursor**
(`.cursor/mcp.json`) — pick whichever launcher you have:

```json
{
  "mcpServers": {
    "karst": { "command": "uvx", "args": ["--from", "karst", "karst-mcp"] }
  }
}
```

`uvx` needs nothing pre-installed — it fetches and runs karst on demand. Already
installed it? `{ "command": "karst-mcp" }` works too. No PATH at all? Use
`{ "command": "python", "args": ["-m", "karst.mcp_server"] }`.

Restart the host, then ask normally — it calls karst's tools when useful and
gets back scoped, cited context. Full setup is in [docs/MCP.md](docs/MCP.md).

## How it works

1. **Index** — tree-sitter splits every function, class and method into an
   AST-aware chunk (Python, JS, TS, Go, Rust, Java); chunks are embedded into a
   local Qdrant store. Incremental: a SHA manifest + embedding cache skip
   unchanged files.
2. **Graph** — a NetworkX knowledge graph of `CALLS` / `IMPORTS` / `CONTAINS`
   edges powers impact analysis ("what depends on this?").
3. **Pack** — related files become named, attachable context packs (`auth`,
   `billing`). A query loads only its pack.
4. **Serve** — the MCP server returns ranked, `file:line`-cited chunks; your
   host's model reasons over them.

Everything is local and offline-capable (FastEmbed/ONNX embeddings, Qdrant
local mode, sqlite caches — no Docker, no daemon).

## Status

Live: AST chunking (6 languages), call/import graph + impact analysis,
pack-scoped retrieval, token + cost meter, incremental indexing + embedding
cache, diff code review, and the MCP server. Coming next: hosted indexing,
team-shared pack libraries, a GitHub PR review bot.

## License

Apache-2.0. See [LICENSE](LICENSE).
