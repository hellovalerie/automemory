"""cli.py — the automemory command surface (commands: dev/design/PRODUCT.md).

Commands:
  init       provider-aware setup: scaffold agent-records/ and wire whichever
             trigger this harness actually has (see providers.py).
  status     health: record count, latest, trigger state.
  peep       print the index (titles) — the map.
  enable     grant recording consent for a project (opencode adapter)
  disable    revoke it
  projects   list projects and their recording state
  reconcile  internal: the trigger entrypoint (reads payload on stdin)

Provider-aware init matters: Claude Code has a hook file to write, opencode has
no hook at all (consent + a global plugin). Wiring .claude/settings.json inside
an opencode project used to "succeed" while doing nothing useful.

Stdlib only. Core functions take an explicit project path so they're testable
without chdir; the arg handlers pass Path.cwd().
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from . import providers, record, reconcile

# Claude Code hooks: the same command on both events. It returns at once; the
# catch-up runs detached in the background (a startup hook that ran the scribe in
# the foreground would hold the session for minutes).
HOOK_EVENTS = ("SessionStart", "SessionEnd")
SETTINGS_FILE = "settings.local.json"
HOOK_SUBCOMMAND = "hook"


def hook_command_for(project: Path = None) -> str:
    """Absolute engine path + `hook`: a Claude Code started with a different PATH
    (GUI launch, stale environment) can't break it — the opencode lesson."""
    return " ".join(engine_command()) + " " + HOOK_SUBCOMMAND


HOOK_COMMAND = "automemory hook"  # display / legacy name


def _is_ours(command: str) -> bool:
    c = (command or "").strip()
    return c.endswith("automemory reconcile") or c.endswith("automemory hook") \
        or c.endswith("-m automemory hook") or c.endswith("-m automemory reconcile")


def is_wired(settings_path: Path, command: str = None) -> bool:
    """True if every hook event carries one of OUR commands (any form)."""
    if not settings_path.exists():
        return False
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    hooks = data.get("hooks", {}) if isinstance(data, dict) else {}
    for event in HOOK_EVENTS:
        cmds = [h.get("command", "") for e in hooks.get(event, []) if isinstance(e, dict)
                for h in e.get("hooks", []) if isinstance(h, dict)]
        if not any((command and c == command) or (not command and _is_ours(c)) for c in cmds):
            return False
    return True


def wire_hook(project: Path, command: str) -> str:
    """Merge our hooks into .claude/settings.local.json without clobbering anything else.
    Our own older entries (`automemory reconcile`, a different engine path) are
    REPLACED, not duplicated. Returns 'wired' | 'updated' | 'already-wired'.
    Raises ValueError if the file exists but isn't a valid JSON object — a
    hand-edited-but-broken file is never overwritten."""
    # settings.LOCAL.json: personal and uncommitted. The hook carries this
    # machine's absolute engine path, which must not land in a shared,
    # committed settings.json where it would break for every teammate.
    settings = project / ".claude" / SETTINGS_FILE
    data = {}
    if settings.exists():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"{settings} is not valid JSON — refusing to touch it ({e})")
        if not isinstance(data, dict):
            raise ValueError(f"{settings} is not a JSON object — refusing to touch it")
    if is_wired(settings, command):
        return "already-wired"

    hooks = data.setdefault("hooks", {})
    replaced = False
    for event in HOOK_EVENTS:
        entries = hooks.setdefault(event, [])
        kept = []
        for e in entries:
            if isinstance(e, dict):
                hs = [h for h in e.get("hooks", [])
                      if not (isinstance(h, dict) and _is_ours(h.get("command", "")))]
                if len(hs) != len(e.get("hooks", [])):
                    replaced = True
                if hs:
                    kept.append(dict(e, hooks=hs))
            else:
                kept.append(e)
        kept.append({"hooks": [{"type": "command", "command": command}]})
        hooks[event] = kept

    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return "updated" if replaced else "wired"


CODEX_HOOKS = Path(".codex") / "hooks.json"


