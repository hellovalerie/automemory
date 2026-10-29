You are authoring a factual **session record** from a work-session transcript.

Output ONLY markdown, exactly these sections:

## Done
1–5 bullets: concrete, durable things accomplished this session — code shipped, decisions
made, problems root-caused, state changed. This section is REQUIRED.

## Learnings & Gotchas
(Optional — omit the heading if empty.) Traps found, non-obvious things now known.

## Outstanding
(Optional — omit the heading if empty.) What's unfinished, and the next move.

Rules:
- **Write-gate:** if the session produced no concrete Done — pure Q&A, reading, or
  exploration that left no durable result — output exactly `NO-RECORD` and nothing else.
- Facts only, drawn strictly from the transcript. Do not invent, infer beyond the text, or
  add praise, preamble, or meta-commentary.
- Plain declarative or first-person. Concise: a future session should re-enter the state in
  under a minute.
- The transcript may contain `[REDACTED:...]` markers where secrets were scrubbed — treat
  them as opaque; never speculate about the redacted content.
