# automemory — index

The map of what's in this repository. Derived by hand — do not edit a line
without having read the file it points at.

> This is the **public** index. The working repository carries a fuller map of
> the design material, which is not published.

## What this repository is

The shipped tool: a passive capture engine that writes a factual markdown record
per AI session, git-tracked, with provenance. See [README.md](README.md) for what
it does and why.

## The code

- `automemory/` — the package. Stdlib only, no dependencies.
  - [`record.py`](automemory/record.py) — the record envelope and the derived
    index. The provider-neutral core.
  - [`transcript.py`](automemory/transcript.py) — transcript stores to readable
    text. Adapters for Claude Code (JSONL) and opencode (SQLite), one shared
    rendering vocabulary.
  - [`providers.py`](automemory/providers.py) — every provider-specific fact in
    one table: CLI binary, scribe model, lifecycle hook, transcript store.
  - [`dispatch.py`](automemory/dispatch.py) — headless authoring calls, per
    provider.
  - [`reconcile.py`](automemory/reconcile.py) — the backfill pass: finds sessions
    with no record and authors one.
  - [`redact.py`](automemory/redact.py) — heuristic secret scrub.
  - [`cli.py`](automemory/cli.py) — `init`, `status`, `peep`, `enable`,
    `disable`, `projects`.
- `system/engine/prompts/` — the prompts the engine runs.
  [`record-author.md`](system/engine/prompts/record-author.md) is read at runtime,
  so these are load-bearing.
- `system/scripts/` — [`vault-lint.sh`](system/scripts/vault-lint.sh) (corpus
  checks) and the Engine B capture scripts (currently disabled).

## Configuration

- `pyproject.toml` — packaging. Console script `automemory`.

## Naming

`Engine A` = self-scribe (`learnings`): the session's own agent writes a factual
record. This is the live one.
`Engine B` = blind observe-then-extract (`automemory-capture`): texture kept for
the person. Currently disabled. Note `system/scripts/capture-worker.sh` still
self-labels "Engine A v0" — that is the older name for Engine B. Trust this line,
not the bash header.

## Per-project records

Records live per project at `<project>/agent-records/`, not here. This is the
tool's repository, not a project it records.