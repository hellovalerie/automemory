"""memory_mirror.py — mirror Claude Code's per-project memory into the record folder.

Claude Code keeps per-project memory at ~/.claude/projects/<slug>/memory/ (MEMORY.md
plus files). That dir is local and dies with the machine — its own header says NOT in
git. This mirrors it into <records_dir>/claude-memory/ so whatever the in-session agent
thought worth remembering becomes visible, durable and portable: filed alongside the
session records, in one gitable folder the user can commit where they like, or not.

Append-only on the FILESYSTEM, not via git history — git is optional in this product,
so the append-only guarantee can't be made to depend on committing:
  - a memory file that CHANGED        -> append a dated `## Superseded` block (old kept)
  - a memory file DELETED from source -> append a dated `## Retired` note (content kept)
Nothing is ever lost from the mirror, committed or not. Each mirrored file becomes a
running ledger of how that memory evolved.

Change tracking is a small `.sync-state.json` (filename -> content hash) living in the
mirror dir — the git-optional stand-in for a diff.

No redaction: the content already lived privately in ~/.claude; mirroring it to another
private spot doesn't change its exposure, and we ADD provenance. Claude-Code-only
(opencode has no equivalent memory dir).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

MIRROR_DIRNAME = "claude-memory"
_STATE_FILE = ".sync-state.json"


def memory_dir_for(projects_root, slug: str) -> Path:
    """Where Claude Code keeps this project's memory."""
    return Path(projects_root) / slug / "memory"


def mirror_dir_for(records_dir) -> Path:
    """Where the mirror lives, under the records folder (inherits its privacy)."""
    return Path(records_dir) / MIRROR_DIRNAME


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _header(source_name: str, today: str) -> str:
    # HTML comment so it doesn't disturb the markdown it prefixes.
    return (f"<!-- automemory mirror\n"
            f"source: ~/.claude/projects/<slug>/memory/{source_name}\n"
            f"first-captured: {today}\n"
            f"-->\n\n")


@dataclass
class MirrorResult:
    new: int = 0
    superseded: int = 0
    retired: int = 0
    unchanged: int = 0
    skipped: bool = False  # no source memory dir

    def touched(self) -> int:
        return self.new + self.superseded + self.retired


def _load_state(mirror_dir: Path) -> dict:
    try:
        return json.loads((mirror_dir / _STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(mirror_dir: Path, state: dict) -> None:
    (mirror_dir / _STATE_FILE).write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sync(memory_dir, mirror_dir, *, today: str = None) -> MirrorResult:
    """Append-only mirror of memory_dir into mirror_dir. Never raises for a missing
    source dir (returns skipped=True). `today` is overridable for tests."""
    today = today or date.today().isoformat()
    res = MirrorResult()
    memory_dir = Path(memory_dir)
    if not memory_dir.is_dir():
        res.skipped = True
        return res

    mirror_dir = Path(mirror_dir)
    mirror_dir.mkdir(parents=True, exist_ok=True)
    state = _load_state(mirror_dir)

    # live memory files, flat, markdown only
    live = {p.name: p.read_text(encoding="utf-8", errors="replace")
            for p in sorted(memory_dir.glob("*.md"))}

    # 1. new / changed / unchanged
    for name, content in live.items():
        h = _hash(content)
        meta = state.get(name)
        mpath = mirror_dir / name
        prior_hash = meta.get("hash") if isinstance(meta, dict) else None
        prior_status = meta.get("status") if isinstance(meta, dict) else None

        if meta is None and not mpath.exists():
            mpath.write_text(_header(name, today) + content, encoding="utf-8")
            res.new += 1
        elif prior_hash == h and prior_status == "live" and mpath.exists():
            res.unchanged += 1
        elif not mpath.exists():
            # mirror file lost but state knew it — re-seed rather than crash
            mpath.write_text(_header(name, today) + content, encoding="utf-8")
            res.new += 1
        else:
            # changed, or reappeared after a retire: append, never overwrite
            with mpath.open("a", encoding="utf-8") as fh:
                fh.write(f"\n\n## Superseded {today}\n\n{content}\n")
            res.superseded += 1
        state[name] = {"hash": h, "status": "live"}

    # 2. deleted from source -> retire (content preserved; noted once)
    for name, meta in list(state.items()):
        if name in live:
            continue
        if isinstance(meta, dict) and meta.get("status") == "retired":
            continue
        mpath = mirror_dir / name
        if mpath.exists():
            with mpath.open("a", encoding="utf-8") as fh:
                fh.write(f"\n\n## Retired {today}\n\n"
                         f"(removed from source memory; content preserved above)\n")
            res.retired += 1
        prev_hash = meta.get("hash") if isinstance(meta, dict) else None
        state[name] = {"hash": prev_hash, "status": "retired"}

    _save_state(mirror_dir, state)
    return res