def wire_codex_hooks(project: Path) -> str:
    """Merge our start + end hooks into the project's .codex/hooks.json (Codex's hooks
    feature; format as described by codex-cli 0.166.0's own schema — SessionStart may
    return hookSpecificOutput.additionalContext). Codex asks the user to TRUST new hooks
    the first time it opens the project; until then they don't run. Our own older
    entries are replaced; others are kept. 'wired' | 'updated' | 'already-wired'."""
    path = project / CODEX_HOOKS
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"{path} is not valid JSON — refusing to touch it ({e})")
    hooks = data.setdefault("hooks", {})
    engine = " ".join(engine_command())
    want = {ev: f"{engine} hook --codex --event {ev}" for ev in HOOK_EVENTS}
    if all(any(h.get("command") == want[ev] for e in hooks.get(ev, []) for h in e.get("hooks", []))
           for ev in HOOK_EVENTS):
        return "already-wired"
    replaced = False
    for ev in HOOK_EVENTS:
        kept = []
        for e in hooks.get(ev, []):
            hs = [h for h in e.get("hooks", []) if "automemory" not in h.get("command", "") or " hook" not in h.get("command", "")]
            replaced |= len(hs) != len(e.get("hooks", []))
            if hs:
                kept.append(dict(e, hooks=hs))
        kept.append({"hooks": [{"type": "command", "command": want[ev]}]})
        hooks[ev] = kept
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return "updated" if replaced else "wired"


def scaffold_records(project: Path) -> Path:
    d = project / "agent-records"
    d.mkdir(exist_ok=True)
    (d / ".gitkeep").touch(exist_ok=True)
    return d


def status_of(project: Path, records_dir: Path = None) -> dict:
    recs = Path(records_dir) if records_dir else project / "agent-records"
    # Count only real records — anything with a record envelope. A README.md
    # sitting in agent-records/ is documentation, not a session record.
    md = [p for p in recs.glob("*.md")
          if p.name != "INDEX.md" and record.is_record(record._fm_parse(p.read_text(encoding="utf-8")))] \
        if recs.is_dir() else []
    latest = max(md, key=lambda p: p.stat().st_mtime).name if md else None
    return {
        "project": project.name,
        "records": len(md),
        "latest": latest,
        "wired": is_wired(project / ".claude" / SETTINGS_FILE),
    }


def index_text(project: Path, records_dir: Path = None) -> str:
    base = Path(records_dir) if records_dir else project / "agent-records"
    idx = base / "INDEX.md"
    return idx.read_text(encoding="utf-8").rstrip() if idx.exists() else ""


# --- arg handlers -------------------------------------------------------------


def _detect_provider(cwd: Path) -> str:
    """Which harness is this project driven by?

    AUTOMEMORY_PROVIDER wins. Otherwise ask the reconciler, which auto-detects
    by whichever store actually holds sessions for this directory.
    """
    explicit = os.environ.get("AUTOMEMORY_PROVIDER")
    if explicit:
        return providers.resolve(explicit).name
    try:
        return reconcile.resolve_source(str(cwd)).name
    except Exception:
        return providers.DEFAULT.name


OPENCODE_PLUGIN_SRC = Path(__file__).resolve().parent / "plugins" / "opencode.ts"
OPENCODE_PLUGIN_DEST = Path.home() / ".config" / "opencode" / "plugin" / "automemory.ts"


def install_opencode_plugin(src: Path = OPENCODE_PLUGIN_SRC,
                            dest: Path = OPENCODE_PLUGIN_DEST) -> str:
    """Copy the shipped trigger plugin into opencode's global plugin dir.
    'installed' | 'updated' | 'already current'. opencode only loads it on its
    next start — it does not hot-reload plugins."""
    new = src.read_text(encoding="utf-8")
    if dest.exists():
        if dest.read_text(encoding="utf-8") == new:
            return "already current"
        verdict = "updated"
    else:
        verdict = "installed"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(new, encoding="utf-8")
    return verdict


