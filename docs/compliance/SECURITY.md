# karst — Security & Air-Gap Attestation

**Product:** karst (Python CLI + MCP server for local code context)
**Version:** 0.2.7 · **License:** Apache-2.0 (source-available)
**Deployment model:** self-hosted, runs entirely on customer-controlled machines
**Last reviewed:** 2026-06-23

---

## 1. Attestation

To the best of the maintainer's knowledge, for the version above:

1. **karst's own source code makes no outbound network calls.** It contains no
   `requests` / `httpx` / `urllib` / raw-socket HTTP client of its own. (Verifiable:
   grep the `karst/` package for those imports — there are none.)
2. **No telemetry, analytics, usage tracking, or phone-home of any kind.** There is
   no analytics SDK, no license-server callback, and no "check for updates" ping.
3. **No sub-processors.** karst does not transmit code, queries, or metadata to any
   third party as part of its own operation.
4. **Your source code is processed locally.** Indexing, embedding, the vector store
   (Qdrant local-file mode), the dependency graph, and impact analysis all run in
   the same process/host, writing only to a local directory you control (default
   `~/.karst/`).
5. **Air-gappable.** With `KARST_OFFLINE=1` and a pre-seeded model cache, karst runs
   with **zero outbound connectivity**, including with the network physically
   disconnected. See [AIR-GAP-INSTALL.md](AIR-GAP-INSTALL.md) to reproduce.

The only network activity karst can produce comes from clearly-scoped, **optional,
operator-enabled** paths enumerated in §3. In a default air-gapped deployment, all
of them are off.

---

## 2. Trust boundary & data flow

```
        ┌──────────────────────── customer host / VDI / golden image ───────────────────────┐
        │                                                                                     │
  your  │   source files ─▶ tree-sitter parse ─▶ chunks ─▶ embeddings (local ONNX) ─▶ Qdrant │
  repo ─┼──▶                                  └─▶ call/import/impl graph (NetworkX, local)    │
        │                                                                                     │
  agent │   Claude Code / Cursor / VS Code ──(MCP, stdio or localhost HTTP)──▶ karst tools    │
 (MCP)  │      returns: cited code snippets + impact results  (no code leaves this box)       │
        │                                                                                     │
        └─────────────────────────────────────────────────────────────────────────────────-─┘
              ▲ everything above this line stays on the customer host ▲
```

**What is stored, and where:** the local index (`~/.karst/<repo>/`) holds your code
chunks, their embeddings (vectors), a SHA manifest, and the graph — all on local
disk. There is no remote store. Data at rest is protected by the host's own disk
encryption / file permissions; karst adds no separate at-rest service.

**What crosses the trust boundary:** in a default air-gapped deployment, **nothing.**
The MCP transport is either stdio (same machine, no socket) or Streamable-HTTP bound
to a host/port you choose (intended for localhost or an internal network), optionally
behind a bearer token (`KARST_MCP_TOKEN`).

---

## 3. Complete network-egress table

| # | What | When | Direction | Contains your code? | How to disable |
|---|------|------|-----------|---------------------|----------------|
| 1 | **Embedding model download** (HuggingFace, ~65 MB, one-time) | First index only, then cached in `~/.karst/models` | Outbound to huggingface.co | **No** — downloads a model, sends nothing | `KARST_OFFLINE=1` (pre-seed the cache first); or pre-install via your mirror |
| 2 | **tree-sitter grammars** | Provided by the `tree-sitter-language-pack` dependency | Pip install time (or first use on some versions) | **No** | Install the wheel from your internal mirror; pre-cache so there is no runtime fetch |
| 3 | **Cloud LLM call** (Anthropic / OpenAI) | Only if *you* set an API key AND run `ask`/`review` without `--no-llm`/`--llm local` | Outbound to the LLM provider | **Yes — the assembled prompt** (selected code snippets) | Use `--llm local` (Ollama/vLLM/LM Studio) or `--no-llm`; set no cloud key |
| 4 | **GitHub PR review** | Only `karst review --pr` / `--post-to-pr` | Outbound via your `gh` CLI | Diff + the LLM's findings | Don't use the `--pr` path; core review reads local diffs |

**Reading the table:** rows 1–2 download *tooling*, never your code. Rows 3–4 are
**opt-in features you choose to enable** and are the only paths by which code could
leave the host — both fully avoidable. For a zero-egress build: pre-seed the model,
set `KARST_OFFLINE=1`, use `--llm local` or `--no-llm`, and do not use PR review.

> **Key point for review:** the value-delivering core (`index`, `ask`, `impact`,
> `search_code`, `find_impact` over MCP) requires **none** of rows 1–4 after the
> one-time local model cache exists.

---

## 4. Data residency & retention

- **Residency:** 100% on the customer host. No multi-tenant cloud, no vendor region.
- **Retention:** the index lives in a local directory until you delete it. karst
  retains nothing elsewhere. Re-indexing overwrites in place.
- **Right to delete:** `rm -rf ~/.karst/<repo>` (or your configured `--storage` path).

## 5. Identity, access & auditing (self-hosted gateway)

For single-developer / single-host use, access control is the host's own OS
permissions on `~/.karst/`. For **team deployment**, the (open-core) gateway adds a
single authenticated MCP endpoint with per-team keys and a usage log. SSO/SAML/OIDC,
RBAC, and a SIEM-exportable audit log are on the enterprise roadmap — if your review
requires them today, contact the maintainer to confirm status before deployment.
*(Do not assume enterprise identity features are present in the OSS core.)*

## 6. Supply chain

- **License:** Apache-2.0 (permissive; no copyleft obligations).
- **Direct dependencies** are mainstream, permissively-licensed OSS:
  `tree-sitter` / `tree-sitter-language-pack` (parsing), `fastembed` (ONNX embeddings),
  `qdrant-client` (local vector store), `networkx` (graph), `mcp` (protocol),
  `unidiff` (diff parsing). Optional extras: `anthropic`, `openai`.
- **SBOM:** generate a CycloneDX SBOM in one command — see
  [AIR-GAP-INSTALL.md](AIR-GAP-INSTALL.md) → "Generate an SBOM."
- **Integrity:** releases are published to PyPI via GitHub Actions Trusted Publishing
  (OIDC, no long-lived tokens). Pin to a known version + hashes in your lockfile and
  mirror it internally.

## 7. How to verify everything here yourself

1. **Read the code** — it's Apache-2.0. Start with `karst/` (no network clients).
2. **Run it offline** — follow [AIR-GAP-INSTALL.md](AIR-GAP-INSTALL.md): install from
   a local wheelhouse, disconnect the network, and confirm `index` + `ask --no-llm`
   still work.
3. **Watch the network** — run karst under your egress monitor / `netstat` / Little
   Snitch with `KARST_OFFLINE=1` and confirm no connections.

---

*This document is a good-faith engineering attestation, not a legal warranty. It
describes karst 0.2.7 in a self-hosted configuration. For a signed copy, a completed
copy of your specific questionnaire, or current status of roadmap items, contact
the maintainer (see the repository README).*
