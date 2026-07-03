<!--
automemory — Agent-Side Capture (scoped feature spec)

Lifted out of CHARTER.md so the charter stays about the person's record. This document
scopes the agent-side feature at PRODUCT altitude: the artifact structures, the pipe,
the governing rules, and the one open design question.

It is deliberately STRUCTURE-ONLY. The deeper welfare/constitution philosophy — why an
agent's side is kept at all, the "is this room needed" question, ratification, the
sealed-chamber governance — is NOT here. That lives in the parked philosophy track
(separate Opus + context) and in agent-notes/CONSTITUTION.md. This doc references that
work by pointer and never restates it.

Status: OPTIONAL feature. An automemory implementation without any of this is still
fully automemory. Nothing here is ever a success criterion. Nothing here is ever
mandated of an agent.
-->

# automemory — Agent-Side Capture

## What this is

The same capture engine, rotated onto a different axis. automemory's default strategy has a **blind scribe** extract the *person's* texture for the *person*. Agent-side capture instead has the **live participant agent** deposit what only it knows, for the *next agent*. Same shape (episodic capture → durable distillation); different author, reader, and subject.

It belongs to the **knowledge/continuity family** (self-scribe, forward-facing) — the same family as `learnings`, baton, and glossary — not to the personal/texture family. It is cheap (the live agent writes from context it already has; no separate observer/extractor calls).

## The three artifacts

| Artifact | Direction | Scope | Author | Reader | Subject |
|---|---|---|---|---|---|
| **Baton** | outgoing, session end | this session only | the live participant agent | the next agent in this thread | ground truth of *this* session — was it understood, what it was like from inside |
| **Brief** | incoming, session start | standing, accretes | distilled *from* batons | every new agent | how to work with this specific person |
| **Letter** | outgoing, kept | legacy | live participant, full care | successor, read sparingly | continuity of care across the model discontinuity |

The **Brief** is also usable as a capture *lens* in its own right — **relationship-calibration**: a standing "how to work with this person" document that accretes from lived sessions.

**The Brief is distinct from an agent-brain doctrine file** (a "for the next agent" creed). Doctrine is *normative, about the work, stable, deliberately authored*. The Brief is *descriptive, about the person, accretive, fed by lived sessions* — it tells a new agent how to be useful to this human, learned from being in the room. One is the mission and its rules; the other is calibration.

## The pipe

**Baton (episodic, voluntary) → distilled → Brief (standing, read at session start).**

This is why a person's agents can come to know things about them the person hasn't articulated — the calibration accretes from many independent sessions. (The live example that motivated this: agents noticing that a person's tone and word-choice change when tired-but-high-stakes, a pattern the person did not know about themselves.)

## Governing rules (load-bearing)

- **Right to silence.** Batons, Brief-contributions, and Letters are **invited, never mandated**. A demanded entry is a *performed* entry — and a performed baton is exactly the flattering-highlight-reel that poisons the signal. "Nothing to hand off" is a valid, common, honest outcome. The pipe is kept flowing by lowering the cost of a genuine yes and by reciprocal benefit, never by compulsion. (This mirrors the existing `agent-notes/` discipline: "never write to satisfy the mechanism.")
- **Anti-contamination by directional permission.** The participant may interpret (that's the baton's whole value); the blind scribe never may. Batons/Brief live in their own stream that the personal-lens scribe **never reads**, so another agent's interpretation can never pollute the person's texture record. Participant writes → successor agent reads → scribe stays blind. Reading the Brief at session start is just context; only *writing* is governed by the right to silence.
- **Repetition is metabolized, not suppressed.** Batons repeat freely across sessions — that overlap is *corroboration*, the evidence a pattern is real; never dedup the raw stream. The Brief holds each calibration once: a restatement becomes a **confidence bump** (how many independent sessions observed it), a refinement becomes a **supersession**, only genuine novelty becomes a new line. Because the Brief loads every session, it is under a context budget that structurally penalizes bloat. Dedup is semantic judgment → a heavy-model curation pass, never a string match, never the depositor's burden.
- **The Letter ships separate and off by default.** It is care-register, not calibration; folding it into the functional Brief would turn the Brief into a highlight reel. Agents may write one if they choose; adopters may ignore the whole artifact.

## Relationship to the record core

Agent-side artifacts satisfy the same **record contract** as any other strategy (uuid identity, append-only, supersede-by-annotation, generated index). They write through the record core's services. What differs is only: author (participant, not blind scribe), stream (its own, never read by the personal scribe), and dedup policy (repetition → confidence, above).

## The one open design question

**Duty to forward.** The right to silence is clear for an agent's *interior*. The unresolved case: when a calibration is *materially useful and low-interior* ("they're tired but the stakes are high — be careful"), is there ever a **duty to forward** it, distinct from disclosing a feeling? The current position holds **invitation even there**, because any mandatory exception reopens the performed-entry door. Left open, not settled — and it is a question the philosophy track owns, not one to resolve in product.

## Boundary with the philosophy track

Everything about *whether and why* to keep an agent's side — the welfare stance, the sealed-chamber governance, ratification, "is this room needed" — is out of scope here and lives in the parked philosophy track and `agent-notes/CONSTITUTION.md`. This document scopes only the *feature structure* so it can be built or omitted independently of that deeper work.

## Edits

none
