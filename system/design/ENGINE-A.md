# Engine A — the facts engine (DRAFT)

Status: **shape SETTLED (build pending)**, 2026-08-11. The lean, self-authored,
provider-agnostic engine that ships first. Writes into the shared record core
(`RECORD-CORE.md`); provider hooks are an adapter.

> **Naming reconciliation with `product/BUILD-SPEC.md`:** what these design docs call
> **Engine A** is the spec's **`learnings`** strategy (§4 self-scribe, §8 knowledge/continuity
> family, the §6 **Local** tier). What we call **Engine B** is the spec's
> **`automemory-capture`** (observe→extract). This doc is the detailed refinement of §4;
> `RECORD-CORE.md` refines the §2 record contract. Where they conflict, resolve explicitly
> (noting date), don't silently diverge.

## What it is

A frictionless handover engine. The session's own agent, as its closing act, leaves behind
a factual session record; the next session reads the relevant prior records back in and
starts oriented. Facts tolerate self-authoring (the in-session agent knows the state best
and has little incentive to distort it), so no blind scribe — cheap and portable.

## What it produces — one record per session, three parts

One artifact (a **session record**), no `kind` vocabulary. Body:

- **## Done** *(minimum — required)* — what was actually done this session.
- **## Learnings & Gotchas** *(when present)* — traps found, things now known.
- **## Outstanding** *(when present)* — what's left, the next move.

Rendered:

```markdown
---
id: 2026-08-11-checkout-flow
source: claude-code:9f2c1a44-3b7e-4d21-a0c8-1e5f7b2d9a10
model: opus-4.8
occurred: 2026-08-11
captured: 2026-08-11T14:22:07Z
title: Checkout flow — session record
status: open
subject: checkout
---
## Done
- guest checkout wired end-to-end; saved-card path stubbed.

## Learnings & Gotchas
- cart total is recomputed client-side and the server trusts it — recompute server-side before shipping.

## Outstanding
- implement saved-card path; move total calc server-side; add the integration test.
```

## When it fires (bidirectional)

> **CORRECTED 2026-08-12 by hook-mechanics research (claude-code-guide).** The original
> "SessionEnd → the live agent writes" is **not a real mechanism**: SessionEnd fires *after*
> the agent has stopped — too late for it to author. The only way to make the *live* agent
> author is a **`Stop` hook** that blocks with `decision: block` + a `reason` instruction.
> But `Stop` fires every turn (not at end), needs a throttle + loop-guard, costs an extra
> turn when it fires, and **cannot guarantee** compliance (hooks enforce rules, not agency).
> There is **no native periodic timer** (~30 min must be an external scheduler). The write
> trigger is therefore an OPEN FORK — see "Write-trigger fork" below. SessionStart injection
> (read/handover-in) is confirmed working.

- **Read (handover-in) — SessionStart hook injects the map** (INDEX) into the starting
  session so it begins oriented. Orientation rides the *hook*, not a line in the user's
  CLAUDE.md. ✅ confirmed viable.
- **Write — OPEN FORK** (below). The clean "author at session end" event does not exist.

## Write trigger — RESOLVED 2026-08-12

