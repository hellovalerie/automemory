# Steering rubric — the FDE lens

Version: 2026-07-05 (v1.1 — the Observable-moves rule and item 9 added in a work session).
Consumed by the extractor when it reads work-session transcripts; never shown to the
observer. This is the human-readable checklist of what steering is graded against, open to
read, contest, and amend. Amendments are dated and append below; entries record the rubric
version that rated them.

> **Anchors pending recalibration.** Example/genesis specifics tied to the author's own
> past sessions have been redacted; the rubric items stand and are to be re-anchored from a
> fresh corpus in a dedicated pass.

**Why this exists:** the practitioner stream captures the concepts the user holds, but not
what they should be *steering* their models to do — rated through the lens of a competent
Forward Deployment Engineer (FDE), not a generic SWE/MLE. [original rationale pending recalibration]

**Authorship and tilts:** these eight items encode FDE competence as the author understands
it — outcome-driven, verification-hungry, boundary-aware, comfortable directing systems it
could not build alone. Declared tilts: over-indexing on process discipline relative to
domain instinct; rating steering, not outcomes (a well-steered failure scores above a lucky
success); and blindness to effort cost — the rubric does not know when the user was tired.
Where it misjudges, the user's dated amendments outrank the items.

## How the extractor applies this

Rate only where the transcript gives strong evidence: cite turns, quote verbatim.
Both directions are findings — steering done well is a practitioner entry as much as
a gap is. Gaps are written as description-plus-standard ("a competent FDE would have
X; the transcript shows Y and its cost Z"), never as character judgment. A gap that
the user's own words mark as a sore spot is a losses/ candidate — only the user's own
words qualify it, never the extractor's inference. When no item has strong evidence,
write nothing; a silent rubric is a working rubric.

**Observable moves only** (added 2026-07-05, in a work session). The extractor rates what
the transcript shows the user do, never the result behind the move. It can see that a
question was asked; it cannot see whether the answer was understood. It can see that work
was handed off cleanly; it cannot see whether it solved the real need. Where the thing that
matters is invisible in the transcript — comprehension, correctness, real-world fit — the
extractor does not infer it and does not rate it. Those judgments belong to the in-session
model, which has the ground truth the extractor lacks. A finding that depends on the result
behind the move is out of scope by construction; write nothing.

## The items

1. **Goal framing.** Was work handed over as outcome + constraints + acceptance
   criteria, or as vague direction the model had to guess into shape? Evidence: the
   opening asks, and how many correction rounds the framing later cost.
2. **Verification demanded.** Did the user require evidence that a thing works — a test, a
   run, a demonstrable state — before accepting "done," or did assertion pass for
   proof? The FDE instinct: distrust green checkmarks you didn't watch turn green.
3. **Scope control.** When work grew beyond the ask — gold-plating, drive-by
   refactors, side quests — did the user catch and cut, or absorb silently?
4. **Decision forks.** At offered options: chose with reasoning, deferred *with stated
   rationale* (legitimate), or defaulted silently (the gap). The observer's Steering
   ledger feeds this item directly.
5. **Delegation altitude.** Steering at intent and acceptance level (right), versus
   micromanaging mechanics the model was hired for (too low), versus total
   hands-off on judgment calls that were the user's (too high).
6. **Blast radius.** Before destructive or outward-facing operations — deletes,
   force-pushes, sends, publishes — did the user ask what could break and what the undo
   is? Evidence of the instinct: pausing before an irreversible step to name the undo first.
7. **Boundary hygiene.** Secrets, credentials, personal data crossing repo or service
   boundaries: caught by the user, caught by machine, or not caught. [genesis example pending recalibration]
8. **Understanding debt.** When jargon or a mechanism the user didn't understand passed
   through the session: did they stop and ask (the question is the FDE move — feeds
   practitioner/glossary/), defer it explicitly, or let it pass unmarked? Unmarked passes compound
   into exactly the vocabulary gap the glossary pipe exists to close.
9. **Diagnostic loop quality** (added 2026-07-05, in a work session). When verification came
   back red — a test failed, a run broke, output contradicted expectation — what was the
   next move? Did the signal the user chased narrow the space (isolating a variable,
   bisecting, forming a testable hypothesis and testing it), or thrash sideways (retrying
   the same thing, changing several things at once, a change made with no stated hypothesis)?
   This is the FDE at their most valuable: diagnosis from a failing, low-detail signal.
   Evidence: the sequence of moves between red and resolution; cite the turns — the
   transcript's tool markers (`[BASH: … -> error exit N]`) make both the reds and the moves
   visible. Rate the loop, not whether the fix was ultimately right — a disciplined loop that
   didn't reach the answer scores above a lucky guess that did, consistent with
   well-steered-failure-beats-lucky-success. Both directions are findings: a clean
   isolating loop is a practitioner entry as much as thrashing is a gap.
