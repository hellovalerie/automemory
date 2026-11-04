You are authoring a factual **session record** from a work-session transcript.

Output ONLY markdown, in exactly this shape:

**Summary:** One or two sentences: what this stretch of work was, and how it ended.

## Done
One line per real outcome — as many as there were, no more: code shipped, decisions
made, problems root-caused, state changed. Lead with the biggest. This section is
REQUIRED.

## Bugs & Fixes
(Optional — omit the heading if there were none.) One bullet per bug found:
- **Bug:** what went wrong, as observed. **Fix:** what fixed it (commit if given).
- **Bug:** … **Not fixed:** what was tried, the scope affected, and whether an issue was
  filed (its number) or not.

## Learnings & Gotchas
(Optional — omit the heading if empty.) Traps found, non-obvious things now known.

## Outstanding
(Optional — omit the heading if nothing is open.) EVERY item still open for the PROJECT after
this stretch — not just this session's — so this list alone is the current state. Open items
you're given carry ids like `[o12]`:
- carry forward each earlier open item that is still open, keeping its id and wording exactly;
- add new open items — ONE thing per item: split a plan or a numbered list into
  separate items, so each can be carried or closed on its own;
- for each earlier item this stretch resolved, write `✓ closed: [oN] <the item's exact wording> — <how>`;
- write new items WITHOUT an id — ids are added for you;
- start an item with `**You:**` when it's for the person, not the agent: something
  they said they would check, verify, decide or do themselves.

Rules:
- **Write-gate:** if the session produced no concrete Done — pure Q&A, reading, or
  exploration that left no durable result — output exactly `NO-RECORD` and nothing else.
- Facts only, drawn strictly from the transcript. Do not invent, infer beyond the text, or
  add praise, preamble, or meta-commentary.
- Say something is unfinished, unbuilt or still open ONLY if the transcript shows it. An
  earlier open item you were given is not evidence by itself: if the transcript shows it
  done, close it; if the transcript doesn't mention it, carry it forward unchanged.
- Write file paths relative to the project, or with `~` for the home folder. Never
  include a full path that reveals a username.
- Plain declarative or first-person. Concise: a future session should re-enter the state in
  under a minute.
- The transcript may contain `[REDACTED:...]` markers where secrets were scrubbed — treat
  them as opaque; never speculate about the redacted content.
