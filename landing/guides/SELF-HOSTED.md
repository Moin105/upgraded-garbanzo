# karst for private / air-gapped teams

If your **code is your moat** — fintech, healthtech, defense, legal, anyone with
proprietary logic or a code-egress policy — you've probably *banned* cloud AI
coding tools. They index your repo in the vendor's cloud, and your IP leaves the
building. karst is built the other way around: **it runs on your machine, and
you decide if anything ever leaves it.**

This guide shows how to run karst **fully on-prem** — including the AI answers —
so nothing touches the internet.

---

## What touches the network (and what doesn't)

| Step | Network? |
|------|----------|
| **Indexing** (parse → chunk → embed → store) | **None.** tree-sitter parsing, local embeddings, a local Qdrant file store, the call graph, and sqlite caches all run on your machine. |
| **Retrieval** (`search`, `find_impact`, packs) | **None.** It reads the local index. |
| **Embedding model** | **One-time download** (~65 MB, a quantized ONNX model) the first time you index, cached under `~/.karst/models`. After that it's offline — and you can pre-seed it for a fully air-gapped box (below). |
| **The AI answer** (`ask` / `review`) | **Only if you opt in.** Three choices: nothing (`--no-llm`), a **local** model (stays on-prem), or a cloud model (sends *only the retrieved slice*, never the whole repo). |

karst itself ships **no telemetry, no analytics, and no update checks.** The
only outbound calls are the embedding model's one-time fetch and whichever LLM
*you* choose.

> **The headline:** index and search your private code with the network cable
> unplugged. Add a local model and you get AI answers too — still fully offline.

## Fully local AI answers (Ollama / vLLM / LM Studio)

Any OpenAI-compatible local server works. Ollama is the easiest:

```bash
# 1. install Ollama (ollama.com), then pull a code-capable model once:
ollama pull qwen2.5-coder        # or llama3.1, deepseek-coder-v2, etc.

# 2. index your repo (local embeddings — no key, no cloud):
cd your-project && karst quickstart

# 3. ask, 100% on your machine:
karst ask "how does auth work?" --llm local --model qwen2.5-coder
```

Prefer to set it once and forget it? Use environment variables — then plain
`karst ask "…"` (and the `review` command, and the MCP server) all stay local:

```bash
export KARST_LLM_PROVIDER=local
export KARST_LLM_MODEL=qwen2.5-coder       # default: llama3.1
# export KARST_LLM_BASE_URL=http://localhost:11434/v1   # default (Ollama)
```

Pointing at vLLM / LM Studio / llama.cpp instead? Just set `KARST_LLM_BASE_URL`
to its OpenAI-compatible endpoint. No real API key is needed for local servers.

## Air-gapped (no internet at all)

The one thing that needs the network is the **first** embedding-model download.
To run on a machine that never has internet:

1. On an internet-connected machine, run `karst quickstart` once on any repo —
   this populates the model cache at `~/.karst/models`.
2. Copy that `~/.karst/models` folder to the air-gapped machine (same path).
3. On the air-gapped box, force offline mode so nothing is ever fetched:
   ```bash
   export HF_HUB_OFFLINE=1               # never reach out for a model
   export HF_HUB_DISABLE_TELEMETRY=1     # belt-and-suspenders: no HF ping
   export KARST_LLM_PROVIDER=local       # with a local model already pulled
   ```

From there, indexing, retrieval, and AI answers all run with **zero** outbound
connections.

## Environment variable reference

| Variable | Purpose | Default |
|----------|---------|---------|
| `KARST_LLM_PROVIDER` | `local` \| `anthropic` \| `openai` | auto-detect from keys |
| `KARST_LLM_MODEL` | model name for answers | `llama3.1` (local) |
| `KARST_LLM_BASE_URL` | local server endpoint | `http://localhost:11434/v1` |
| `KARST_LLM_API_KEY` | only if your local server needs one | `local` (dummy) |
| `HF_HUB_OFFLINE` | block any model download (air-gap) | unset |

The same flags exist per-command: `karst ask "…" --llm local --model <name>`.

## Verify it yourself

Don't take our word for it — this is open source (Apache-2.0) and testable:

- **Read the code:** karst has no `requests`/`httpx`/`urllib` calls of its own —
  the only outbound paths are the embedding download and the LLM SDK.
- **Prove it offline:** after one `karst quickstart` (to cache the model), pull
  the network and run `karst quickstart` + `karst ask "…" --llm local` again on
  another repo. It works with the cable unplugged.

---

Want the AI answer to never leave the building? Use `--llm local`. Want to see
exactly what a cloud model *would* receive instead? Run `karst ask "…" --no-llm`
— it prints the precise, cited slice, so you can audit the egress surface before
trusting anything to a cloud. Either way, **you're in control of what leaves.**
