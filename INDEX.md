# automemory — index

The map of the system: one line per design doc, newest-first. Derived by hand from
the files themselves — do not edit a line without having read the file it points at.

This is the **design/knowledge map**. It is not the **session-record map** — that one
is per-project and lives at `agent-records/INDEX.md`, regenerated from frontmatter
by `record.rebuild_index()`. Engine A moved the record map (see
`system/design/ENGINE-A.md`, "Storage & backfill") but this map stays at the root.

## The method

- [SCHEMA.md](system/design/SCHEMA.md) — the entry contract: envelope fields, `## Edits`
  ledger, supersession, drift prevention. The format every artifact must satisfy.
- [TEXTURE-CRITERIA.md](system/design/TEXTURE-CRITERIA.md) — what gets captured: the
  taxonomy of signal types, the typo rule, scope and privacy boundaries. Where
  provider-specific adapters are named as adapters.
- [RECORD-CORE.md](system/design/RECORD-CORE.md) — the shared envelope both engines
  write into. Provider-neutral by construction; defines what an *adapter* is.
- [STEERING-RUBRIC.md](system/design/STEERING-RUBRIC.md) — how an extract is judged.
  Provider-neutral.

## The engines

- [ENGINE-A.md](system/design/ENGINE-A.md) — the facts engine (`learnings`). The live
  one: the session's own agent authors a factual record, and a SessionStart reconciler
  backfills what was missed. Includes the write-trigger fork and its resolution.
- [MVP-SCOPE.md](system/design/MVP-SCOPE.md) — the bounded first build, its resume
  point, and build status. Engine B is explicitly out of the MVP slice.

## Naming

`Engine A` = BUILD-SPEC §4 `learnings` (self-scribe, facts).
`Engine B` = BUILD-SPEC §4 `automemory-capture` (blind observer → extractor, texture).
Note `system/scripts/capture-worker.sh` still self-labels "Engine A v0" — that is the
older name for Engine B. Trust this line, not the bash header.

## Runtime records

Per project, not here: `agent-records/INDEX.md`. Regenerated from record frontmatter;
never hand-edited.