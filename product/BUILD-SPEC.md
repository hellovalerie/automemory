<!--
automemory — Product Build Spec (v1 draft)

The PRODUCT track only. Executional, no philosophy. This document is what an engineer
builds from. The philosophy/constitution track lives elsewhere and is referenced by
pointer, never restated here.

Supersedes the product half of the walked-back CHARTER.md. Ends with an adversarial
challenge section (self-red-team) — read it; it is part of the spec, not an appendix.

Update log:
- 2026-07-19 — folded in the session's locked decisions: Claude-Code-only for v1
  (Codex/ChatGPT deferred); capture tiers + always-visible cost (§6); secret-scrubbing
  default-deny (§7); the locked family taxonomy (§8); the full CLI set (§9);
  prudence & parsimony named as product values (§0). Adversarial items A1/A4/A5/A6/A7
  annotated with their resolution or deferral.
-->

# automemory — Product Build Spec

## 0. What it is (product framing)

automemory is a **passive capture engine for AI conversations** that builds a durable, human-read knowledge base automatically. It is an alternative to manual PKM/PKB tools (Obsidian, Logseq, Notion): those require *you* to file; automemory captures from your AI sessions and files for you.

The product is **a record engine + pluggable capture strategies**, not a single pipeline. This framing is load-bearing (see §2 and the family taxonomy in §8).

**Product values (they decide the hard calls):**
- **Prudence & parsimony.** Capture the least that serves; expose cost; never make the user contain a hazard the engine should refuse. These two values drove the secret-scrubbing default (§7) and the tiers (§6).
- **No silent failures.** Every failure surfaces loudly and leaves a recoverable trail.
- **The user owns their data.** We build doors; we don't police which ones they use (see `purge`, §9).

**Design commitments, fixed:**
- **Language: Python 3, standard library only. Zero third-party (pip) dependencies.** (Removes the current `jq` dependency; keeps setup to one brew command.)
- **Storage: the filesystem is canonical. Git is optional** (one sync option among several / none).
- **Never overwrite user data, config, or lens.** Architectural changes ship with a `protect`/`package` path (§9).
- **v1 target surface: Claude Code only.** Codex and ChatGPT (export) are *deferred, not designed out* — the adapter seams (§5) keep the door open.
- **Cost is always visible; secrets are never captured by default** (§6, §7).
- **No personal content ever ships.** The product repo is born clean and never contains a corpus.

## 1. The two layers

automemory's own capture (observe → extract) and LEARNINGS (self-scribe) share a *record*, not a *pipeline*. So the engine boundary is drawn at the **record layer**, and capture sits above it.

```
  ┌─────────────────────────────────────────────┐
  │  CAPTURE STRATEGIES (pluggable)              │
  │   • automemory-capture  (observe → extract)  │  ← reference / default
  │   • learnings           (self-scribe)        │  ← official second strategy
  │   • (future: baton, glossary, …)             │
  └───────────────────────┬─────────────────────┘
                          │  produces entries conforming to the record contract
  ┌───────────────────────▼─────────────────────┐
  │  RECORD CORE (services)                      │
  │   identity (uuid) · append-only ·            │
  │   supersede-by-annotation · update-append    │
  │   (watermark) · INDEX generation ·           │
  │   locking/concurrency · failure surfacing ·  │
  │   optional sync (git/…) · partitions         │
  └─────────────────────────────────────────────┘
```

The record core provides persistence, identity, concurrency, sync, and failure-handling **as services**. A capture strategy's only job is: *given a session, produce entries that satisfy the record contract.*

## 2. The record contract

An entry is a markdown file with YAML frontmatter. Identity is the **session uuid**, never the filename (filenames are display labels and may be renamed without forking the record). Invariants:

- **Append-only.** Nothing is deleted by the engine. A newer entry that overturns an older one annotates it and adds a forward link (`superseded_by`). *(This is the engine's own discipline for the record's integrity — it is not a cage on the user, who may `purge` their own data; §9.)*
- **Update-append by watermark.** Re-capturing a grown session appends a dated `## Update` section for transcript lines past the recorded `lines:` watermark; wholesale regeneration is banned.
- **Model-attributed.** `model:` names the writing model with version.
- **INDEX is generated,** never hand-edited — a derived view rebuilt from frontmatter.

Two entry kinds ship: **field notes** (descriptive, blind-observed) and **texture/knowledge entries** (selected per a lens). A capture strategy may use either or both.

## 3. Capture strategy: automemory-capture (reference)

The current engine, ported to Python. Detached, third-party, two-stage:

