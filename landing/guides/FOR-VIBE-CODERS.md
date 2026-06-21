# karst for vibe coders — no CLI, just chat

If you live in **Cursor** or **Claude Desktop** and you'd rather not run
terminal commands, this is for you. You set karst up **once**, and from then on
you just chat normally — your AI quietly uses karst to find the right code, cite
it, and check what a change would break. You never type a karst command again.

---

## The idea in one picture

```
        You (chatting normally)
                 │
                 ▼
        Cursor / Claude Desktop
                 │   "find the code about X" / "what breaks if I change Y"
                 ▼
        karst  ──►  your indexed repo  (local, on your machine)
                 │
                 ▼
        cited code snippets + blast-radius  ──►  back to your AI  ──►  a grounded answer
```

Your AI was already guessing which files to read. karst makes it *ask a tool*
that actually knows — and every snippet comes back stamped with `file:line` so
the answer is verifiable, not vibes.

## Setup (once, ~2 minutes)

### Step 1 — install karst

```bash
uv tool install karst     # or: pipx install karst   /   pip install -U karst
```

(You need this even as a "no-CLI" user — it's the engine your AI will call. It's
a one-time install.)

### Step 2 — add karst to your AI client

**Claude Desktop** — edit `claude_desktop_config.json`
(Settings → Developer → Edit Config) and add:

```jsonc
{
  "mcpServers": {
    "karst": { "command": "uvx", "args": ["--from", "karst", "karst-mcp"] }
  }
}
```

The `uvx` launcher needs nothing pre-installed — it fetches and runs karst on
demand (requires [uv](https://docs.astral.sh/uv/)). Already installed karst?
`{ "command": "karst-mcp" }` works too. Neither on PATH?
`{ "command": "python", "args": ["-m", "karst.mcp_server"] }`.

**Cursor** — create/edit `~/.cursor/mcp.json` (or `.cursor/mcp.json` in your
project) with the same block.

> **On Windows**, if the app reports `spawn ENOENT`, it's a launcher-path quirk
> (GUI apps don't always see your PATH). Easiest fix: point `command` at the
> full path of `karst-mcp.exe`, or use `karst-mcp` after a normal install. See
> [CONNECT.md](CONNECT.md) for per-tool details.

Restart the app. You should now see karst's tools available.

### Step 3 — index your project (you can ask the AI to do it!)

Just tell your AI, in chat:

> "Index this repo with karst" — point it at your project folder.

It'll call karst's `index_repository` tool for you, which builds the search
index and the call/import graph — enough for `search_code` and `find_impact` to
work.

> **One thing the chat path can't do: create *packs*.** The `index_repository`
> tool builds the index + graph but not context packs. If you want pack-scoped
> search (see [COOKBOOK.md](COOKBOOK.md)), run this once in a terminal:
> `cd your-project && karst quickstart` (it does everything `index_repository`
> does **plus** suggests packs). The chat tools can then *list and use* packs,
> but can't create them.

That's the whole setup. **Now just chat.**

## What you can now ask — in plain English

You don't call tools; you describe what you want and the AI picks the right
karst tool:

| You say… | karst tool the AI uses | What you get |
|----------|------------------------|--------------|
| "How does login work in this app?" | `search_code` | The exact relevant functions, cited to `file:line` |
| "Where do we charge the customer?" | `search_code` | Ranked snippets — not a guess, not the whole file |
| "What breaks if I change `chargeUser`?" | `find_impact` | A blast-radius list: everything that depends on it |
| "Is this repo indexed yet?" | `index_status` | Whether karst is ready, and how big the index is |
| "Re-index, I changed a bunch of files" | `index_repository` | A fresh index (incremental — only changed files) |

The five tools your AI gets: **`search_code`**, **`find_impact`**,
**`list_packs`**, **`index_status`**, **`index_repository`**.

## Why this is better than just asking the AI directly

Without karst, the AI either reads too little (and hallucinates) or dumps whole
files into its own context (slow, expensive, and it loses the thread). With
karst:

- **It reads the *right* code, not all of it.** Sharper answers, fewer tokens.
- **Every claim is cited.** You can click `file:line` and check it.
- **It can warn you before a risky change** — the blast-radius check is
  something a plain chat model simply can't do.
- **Your code stays on your machine.** karst runs locally; nothing is uploaded.

## FAQ

**Do I need an API key?** No. Your IDE/desktop app already has the model —
karst only supplies the context. (The CLI's `karst ask` can call an LLM
directly if you want, but the MCP path doesn't need a key.)

**Does my code get uploaded anywhere?** No. The index lives in
`~/.karst/indexes/` on your computer.

**My repo changed a lot — do I re-index?** Just say "re-index this repo." It's
incremental, so it only re-processes the files you touched (seconds, not
minutes).

**Can I use this from ChatGPT or claude.ai in the browser?** Not directly yet.
Their built-in custom-connector UIs expect OAuth, which karst's HTTP server
doesn't implement — so today the browser path works only via a bridge like
`mcp-remote` against karst's bearer-token HTTP server (details in
[MCP.md](MCP.md)). The **desktop apps above are the easy, supported path** — use
those.

---

Next: [COOKBOOK.md](COOKBOOK.md) for concrete scenarios, or [WHY.md](WHY.md) for
the bigger picture.
