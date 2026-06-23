# karst — Pre-filled Security Questionnaire

Standard vendor-risk / CAIQ / SIG-style questions with karst's answers for a
**self-hosted** deployment (karst 0.2.7). Many "vendor" questions are *Not
Applicable* precisely because there is no vendor cloud — karst runs entirely on
your infrastructure. Where a control is the customer's responsibility (because the
software is self-hosted), that's stated plainly.

> Copy the relevant rows into your own questionnaire format. The maintainer can
> sign a completed copy of your specific template on request.

## A. Data handling & privacy

| Question | Answer |
|---|---|
| Does the product transmit customer data (source code) to the vendor or any third party? | **No.** In an air-gapped configuration nothing leaves the host. The only paths that can transmit code are operator-enabled (a cloud LLM you configure, or `gh`-based PR review) — both avoidable. See SECURITY.md §3. |
| Where is customer data stored? | On the customer host only (`~/.karst/`). No vendor-side storage. |
| Is customer data used to train models? | **No.** The local embedding model is pre-trained and read-only; karst performs no training. |
| Data classification supported | Suitable for confidential / regulated / CUI source when run air-gapped (operator-configured). |
| Data retention / deletion | Customer-controlled. Delete the local index dir to erase. No vendor copy to request deletion of. |
| PII processing | karst processes source code, not personal data, and stores it only locally. No PII is transmitted. |

## B. Network & egress

| Question | Answer |
|---|---|
| Does the software "phone home" / send telemetry? | **No.** No telemetry, analytics, or update pings. (Verifiable in source.) |
| Required outbound connections for normal operation | **None**, after a one-time local embedding-model cache. With `KARST_OFFLINE=1` and a pre-seeded cache, zero outbound. |
| Inbound listeners | Default stdio (no socket). Optional Streamable-HTTP MCP server binds a host/port you choose; protect with `KARST_MCP_TOKEN` and your network controls. |
| License-server / activation callback | **None.** No activation, no license check. |

## C. Identity & access management

| Question | Answer |
|---|---|
| Authentication for single-host use | OS-level: file permissions on the local index directory. |
| Authentication for team gateway | Per-team bearer keys today (open-core gateway). **SSO/SAML/OIDC, SCIM, RBAC: roadmap — confirm status before relying on them.** |
| Audit logging | Gateway keeps a usage log; SIEM-exportable, tamper-evident audit log is on the roadmap. Single-host use relies on OS/host logging. |
| Least privilege | Runs as the invoking user; needs read access to the repo and write access to its storage dir only. No elevated privileges. |

## D. Supply chain & integrity

| Question | Answer |
|---|---|
| License | Apache-2.0 (permissive). |
| Third-party components | tree-sitter, tree-sitter-language-pack, fastembed (ONNX), qdrant-client, networkx, mcp, unidiff (+ optional anthropic/openai). All mainstream, permissively licensed. |
| SBOM available? | Yes — generate a CycloneDX SBOM yourself (AIR-GAP-INSTALL.md). |
| Build/release integrity | Published to PyPI via GitHub Actions Trusted Publishing (OIDC; no stored tokens). Pin version + hashes and mirror internally. |
| Can we install from our internal mirror? | Yes — it's a standard Python package; vendor a wheelhouse into Artifactory/Nexus. |
| Source code review possible? | Yes — fully source-available under Apache-2.0. |

## E. Operational & compliance

| Question | Answer |
|---|---|
| Hosting model | Self-hosted only. No SaaS, no managed cloud. |
| SOC 2 / ISO 27001 of the vendor cloud | **N/A** — there is no vendor cloud to certify. Operational controls (host hardening, patching, access) are the customer's, on customer infrastructure. |
| Vulnerability management | Open-source; issues tracked publicly. Customers control patch cadence by choosing which version to mirror. |
| Encryption in transit | **N/A in air-gapped mode** (no transit). If the HTTP MCP server is exposed on a network, terminate TLS at your proxy. |
| Encryption at rest | Provided by the host (disk encryption / FS permissions). karst adds no separate at-rest layer. |
| Incident response / breach notification | No vendor-side data to breach. Standard responsible-disclosure for code vulnerabilities via the repository. |
| Business continuity | The index is a derived artifact — re-buildable from source at any time with one command. No vendor dependency for continuity. |

## F. AI-specific

| Question | Answer |
|---|---|
| Does an LLM see our code? | Only if you enable a cloud LLM. Use `--llm local` (on-prem model) or `--no-llm` (retrieval only) and no LLM sees code outside your boundary. |
| Are outputs deterministic / auditable? | The retrieval, graph, and **impact/blast-radius analysis are deterministic and computed** (not model-sampled), so results are reproducible and explainable. LLM-written prose (optional) is the only probabilistic part. |
| Model provenance | Default embedding model: BAAI/bge-small-en-v1.5 (open weights, ONNX). Swappable. |

---

*Answers describe karst 0.2.7, self-hosted. Items marked "roadmap" are not present
in the current OSS core — verify before relying on them. Contact the maintainer for
a signed/dated copy against your specific questionnaire template.*
