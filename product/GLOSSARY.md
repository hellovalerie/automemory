## Glossary stream 

One file per term: `practitioner/glossary/<term-slug>.md`. Source of truth is the markdown; a sweep
compiles `practitioner/glossary/glossary.json` (schema per the 08-technical-glossary design:
learning-first entries — metaphor + in_practice as anchors) for the Artifact renderer.
The JSON is derived — never hand-edited.

```markdown
---
term: idempotency
type: from-my-work | field-literacy      # per the 08 design's two entry types
first_captured: 2026-07-05
texture_criteria_version: 2026-07-05
sources:                                  # session/conversation uuids, accumulating
  - <uuid>
status: open                              # open | superseded
---

**Metaphor** (verbatim, with which Claude made it): ...
**Plain**: one-paragraph definition in the register she'd say aloud.
**In practice** (from her own sessions — cite the moment): ...

## Edits
none
```

Capture trigger (extractor): a Claude explained a concept via metaphor/scenario
*responsively* — she asked, or was visibly navigating the concept. Reception is NOT
required (no confirm/reuse test: things that stick instantly never get mentioned
again and are the best entries). Over-collection is pruned at sweep time: prune, not
mourn. A new metaphor for a known term update-appends to the term's file.
