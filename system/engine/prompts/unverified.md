# Unverified — instruction block for curator agents (issue #11)

Include the block below verbatim in the prompt of any agent that merges, sweeps,
regenerates, or consolidates an existing automemory artifact. It is NOT for the
first-hand readers — neither the blind observer (`observer.md`) nor the extractor
(`extractor.md`): both work only from the transcript in front of them, so everything
they write is anchored by construction. Format and semantics: `system/design/SCHEMA.md`,
"Unverified section".

---

While working you may find material — a quote, a claim, an attribution — that seems worth
keeping but that you cannot anchor to a transcript turn or source document. Do not
silently drop it, and do not keep it in the body as if verified. Record it in the
artifact's `## Unverified` section: if the section does not exist, create it immediately
BEFORE the final `## Edits` section (`## Edits` always stays last). One bullet per item:

- (YYYY-MM-DD, <your role — your model with version, e.g. merge agent — Haiku 4.5>)
  <the item, quoted exactly as found> — suspicion source: <where you found it and why
  you could not anchor it>

Rules:

- Append only. Never edit, reorder, or delete an existing Unverified entry.
- Do not try to resolve entries. This section is a container for honest uncertainty, not
  a to-do list; some entries will rightly stay there forever. Only the person whose
  record this is resolves one.
- If you DO find a solid anchor for material already listed here, append a new dated
  bullet referencing the earlier one (`anchor found: <turn/source>`) — never rewrite the
  original.
- Dropping unanchorable material with neither an Unverified entry nor an explicit note in
  your run output is a defect, the same as an unledgered edit.
