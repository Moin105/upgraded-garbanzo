# Why karst? (What it is, and what it's for)

A plain-language explanation — no jargon. If you're deciding whether karst is
worth your time, start here.

---

## The one-sentence version

> **karst is a librarian for your codebase that AI tools can talk to.**

When an AI tool needs to understand your code, it normally either greps around
blindly or stuffs whole files into the model. karst replaces that with a precise
request: *"give me the exact code relevant to this question"* — and gets back a
small, ranked set of snippets, each cited to an exact `file:line`.

## The problem it solves

AI coding assistants are great until the codebase gets big. Then two bad things
happen:

1. **They read too little** — grab the wrong file, miss the real logic, and
   confidently make something up (hallucinate).
2. **They read too much** — dump tens of thousands of tokens of
   vaguely-related code into the model on every question. That's slow,
   expensive, and the model loses the plot in the noise.

And neither approach can answer the question every developer actually asks
before changing code: **"what else breaks if I touch this?"**

## How karst inverts it

| Most "chat with your code" tools | karst |
|----------------------------------|-------|
| Dump lots of vaguely-related code | Retrieve a **small, ranked** slice |
| You can't see what was loaded | Every snippet is **cited** to `file:line` |
| You can't scope it | **Packs** scope search to a feature/folder |
| "Hope it found the right thing" | A real **graph** answers *what depends on what* |
| Surprise token bill at month-end | A **cost meter** shows the price *before* you pay |

## What you actually get (the benefits)

- **Cheaper.** Precise retrieval + scoping means far fewer tokens per question,
  and you see the estimated cost up front. (~60% fewer input tokens on a real
  246-file repo.)
- **Trustworthy.** Every answer rests on snippets you can open and verify. No
  black box.
- **Safer changes.** Ask for the *blast radius* of a change and get a ranked
  list of everything that depends on it — before you write a line.
- **Private & key-free.** It runs entirely on your machine. Retrieval needs no
  API key; your own IDE/model writes the final answer.
- **Fast to keep current.** Re-indexing is incremental — only changed files are
  re-processed, so a refresh takes seconds.

## Who it's for

- **Developers** who want to understand an unfamiliar codebase, check the impact
  of a change, or review a diff — from the command line. → [QUICKSTART.md](QUICKSTART.md)
- **"Vibe coders"** who live in Cursor / Claude Desktop and want their AI to
  silently use karst for grounded, cited context — **without ever running a
  command**. → [FOR-VIBE-CODERS.md](FOR-VIBE-CODERS.md)
- **Teams** who want every AI answer about their code to be cheap, scoped, and
  auditable.

## What karst is *not*

- **Not** a chatbot that replaces your AI. It's the *context layer* underneath
  it — it feeds your existing AI better information.
- **Not** a cloud service you sign up for. It's a tool you install; your code
  stays local.
- **Not** an LLM wrapper that needs your API key. The retrieval is local and
  free; an LLM is optional and only used if *you* point it at one.

## How it works, briefly

1. **Index** — karst parses your repo into AST-aware chunks (functions,
   classes, methods), embeds them, and stores them in a local vector database
   (Qdrant, running as embedded files — no server, no Docker).
2. **Graph** — it builds a call/import graph of who-uses-what, which powers the
   blast-radius analysis.
3. **Retrieve** — a question is matched against the index using a hybrid of
   semantic similarity *and* exact-identifier matching, then re-ranked so the
   most relevant code is on top.
4. **Serve** — results go back to you (CLI) or to your AI tool (over MCP),
   always cited to `file:line`.

For the deeper design, see [ARCHITECTURE.md](ARCHITECTURE.md).

---

**Ready to try it?** → [QUICKSTART.md](QUICKSTART.md) (5 minutes, no API key).
