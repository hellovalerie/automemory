"""providers.py — every provider-specific fact, in one table.

Why this module exists
----------------------
`haiku` is a Claude Code model alias. `reconcile.py` hardcoded it as the backfill
scribe and passed it explicitly, which overrode each dispatcher's own default and
made EVERY opencode backfill fail with "Invalid model reference: haiku". The fix
was to look the model up per provider — but the real problem was that provider
config was scattered across three modules (model default in reconcile, binary and
flags in dispatch, hook path in cli), so there was no single place a new provider
had to be declared and no way to see what was still Claude-shaped.

Everything provider-specific now lives here. Adding a provider means adding one
Provider entry — not hunting for stray defaults.

What is deliberately NOT here
-----------------------------
- Transcript parsing/rendering (lives in transcript.py behind the render
  vocabulary both providers share)
- The dispatch implementations (dispatch.py) — referenced, not redefined
- Record format (record.py) — provider-neutral; `provider:` is just a label

Verified against opencode 1.19.0 and Claude Code, 2026-10-15.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Provider:
    """One supported agent harness."""

    name: str
    aliases: tuple

    # --- dispatch (how the scribe is invoked) --------------------------------
    cli: str
    #: The cheap backfill scribe model, in THIS provider's namespace. Namespaces
    #: do not overlap: opencode rejects "haiku" outright.
    scribe_model: str

    # --- session trigger (how backfill is requested) -------------------------
    #: Does this harness expose a session lifecycle event a plugin/hook can use?
    #: Claude Code: yes (SessionStart/SessionEnd). opencode: only "prompt".
    has_lifecycle_hook: bool
    #: What "hook wiring" means for this provider. Claude Code writes a settings
    #: file; opencode has no hook file at all — a global plugin covers every
    #: project, so "wiring" is just a consent entry, and `init` must not pretend
    #: otherwise.
    wiring: str

    # --- transcript store ----------------------------------------------------
    #: Where a raw transcript is read from, and whether it's one file per
    #: session (claude-code) or rows in a shared database (opencode).
    store_kind: str

    notes: str = ""


CLAUDE_CODE = Provider(
    name="claude-code",
    aliases=("claude", "claude_code", "cc"),
    cli="claude",
    scribe_model="haiku",
    has_lifecycle_hook=True,
    wiring="settings-file",   # .claude/settings.json, merged never clobbered
    store_kind="jsonl-per-session",
    notes="Scribe runs as `claude -p`; tool denial via --disallowed-tools. "
          "Neutral cwd gives real geographic isolation: the scribe's transcript "
          "lands under a tempdir project slug the reconciler never scans.",
)

OPENCODE = Provider(
    name="opencode",
    aliases=("open-code", "oc"),
    cli="opencode",
    scribe_model="opencode/space-bunny-free",
    has_lifecycle_hook=False,   # only session.hook("prompt") fires; see below
    wiring="consent-only",      # global plugin + per-project consent
    store_kind="sqlite",
    notes="Scribe runs as `opencode run --standalone --format json` (an NDJSON "
          "EVENT stream, not one object). No --disallowed-tools; blindness via an "
          "OPENCODE_CONFIG_CONTENT agent-scoped permission merge — but `read` must "
          "NOT be denied: on the free tier that reproducibly 403s every call. "
          "No idle/ended lifecycle hook exists (28 candidates probed, only "
          "'prompt' fires), so a project's final session cannot be backfilled — "
          "the same gap ENGINE-A.md already records for Claude Code.",
)

_ALL = (CLAUDE_CODE, OPENCODE)

_BY_NAME: dict = {}
for _p in _ALL:
    _BY_NAME[_p.name] = _p
    for _a in _p.aliases:
        _BY_NAME[_a] = _p

DEFAULT = CLAUDE_CODE


def resolve(name: str = None) -> Provider:
    """Provider by name/alias. None/empty -> DEFAULT. Unknown -> DEFAULT."""
    if not name:
        return DEFAULT
    return _BY_NAME.get(str(name).strip().lower(), DEFAULT)


def names() -> list:
    return [p.name for p in _ALL]


def scribe_model(name: str = None, fallback: str = None) -> str:
    """The backfill scribe model for a provider, in that provider's namespace."""
    p = resolve(name)
    return fallback if fallback is not None else p.scribe_model