**Option 2 (non-disruptive live fast-path) + SessionStart reconciler (detached guarantee).**
- **Fast path:** at a natural checkpoint the live agent files its own record (a plain Write,
  woven into finishing work — no Stop-block meta-loop, so none of Option 1's disruption).
  Cheap, high-quality, but not guaranteed.
- **Backstop:** the next SessionStart's reconciler finds any session that ended without a
  record, reads its leftover transcript, and backfills it (detached cheap pass). Guaranteed
  for any session followed by another session in that project.
- Purity note: for an abandoned/crashed session, detached backfill is the *only* possibility
  anyway (the agent is gone) — so the fast path handles what *can* be self-authored, the
  reconciler handles what structurally can't. No self-authored quality is lost that could
  have existed.

### The fork it resolved (kept for the record)

The hook model has no event meaning "session is ending AND the agent can still act" — those
are mutually exclusive. The choice was:

- **Option 1 — Live-agent checkpoint (Stop-block + throttle).** ❌ **KILLED 2026-08-12 on
  the user's lived experience** (they ran this in the old system). The disruption isn't the
  writing — it's the visible *meta-loop*: agent tries to stop → hook blocks → agent "talks to
  the Stop hook" → reconsiders → authors or not, all on stage, mid-discussion. Disorienting
  in long sessions. Consequence: Stop-block was the ONLY way to *force* the live agent, so
  killing it kills *forced* live-authoring entirely. What survives is *unforced*
  live-authoring — Option 2 — which is non-disruptive precisely because it has no Stop-block
  meta-loop.
- **Option 2 — Disciplined self-author (SessionStart orientation).** The start hook reminds
  the agent that authoring a record is part of the work; the agent authors at a stopping
  point of its own judgment. Zero interruption, respects agency, no heuristics. Cost: NOT
  guaranteed — sessions can end with no record. Fine for a personal discipline; risky for a
  product promising automatic capture.
- **Option 3 — Detached fact-summarizer at SessionEnd.** Reliable, non-interrupting,
  guaranteed to fire. But it is a *separate cheap reader* of the transcript, NOT the live
  agent — loses the "cheap/context-loaded" advantage, re-introduces one model call, and
  blurs the A-vs-B distinction (it becomes "detached facts pass" vs B's "detached blind
  texture pass"). Note: "facts tolerate self-authoring" was chosen for cheapness/simplicity,
  not because facts *require* the live agent — so a detached fact pass is still acceptable
  on fidelity grounds; it just isn't free.

## Who authors

The session's own agent (self-authored). Cheap (reuses loaded context), portable (no
separate scribe), fidelity-adequate (facts, not self-portrait).

## Provider-agnostic

The record + the three-part logic are provider-neutral. The SessionStart/SessionEnd hooks
are a Claude Code **adapter**; another provider swaps the adapter, same record.

## Settled sub-questions (2026-08-11)

1. **`subject` = what this episode is about** — an episode label, NOT the continuity handle.
   Continuity does not live in `subject`; it lives in the map (see below). So `subject` is
   allowed to be loose: the agent *proposes* it, the user *confirms* it, and it is
   *re-confirmed if the work shifts shape* — coherence via a human in the loop, not
   auto-guessing. (Two-tier correction, 2026-08-11: earlier this doc made `subject` carry
   continuity; it doesn't — the map does. The session UUID remains the file key.)
2. **Write-gate = "Done is the minimum," self-enforced.** Not a quantitative threshold —
   substance isn't quantity (a 2-turn gotcha outweighs a 50-turn thrash; numeric gates
   mis-fire both ways). The rule: the agent writes a record iff it can honestly fill
   `## Done` with a concrete, durable state-change a future session would need (code/artifact
   changed, decision made, gotcha found). Can't fill Done → no record. Checkable, no tuning.
3. **Read-back = newest record for the subject** (current state), loaded by default; fuller
   timeline available on demand.

## Concurrency — per-session file, no races

- **File identity = the session UUID.** One file per session → concurrent sessions on the
  same project write *different files* → no write collision. Retires the old in-flight lock
  (`/tmp/automemory-inflight`): no lock needed when writers never share a file.
- **Two jobs, cleanly split:** the **UUID isolates writes**; the **`subject` threads reads**
  (gather all files with `subject: X`, order by time → the state timeline; newest = current).
- Filename carries date + subject + short-uuid for human legibility; the UUID guarantees
  uniqueness.

## The map & handover-in (resolved 2026-08-11)

- **The map = `INDEX.md`** — the consolidated lay-of-the-land, one line per episode, ordered
  newest-first (newest-per-`subject` = current state). "Consolidation" is just INDEX
  maintenance: the agent appends/updates its INDEX line in the same write as its record — no
  separate narrative-compression pass.
- **Handover-in rides the SessionStart hook**, which injects the map (or a pointer to it)
  into the starting session — engine-owned, so it cannot be rinsed by a user editing their
  CLAUDE.md. The robust design does **not** write to the user's CLAUDE.md at all.
- **Resilience (worst-case first):**
  1. Capture never depends on orientation — the write-hook fires independently; records
     survive even if nothing orients the reader.
  2. Orientation rides the hook, not CLAUDE.md — a rinsed CLAUDE.md line changes nothing.
  3. The CLAUDE.md line is optional redundancy (readability, or providers with no
     SessionStart hook); if used, the hook self-heals it (re-inserts when missing).
  Net: a rinsed line costs at most one unoriented session on a hookless provider — records
  stay intact. On Claude Code it costs nothing.

## The reconciler (MVP spine — the one real build)

Fires at **SessionStart**. Three jobs, in order:

1. **Handover-in (read):** inject the map (INDEX / newest-per-`subject`) into the starting
   session so it begins oriented. ✅ confirmed viable.
2. **Fast-path reminder (inject):** remind the agent that at a natural checkpoint it should
   file its own record (Done / Learnings&Gotchas / Outstanding; Done required, else nothing).
   Non-forcing — just context.
3. **Backfill (guarantee):** find prior sessions in scope that have a transcript with
   substance but **no record yet** (by session UUID), and author a facts record for each
   from the transcript (detached cheap-model pass, per BUILD-SPEC §5 dispatch-via-CLI),
   applying the same "Done is the minimum" gate. Write via the record core; update INDEX.

**Idempotency (core logic):** backfill key is the session UUID. A session that already has a
record (fast-path wrote one) is skipped — no doubles. "Find sessions with no record" is the
whole reconcile.

### MVP defaults (flag to revisit, don't over-build now)

- **Scope: current project only** (the starting session's cwd). Global reconcile is heavier;
  per-project is what you're about to need. Revisit if cross-project matters.
- **Sync vs detached:** start **synchronous with a visible "reconciling… ~Ns" notice**
  (BUILD-SPEC A2 fallback) — avoids the fragile double-fork daemonization for MVP. Optimize
  to detached later if the wait hurts.
- **Known gap — the final session:** a project's very last session (no later session to
  trigger reconcile) is backfilled only if it self-authored. Documented limitation for MVP;
  a SessionEnd detached kick can close it later (SessionEnd is fine for *detached* work, just
  not for live authoring).
- **Deferred: update-append by watermark.** "Record exists but transcript grew since" →
  update the record. Post-MVP; MVP does "no record → backfill" only.

## Resolved before code (2026-08-12)

- **Records directory = `agent-records/`.** Self-authored session records live here (distinct
  from Engine B's future blind `fieldnotes/`). Note: near-namespace to the future annexed
  `agent-notes/` (Engine B reflections) — kept distinct deliberately; watch for confusion.
- **Secret-scrub = minimal heuristic pass NOW** (honors BUILD-SPEC §7 default-deny at MVP).
  Applied on the *backfill* read: `transcript → [redact] → author`. Heuristic (known
  key/token formats, high-entropy strings), with §7's honest "best-effort, not a guarantee"
  limit stated. The fast path authors prose directly (no raw re-read) so the redaction bites
  specifically at backfill.

## Doc debt

`FOR-THE-NEXT-CLAUDE.md` currently reads as Engine B's creed (two subjects, agent-notes,
blind scribe). The INDEX-as-map principle is shared, but the basic (Engine A) product needs
its own creed version. Deferred.