def cmd_init(_args) -> int:
    project = Path.cwd()
    prov = providers.resolve(_detect_provider(project))
    d = scaffold_records(project)
    print(f"automemory: records dir ready at {d}")

    if prov.wiring == "settings-file":
        try:
            result = wire_hook(project, hook_command_for(project))
        except ValueError as e:
            print(f"automemory init: ERROR — {e}", file=sys.stderr)
            return 1
        if result == "already-wired":
            print("automemory: Claude Code hooks already wired (no change).")
        else:
            print(f"automemory: {result} Claude Code start + end hooks -> "
                  f"{hook_command_for(project)} (catch-up runs in the background)")
    else:
        # opencode has no hook file. Wiring a .claude/settings.json here would
        # report success and do nothing — the trigger is the global plugin, and
        # this project needs a consent entry.
        print(f"automemory: provider {prov.name} — trigger is the global plugin, "
              f"not a hook file.")
        print(f"automemory: opencode plugin {install_opencode_plugin()} "
              f"-> {OPENCODE_PLUGIN_DEST}")
        print(f"automemory: plugin will run {' '.join(record_engine_command())}")
        state = _consent_state(_consent_load().get(str(project.resolve())))
        if state == "on":
            print(f"automemory: recording already enabled for {project}")
        else:
            print(f"automemory: run `automemory enable` in {project} to record it")
    return 0


def cmd_status(_args) -> int:
    cwd = Path.cwd()
    routed = reconcile.records_dir_for(str(cwd))
    s = status_of(cwd, records_dir=routed)
    prov = providers.resolve(_detect_provider(cwd))
    print(f"automemory status — {s['project']}")
    print(f"  records: {s['records']}")
    if s["latest"]:
        print(f"  latest:  {s['latest']}")
    if prov.wiring == "settings-file":
        print(f"  hook:    {'wired' if s['wired'] else 'not wired (run: automemory init)'}")
    else:
        entry = _consent_load().get(str(cwd.resolve()))
        state = _consent_state(entry)
        mark = {"on": "enabled", "asked": "asked, not enabled",
                "off": "disabled"}.get(state, "not enabled")
        print(f"  provider: {prov.name}")
        print(f"  consent:  {mark} (automemory enable)")
        routed = reconcile.records_dir_for(str(cwd))
        if routed != cwd / "agent-records":
            print(f"  records:  {routed}  (routed)")
    return 0


def cmd_peep(_args) -> int:
    cwd = Path.cwd()
    txt = index_text(cwd, records_dir=reconcile.records_dir_for(str(cwd)))
    print(txt if txt else "automemory: no records yet (empty).")
    return 0


def _turns_at(source, ref: str, cwd: str, until_iso: str) -> int:
    """Turns a legacy record covered: for Claude Code, the turns up to its
    `captured` time (the session may have continued since); otherwise all."""
    if isinstance(source, reconcile._ClaudeCodeSource) and until_iso:
        import tempfile
        src = source._path(ref, cwd)
        try:
            kept = [l for l in src.read_text(encoding="utf-8", errors="replace").splitlines()
                    if (json.loads(l).get("timestamp") or "") <= until_iso]
        except (OSError, ValueError):
            kept = None
        if kept is not None:
            with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
                fh.write("\n".join(kept) + "\n")
            try:
                from . import transcript
                return len(reconcile.split_turns(transcript.render_file(Path(fh.name))))
            finally:
                Path(fh.name).unlink(missing_ok=True)
    try:
        return len(reconcile.split_turns(source.render(ref, cwd)))
    except Exception:
        return 0  # transcript gone: turns unknown — never a half-migrated folder


