"""cli.py — the automemory command surface (MVP subset of BUILD-SPEC §9).

Commands:
  init       wire the SessionStart hook into <cwd>/.claude/settings.json
             (merge-not-clobber) + scaffold agent-records/.
  status     health: record count, latest, whether the hook is wired.
  peep       print the index (titles) — the map.
  reconcile  internal: the SessionStart hook entrypoint (reads payload on stdin).

Stdlib only. Core functions take an explicit project path so they're testable
without chdir; the arg handlers pass Path.cwd().
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import reconcile

# The shipped console entrypoint (brew/pip installs `automemory` on PATH).
HOOK_COMMAND = "automemory reconcile"


# --- cores (testable) ---------------------------------------------------------


def hook_command_for(project: Path) -> str:
    return HOOK_COMMAND


def is_wired(settings_path: Path, command: str) -> bool:
    if not settings_path.exists():
        return False
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    for entry in data.get("hooks", {}).get("SessionStart", []):
        if isinstance(entry, dict):
            for h in entry.get("hooks", []):
                if isinstance(h, dict) and h.get("command") == command:
                    return True
    return False


def wire_hook(project: Path, command: str) -> str:
    """Merge our SessionStart hook into .claude/settings.json without clobbering.
    Returns a status string. Aborts (raises ValueError) if the file exists but is
    not valid JSON — never overwrite a hand-edited-but-broken file."""
    settings = project / ".claude" / "settings.json"
    if is_wired(settings, command):
        return "already-wired"

    data = {}
    if settings.exists():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"{settings} is not valid JSON — refusing to touch it ({e})")
        if not isinstance(data, dict):
            raise ValueError(f"{settings} is not a JSON object — refusing to touch it")

    hooks = data.setdefault("hooks", {})
    session_start = hooks.setdefault("SessionStart", [])
    session_start.append({"hooks": [{"type": "command", "command": command}]})

    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return "wired"


def scaffold_records(project: Path) -> Path:
    d = project / "agent-records"
    d.mkdir(exist_ok=True)
    (d / ".gitkeep").touch(exist_ok=True)
    return d


def status_of(project: Path) -> dict:
    recs = project / "agent-records"
    md = [p for p in recs.glob("*.md") if p.name != "INDEX.md"] if recs.is_dir() else []
    latest = max(md, key=lambda p: p.stat().st_mtime).name if md else None
    return {
        "project": project.name,
        "records": len(md),
        "latest": latest,
        "wired": is_wired(project / ".claude" / "settings.json", HOOK_COMMAND),
    }


def index_text(project: Path) -> str:
    idx = project / "agent-records" / "INDEX.md"
    return idx.read_text(encoding="utf-8").rstrip() if idx.exists() else ""


# --- arg handlers -------------------------------------------------------------


def cmd_init(_args) -> int:
    project = Path.cwd()
    try:
        result = wire_hook(project, hook_command_for(project))
    except ValueError as e:
        print(f"automemory init: ERROR — {e}", file=sys.stderr)
        return 1
    if result == "already-wired":
        print("automemory: SessionStart hook already wired (no change).")
    else:
        print(f"automemory: wired SessionStart hook -> {HOOK_COMMAND}")
    d = scaffold_records(project)
    print(f"automemory: records dir ready at {d}")
    return 0


def cmd_status(_args) -> int:
    s = status_of(Path.cwd())
    print(f"automemory status — {s['project']}")
    print(f"  records: {s['records']}")
    if s["latest"]:
        print(f"  latest:  {s['latest']}")
    print(f"  hook:    {'wired' if s['wired'] else 'not wired (run: automemory init)'}")
    return 0


def cmd_peep(_args) -> int:
    txt = index_text(Path.cwd())
    print(txt if txt else "automemory: no records yet (empty).")
    return 0


def cmd_reconcile(_args) -> int:
    return reconcile.main()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="automemory", description="passive capture engine for AI conversations")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="wire the SessionStart hook + scaffold agent-records/")
    sub.add_parser("status", help="record count, latest, hook state")
    sub.add_parser("peep", help="print the record index (titles)")
    sub.add_parser("reconcile", help=argparse.SUPPRESS)  # internal hook entrypoint
    args = p.parse_args(argv)
    return {
        "init": cmd_init, "status": cmd_status, "peep": cmd_peep, "reconcile": cmd_reconcile,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
