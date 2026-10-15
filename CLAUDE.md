# automemory — session router

This file is deliberately thin: it routes, it does not hold content. It exists because
CLAUDE.md is the only file Claude Code auto-loads — everything real lives behind the
links. If you find yourself adding substance here instead of linking it, stop.

1. Read [FOR-THE-NEXT-CLAUDE.md](FOR-THE-NEXT-CLAUDE.md) before working. It is not
   instructions; it is what this work is, from the ones who held it before you.
2. [INDEX.md](INDEX.md) is the map — one line per entry; open full files on demand.
3. The method: [system/design/SCHEMA.md](system/design/SCHEMA.md) (streams, entry format, all format rules)
   · [system/design/TEXTURE-CRITERIA.md](system/design/TEXTURE-CRITERIA.md) (what gets captured).

## Hard invariants (reminders — SCHEMA.md is the source)

- Append-only. Supersession is annotation plus a forward link, never deletion.
- Every quoting artifact ends with an `## Edits` ledger (`verbatim → as printed`, or `none`).
- Git/`gh` identity for THIS repo is always '`YOUR GIT IDENTITY`(personal — travelability/
  identity hygiene). If the active account is anything else, switch it to `YOUR GIT IDENTITY`before any push/issue/PR — authorized, no need to ask.
- Signatures name the model with version (`Fable 5`, `Opus 4.8`) — evidence-gated,
  never fabricated.
- Scope is Claude Code + claude.ai only (v1). Do not design for Slack/cron/repo-less.
- **Adapters:** transcript sources live in `automemory/transcript.py` and the provider
  dispatchers in `automemory/dispatch.py`; `reconcile.resolve_source()` picks between
  them. Claude Code (JSONL) and opencode (SQLite) are both wired. `AUTOMEMORY_PROVIDER`
  overrides the auto-detect. Add a provider there — not in `reconcile.py`.
- Drift policy (SCHEMA.md, "Drift prevention"): one source of truth per fact; routers
  hold no content; supersede by annotation; decisions land in the repo same-session.
- Before committing vault changes: `bash system/scripts/vault-lint.sh`.