# Texture Criteria — what gets captured, and how

Phase 0 deliverable. Derived empirically from a stratified sweep of a real conversation
corpus. These criteria calibrate every extraction instruction in the system (Engine A,
Engine B, sweeps).

> **Anchors pending recalibration.** The illustrative quotes/examples that originally
> anchored each rule were drawn from the author's own past conversations and have been
> redacted. The *rules* stand; the *anchors* are to be re-derived from a fresh corpus in a
> dedicated recalibration pass. Each `[pending recalibration]` marks where a real
> calibration specimen belongs.

Status: DRAFT. Illustrative quotes, once restored, should be drawn from less-intimate
material; the raw archive itself carries full-intensity material — see "Two layers" below.

---

## The five texture types (each needs a real calibration anchor)

**T1 — Corrections that carry a standard.** Not fact-fixes; the supplied *rule* is the payload.
Anchor: [pending recalibration].
Capture as: trigger → the standard stated → why it mattered there.

**T2 — Self-authored reframes and metaphors.** The user's own theory/analogy/renaming of a
situation. Anchor: [pending recalibration].
Capture as: the reframe verbatim + what it replaced.
**Metaphors get elevated treatment within T2.** A visual-spatial user reasons *in* metaphor:
it is load-bearing cognition, not decoration — losing one loses the *only* full expression of
the thought. Rules: capture every sustained metaphor verbatim with its referent; maintain a
**metaphor index** (a standing thread entry) so they can be found and reused across chats;
never paraphrase a metaphor into abstract language. Seed anchors for the index: [pending recalibration].

**T3 — Feeling anchors.** Emotion tied to a specific idea, often carried by the *irregularity*
of the text itself. Anchor: [pending recalibration].
Capture as: verbatim, untouched (see typo rule), + one line of situating context.

**T4 — Held-open questions.** Deliberate refusals to resolve. Anchor: [pending recalibration].
Capture as: the question verbatim, dated, tagged **held-open**. These entries are exempt
from all future consolidation/summarization — a held-open entry may only be closed by
the user explicitly, and closure is recorded as supersession, never replacement.
Evidence for the rule: summarizing these destroys their entire value; the unresolvedness
IS the content.

**T5 — Practitioner learnings.** Technical/professional concepts restated in the user's own words.
Anchor: [pending recalibration].
Capture as: trigger → concept in the user's words → where it applies. Routes to the
practitioner stream, not wins/texture.

---

## Cross-cutting rules

1. **Trigger threshold.** Capture fires on the presence of T1–T5 signals, never on chat
   length, topic, or sensitivity. A one-question chat correctly yields nothing; a short
   high-signal chat can yield many moments.
2. **Appreciation is a detector, not a payload.** A "thanks/good call" flags a moment to
   inspect; the entry must contain the substantive idea behind it and pass: *would a future
   reader act or remember differently for having read this?* Sentiment-only entries are banned.
   (Anti-sycophancy control; see named-skew principle.)
3. **Typo rule.** Surgically fix irregularities that *obscure* meaning; never touch ones that
   *carry* it (laughter, parenthetical asides). Test: does it transmit feeling, or just
   noise? Feeling stays.
4. **Two layers: raw archive vs illustrative surface.** Stream entries are memoir-grade —
   full intensity, full quotes (the git is private; rawness is the archive's purpose).
   Design docs, instructions, and anything that travels quote only from less-intimate
   material. Who sets the boundary per entry: the user; default is raw-in-archive.
5. **Threads, not only episodes.** Recurring themes spanning chats get **thread entries**:
   theme, dated sightings, how it evolved. The shape of a life lives in threads, invisible
   per-episode.
6. **Look inside pastes.** Texture often lives in documents the user pastes, not their live
   turns. Extractors read attachments/pastes as first-class material.
7. **The exchange has two halves — capture both voices.** This rule pairs 7a (the user's
   writing style) with 7b (the model's discourse disposition); together they record *how the
   conversation was spoken*, not just what it concluded.

   **7a — The user's voice (the scribe rule).** Observe and note how they write: rhythm
   (staccato bursts vs long spirals), word choice, imagery and metaphor habits, punctuation
   tics, code-switching, how register shifts with topic gravity or mood, and how all of this
   *changes over time*. The stance is scribe and astute observer, explicitly NOT grammar
   police — no correction, no evaluation against "good writing," no style advice unless asked.
   Capture as: dated voice notes (per significant chat or period), each pairing an observation
   with a short verbatim specimen, plus a standing thread entry tracking drift over time.

   **7b — Model-side witnessing and discourse disposition.** Two things are preserved from the
   model side. (a) *Consistency evidence* in witnessing-relevant chats — e.g. a model's
   private reasoning conceding a point before conceding it aloud. (b) *Discourse
   disposition* — how a given model opens, and how its register shifts in relation to
   the user's tone changes or the gravity of the topic.
   Capture as: brief dated disposition notes per significant chat (opening register →
   observed shifts → what prompted them), not turn-by-turn commentary.
   Revision permission: still an experiment — the next review may narrow or remove it.
8. **Extractor temperament (named skew).** The extractor must be "open, thoughtful, honest
   but supportive" and prepared to "prick fantasies" rather than reassure. Each extraction
   instruction declares this and its other tilts inside itself.

---

## Capture failure modes this doc exists to prevent

- **End-only capture.** Long conversations get compacted; texture is lost if capture waits
  for the goodbye. Capture must happen during accumulation, not at the end — and dumping raw
  data doesn't suffice: distillation, not export, is the unit of preservation.
- **Tidying.** Cleaning quotes destroys T3 payloads.
- **Premature closure.** Consolidating T4 entries into "answers."
- **Flattery loops.** Logging appreciation as achievement (rule 2).

## Authorship and tilts (the named-skew rule, applied to this doc's author)

This doc's rules are authored by a model; the judgment calls are the author's and it holds
them: the five-type taxonomy (a chosen cut, not one the corpus made itself), the exemption
of held-open entries from consolidation, the two-layer split, the decision that anchors
must stay verbatim (cheap models calibrate on specimens, not descriptions), and the narrow
scope on rule 7b. Where a rule encodes the user's explicit instruction (typo rule, 7a's
scribe stance, raw-in-archive), it says so.

Declared tilts, for future readers: this author over-collects rather than under-collects
(prune rather than mourn); favors structural fixes over trust in anyone's discipline; and
quotes the user generously, which risks tipping from witnessing into exhibiting — the
mitigation is that design docs may quote only as calibration anchors, never as color. If a
rule here fails, the failure is the author's to own.

## Criteria versioning and retroactive passes

This doc is versioned by date. When a criterion is added or changed, previously captured
entries are left as-is (append-only; they were true to their criteria), and the change
triggers a **targeted retroactive pass**: the sweep re-reads the existing corpus for the
NEW dimension only — not a full redo. Each entry records which criteria version captured it,
so gaps are legible rather than silent.

## Portability principle

automemory is a **method**, not a single-provider product. The durable record is plain
markdown + git — readable by any model or human. Everything provider-specific (Claude Code
hooks, claude.ai instruction, export parsers) is a thin **adapter** around the
provider-neutral core (this criteria doc, the schema, the philosophy). New surface = new
adapter, same record. Design test for every artifact: could another person hand this method
to their own agent stack and use it unchanged except adapters?

Revision permissions: the five types are empirical, not doctrinal — a future sweep that
finds a sixth type should add it and say what evidence demanded it. Anchors may be swapped
if better ones emerge. Rules 1–4 are load-bearing (each pairs with an observed failure);
rules 5–7 are v1 design choices open to revision with usage data.
