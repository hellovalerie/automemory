# Record Core — the shared envelope (DRAFT)

Status: **envelope SETTLED**, 2026-08-11 (contents + adapters still to build). This is the
redesign of the record substrate that both engines write into. It supersedes the
record-format parts of `SCHEMA.md` once contents/adapters land.

> Refines the `product/BUILD-SPEC.md` §2 record contract. "Engine A" = the spec's
> `learnings` strategy; "Engine B" = the spec's `automemory-capture`. See `ENGINE-A.md`
> header for the full naming map.
Provider-neutral by construction: plain markdown + git, nothing provider-specific in the
core.

## Principle: envelope vs. contents

Both engines produce a **record**. What they share is not a capture mechanism — it's the
*envelope*: identity, provenance, lifecycle, the edits ledger, the index. What goes
*inside* the envelope (types, body shape, texture rules) is each engine's own business,
and the core never opens the letter.

- **Core** = the envelope (below). The load-bearing walls.
- **Engine** = the contents (its own fields + body). The interior fit-out.
- **Adapter** = anything provider-specific (transcript locations, formats, watermarks).

Keep the core minimal. A foundation you over-decorate is one every occupant has to fight.

## The envelope (the shared contract)

```markdown
---
id:            <stable unique key — never displayed, never reused>
source:        <provider>:<session-ref>   # provenance pointer (opaque; adapter resolves)
model:         <witness/author model + version, when known>   # which colleague drew this
occurred:      YYYY-MM-DD                  # when the thing happened
captured:      <ISO-8601>                  # when written down
title:         <human label — display only, never a key>
status:        open | superseded
superseded_by: <id>                        # OPTIONAL; append-only annotation, never delete
subject:       <linking key>               # OPTIONAL; groups related entries into a timeline (A) / thread (B)
---

<body — the engine's own shape; the core is agnostic to it>

## Edits
verbatim → as printed        # or: none   (OPTIONAL; see below)
```

### Field notes
- **`id`** — the artifact's stable key. Identity, not display. (Slug, ULID, or uuid — TBD.)
- **`source`** — the *receipt pointer*: `<provider>:<session-ref>`, opaque. The core stores
  the address; the adapter knows how to resolve it (e.g. claude-code adapter:
  session-id → `~/.claude/projects/<slug>/<id>.jsonl`, or an archived copy). Keeping an
  archived *copy* of the raw transcript is an OPTIONAL engine/adapter policy (Engine B,
  memoir-grade, wants it; Engine A, facts, usually doesn't), never a core mandate.
- **`model`** — which AI witnessed/authored. Core provenance: knowing *which* colleague
  (Claude, Gemini, Grok, …) drew the record is central to the thesis.
- **`occurred` / `captured`** — when it happened vs. when written. Two timestamps, distinct.
- **`title`** — human orientation only. Never used as a key or for lookup.

## What is NOT in the core (moves out)

- **Engine-specific:** `kind` / `type` vocabularies, body structure, `texture_criteria_version`,
  the `## Unverified` / curator apparatus (Engine B), the distilled-moment template (Engine B).
- **Adapter-specific:** raw transcript location, the update/line watermark, transcript
  format — all the provider mechanics. Kept out of the envelope.

## Rendered in practice

Same envelope, two engines. The top block is byte-identical in shape; below the `---` each
engine speaks its own language.

**Engine A — a handover (facts, self-authored):**
```markdown
---
id: 2026-08-05-checkout-flow-handover
source: claude-code:9f2c1a44-3b7e-4d21-a0c8-1e5f7b2d9a10
model: opus-4.8
occurred: 2026-08-05
captured: 2026-08-05T14:22:07Z
title: Checkout flow — handover
status: open
subject: checkout
---
kind: handover

**State:** guest checkout works end-to-end; the saved-card path is stubbed.
**Gotcha:** cart total is recomputed client-side and the server trusts it — don't ship until the server recomputes.
**Next:** implement saved-card path; move total calc server-side; add the integration test.

## Edits
none
```

**Engine B — a texture reframe (self-portrait, blind scribe):**
```markdown
---
id: 2026-08-05-the-diagram-was-lying
source: claude-code:9f2c1a44-3b7e-4d21-a0c8-1e5f7b2d9a10
model: fable-5
occurred: 2026-08-05
captured: 2026-08-05T14:41:00Z
title: "the diagram was lying to me"
status: open
subject: trusting-docs-over-artifacts
---
type: reframe
texture_criteria_version: 2026-08-05

Trigger: an architecture diagram that didn't match the running system.
Reframe (verbatim): "the diagram was lying to me — I was debugging the drawing, not the code."
Why it mattered: named the failure mode of trusting the doc over the artifact.

## Edits
none
```

## Design decisions settled

1. **The receipt is a pointer, not a copy.** `source: provider:session-ref`, adapter-resolved.
   Archiving raw = optional policy, not core.
2. **Two axes, never conflated:**
   - **Correctness axis** — *this account was wrong/replaced.* → `superseded_by` (strict,
     core-**optional**; Engine B uses it, Engine A rarely).
   - **Currency axis** — *this account was true, but the work moved on.* → NOT supersession.
     It's a **time-ordered log**: entries accrue, old snapshots stay true as history, and
     "current state" = the newest entry for a `subject`. Nothing extra to capture; it falls
     out of chronology. Marking a true-but-old snapshot `superseded_by` would defame it —
     forbidden.
3. **`superseded_by` and `## Edits` are core-OPTIONAL** — provenance discipline for any
   writer, but Engine A (facts) rarely corrects a snapshot; it writes a fresh one.

4. **One shared `subject` key** (resolved 2026-08-11). A single linking key serves both
   Engine A's state-timelines and Engine B's recurring threads — one field, two uses, no
   separate relations (N=2; don't over-abstract).
   - **Engine A** queries it for *current state*: the newest entry for a `subject` is the truth now.
   - **Engine B** queries it for *the whole thread*: all entries for a `subject`, in order, are its evolution.
   Same field, different query — the schema doesn't need to know which. Subjects are
   free-text keys scoped by their engine's usage; no global registry.

## Next

Envelope settled. Build **Engine A** lean on this core.
