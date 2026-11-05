# automemory — index

The map of what's in this repository. Derived by hand — do not edit a line
without having read the file it points at.

> This is the **public** index. The working repository carries a fuller map of
> the design material, which is not published.

## What this repository is

The shipped tool: it reads the transcripts your AI coding tools already keep
(Claude Code, Codex, opencode) and writes a factual, append-only markdown record per
session, with provenance, plus one open list shared across sessions and tools. See [README.md](README.md) for what
it does and why.

## The code

- `automemory/` — the package. Stdlib only, no dependencies.
  - [`record.py`](automemory/record.py) — the record format and the generated
    index; the only code that writes records, and it can only add (no delete).
  - [`transcript.py`](automemory/transcript.py) — transcript stores to readable
    text. Adapters for Claude Code (JSONL), Codex (JSONL) and opencode (SQLite),
    one shared rendering vocabulary.
  - [`providers.py`](automemory/providers.py) — every provider-specific fact in
    one table: CLI binary, scribe model, lifecycle hook, transcript store.
  - [`dispatch.py`](automemory/dispatch.py) — the scribe: a headless, tool-less
    model call per provider (blindness tested with a canary file).
  - [`reconcile.py`](automemory/reconcile.py) — finds sessions with turns no record
    covers yet; writes a new record or continues the existing one.
  - [`memory_mirror.py`](automemory/memory_mirror.py) — mirrors Claude Code's own
    project memory into the records folder, append-only.
  - [`redact.py`](automemory/redact.py) — cautious secret scrub, run on what the
    scribe reads and again on what it writes.
  - [`settings.py`](automemory/settings.py) — user settings (`automemory config`):
    time zone and header parts, records in git, scribe model per tool.
  - [`gitsync.py`](automemory/gitsync.py) — records on their own `agent-records`
    branch (a worktree), asked once per project; never touches code branches.
  - [`cli.py`](automemory/cli.py) — plain `automemory` (setup), `reconcile`,
    `status`, `peep`, `doctor`, `ignore`, `recheck`, `config`, `git`, `migrate`,
    `enable`/`disable`/`projects`, and the hook entry point.
  - [`progress.py`](automemory/progress.py) — progress shown during a manual run.
  - [`plugins/opencode.ts`](automemory/plugins/opencode.ts) — the opencode trigger,
    installed by `automemory`.
  - [`prompts/record-author.md`](automemory/prompts/record-author.md) — the
    scribe's prompt, read at runtime (load-bearing).

## Configuration

- `pyproject.toml` — packaging. Console script `automemory`.
- [`LICENSE`](LICENSE) — MIT.

## Per-project records

Records live per project at `<project>/agent-records/` (in a git repo, optionally on
their own `agent-records` branch), not here. This is the
tool's repository, not a project it records.