1. **Trigger** fires at session end (surface adapter, §5); a thin launcher detaches a worker so the work survives the host's bounded hook window. The live session writes nothing.
2. Worker archives the raw transcript **(after the secret-scrub pass, §7)**.
3. **Blind observer** (small model, *no repo/vault access*, transcript-only) → field notes. Blindness is structural, which keeps the record free of the observer's bias.
4. **Lensed extractor** (larger model, reads the user's lens/criteria) → knowledge entries + INDEX.
5. Persist via the record core; sync if configured.

Everything gnarly in today's bash — uuid in-flight locks, git push lock, watermark splice-then-advance, proportional word-floor validation with one retry, honest ledger backstop, failure breadcrumbs, self-capture guard — is preserved semantics, translated to Python (cleaner: no `jq`, no awk frontmatter surgery, real subprocess/daemon handling).

## 4. Capture strategy: learnings (self-scribe)

The participant agent writes its own distilled entry at session end, synchronous, first-person. No blind observer, no extractor, ~zero extra inference. It **writes through the record core** (identity, append-only, update-append, INDEX, sync, locking) and satisfies the same record contract — that is what makes it a strategy on the engine rather than a separate app. This is also the **Local** capture tier (§6) and the cheap path.

## 5. Surface adapters

Surface-specific concerns are abstracted so the core is surface-agnostic, even though **only Claude Code is wired in v1:**

- **Trigger adapter** — Claude Code: session-end hook (passive). *(Codex hook / export-ingest for ChatGPT & claude.ai: deferred; the seam exists.)*
- **Transcript adapter** — parses a surface's format into one internal turn-shape. v1: Claude Code JSONL.
- **Dispatch adapter** — which model CLI runs the observer/extractor. Defaults to the user's model CLI (`claude -p`); configurable. **Dispatch is via installed CLIs, not SDKs** — this is what keeps the product zero-dependency.

## 6. Capture tiers & cost visibility

**Cost is never a surprise.** Consumption (tokens/bandwidth, and any host limits we can read) is surfaced continuously — the way a TUI keeps context-window / 5H / 7D usage in its status bar. `status` reports it; a live indicator shows it.

The user chooses the fidelity/cost tradeoff explicitly:

| Tier | What it does | Cost |
|---|---|---|
| **High** | full observed-capture, most faithful | most tokens |
| **Mid** | smaller models / shallower prompts / less frequency | moderate |
| **Low** | most lossy observed-capture | fewest tokens (of the observed tiers) |
| **Local** | self-scribe (`learnings` strategy) — no separate model calls | ~free, but no independent observation |

Lossier = fewer tokens. The tier is visible, changeable, and explained in plain terms at the point of choice.

## 7. Secret scrubbing (default-deny)

**The engine never transcribes secrets, IDs, webhook data, tokens, keys — by default.** A redaction pass runs **before** anything is archived or sent to the observer. This is not "scope tightly to reduce exposure" (that would put the hazard on the user, against *prudence & parsimony*); it is the engine refusing to record the material at all.

Honest limit, stated plainly: perfect secret detection is impossible — scrubbing is heuristic (known key/token formats, high-entropy strings). So the promise is **best-effort default scrubbing and no surprises**, not a mathematical guarantee. A user who deliberately wants such material captured must invent/opt into a **separate protocol**; it is never the default path.

## 8. The family taxonomy (LOCKED)

Sort capture by *who authors it and who it's for*:

- **Knowledge / continuity family** — self-authored, forward-facing, for a successor / team / future-you: **findings (`learnings`), baton (handoff), glossary (revision).** Cheap (self-scribe). This is the PKM/PKB product.
- **Texture / person family** — observed, inward, for the person themselves: **personal automemory.** Expensive (observed), specialist.

`learnings` is therefore the **continuity strategy**, and baton + glossary are its natural member entry-types — not bolt-ons to personal automemory. Details (the plugin interface) are deferred until a third strategy proves the need; we do **not** build a formal plugin SDK for N=2. What we build now is the crisp record contract (§2) that all strategies share.

## 9. The CLI (the only setup surface)

`brew install <tap>/automemory` → `automemory init` → done. Commands:

- **Setup:** `init` (idempotently wires the host hooks + scaffolds `~/.automemory/` config, never clobbers) · `partition <path|uuid>` (set what is / isn't monitored — replaces hand-editing a config file).
- **Capture:** `process` (force/on-demand capture for mid-sessions or new chats — also the cost lever) · `port` (prep a claude.ai export folder for ingestion — *deferred with claude.ai*).
- **Verify / read:** `parse <uuid>` (raw JSONL → human-readable, standalone, for the user's eyes) · `peer <date|range>` (field notes/findings produced, full) · `peep <date|range>` (index-only, titles) · `status` (health, failure count, last capture, live cost).
- **Data safety:** `purge <uuid|fuzzy-title>` (remove crawled content — the user's own door; **no lock, but it prints a receipt of what it removed** — honesty, not permission) · `protect` (timestamped, read-only snapshot before any structural/migration change; restore point) · `package` (bundle all transcribed data into a tidy zip, folder structure intact, for carry-out/archive/handoff).

`protect` + `package` together are pref-#1's seal/protect/transfer: `protect` = insurance you might restore from; `package` = luggage you carry out.

*(Naming: the `p`-family is `partition · parse · purge · peer · peep · process · port · protect · package`. `init`/`status` are the conventional holdouts; `prime`/`pulse` are available if the alliteration is ever made load-bearing.)*

## 10. Versioning & migration

- The **data format is versioned** in frontmatter/config. Migrations are **never destructive**: `protect` first, migrate a copy, verify, then swap — always reversible from the snapshot.
- Future upgrades are open issues / bugs / net-new features shipped via brew; **none may touch user data in place.** Engine prompts are code and ship with these updates.

## 11. What never ships

No corpus, notes, batons, letters, fieldnotes, exports, transcripts, sweeps, analyses, wins, or any content — ever. The product repo is born clean. Only method ships: code, engine prompts, CLI, docs.

---

# Adversarial challenge — blind spots (self-red-team, pass 1)

Attacking the spec. Ranked by threat. Resolutions/deferrals from the 2026-07-19 session annotated inline.

**A1 — "Passive capture" is a lie for ChatGPT, and partly for Codex.** ChatGPT has no hook — capture is export-only (manual, batched, delayed). "Passive, no filing" is a Claude-Code property, not a product property. → **DEFERRED: v1 is Claude Code only, where passive is true. Re-open when multi-surface lands, and never market "passive" for a surface that can't be.**

**A2 — Detached background work is host-dependent and fragile in Python.** Replicating "spawn a process that outlives the hook" in pure-stdlib Python (double-fork daemonization) is fiddly and host-varying; if a host kills children on hook-return, capture dies silently. → **Shrunk by v1 scope: only one host to get right, and the bash already proves it there. Port the working mechanism; fallback is synchronous capture with a visible "capturing… ~1 min" notice — a wait, never a silent loss.**

**A3 — Are we building a plugin architecture for N=2?** The two strategies share little capture machinery; a formal plugin API is premature abstraction. → **RESOLVED (§8): build the shared record contract, not a plugin SDK. Promote to a plugin API only when a third strategy needs it.**

**A4 — Per-session inference cost, at a stranger's scale.** Two model calls per session × global capture = a surprise bill. → **RESOLVED (§6): cost is always visible; High/Mid/Low/Local tiers make the fidelity/cost tradeoff an explicit, changeable choice. The per-capture two-pass cost is intrinsic to observed-capture (the price of independence); Local/self-scribe is the ~free escape.**

**A5 — Passive global capture is a secrets hazard.** Reading every session ingests credentials/keys/client data. → **RESOLVED (§7): default-deny secret scrubbing before store/observe. The engine refuses to record secrets; containment is not pushed onto the user (prudence & parsimony). Honest heuristic limit stated.**

**A6 — brew is macOS/Linux; broad audiences include Windows.** → **DEFERRED with multi-surface. v1 audience is macOS/Linux devs on Claude Code; state it honestly rather than imply universality.**

**A7 — Dispatch assumes the user has some model CLI.** A ChatGPT-only user has no `claude`/`codex` to run the observer/extractor. → **DEFERRED with multi-surface. In v1 the Claude Code user has `claude` by definition, so dispatch-via-CLI holds. Re-open for export-only surfaces.**

**A8 — "Never overwrite user data" vs. brew upgrades needs a test, not a promise.** brew replaces the cellar on upgrade; one stray default path inside it and pref #1 is violated silently. → **OPEN: enforce the boundary with a CI test, not discipline.**

**A9 — The blind-observer guarantee is only as blind as the process sandbox.** In Python calling a model CLI, blindness must be enforced (cwd, env, tool permissions) and tested — it's easy to regress. → **OPEN: add an explicit test that the observer cannot read the vault or criteria.**

**Not-yet-challenged (needs a second, independent pass):** the surface-adapter abstraction against a *real* Codex transcript; whether `init` can safely edit a `settings.json` a user also hand-edits; cross-surface concurrency; whether "record core as a library" survives a self-scribing agent running *inside* a live session.

## Edits

none