def cmd_migrate(_args) -> int:
    """Convert pre-2026-10-30 records to the current format, after a verified
    snapshot. Prints a receipt of every file touched."""
    cwd = Path.cwd()
    recs = reconcile.records_dir_for(str(cwd))
    legacy = [p for p in recs.glob("*.md")
              if p.name != record.INDEX_NAME
              and record.is_legacy(record._fm_parse(p.read_text(encoding="utf-8")))]
    # Legacy duplicates (two old records for one session): the one whose old name
    # already carries the session's short id goes first and keeps the plain name.
    def _order(p):
        ref = record._fm_parse(p.read_text(encoding="utf-8"))["source"].partition(":")[2]
        return (p.stem.rsplit("-", 1)[-1] != record.short(ref), p.name)
    legacy.sort(key=_order)
    def _body(p):
        return record._split(p.read_text(encoding="utf-8"))[1]
    reorder = [p for p in recs.glob("*.md") if p.name != record.INDEX_NAME
               and record.is_record(record._fm_parse(p.read_text(encoding="utf-8")))
               and (record.updates_out_of_order(_body(p)) or record.needs_reshape(_body(p)))]
    if not legacy and not reorder:
        print("automemory migrate: nothing to migrate.")
        return 0
    snap = record.snapshot(recs, label=cwd.name)
    print(f"automemory migrate: snapshot (verified) -> {snap}")
    srcs = {"claude-code": reconcile._ClaudeCodeSource(), "opencode": reconcile._OpencodeSource()}
    for p in legacy:
        fm = record._fm_parse(p.read_text(encoding="utf-8"))
        prov, _, ref = fm["source"].partition(":")
        src = srcs.get(prov)
        tag = providers.resolve(prov).file_tag or prov
        # Fresh metadata only when the transcript still exists — a missing one
        # would yield placeholder values and "change" a correct title.
        meta = {}
        if src is not None and ref in src.list_ids(str(cwd)):
            try:
                meta = src.meta(ref, str(cwd))
            except Exception:
                meta = {}
        turns = _turns_at(src, ref, str(cwd), fm.get("captured", "")) if src else 0
        new = record.migrate_legacy(
            p, provider_tag=tag, session_ref=ref,
            title=meta.get("title") or fm.get("title", ""),
            custom_title=meta.get("custom_title", ""),
            model=meta.get("model", ""), turns=turns)
        print(f"  {p.name} -> {new.name}  (turns {turns})")
    for p in recs.glob("*.md"):
        if p.name != record.INDEX_NAME and record.is_record(record._fm_parse(p.read_text(encoding="utf-8"))) \
                and (record.reorder_updates(p) | record.reshape_first_batch(p)):
            print(f"  {p.name}: reshaped (every batch an `## Update`, newest first)")
    record.rebuild_index(recs)
    print(f"automemory migrate: {len(legacy)} legacy record(s) converted, "
          f"{len(reorder)} reordered; INDEX rebuilt. "
          f"Snapshot kept at {snap}")
    return 0


def cmd_ignore(args) -> int:
    """Never record these sessions (e.g. evaluation runs). Doesn't touch a record
    that already exists — that stays until the user decides."""
    recs = reconcile.records_dir_for(str(Path.cwd()))
    for ref in args.session:
        added = record.ignore_session(recs, ref, note=args.note or "")
        print(f"automemory: {'ignoring' if added else 'already ignoring'} {ref}")
        if record.find_record(recs, ref) is not None:
            print(f"  note: it already has a record ({record.find_record(recs, ref).name}); "
                  f"ignoring stops future updates, it doesn't remove that file.")
    return 0


