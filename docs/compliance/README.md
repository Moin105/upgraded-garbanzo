# karst — Compliance & Air-Gap Pack

> For the platform / DevEx / security engineer evaluating karst for a regulated
> or air-gapped environment. Hand these documents to your AppSec / procurement
> team — they are written to answer their questions, not a developer's.

## The one-paragraph version

**You are not procuring a SaaS vendor. You are installing an open-source library
you can read and run entirely inside your own perimeter.** karst's own code makes
**zero outbound network calls** — no telemetry, no phone-home, no license server,
no sub-processors. It indexes your code, builds a dependency graph, and answers
questions *on the machine it runs on*. In a fully air-gapped configuration,
**nothing — including your source code — ever leaves your boundary.** Because it's
Apache-2.0 and source-available, your security team can verify every claim below
by reading the code instead of trusting us.

## What's in this pack

| Document | Purpose | Hand to |
|----------|---------|---------|
| [SECURITY.md](SECURITY.md) | Air-gap attestation, data-flow & trust boundary, full network-egress table, data residency | AppSec / security review |
| [SECURITY-QUESTIONNAIRE.md](SECURITY-QUESTIONNAIRE.md) | Pre-filled answers to the standard vendor questionnaire (CAIQ / SIG-style) | Procurement / vendor risk |
| [AIR-GAP-INSTALL.md](AIR-GAP-INSTALL.md) | Install from your internal mirror with no internet, generate an SBOM, and run the "prove it offline" test yourself | Platform / DevEx team |

## Why the review is lighter than a typical AI tool

| Typical cloud AI coding tool | karst (self-hosted) |
|---|---|
| Source code transits the vendor's cloud | Source never leaves the machine |
| Vendor data-residency / sub-processor review | No data leaves the boundary → nothing to assess |
| Trust a black-box binary / API | Read the Apache-2.0 source |
| Telemetry & usage analytics to whitelist | None — no phone-home |
| New-vendor onboarding + DPA + SOC 2 of *their* cloud | Installs from your mirror like any vetted library |

The structural fact that collapses most of a vendor questionnaire: **there is no
data flow to assess.** A cloud architecture cannot make that claim; karst can,
and you can prove it (see [AIR-GAP-INSTALL.md](AIR-GAP-INSTALL.md) → "Prove it
offline").

## Scope & honesty note

karst has exactly **two optional, operator-chosen integrations** that *can* reach
the network, both off by default in an air-gapped deployment and both documented
in [SECURITY.md](SECURITY.md):

1. **A cloud LLM** for written answers — only if *you* configure an Anthropic/OpenAI
   key. Use a local model (`--llm local`, e.g. Ollama) or retrieval-only (`--no-llm`)
   and no prompt ever leaves the box.
2. **GitHub PR review** (`karst review --pr`) — shells out to *your* `gh` CLI. Don't
   use that one command and there is no GitHub traffic.

The core — `index`, `ask`, `impact`, `search` — never touches the network once the
embedding model is cached locally.

---

*Pack version tracks the karst release it ships with. Current: karst 0.2.7,
Apache-2.0. Maintainer attestation in [SECURITY.md](SECURITY.md).*
