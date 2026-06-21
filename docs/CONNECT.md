# Connect karst to your AI tool

karst talks to AI tools over **MCP** (Model Context Protocol). This is the
copy-paste guide for every major client — desktop apps, IDEs, the CLI, and the
web apps.

- **Desktop apps & IDEs** run karst **locally** (stdio) — nothing to host, code
  never leaves your machine. This is the easy, recommended path.
- **Web apps** (claude.ai, ChatGPT) can't launch a local process, so they need
  karst running as a **remote HTTP** endpoint. More work, and auth is currently
  limited — see [Web apps](#web-apps-remote).

> First time? You don't have to read all of this — find your tool in the table,
> jump to its section, paste one block.

## The one snippet (works in most clients)

Almost every client uses the same idea: a server named `karst` with a launch
command. Pick whichever launcher fits your setup:

```jsonc
// most robust — needs no prior install of karst (requires `uv`)
{ "command": "uvx", "args": ["--from", "karst", "karst-mcp"] }

// if you've installed karst and `karst-mcp` is on PATH
{ "command": "karst-mcp" }

// if `karst-mcp` isn't on PATH but the package is importable
{ "command": "python", "args": ["-m", "karst.mcp_server"] }
```

What differs between clients is only **(a)** the config file location and
**(b)** the top-level key wrapping it — `mcpServers`, `servers`, or
`context_servers`. The table tells you which.

## Quick reference

| Tool | Type | Config location | Top-level key |
|------|------|-----------------|---------------|
| [Claude Desktop](#claude-desktop) | desktop | macOS `~/Library/Application Support/Claude/claude_desktop_config.json` · Win `%APPDATA%\Claude\claude_desktop_config.json` | `mcpServers` |
| [Claude Code](#claude-code-cli) | CLI | `claude mcp add …` → `.mcp.json` / `~/.claude.json` | `mcpServers` |
| [Cursor](#cursor) | IDE | `~/.cursor/mcp.json` (global) · `.cursor/mcp.json` (project) | `mcpServers` |
| [Windsurf](#windsurf) | IDE | `~/.codeium/windsurf/mcp_config.json` | `mcpServers` |
| [VS Code (Copilot)](#vs-code-github-copilot) | IDE | `.vscode/mcp.json` | **`servers`** |
| [Cline / Continue](#cline--continue-vs-code-extensions) | IDE ext | extension config | `mcpServers` |
| [Zed](#zed) | IDE | `settings.json` | **`context_servers`** |
| [JetBrains](#jetbrains-intellij-pycharm-) | IDE | Settings UI (AI Assistant) | `mcpServers` |
| [claude.ai (web)](#claudeai-web) | web | UI — paste remote URL | n/a (remote) |
| [ChatGPT (web)](#chatgpt-web) | web | UI — paste remote URL | n/a (remote) |

After connecting, karst exposes **five tools**: `search_code`, `find_impact`,
`list_packs`, `index_status`, `index_repository`. Once connected, just chat — the
AI calls them when useful. (First, make sure your repo is indexed: ask the AI to
"index this repo," or run `karst quickstart` once — see [QUICKSTART.md](QUICKSTART.md).)

---

## Desktop apps & IDEs (local / stdio)

### Claude Desktop

1. Open **Settings → Developer → Edit Config** (creates/opens
   `claude_desktop_config.json`).
2. Add:
   ```json
   {
     "mcpServers": {
       "karst": { "command": "uvx", "args": ["--from", "karst", "karst-mcp"] }
     }
   }
   ```
3. Save, then **fully quit and relaunch** Claude Desktop (closing the window
   isn't enough — Cmd+Q on macOS, or quit from the tray on Windows).
4. Confirm: the tools/MCP icon near the message box should list karst's 5 tools.

> **Windows `spawn ENOENT`?** Claude Desktop spawns the process directly (no
> shell), so a `uvx`/`python` shim may not resolve. Either wrap it —
> `{ "command": "cmd", "args": ["/c", "uvx", "--from", "karst", "karst-mcp"] }` —
> or point `command` at the full exe path (e.g.
> `C:\Users\<you>\AppData\Roaming\Python\Scripts\karst-mcp.exe`).

### Claude Code (CLI)

One command from your project directory:

```bash
claude mcp add --scope project --transport stdio karst -- karst-mcp
#   no install on PATH?  …  -- uvx --from karst karst-mcp
```

- `--scope project` writes `./.mcp.json` (shareable, committed). Use
  `--scope local` (default, private to you) or `--scope user` (all your
  projects) instead if you prefer.
- The `--` is required — it separates Claude's flags from the server command.
- Verify with `claude mcp list` (or `/mcp` inside a session). Project-scoped
  servers prompt for a one-time trust approval on first launch.

### Cursor

1. Create `~/.cursor/mcp.json` (all projects) or `.cursor/mcp.json` (one repo):
   ```json
   {
     "mcpServers": {
       "karst": { "command": "uvx", "args": ["--from", "karst", "karst-mcp"] }
     }
   }
   ```
2. **Settings → Tools & Integrations** (the MCP section), find `karst`, toggle it
   on / click **Refresh**.
3. It should show green with 5 tools. Use it from **Agent** chat (Cursor asks
   before running a tool the first time).

### Windsurf

1. Edit `~/.codeium/windsurf/mcp_config.json` (create the file/folders if
   missing — Windsurf doesn't make it for you):
   ```json
   {
     "mcpServers": {
       "karst": { "command": "uvx", "args": ["--from", "karst", "karst-mcp"] }
     }
   }
   ```
2. In the **Cascade** panel open the MCP/Plugins menu and click **Refresh**
   (or restart Windsurf).
3. `karst` should appear active. Note Cascade's cap of **100 tools** across all
   MCP servers (karst's 5 are well within it).

> Windsurf's *remote* form uses the key `serverUrl` (not `url`) — see
> [Web/remote](#web-apps-remote) if you go that route.

### VS Code (GitHub Copilot)

VS Code's native MCP uses the key **`servers`** (not `mcpServers`) and tools only
work in **Agent** mode. Needs VS Code ~1.102+ and a Copilot subscription.

1. Create `.vscode/mcp.json` in your repo:
   ```jsonc
   {
     "servers": {
       "karst": {
         "type": "stdio",
         "command": "uvx",
         "args": ["--from", "karst", "karst-mcp"]
       }
     }
   }
   ```
   (Or run **MCP: Add Server** from the Command Palette.)
2. Click the **Start** code-lens above the entry.
3. Open Copilot Chat, switch the mode dropdown to **Agent**, and confirm karst's
   tools via the Tools icon.

### Cline / Continue (VS Code extensions)

These are separate extensions with their own config (both use `mcpServers`):

- **Cline:** open the Cline panel → **MCP Servers → Configure**, add:
  ```json
  { "mcpServers": { "karst": { "command": "uvx", "args": ["--from", "karst", "karst-mcp"] } } }
  ```
- **Continue:** create `.continue/mcpServers/karst.yaml`:
  ```yaml
  name: karst MCP
  version: 0.0.1
  schema: v1
  mcpServers:
    - name: karst
      type: stdio
      command: uvx
      args: ["--from", "karst", "karst-mcp"]
  ```
  Then switch Continue to **Agent** mode.

### Zed

Zed uses the key **`context_servers`** in `settings.json`
(macOS/Linux `~/.config/zed/settings.json`, Windows `%APPDATA%\Zed\settings.json`):

```json
{
  "context_servers": {
    "karst": {
      "command": "uvx",
      "args": ["--from", "karst", "karst-mcp"],
      "env": {}
    }
  }
}
```

Save — Zed restarts the server automatically. In the Agent Panel settings, the
dot next to `karst` should turn green.

### JetBrains (IntelliJ, PyCharm, …)

Configured through the UI, not a file. Needs the **AI Assistant** plugin + a
JetBrains AI account (2025.2+).

1. **Settings → Tools → AI Assistant → Model Context Protocol (MCP)**.
   *(Not "Tools → MCP Server" — that's the reverse direction, exposing the IDE
   to other tools.)*
2. Click **+**, then either fill the form (Name `karst`, Command `uvx`,
   Arguments `--from karst karst-mcp`) or switch the dialog to **As JSON** and
   paste:
   ```json
   { "mcpServers": { "karst": { "command": "uvx", "args": ["--from", "karst", "karst-mcp"] } } }
   ```
3. **OK → Apply.** In AI Assistant chat, type `/` to see karst's tools.

> JetBrains' MCP-client support has moved around across 2025.x builds; if the
> menu path differs, search Settings for "Model Context Protocol." Verify
> details against your IDE version.

---

## Web apps (remote)

claude.ai and ChatGPT run in the browser and **cannot launch a local process**,
so karst must run as a **remote HTTPS endpoint**. Honest heads-up: both web UIs'
connector forms currently support only **OAuth or no-auth** — neither has a field
for karst's simple `Authorization: Bearer` token. So today the clean path is the
**desktop/IDE clients above**; the web route needs extra plumbing.

### Run karst as a remote server

```bash
export KARST_MCP_TOKEN="$(openssl rand -hex 32)"   # a long random secret
karst-mcp --http                                   # serves on 0.0.0.0:8080
```

- Endpoint: `http://<host>:8080/mcp` · open health check: `http://<host>:8080/healthz`
- Override with `--host` / `--port` (or env `KARST_MCP_HOST` / `KARST_MCP_PORT`).
- **Without `KARST_MCP_TOKEN` the server is unauthenticated** — it warns you.
  Always set it (or otherwise lock the endpoint down) before exposing it.
- For the browser to reach it, terminate TLS in front (a reverse proxy or a
  tunnel like Cloudflare Tunnel / ngrok) so the public URL is `https://`.

See [MCP.md](MCP.md) for hosting specifics; [the launch discussion in the
README](../README.md) explains why a hosted endpoint serves *one* index, not a
multi-tenant service.

### claude.ai (web)

Available on Free (1 connector) / Pro / Max / Team / Enterprise; in beta.

1. Expose karst publicly over HTTPS (above).
2. **Settings → Customize → Connectors → + → Add custom connector.**
3. Paste `https://<your-host>/mcp`.
4. **Auth caveat:** the form offers only OAuth (Client ID/Secret) or none —
   **no bearer-token field**. So either run karst **authless** behind other
   controls (IP allowlist / mTLS at the proxy), or front it with an **OAuth
   proxy**. A static `KARST_MCP_TOKEN` can't be entered here directly.

### ChatGPT (web)

Requires a paid plan with **Developer mode** (Settings → Apps & Connectors →
Advanced settings); beta, and admin-gated on Business/Enterprise/Edu.

1. Run karst in HTTP mode and expose it over HTTPS (a tunnel, or OpenAI's
   **Secure MCP Tunnel** which can wrap a *local* karst and keep it off the
   public internet).
2. **Settings → Apps & Connectors → Create**; Name `karst`, paste the
   `https://<host>/mcp` URL.
3. **Auth caveat:** like claude.ai, the form has no custom-header field — use
   OAuth, or keep the token-gated server reachable only through the private
   tunnel. Enable the connector per-chat via **+ → More → Developer mode**.

---

## Troubleshooting

- **`karst-mcp` not found / not on PATH** → use the `uvx` launcher (needs `uv`),
  or `{ "command": "python", "args": ["-m", "karst.mcp_server"] }`.
- **Windows `spawn ENOENT`** → the GUI app can't resolve a shim. Wrap with
  `cmd /c` (`{ "command": "cmd", "args": ["/c", "uvx", "--from", "karst", "karst-mcp"] }`)
  or point `command` at the absolute `.exe` path. Installing karst so
  `karst-mcp.exe` exists and using that directly is the most reliable.
- **Tools don't appear** → many clients only expose MCP tools in **Agent** mode
  (VS Code, Cursor, Continue). Switch modes. Also fully restart the app after
  editing config (especially Claude Desktop).
- **"No packs defined" / pack scoping empty** → packs are built by the CLI
  (`karst quickstart` or `karst packs … suggest`), not by the `index_repository`
  tool. See [COOKBOOK.md](COOKBOOK.md).
- **Tools work but return "not indexed"** → run `index_repository` from chat, or
  `karst quickstart` in the repo once.

Need the plain-English "what is this" first? → [WHY.md](WHY.md). Want to drive it
from the terminal? → [QUICKSTART.md](QUICKSTART.md).