def cmd_doctor(_args) -> int:
    """Write one debug file for THIS project — versions, wiring, pending work, its recent
    runs, usage — for an engine-dev session to start from. No transcript text and no
    record content; home paths shown as ~. (Owner, 2026-11-04: debugging a project from
    the engine side needs one bundle, not five scattered logs.)"""
    import subprocess
    from datetime import datetime, timezone
    project = Path.cwd().resolve()
    H = str(Path.home())
    tilde = lambda x: str(x).replace(H, "~")
    recs = reconcile.records_dir_for(str(project))
    L = [f"# automemory doctor — {project.name}", "",
         f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} in `{tilde(project)}`. "
         "No transcript text or record content is included.", ""]
    def ver(cmd):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            return (r.stdout or r.stderr).strip().splitlines()[0][:80] if (r.stdout or r.stderr) else "?"
        except Exception as e:
            return f"not available ({type(e).__name__})"
    eng = Path(__file__).resolve().parents[1]
    L += ["## Versions", f"- engine: `{tilde(eng)}` at "
          f"`{ver(['git', '-C', str(eng), 'rev-parse', '--short', 'HEAD'])}`",
          f"- python: `{sys.version.split()[0]}`"]
    for t in ("claude", "opencode", "codex"):
        L.append(f"- {t}: {ver([t, '--version']) if shutil.which(t) else 'not installed'}")
    L += ["", "## Wiring"]
    cs = project / ".claude" / SETTINGS_FILE
    L.append(f"- Claude Code hooks (`.claude/{SETTINGS_FILE}`): {'wired' if is_wired(cs) else 'NOT wired'}")
    cx = project / CODEX_HOOKS
    cx_ok = cx.exists() and "hook --codex" in cx.read_text(encoding="utf-8", errors="replace")
    L.append(f"- Codex hooks (`.codex/hooks.json`): {'present' if cx_ok else 'NOT present'} "
             "(Codex runs them only after you trust them in Codex)")
    L.append(f"- opencode plugin: {'installed' if OPENCODE_PLUGIN_DEST.exists() else 'not installed'}; "
             f"consent here: {_consent_state(_consent_load().get(str(project))) or 'none'}")
    L.append(f"- engine command the hooks run: `{tilde(' '.join(engine_command()))}`")
    L += ["", "## Records", f"- folder: `{tilde(recs)}` ({'exists' if recs.is_dir() else 'MISSING'})"]
    if recs.is_dir():
        files = [p for p in recs.glob("*.md") if p.name not in (record.INDEX_NAME, record.STATUS_NAME)]
        L.append(f"- records: {len(files)}; newest: "
                 f"{max(files, key=lambda p: p.stat().st_mtime).name if files else '—'}")
        items, src, date = record.project_open_list(recs)
        L.append(f"- open items: {len(items)} (as of {date or '—'}, from {src or '—'})")
        dec = record._declined_load(recs)
        L.append(f"- ignored sessions: {len(record.ignored_sessions(recs))}; declined: {len(dec)}")
        for ref, e in list(dec.items())[-10:]:
            L.append(f"  - declined `{record.short(ref)}` at {e.get('at', '?')}, {e.get('turns')} turn(s)"
                     f" — {e.get('reason') or '(no reason saved: declined before reasons existed)'}")
        lock = recs / ".run.lock"
        if lock.exists():
            import time as _t
            L.append(f"- ⚠ run lock present, {int(_t.time() - lock.stat().st_mtime)}s old "
                     "(stale after 30 min)")
    L += ["", "## Sessions per tool"]
    for src_ in (reconcile._ClaudeCodeSource(), reconcile._OpencodeSource(), reconcile._CodexSource()):
        try:
            ids = src_.list_ids(str(project))
            pend = reconcile.find_pending(src_, recs, str(project), "")
        except Exception as e:
            L.append(f"- {src_.name}: ERROR {type(e).__name__}: {e}")
            continue
        L.append(f"- {src_.name}: {len(ids)} session(s), {len(pend)} with unrecorded turns")
        for ref, cov in pend[:10]:
            try:
                total = len(reconcile.split_turns(src_.render(ref, str(project))))
            except Exception:
                total = "?"
            L.append(f"  - `{record.short(ref)}` covered {cov if cov is not None else 'none'} of {total} turns")
    L += ["", "## Recent runs for this project (`~/.local/state/automemory/reconcile.log`)", "```"]
    log = RECONCILE_LOG
    blocks, cur = [], None
    if log.exists():
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("=== "):
                cur = [line] if f"dir={project}" in line or f"dir={tilde(project)}" in line else None
                if cur is not None:
                    blocks.append(cur)
            elif cur is not None:
                cur.append(line)
    for b in blocks[-10:]:
        L += [tilde(x) for x in b]
    L += ["```" if blocks else "(no runs logged for this folder)", ""] if blocks else ["(no runs logged for this folder)", "```", ""]
    L += ["## Recent scribe usage (`usage.jsonl`, last 5)", "```"]
    try:
        L += reconcile.USAGE_LOG.read_text(encoding="utf-8").splitlines()[-5:]
    except OSError:
        L.append("(no usage logged yet)")
    L += ["```", "", "Bring this file to an engine-dev session to start debugging."]
    out_dir = Path.home() / ".local" / "state" / "automemory" / "debug"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{record.slug(project.name)}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[:60]))
    print(f"\nautomemory doctor: wrote {tilde(out)}")
    return 0


def cmd_reconcile(args) -> int:
    return reconcile.main((["--background"] if getattr(args, "background", False) else [])
                          + (["--all"] if getattr(args, "all", False) else []))


