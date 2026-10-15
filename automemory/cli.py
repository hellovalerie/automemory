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
import os
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


# --- consent (opencode adapter) -----------------------------------------------
# The opencode trigger plugin is installed globally and is DEFAULT OFF per
# project. Consent lives in one global config file keyed by directory, so turning
# recording on leaves no trace inside the project — no marker file, nothing to
# gitignore, nothing to accidentally commit.


def _consent_path() -> Path:
    return Path(os.environ.get("AUTOMEMORY_CONFIG")
                or (Path.home() / ".config" / "opencode" / "automemory.json"))


def _consent_load() -> dict:
    try:
        return json.loads(_consent_path().read_text(encoding="utf-8")).get("projects", {})
    except (OSError, ValueError):
        return {}


def _consent_save(projects: dict) -> None:
    p = _consent_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"projects": projects}, indent=2) + "\n", encoding="utf-8")


def cmd_enable(args) -> int:
    proj = str(Path(args.path).resolve() if args.path else Path.cwd())
    projects = _consent_load()
    projects[proj] = "on"
    _consent_save(projects)
    print(f"automemory: recording enabled for {proj}")
    print("  (open an opencode session there to trigger the first reconcile)")
    return 0


def cmd_disable(args) -> int:
    proj = str(Path(args.path).resolve() if args.path else Path.cwd())
    projects = _consent_load()
    if projects.pop(proj, None) is None:
        print(f"automemory: not enabled for {proj} (no change)")
    else:
        _consent_save(projects)
        print(f"automemory: recording disabled for {proj}")
    return 0


def cmd_projects(_args) -> int:
    projects = _consent_load()
    if not projects:
        print("automemory: no projects configured (enable with: automemory enable)")
        return 0
    for proj, state in sorted(projects.items()):
        print(f"  {state:6} {proj}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="automemory", description="passive capture engine for AI conversations")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="wire the SessionStart hook + scaffold agent-records/")
    sub.add_parser("status", help="record count, latest, hook state")
    sub.add_parser("peep", help="print the record index (titles)")
    p_en = sub.add_parser("enable", help="record sessions for this project (opencode adapter)")
    p_en.add_argument("path", nargs="?", help="project dir (default: cwd)")
    p_di = sub.add_parser("disable", help="stop recording a project")
    p_di.add_argument("path", nargs="?", help="project dir (default: cwd)")
    sub.add_parser("projects", help="list projects and their recording state")
    sub.add_parser("reconcile", help=argparse.SUPPRESS)  # internal hook entrypoint
    args = p.parse_args(argv)
    return {
        "init": cmd_init, "status": cmd_status, "peep": cmd_peep, "reconcile": cmd_reconcile,
        "enable": cmd_enable, "disable": cmd_disable, "projects": cmd_projects,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
