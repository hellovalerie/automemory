# About

automemory is a human-curated, append-only episodic memory store built *for the person*, not for the model. Most "LLM memory" (mem0, Letta, Zep) is retrieval infrastructure — a filing room a bot queries at inference time to improve its next completion. automemory inverts that objective: it exists so a person has continuity across models and sessions that individually have none. The corpus is the product, not a retrieval index.

There's no vector store, no embeddings, no RAG pipeline. Each conversation is processed once, at write time, by an extraction agent that reads the raw transcript and distills it into durable, git-tracked markdown. A hand-tuned taxonomy of five signal types — corrections that carry a standard, self-authored reframes, feeling-anchored moments, deliberately unresolved questions, and practitioner learnings — decides what survives compaction and context loss. Every artifact carries source provenance and an edits ledger, supersession is annotate-never-delete, and every extraction prompt declares its own known biases inline (an explicit counter to sycophancy in what gets flagged as "worth keeping").

v1 ships thin adapters for Claude Code, opencode, and claude.ai; the underlying criteria, schema, and philosophy are provider-neutral by design.

---

## In plain terms

Most AI chat tools either forget everything the moment you close the tab, or they quietly build a memory profile of you that you can't see or check — the model just starts "knowing" things about you and you have to trust it got them right. automemory does neither. It's a personal journal that an AI writes *for you*, not for itself: after a real conversation, it pulls out the moments that actually mattered — a good idea, an unresolved question, something you said that was worth keeping — and saves it as a plain text file you can open, read, and correct. Every entry says exactly which conversation it came from, so you can always go check the receipts. Nothing gets memorized in secret, and nothing gets silently changed later — if something needs updating, it's added as a note, with the old version still there.

The short pitch: it's memory you can audit, built for your benefit instead of the model's.

---

## In a metaphor

**The ship's log.** Every AI session is a new crew member who's never sailed with you before — they know nothing of yesterday's storm or why you changed course. automemory is the captain's log itself: written in plain language, dated, entries never torn out — if something needs correcting later, you add a note in the margin, you don't erase the page. Any new crew member can pick it up and know exactly where you've been and why, in your own words, not theirs.

**The relay baton.** Each runner carries no memory of the last leg — they're handed the baton cold. So instead of trusting the next runner to somehow "just know," you tape a note to the baton itself: what happened, when, in whose words. The runners are interchangeable and forgettable; the baton is what actually finishes the race.

---

## Longer version

**What it is:** A human-curated, append-only episodic memory store built *for the person*, not for the model. Most "LLM memory" (mem0, Letta, Zep) is retrieval infrastructure: a filing room a bot queries at inference time to improve its next completion. automemory inverts the objective function — it exists so a person has continuity across models and sessions that individually have none. The corpus is the product, not a retrieval index.

**Core mechanism:** Write-time extraction, not retrieval-augmented generation. There's no vector store, no embeddings, no RAG pipeline pulling memories back into a context window at query time. Instead, each conversation is processed once by a headless extraction agent (fired on Claude Code's `SessionEnd` hook, or via a global custom-instruction on claude.ai) that reads the raw transcript and distills it into durable markdown + git. Retrieval, if it happens, is a human opening a file — not a model doing similarity search over embeddings.

**Extraction taxonomy (the "texture criteria"):** A user-defined taxonomy of typed signal classes determines what survives compaction and context loss. The taxonomy is hand-tuned rather than learned: the user defines the classes, their boundaries, and their preservation semantics. These may include categories such as corrections that establish a standard, self-authored reframes or metaphors, affect-anchored moments, deliberately unresolved questions, practitioner or domain learnings, or any other distinctions appropriate to the application. The number and meaning of the classes are not fixed. The classifier is calibrated empirically against representative conversations rather than derived from a model's own judgment of salience.

**Provenance and grounding:** Every artifact carries source attribution (session/conversation UUID, not title — titles are mutable labels, not identity keys) and an `## Edits` ledger logging any transformation from verbatim source to printed text (`verbatim → as printed`). This is a grounding/citation discipline against hallucinated or drifted memory — the record has to survive an audit, not just read well.

**Bias controls:** Named-skew principle — every extraction prompt declares its own known tilts inline, since no prompt is neutral. Anti-sycophancy filter treats praise/appreciation as a *detector*, not a payload (an entry must pass "would a future reader act differently for this?" or it's discarded) — a direct counter to RLHF-induced agreeableness bias in what gets surfaced as "important."

**Consistency/identity handling:** Supersession is append-and-annotate, never overwrite or delete, analogous to event-sourcing rather than mutable state. Model signatures are versioned (`Opus 4.8`, not `Opus`) since the corpus spans many model instances with no continuity between them; the system explicitly compensates for the fact that each session starts with a blank context window and zero persistent state.

**Scope:** v1 adapters are Claude Code (git hook), opencode (SQLite store), and claude.ai (custom-instruction injection) only; the criteria/schema/philosophy layer is designed to be provider-neutral so other model stacks could plug in via a thin adapter.
