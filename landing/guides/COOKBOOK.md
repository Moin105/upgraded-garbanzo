# karst cookbook — real scenarios

Concrete jobs developers actually have, the exact command, and **real output**
(these are live runs against karst's own repo). Each one ends with *why it
helps* so you know what you're buying.

**Prerequisite:** run `karst quickstart` in your repo first (see
[QUICKSTART.md](QUICKSTART.md)). That builds the index + graph and prints your
storage path. Without it, `ask`/`impact` exit with "no index found".

Throughout, **`S` = your index path** — the one quickstart printed, which is
`~/.karst/indexes/<your-folder-name>`. The snippets below use `S` as a shell
variable, so set it once and they run as written:

```bash
export S=~/.karst/indexes/myapp        # replace myapp with your folder name
```

> Shortcut: if you `cd` into the project folder, you can **omit `--storage`
> entirely** — `ask`/`index` default to that folder's index. (`impact` still
> needs `--graph-path "$S/graph.pkl"`.)

---

## 1. "I just cloned this repo. What is it and where's anything?"

Onboarding to unfamiliar code. Ask in plain English; read the cited hits.

```bash
karst ask "where is the vector store similarity search implemented?" --no-llm --top-k 4
```

```text
Retrieved chunks:
  [1] karst/store.py:176-222          method    ChunkStore.search    (score 0.720)
  [4] karst/mcp_server.py:174-226     method    search_code          (score 0.669)
```

**Why it helps:** you go from "246 files, no idea" to the 3–4 functions that
actually matter, each clickable. No full-text grep archaeology.

---

## 2. "What breaks if I change this?" (blast radius)

The check you usually do in your head — made explicit, before you touch code.

```bash
karst impact --target search --graph-path "$S/graph.pkl"
```

```text
Targets (1):
  - karst/store.py::ChunkStore.search
Affected: 27  Risk: HIGH

  [function ] depth 1  score 1.150  via calls            karst/ask.py::ask  (karst/ask.py:55-97)
  [function ] depth 1  score 1.150  via calls,contains   karst/mcp_server.py::search_code  (karst/mcp_server.py:173-226)
  [function ] depth 2  score 0.575  via calls            karst/cli.py::_answer_once  (karst/cli.py:176-235)
  [file     ] depth 2  score 0.345  via contains,imports karst/store.py  (karst/store.py)
  …and more
```

You can also target a diff instead of a name:

```bash
karst impact --staged --graph-path "$S/graph.pkl"       # what your staged changes touch
karst impact --base origin/main --graph-path "$S/graph.pkl"
```

**Why it helps:** it's a real call/import graph, so it catches dependents that
share no keywords with your change — exactly what embedding search misses. The
`depth` and `via` columns tell you *how close* and *how connected* each risk is.

---

## 3. "Make answers cheaper and sharper" (context packs)

A pack is a named slice of the repo. Attach one and retrieval only searches that
slice instead of the whole index.

```bash
karst packs --storage "$S" list
```

```text
ID                         LABEL                CHUNKS   TOKENS AUTO
pack_dashboard             Dashboard               176    18437 yes
pack_karst_mcp_server_py   Karst Mcp Server.py      25     5389 yes
pack_karst_graph           Karst Graph              68    10372 yes
…
```

These pack ids come from karst's own repo — yours will differ. Copy a real id
from *your* `packs … list` output (above) before attaching:

```bash
karst packs --storage "$S" attach pack_karst_mcp_server_py   # next `ask` is scoped to it
karst packs --storage "$S" pin pack_karst_graph              # keep a pack active for every query
karst packs --storage "$S" status                            # see what's attached/pinned
```

**Why it helps:** in this repo's index, scoping to that pack searches 25
relevant chunks instead of all 629, so the top hits are on-topic and you can
lower `--top-k` with confidence — fewer tokens in the prompt, less for the model
to wade through. (On a separate 246-file repo, packs measured ~60% fewer input
tokens per question.)

---

## 4. "Review my changes before I push"

karst reviews a diff and returns severity-tagged findings, each grounded in
retrieved context from the index.

```bash
karst review --staged --storage "$S"                       # your staged changes
karst review --base origin/main --storage "$S"             # everything since main
karst review --pr 42 --repo owner/name --storage "$S"      # a GitHub PR (uses the gh CLI)
```

Add `--post-to-pr` (with `--pr`) to post findings as inline review comments.

> **Note:** `review` always uses an LLM — so unlike `ask`, it has **no
> `--no-llm` mode**. It needs an API key **and** the provider extra installed
> (`uv tool install 'karst[anthropic]'` or `'karst[openai]'`). Without them it
> exits with "No LLM configured".

**Why it helps:** unlike a generic linter, the review sees the *surrounding*
code karst pulls in, so findings are about your actual logic, not just style.

---

## 5. "I changed a bunch of files — keep the index current"

Re-indexing is incremental: it hashes every file and skips the unchanged ones.

```bash
karst index . --storage "$S"
```

```text
Files: 121 total = 5 indexed + 116 reused
Embeddings: 11 computed + 44 from cache
Chunks in collection: 629  (5.9s)
```

**Why it helps:** `5 indexed + 116 reused` — only the files you touched get
re-embedded. A repo that took minutes to index the first time refreshes in
seconds, so the index is never stale.

---

## 6. "Let my AI do all of this for me"

Everything above is also available to Cursor / Claude Desktop over MCP, so you
can stay in chat and never run a command. See
[FOR-VIBE-CODERS.md](FOR-VIBE-CODERS.md).

---

## Handy reference

| Goal | Command |
|------|---------|
| Get a repo ready | `karst quickstart` |
| Explore, no API key | `karst ask "…" --no-llm` |
| Interactive Q&A | `karst ask -i` |
| Written answer | `karst ask "…"` (set `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`) |
| Blast radius | `karst impact --target NAME --graph-path "$S/graph.pkl"` |
| Scope a search | `karst packs --storage "$S" attach <pack-id>` |
| Review a diff | `karst review --staged --storage "$S"` |
| Refresh the index | `karst index . --storage "$S"` |
| Cheatsheet | `karst examples` |

Every command supports `--help` — e.g. `karst impact --help`.
