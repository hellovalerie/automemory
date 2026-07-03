# Schema — how the record is structured

Phase 1 deliverable, 2026-07-05. Companion to `TEXTURE-CRITERIA.md` (what gets captured);
this doc defines where it lives and what an entry looks like. Provider-neutral: plain
markdown + git, per the portability principle.

## Streams (top-level directories)

| Stream            | Holds                                                                                                       | Travels?               |
| ----------------- | ----------------------------------------------------------------------------------------------------------- | ---------------------- |
| `ops/sweeps/`     | Sweep run logs: what was read, what was captured, what was skipped (no silent gaps)                         | Private                |
| `system/design/`  | The method itself (criteria, schema, plan, steering rubric)                                                 | The generalizable part |
| `system/engine/`  | Agent prompts (observer, extractor, unverified) that run the capture pipeline (added 2026-07-12)            | The generalizable part |
| `system/scripts/` | Capture pipeline automation — hooks, worker, lint (added 2026-07-12)                                        | The generalizable part |
| `ops/exports/`    | Raw transcript archive (insurance vs. Claude Code's own cleanup) (added 2026-07-12)                         | Private — stays here   |
| `ops/logs/`       | Capture failure records and the scribe drawer (added 2026-07-12)                                            | Private — stays here   |

## Entry format

One entry per file: `<stream>/YYYY-MM-DD-short-slug.md`. Threads are standing files
(no date prefix); they accumulate dated sightings instead.

```markdown
---
date: 2026-07-05            # when the moment happened (not when captured)
captured: 2026-07-05        # when written down
type: win | held-open | practitioner | thread | voice | disposition
source: "claude-code: A Sample Chat" | conversation name
texture_criteria_version: 2026-07-05   # version date of TEXTURE-CRITERIA.md under which the entry was captured
status: open                # open | held-open | superseded | resolved 
superseded_by:              # filename, if status: superseded (never delete; annotate)
---

The distilled moment: trigger → the thing itself → why it mattered. Verbatim quotes per
the typo rule. Short — a future reader should re-enter the moment in under a minute.
```

Rules inherited from the criteria doc: append-only; supersession is annotation plus a
forward link, never deletion; 
## Field-note updates 

Field notes are one file per conversation, forever. When a conversation grows after its
note was written, the sweep appends to the SAME file: a horizontal rule, then a dated
session header, then the new notes —

```markdown
---
## Update — 2026-07-31 21:45–03:15 SGT · turns 75–98
```

Conversations are keyed by their export UUID (in frontmatter), not by title: renaming a
chat must never fork its field note. On rename, the filename may be updated to match,
with the old name noted in the Edits ledger.

### Identity block (frontmatter — issue #9)

Every field note carries YAML frontmatter; the `uuid` is its identity, the filename is
only a display label (rename survival: capture always looks notes up by frontmatter
uuid, never by filename):

```yaml
---
uuid: <session uuid (claude-code) or conversation uuid (claude-ai)>
source: claude-code | claude-ai
date: YYYY-MM-DD                # when the conversation happened
raw: <repo-relative path to the raw transcript; claude-ai notes use a
      corpus-relative path prefixed "corpus:" (the automemory-corpus repo)>
captured: <ISO-8601 of last capture>
lines: <line count of the raw transcript at last capture — the update watermark>
title: <the session/chat title — Claude Code's session title records, or the
        claude.ai chat name incl. the owner's renames>
model: <the assistant model the transcript evidences, versioned, e.g. opus-4.8 | fable-5 | haiku-4.5>
---
```

`title:` and `model:` This block renders as a properties table as the
human-readable face of the note. UUID remains the only key automemory itself uses;
title and model are never used for lookup or consolidation.

`lines:` is omitted for claude-ai notes: the corpus markdown split has no meaningful
line-count watermark

### Update-append mechanics (issue #9 — wholesale regeneration is BANNED)

Field notes accumulate human/merge edits and an Edits ledger, so a re-capture never
rewrites an existing note. Instead:

- Delta = raw transcript lines beyond the frontmatter `lines:` watermark. Empty delta →
  only the raw file copy is refreshed and `captured:` touched.
- The observer is re-run with its normal instruction, its OWN prior note body (for
  continuity of style and turn-numbering — its own product, so blindness holds; it still
  sees no repo context), and the full transcript with the new region marked. It produces
  ONLY an addendum, ending with a `### Edits (this update)` subsection (`none` if empty).
- The addendum is appended per the format above (`---`, then `## Update — <capture
  date>`; finer time/turn detail lives in the addendum body), inserted BEFORE the
  top-level `## Edits` section — **Edits stays last**, always.
- `captured:` and `lines:` are then advanced. The raw file in
  `ops/exports/claude-code-export/raw/` is overwritten with the fuller transcript under the
  SAME filename (raw is verbatim source — safe to replace; the filename's date prefix
  stays the first capture's date).
- Concurrency: workers hold a per-uuid in-flight marker + lock under
  `/tmp/automemory-inflight/` (shared with the reconciler, stale after 1h) so two
  captures of the same session never interleave.
## Unverified section (added 2026-07-06, issue #11)

Rigor drops what it cannot anchor; without a pressure valve, every drop is silent loss.
Any **curator agent** that touches an existing artifact — merge, sweep, regeneration,
consolidation — may record material it suspects is worth keeping but cannot anchor to a
transcript turn or source document, under a top-level `## Unverified` section. The blind
first-pass observer never writes one: everything it produces is anchored by construction
(the transcript is its entire world). Curator prompts include
`system/engine/prompts/unverified.md` verbatim.

- Placement: immediately before `## Edits` (**Edits stays last, always**). Update-appends
  insert before `## Unverified` when present, else before `## Edits`.
- One dated bullet per item:
  `- (YYYY-MM-DD, <agent role — model, e.g. merge agent — Haiku 4.5>) <the item, quoted exactly as found> — suspicion source: <where it surfaced and why it could not be anchored>`

## Semantic 
**This is a **container for permanent honest uncertainty, not a review queue**.Verification power decays — even the human arbiter's, and emotional salience distorts it — so entries carry no expectation of resolution; unresolved forever is an acceptable end state.
- Append-only. Agents never edit or delete an entry; if a later pass finds an anchor, it
  appends a dated follow-up bullet referencing the earlier one (`anchor found: turn N`).
  Only the user moves material into the body (with an Edits ledger line) or marks an entry
  resolved.
- An agent that drops unanchorable material with neither an Unverified entry nor an
  explicit line in its run log is defective — same standard as unledgered edits.

## Index

`INDEX.md` at repo root: one line per entry (`- [title](path) — hook`), newest first per
stream. This is the cheap-to-load surface — a session reads INDEX.md (a few hundred
tokens) and pulls full entries only on demand. Extractors append the index line in the
same commit as the entry.

## Edits ledger 

Any automemory-produced artifact that quotes or reproduces source text — field notes,
entries, thread sightings; everything except explicit throwaways — ends with an
`## Edits` section: one line per surgical edit, `verbatim → as printed`, or `none`.
The ledger is what licenses editing at all: agents are freed to fix what obscures
because every fix is visible. An artifact with unledgered edits is defective.

## Drift prevention 

Many documents drift apart unless each fact has exactly one home. The policy:

1. **One source of truth per fact; routers hold no content.** Format rules live HERE. CLAUDE.md and INDEX.md only route. A doc restating another doc's rule — rather
   than linking it — is a defect to fix on sight.
2. **Supersession is annotation, never silent rewrite or deletion**. Stale text must name its successor with a date, so a reader landing on the old
   passage is redirected instead of misled.
3. **Mechanize the fastest-rotting invariants.** What can be checked by script is
   checked by `system/scripts/vault-lint.sh` (orphans, dead links, frontmatter, Edits ledger,
   section ordering). A new invariant gets a new lint check whenever it is checkable.
4. **Version what calibrates.** TEXTURE-CRITERIA and STEERING-RUBRIC carry version
   dates; every entry stamps the version it was captured under; a criteria change
   triggers a targeted retro sweep for that dimension only — never a rewrite.
5. **Periodic staleness sweep of the instruction layer** (cheap readers flag against
   current truth; a frontier model judges and applies; every change annotated). 