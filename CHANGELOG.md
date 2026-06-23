# Changelog

All notable changes to **karst**. This project uses semantic-ish versioning.

## 0.2.8

- **Interface/inheritance edges in the graph.** A new `IMPLEMENTS` edge links a
  class to the interface it implements or the base it extends (Python base
  classes, JS/TS `extends`/`implements`, TS `interface … extends`). So
  `karst impact <Interface>` now lists every class that implements it — and
  GraphRAG pulls a class's interface in as context. Name-only resolution like
  CALLS (Python + JS/TS); rebuild with `karst graph-index` to populate it.
- **Compliance & Air-Gap Pack** (`docs/compliance/`): a security-review packet for
  regulated/air-gapped teams — attestation, full network-egress table, pre-filled
  security questionnaire, and an offline-install + SBOM + "prove it offline" guide.
- **Repositioned** the README + landing around the offline blast-radius wedge
  ("know what your change breaks") and the platform/security buyer.

## 0.2.7

**Polish from an external test report (v0.2.6).**

- **`ask` now explains zero results.** When a query matches nothing, karst says
  so — and if an active pack filter is the cause, it points you at `--all-packs`
  (or `karst packs detach`) instead of printing an empty section.
- **Cost meter reflects the real provider.** The token/cost line no longer always
  quotes Anthropic pricing: local models show "no API cost", OpenAI/Anthropic
  show their own rates, and the `--no-llm` figure is labelled `est.` so it's
  clearly hypothetical.
- **`KARST_OFFLINE=1`** — one switch for air-gapped installs; sets
  `HF_HUB_OFFLINE` + `TRANSFORMERS_OFFLINE` before the embedder loads so it only
  ever reads a pre-seeded model cache. See [Self-hosted](docs/SELF-HOSTED.md).

## 0.2.6

**Run fully on-prem — for teams whose code can't go to the cloud.**

- New **`local` LLM provider**: point karst's answers at a self-hosted,
  OpenAI-compatible server (Ollama / vLLM / LM Studio). `karst ask "…" --llm
  local --model qwen2.5-coder`, or set `KARST_LLM_PROVIDER=local`. No API key,
  nothing leaves the machine.
- `default_llm` is now env-aware (`KARST_LLM_PROVIDER` / `KARST_LLM_BASE_URL` /
  `KARST_LLM_MODEL`); structured output parses leniently for local models that
  don't honour strict JSON mode.
- New guide: **[Self-hosted & air-gapped](docs/SELF-HOSTED.md)** — local-model
  setup, the network-egress breakdown, and how to run with no internet at all.

## 0.2.5

- Listed on the **official MCP Registry** (`io.github.Moin105/karst`): added a
  `server.json` manifest and the `mcp-name` ownership marker so MCP clients can
  discover and install karst directly.
- `karst --version` now reports the real installed version (derived from package
  metadata) — fixes the 0.2.3 wheel mislabeling itself `0.2.2`.

## 0.2.3

**Hybrid retrieval — better ranking, same token budget.**

- `search` now fuses the dense (semantic) ranking with a model-free lexical
  identifier/path score via **Reciprocal Rank Fusion (RRF)**. Pure-vector search
  under-weights exact names — asking "how does the **MCP server** expose tools?"
  used to rank an unrelated `CompiledPack` chunk #1; now `mcp_server.py` is #1.
- Zero extra model, negligible latency: karst over-fetches a small candidate
  pool and re-ranks in-process. The precision gain means you can keep `top_k`
  (and therefore tokens) **small** instead of over-fetching to compensate.
- Safe by construction: the re-rank is a **no-op** when the query has no lexical
  signal, so purely conceptual questions are never penalised.

## 0.2.2

- Cap stored code per chunk (8000 chars) so a single giant generated/spec
  function can't become an 11k-token chunk that dominates retrieval cost. The
  citation still spans the full definition.
- Honest token meter: counts each chunk at its truncated prompt size, so the
  printed token/cost estimate matches what's actually sent to the LLM.

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
