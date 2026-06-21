# Changelog

All notable changes to **karst**. This project uses semantic-ish versioning.

## 0.2.1

- `karst ask` now defaults to the **current folder's** index, so
  `cd your-project && karst ask "…"` works without `--storage`.
- Docs lead with the `python -m karst …` form (works even when the `karst`
  script isn't on PATH).

## 0.2.0

**Run it (any machine):**

```bash
pip install -U karst          # or: uv tool install karst  /  pipx install karst
cd your-project
karst quickstart             # or: python -m karst quickstart   (works without PATH setup)
karst ask "how does X work?" --no-llm        # cited code, no API key
karst ask -i                 # interactive Q&A
karst examples               # full cheatsheet
```

**MCP (use it from Claude Desktop / Cursor — no API key):**

```json
{ "mcpServers": { "karst": { "command": "karst-mcp" } } }
```
No PATH? Use `{ "command": "python", "args": ["-m", "karst.mcp_server"] }`.
Remote/hosted host? `karst-mcp --http` (Streamable HTTP) — see `docs/MCP.md`.

**Highlights**

- **Remote MCP server** — `karst-mcp --http` serves Streamable HTTP with
  bearer-token auth (`KARST_MCP_TOKEN`); stdio stays the default for local hosts.
- **`karst quickstart`** — one command: index + call/import graph + suggested
  packs, then prints the next commands to try.
- **Interactive `ask`** (`karst ask -i`) and a **`karst examples`** cheatsheet.
- **`python -m karst`** entry point — works when the `karst` script isn't on PATH.
- Repo path defaults to the **current folder** for index / quickstart /
  graph-index / analyze.

**Fix (important)**

- **Embedder OOM** — cap FastEmbed's batch size. Its 256 default built a single
  ~3 GB attention buffer and crashed `karst index` ("Failed to allocate memory
  … 3221225472") on ordinary machines. Now guarded by a CI test.

## 0.1.0

- Initial release: AST-aware chunking (Python/JS/TS/Go/Rust/Java), local Qdrant
  vector index, NetworkX call/import graph + impact analysis, context packs,
  token + cost meter, incremental indexing + embedding cache, diff review, and
  the stdio MCP server.