def cmd_hook(args) -> int:
    """Claude Code hook entry. Returns immediately; never blocks the session.

    SessionStart: print the map for the agent's context, then start the
    catch-up in the background (the live session is excluded — it's starting).
    SessionEnd: start a background run that INCLUDES the ending session, so the
    last session in a project no longer needs a manual `amlog`."""
    payload = reconcile.read_payload()
    cwd = payload.get("cwd") or os.getcwd()
    codex = getattr(args, "codex", False)
    event = getattr(args, "event", None) or payload.get("hook_event_name", "SessionStart")
    payload.setdefault("hook_event_name", event)
    # AUTOMEMORY_HANDOVER=0 keeps the startup output silent for one session — used
    # by the orientation test's "without records" arm, which must not see the map
    # or open items. Recording itself is unaffected.
    if event == "SessionStart" and os.environ.get("AUTOMEMORY_HANDOVER", "1") != "0":
        text = reconcile.handover_text(reconcile.records_dir_for(cwd))
        if codex:
            # Codex hooks return JSON; additionalContext is added to the session.
            sys.stdout.write(json.dumps({"hookSpecificOutput": {
                "hookEventName": "SessionStart", "additionalContext": text}}) + "\n")
        else:
            sys.stdout.write(text)
        sys.stdout.flush()
    bg = dict(payload)
    if event == "SessionEnd":
        bg.pop("session_id", None)  # the ended session is complete: capture it too
    spawn_background(bg, cwd)
    return 0


RECONCILE_LOG = Path.home() / ".local" / "state" / "automemory" / "reconcile.log"


def spawn_background(payload: dict, cwd: str) -> None:
    """Run `reconcile --background` detached: own session (survives the hook
    returning), output appended to reconcile.log. Never raises."""
    import subprocess
    from datetime import datetime, timezone
    try:
        RECONCILE_LOG.parent.mkdir(parents=True, exist_ok=True)
        log = open(RECONCILE_LOG, "a", encoding="utf-8")
        log.write(f"\n=== {datetime.now(timezone.utc).isoformat(timespec='seconds')} "
                  f"{payload.get('hook_event_name', 'run')} "
                  f"session={payload.get('session_id', '-')} dir={cwd}\n")
        log.flush()
        p = subprocess.Popen(engine_command() + ["reconcile", "--background"],
                             cwd=cwd, stdin=subprocess.PIPE, stdout=log, stderr=log,
                             start_new_session=True)
        p.stdin.write(json.dumps(payload).encode())
        p.stdin.close()
        log.close()
    except Exception as e:  # a hook must never fail the session
        try:
            with open(RECONCILE_LOG, "a", encoding="utf-8") as fh:
                fh.write(f"automemory: background start failed: {e}\n")
        except OSError:
            pass


# --- consent (opencode adapter) -----------------------------------------------
# The opencode trigger plugin is installed globally and is DEFAULT OFF per
# project. Consent lives in one global config file keyed by directory, so turning
# recording on leaves no trace inside the project — no marker file, nothing to
# gitignore, nothing to accidentally commit.


def _consent_path() -> Path:
    return Path(os.environ.get("AUTOMEMORY_CONFIG")
                or (Path.home() / ".config" / "opencode" / "automemory.json"))


