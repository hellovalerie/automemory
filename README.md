# automemory

A logbook for your AI coding sessions that you can read, check and correct.

After you work with Claude Code, Codex or opencode, automemory reads the session's transcript
and writes a short, plain-markdown record of what actually happened — what got done,
what was learned, what's still open. Each record says exactly which session it came
from, so you can always go check the receipts. Nothing is memorised in secret; nothing
is silently rewritten later.

**The relay baton.** Each AI session is a runner handed the baton cold — it remembers
nothing of the last leg. automemory tapes a note to the baton: what happened, when, from
which session. The runners are interchangeable and forgettable; the baton finishes the race.

---

## How it fits together

Picture a **scribe who lives in one house** and visits **rooms**:

| Piece | Where it lives | What it is |
| --- | --- | --- |
| **The engine** (the scribe's house) | Installed once on your machine, out of sight. You only ever see the `automemory` command. | The code, plus the prompt the scribe writes from. |
| **The diaries** (the attic) | Where your AI tools already keep them: `~/.claude/projects/…` (Claude Code), `~/.codex/sessions/…` (Codex), `~/.local/share/opencode/opencode.db` (opencode) | Session transcripts. automemory **only reads** these — it never copies or moves them. |
| **The shelf** (in each room) | `agent-records/` inside each project you turn it on for | The records. The **only** thing automemory puts in your project. |

So in a project you've enabled, you'll see exactly one new folder:

```
your-project/
  agent-records/
    INDEX.md                                  # a table of every session, most recent first
    STATUS.md                                 # the project's open items + what was recently closed
    2026-10-29-claude-fix-login-loop.md       # a Claude Code session you renamed "Fix login loop"
    2026-11-04-codex-ff5b348b.md              # a Codex session: named by its id
    .declined.json                            # sessions checked and found to have nothing to record (with why)
    .ignore-sessions                          # sessions you told automemory never to record
    claude-memory/                            # Claude Code's own memory for this project, mirrored
```

**File names** are `<day the session started>-<tool>-<name>.md`. The name is whatever you
called the session with `/rename`, lowercased, with anything that isn't a letter or digit
turned into `-`. Never renamed? It's the session's short id. (Codex sessions are always
named by id: Codex names threads itself, so a rename can't be told apart from its own name.) Rename it later and the file
is renamed to match (and the change is logged). If two sessions get the same name, the
second one gets its id added. The file name is only a label — automemory always finds a
record by the session id inside it, never by its name.

It's plain markdown. Commit it, keep it local, or gitignore it — your choice. Git is
never required.

---

## Install

> **⚠ partly —** `uv tool install` verified on macOS (2026-10-29, editable, from a local
> checkout). Installing from the GitHub URL needs a public release first.

```bash
uv tool install git+https://github.com/hellovalerie/automemory    # recommended
# or: pipx install git+https://github.com/hellovalerie/automemory
```

Don't have uv? `brew install uv` (Mac), or see https://docs.astral.sh/uv/ — it brings
its own modern Python, so your system Python doesn't matter.

If your shell then says `automemory: command not found`, the install folder
(`~/.local/bin`) isn't on your PATH yet. Run `uv tool update-shell` once and open a new
terminal.

Requirements: Python 3.9+, no other dependencies (standard library only). You need the
CLI of whichever tool you use (`claude`, `codex` and/or `opencode`) on your PATH — automemory
uses it to write the records.

Then, in each project you want recorded, run `automemory` (see below).

---

## Using it

### Turn it on for a project

From inside the project folder:

```bash
automemory
```

It explains what recording means and asks **"Record sessions in this folder?"**. Say yes
and it sets up every tool you have installed:

- **Claude Code:** two hooks in `.claude/settings.local.json` (your personal settings file
  — never committed; automemory makes sure git ignores it).
- **Codex:** two hooks in `.codex/hooks.json` (kept out of git). The first time you open
  Codex in the folder, it asks you to **trust** them — approve, or they won't run.
- **opencode:** a plugin in `~/.config/opencode/plugin/` (restart opencode once).

It refuses to run in your home folder, and never turns itself on without asking (in a
script, pass `--yes`). Run it again any time to see the folder's status.

After that, recording is automatic and **never makes you wait**: the work happens in the
background, and its output goes to `~/.local/state/automemory/reconcile.log`.

- **Claude Code and Codex:** when a session **starts**, you (and the agent) see the session
  map and the project's **open items** — whichever tool wrote them — and earlier sessions are
  caught up. When a session **ends**, it's recorded too, so you shouldn't need to run
  anything by hand. This is also how work hands over between tools: a Codex session opens
  with what the last Claude session left open, and the other way round.
- **opencode:** there's no end-of-session hook, so a session is recorded when the **next**
  session in that folder starts.

### Starting on a project you've already been working in

Turn automemory on and your past sessions (as long as your tools still keep their
transcripts) are recorded too, **oldest first**, with one open list carried through them
in order, so what's left open at the end is the project's real state. If many are waiting,
a manual run asks first, with an estimate:

```
automemory: 23 sessions are waiting to be recorded (oldest first): ~410k tokens (claude-code);
about $1.80, judging by your recent runs. One model call each, roughly a minute apiece.
Record all 23 now? [y/N]
```

The cost comes from **your own past runs**: every scribe call's tokens and cost are logged
on your machine (`~/.local/state/automemory/usage.jsonl`, never committed), so the estimate
uses your real prices — until a few runs exist, it shows tokens only. Or skip the question
with `automemory reconcile --all`. Automatic runs do up to 5 at a time, in the background.

### Record right now

```bash
automemory reconcile
```

Records every session — **from every supported tool** — that has turns no record covers
yet: new sessions, and sessions you've come back to. In a terminal you see progress:

```
⠙ continuing 1/1 · claude-code · Fix login loop · 14s
  ✓ continued Fix login loop
automemory: run — checked 1: 0 written, 1 continued, 0 declined (~$0.06).
```

How it works: secrets are scrubbed from each transcript, then a **scribe** — a separate
model call with no tools and no access to your files — reads it and writes the record.
Sessions with nothing new are skipped at no cost.

### Other commands

```bash
automemory status               # how many records, the latest one, whether recording is set up
automemory peep                 # print the index
automemory reconcile --all      # record everything waiting, without asking
automemory ignore <session-id>  # never record this session (e.g. a throwaway or a test)
automemory disable              # stop recording this folder (opencode)
automemory projects             # which folders are on or off
```

### When something goes wrong

```bash
automemory doctor
```

Run it inside the project. It writes one debug file (to `~/.local/state/automemory/debug/`)
with versions, how each tool is wired, what's waiting to be recorded, this project's recent
runs and recent usage — **no transcript text and no record content**. Hand that file to
whoever is debugging the engine.

### Upgrading from an older record format

If a run says `record(s) in an older format`:

```bash
automemory migrate     # converts this project's older records to the current format
```

It first copies `agent-records/` to `~/.local/share/automemory/snapshots/<time>-<project>/`
(outside your project) and checks the copy is identical before touching anything. Every
change it makes is logged in each record's `## Changes`. The snapshot stays until you
delete it.

---

## What a record looks like

**One file per session, forever.** When you come back to a session — tomorrow, next
week — its record **continues where it left off**: the new work is added **at the top** as
a dated update. Nothing is regenerated and nothing is duplicated.

Every stretch of work in a session — the first one too — is one `## Update` block, newest
first. Read just the **Summary** lines top to bottom and you have the story.

```markdown
---
title: Fix login loop
source: claude-code:1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d
model: claude-sonnet-5-5
session_started: 2026-10-29
last_active: 2026-10-30
turns: 18
seen_chars: 48211
list_written: 2026-10-30T09:04:13Z
captured: 2026-10-30T09:04:13Z
subject: your-project
status: open
---

## Update — 2026-10-30 · turns 12–18

**Summary:** Added the regression test; the redirect bug is closed.

### Done
- Added a regression test against the staging proxy config.

### Bugs & Fixes
- **Bug:** the test passed locally but failed in CI. **Fix:** CI now runs behind the same proxy (`a1b2c3d`).

### Outstanding
- [o2] Document the proxy quirk in the README.
- ✓ closed: [o1] Add a regression test. — done, runs in CI

## Update — 2026-10-29 · turns 1–11

**Summary:** Found and fixed the login redirect loop; a test is still to do.

### Done
- Fixed the redirect loop in `auth.py`.

### Bugs & Fixes
- **Bug:** login redirected forever after a password change. **Fix:** clear the stale session cookie on change (`9f8e7d6`).

### Learnings & Gotchas
- The staging proxy strips `SameSite`, so local tests can't reproduce it.

### Outstanding
- [o1] Add a regression test.
- [o2] Document the proxy quirk in the README.

## Changes
- 2026-10-30 · title: "Agent-records context" → "Fix login loop"
- 2026-10-30 · model: claude-opus-5-5 → claude-sonnet-5-5
- 2026-10-30 · filename: 2026-10-29-claude-1a2b3c4d.md → 2026-10-29-claude-fix-login-loop.md

## Edits
none
```

### The top block (frontmatter): what each field means

The top block always shows the session's **current** state. How it got there is in
`## Changes`.

| Field | What it is |
| --- | --- |
| `title` | The session's name. Your `/rename` wins; otherwise the tool's own auto-title; otherwise the first words of your first message. |
| `source` | **The receipt.** `<tool>:<session id>` — the exact session this record was written from. You can open that transcript and check. Never changes. |
| `model` | The model used **most recently** in the session. Earlier models are listed in `## Changes`. |
| `session_started` | The day the session began. Never changes. |
| `last_active` | The most recent day there was work in the session. |
| `turns` | How many of your messages the record covers so far. Also how automemory knows where to pick up next time. |
| `seen_chars` | How much of the transcript had been read. If the last turn keeps growing after a run (work that finished later), automemory notices and looks again. |
| `list_written` | When this record last wrote the project's open list. The newest one anywhere is the project's current list. |
| `captured` | When automemory last wrote to this record (UTC). |
| `subject` | The project folder the session ran in. |
| `status` | `open`, or `superseded` if a later record replaced this one. |

### The sections

Each `## Update — <date> · turns A–B` covers only the turns that were new when it was
written (usually: since you last exited). Inside it:

- **Summary** — one or two sentences: what this stretch was, and how it ended.
- **`### Done`** — what actually changed: code shipped, a decision made, a problem
  root-caused. **Required** — see *declined sessions* below.
- **`### Bugs & Fixes`** — each bug found, with its fix; or, if it wasn't fixed, what was
  tried, what it affects, and whether an issue was filed.
- **`### Learnings & Gotchas`** — traps found, non-obvious things now known.
- **`### Outstanding`** — **every** item still open **for the project** — one list shared
  by all sessions and tools, so the newest update's list alone is the current state. Each
  item has an id like `[o12]`, given once and kept for life: carried forward until done,
  then marked `✓ closed: [o12] … — <how>` in whichever session finished it (even in another
  tool). If the scribe ever forgets an item, automemory carries it forward itself, marked,
  so nothing silently disappears. Items for **you** (something you said you'd check or
  decide) start with **You:** and are listed first. This is the list shown when a session
  starts; `agent-records/STATUS.md` shows it plus what was recently closed.

After the updates, two record-wide sections:

- **`## Changes`** — a dated, add-only list of every time the title, model or file name
  changed.
- **`## Edits`** — always last. Lists any change made to quoted text
  (`verbatim → as printed`), or `none`.

**Append-only.** Nothing in a record is ever rewritten or deleted — only added to. The one
thing that changes in place is the top block, which is a "current state" label; its
history is in `## Changes`. The engine has no code that deletes a record.

### Declined sessions

The scribe only writes a record if it can fill `### Done` with something durable. A session
that was just a greeting, a quick question, or reading around (say, a one-line "hi") gets
**declined**: no record file is written, and that's the system working, not failing.

The scribe says why in one line, and you see it:

```
  · declined Implement importer spike — work still in progress, nothing finished yet
```

So it isn't asked again every time, a declined session is noted in
`agent-records/.declined.json` with the reason and how much was checked. If the session
grows — you come back and do real work, or a task that was still running when it was
checked finishes later — it's checked again, and gets a record then. Running a check while
the agent is mid-task is the most common reason for a decline.

## Things worth knowing

- **It costs a little.** Each record is one model call on your own account.
- **Which model writes the records.** automemory never hard-codes one. Claude Code uses
  the `haiku` alias (always the current Haiku). Codex and opencode use the model the
  session ran on, falling back to the one you're using now. (Codex reports tokens but not
  dollars, so its cost shows as unknown.) To choose your own, set `scribe_model`
  in the config, e.g. `{"scribe_model": {"opencode": "openai/gpt-6-luna"}}`.
  **⚠ not yet —** opencode's scribe call is failing on working models; under investigation.
- **Automatic runs do up to 5 sessions at a time,** in the background, so a big backlog
  never stalls you; a manual run offers to do them all (see *Starting on a project…*).
- **opencode's last session waits for the next one.** opencode has no end-of-session hook,
  so its final session in a folder is recorded when another starts there, or when you run
  `automemory reconcile`. Claude Code and Codex record a session as it ends.
- **If something goes wrong, run `automemory doctor`**, or look in
  `~/.local/state/automemory/`: `reconcile.log` (every automatic run), `usage.jsonl` (what
  each scribe call cost), `plugin.log` (opencode triggers). Nothing pops up mid-session. **⚠ not yet —** a one-line warning at the start of
  your next session when the last run failed.
- **Secrets are scrubbed — cautiously.** Before the scribe sees a transcript, and again
  before a record is saved, automemory replaces secrets with `[REDACTED:<kind>]`: known key
  and token formats (OpenAI, Anthropic, GitHub, AWS, Google, Stripe, Slack, Hugging Face,
  …), anything labelled like a secret (`API_KEY=`, `password:`), passwords in URLs,
  `Bearer` tokens, private keys — **and any long random-looking string**. A record can say
  *how* a key was provided, never the key itself.

  To keep records checkable, these shapes **are let through by design** (they're receipts,
  not secrets):

  | Let through | Looks like |
  | --- | --- |
  | Session and message ids | `1a11b0d1-780f-48bf-9cd4-3f039ccc2f2d` (UUID), `ses_…`, `msg_…`, `prt_…`, `toolu_…`, `req_…` |
  | Git commit hashes | 7–12 hex characters (`45681a4`) or the full 40 |
  | SHA-256 hashes | exactly 64 hex characters |
  | Timestamped names | `20261030T032635Z-…` (e.g. snapshot folders) |

  It's pattern-matching, so it errs on the side of scrubbing — occasionally something
  harmless gets redacted. It does **not** scrub personal content: don't turn automemory on
  in a folder whose conversations you don't want written down.
- **Claude Code's memory, mirrored.** If Claude Code keeps a memory for the project, it's
  copied into `agent-records/claude-memory/`. When a memory changes, the old version stays
  and the new one is appended — so you can see what the agent believed, and when.

## Supported tools

| Tool | Records | Automatic trigger |
| --- | --- | --- |
| Claude Code | ✅ | start + end hooks, set up by `automemory` |
| Codex | ✅ | start + end hooks, set up by `automemory`; approve them once when Codex asks |
| opencode | ✅ | a plugin, set up by `automemory`; records when the next session starts |
| claude.ai (web/desktop chat) | ❌ — no local transcript to read | — |

## Licence

[MIT](LICENSE).