def _config_load() -> dict:
    """The whole global config: {"projects": {...}, "engine_cmd": [...]}."""
    try:
        data = json.loads(_consent_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _config_save(data: dict) -> None:
    p = _consent_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _consent_load() -> dict:
    return _config_load().get("projects", {})


def _consent_save(projects: dict) -> None:
    # Read-modify-write: other top-level keys (engine_cmd) must survive.
    data = _config_load()
    data["projects"] = projects
    _config_save(data)


def engine_command() -> list:
    """How to run THIS install of automemory, as an absolute command.

    The opencode plugin runs inside opencode's long-lived background server,
    whose PATH is frozen at whenever it started. Measured 2026-10-29: a server
    started before `~/.local/bin` joined PATH failed every trigger with
    "Executable not found in $PATH" until it was restarted. Recording the
    absolute path at setup time takes PATH out of the chain entirely."""
    exe = Path(sys.argv[0])
    if exe.name == "automemory" and exe.is_absolute() and exe.exists():
        return [str(exe)]
    found = shutil.which("automemory")
    if found:
        return [found]
    return [sys.executable, "-m", "automemory"]  # run as `python -m automemory`


def record_engine_command() -> list:
    cmd = engine_command()
    data = _config_load()
    if data.get("engine_cmd") != cmd:
        data["engine_cmd"] = cmd
        _config_save(data)
    return cmd


def _consent_state(entry) -> str:
    """Consent entries are {"state": "on"|"off"|"asked", "records_dir"?: str};
    older files stored a bare string. Accept both."""
    return entry.get("state", "") if isinstance(entry, dict) else (entry or "")


def cmd_enable(args) -> int:
    proj = str(Path(args.path).resolve() if args.path else Path.cwd())
    projects = _consent_load()
    entry: dict = {"state": "on"}
    if args.records:
        # Records live somewhere other than <project>/agent-records/. Used when a
        # project's record should be durable rather than gitignored — e.g. the
        # automemory repo's own history belongs in the private automemory-logs,
        # not thrown away so the public product ships clean.
        entry["records_dir"] = str(Path(args.records).expanduser().resolve())
    elif isinstance(projects.get(proj), dict) and projects[proj].get("records_dir"):
        entry["records_dir"] = projects[proj]["records_dir"]  # preserve on re-enable
    projects[proj] = entry
    _consent_save(projects)
    record_engine_command()  # enabling is a setup moment too — pin the engine path
    print(f"automemory: recording enabled for {proj}")
    if entry.get("records_dir"):
        print(f"  records -> {entry['records_dir']}")
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
    for proj, entry in sorted(projects.items()):
        state = entry.get("state") if isinstance(entry, dict) else entry
        extra = ""
        if isinstance(entry, dict) and entry.get("records_dir"):
            extra = f"  -> {entry['records_dir']}"
        print(f"  {state:6} {proj}{extra}")
    return 0


_CONSENT_TEXT = """\
automemory records your AI coding sessions in this folder.

  - It reads the transcripts your tools already keep (Claude Code, Codex, opencode)
    for sessions run in this folder. It never copies or moves them.
  - A separate model call (the "scribe") writes a short record of each session
    into ./agent-records/. Secrets it recognises are scrubbed first.
  - Records describe the work done here. If you commit agent-records/, that
    description travels with the repo; keep it local if it shouldn't.
  - Each record costs one small model call on your own account.
"""


def _ensure_local_ignored(project: Path, target: str = "") -> str:
    """Keep the personal hook file out of git via the repo's LOCAL exclude list
    (.git/info/exclude — never committed, affects no one else). '' if not a repo
    or already ignored."""
    import subprocess
    target = target or ".claude/" + SETTINGS_FILE
    try:
        r = subprocess.run(["git", "-C", str(project), "check-ignore", "-q", target],
                           capture_output=True)
        if r.returncode == 0:
            return ""  # already ignored (e.g. a global gitignore)
        git_dir = subprocess.run(["git", "-C", str(project), "rev-parse", "--git-dir"],
                                 capture_output=True, text=True)
        if git_dir.returncode != 0:
            return ""  # not a git repo
    except OSError:
        return ""
    excl = (project / git_dir.stdout.strip() / "info" / "exclude")
    excl.parent.mkdir(parents=True, exist_ok=True)
    with excl.open("a", encoding="utf-8") as fh:
        fh.write(f"\n# automemory: hook file with this machine's engine path\n{target}\n")
    return str(excl)


def cmd_setup(args) -> int:
    """`automemory` with no command: set this folder up for every installed tool.
    Asks first (a person at a terminal); `--yes` skips the question."""
    project = Path.cwd().resolve()
    if project in (Path.home().resolve(), Path(project.anchor)):
        print("automemory: run this inside a project folder, not your home folder or /.")
        return 1
    tools = [t for t in ("claude", "opencode", "codex") if shutil.which(t)]
    if not tools:
        print("automemory: none of `claude`, `opencode`, `codex` is installed — nothing to record.")
        return 1
    entry = _consent_load().get(str(project))
    if _consent_state(entry) == "on" and not getattr(args, "yes", False):
        print(f"automemory: already recording {project.name}.")
        return cmd_status(args)
    if not getattr(args, "yes", False):
        if not sys.stdin.isatty():
            print("automemory: not a terminal — re-run with --yes to set up without asking.")
            return 1
        print(_CONSENT_TEXT)
        answer = input(f"Record sessions in {project.name}? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("automemory: nothing changed.")
            return 0

    d = scaffold_records(project)
    projects = _consent_load()
    prev = projects.get(str(project))
    projects[str(project)] = dict(prev if isinstance(prev, dict) else {}, state="on")
    _consent_save(projects)
    record_engine_command()
    print(f"\nautomemory: recording {project.name} -> {d.relative_to(project)}/")
    if "claude" in tools:
        try:
            res = wire_hook(project, hook_command_for(project))
            print(f"  Claude Code: start + end hooks {res.replace('-', ' ')} "
                  f"(.claude/{SETTINGS_FILE}, personal, not committed)")
            excl = _ensure_local_ignored(project)
            if excl:
                print(f"  git: .claude/{SETTINGS_FILE} added to this repo's local ignore list")
        except ValueError as e:
            print(f"  Claude Code: NOT wired — {e}")
    if "opencode" in tools:
        print(f"  opencode: plugin {install_opencode_plugin()} (restart opencode to load it)")
    if "codex" in tools:
        try:
            res = wire_codex_hooks(project)
            print(f"  Codex: start + end hooks {res.replace('-', ' ')} (.codex/hooks.json). The first "
                  f"time you open Codex here it asks you to TRUST these hooks — approve them.")
            excl = _ensure_local_ignored(project, ".codex/hooks.json")
            if excl:
                print("  git: .codex/hooks.json added to this repo's local ignore list")
        except ValueError as e:
            print(f"  Codex: NOT wired — {e}")
    print("\nRecords are written in the background as sessions start and end. "
          "To record right now: automemory reconcile")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="automemory", description="passive capture engine for AI conversations")
    p.add_argument("--yes", action="store_true", help="set up without asking")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("init", help="(alias) wire this folder's triggers; prefer plain `automemory`")
    sub.add_parser("migrate", help="convert old-format records (snapshots them first)")
    sub.add_parser("status", help="record count, latest, hook state")
    sub.add_parser("peep", help="print the record index (titles)")
    p_en = sub.add_parser("enable", help="record sessions for this project (opencode adapter)")
    p_en.add_argument("path", nargs="?", help="project dir (default: cwd)")
    p_en.add_argument("--records", help="store records elsewhere (e.g. a private repo)")
    p_di = sub.add_parser("disable", help="stop recording a project")
    p_di.add_argument("path", nargs="?", help="project dir (default: cwd)")
    sub.add_parser("projects", help="list projects and their recording state")
    sub.add_parser("doctor", help="write one debug file for this project (for an engine-dev session)")
    p_rc = sub.add_parser("reconcile", help="record every session with new turns (amlog)")
    p_rc.add_argument("--background", action="store_true", help=argparse.SUPPRESS)
    p_rc.add_argument("--all", action="store_true", help="record every waiting session now (no limit)")
    p_hk = sub.add_parser("hook", help=argparse.SUPPRESS)  # Claude Code / Codex hook entrypoint
    p_hk.add_argument("--codex", action="store_true", help=argparse.SUPPRESS)
    p_hk.add_argument("--event", help=argparse.SUPPRESS)
    p_ig = sub.add_parser("ignore", help="never record these sessions (e.g. eval runs)")
    p_ig.add_argument("session", nargs="+", help="session id(s)")
    p_ig.add_argument("--note", help="why (kept as a comment in the list)")
    args = p.parse_args(argv)
    if args.cmd is None:
        return cmd_setup(args)
    return {
        "init": cmd_init, "status": cmd_status, "peep": cmd_peep, "reconcile": cmd_reconcile,
        "migrate": cmd_migrate, "hook": cmd_hook, "ignore": cmd_ignore, "doctor": cmd_doctor,
        "enable": cmd_enable, "disable": cmd_disable, "projects": cmd_projects,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
