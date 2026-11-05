"""Commit and push a project's records on their own branch (owner, 2026-11-05).

Records live on a dedicated `agent-records` branch, never on the project's code branches
— they shouldn't ship with whatever is on `main`. `<project>/agent-records/` becomes a git
worktree of that branch (a second drawer in the same cabinet), kept out of the code
branches by the repo's local exclude list. Agents read the folder as before; the engine
commits only inside it, so it can never commit or push the person's code.

Per project, the person confirms once (remote URL and visibility shown) at a terminal;
background runs never ask. The answer — push / commit / off — is kept with the folder's
other state in the consent file. The `git` setting caps it machine-wide.
"""
import filecmp
import os
import shutil
import subprocess
import time
from pathlib import Path

from . import record, settings

BRANCH = "agent-records"
FOLDER = "agent-records"
TAG = "[skip ci]"  # records need no CI run
_ORDER = ("off", "commit", "push")


def _git(cwd, *args, timeout=60):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")  # a background run must never wait on a password
    try:
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                              timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(args, 1, "", str(e))


def _ok(cwd, *args, timeout=60) -> bool:
    return _git(cwd, *args, timeout=timeout).returncode == 0


def is_repo(project) -> bool:
    return _ok(project, "rev-parse", "--git-dir")


def is_records_worktree(records_dir) -> bool:
    d = Path(records_dir)
    return ((d / ".git").is_file()
            and _git(d, "symbolic-ref", "--short", "HEAD").stdout.strip() == BRANCH)  # works before the first commit


def remote_url(project) -> str:
    return _git(project, "remote", "get-url", "origin").stdout.strip()


def visibility(url: str) -> str:
    """public / private / unknown — GitHub only (via `gh`), and only when it answers."""
    import re
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", url or "")
    if not m or not shutil.which("gh"):
        return "unknown"
    try:
        r = subprocess.run(["gh", "repo", "view", f"{m.group(1)}/{m.group(2)}", "--json",
                            "visibility", "-q", ".visibility"], capture_output=True, text=True,
                           timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    v = r.stdout.strip().lower()
    return v if r.returncode == 0 and v in ("public", "private", "internal") else "unknown"


# --- the per-project answer, kept with the folder's other state -------------------

def decision(project) -> str:
    from . import cli
    entry = cli._consent_load().get(str(Path(project).resolve()))
    return entry.get("git", "") if isinstance(entry, dict) else ""


def save_decision(project, mode: str) -> None:
    from . import cli
    projects = cli._consent_load()
    key = str(Path(project).resolve())
    entry = projects.get(key)
    entry = dict(entry) if isinstance(entry, dict) else ({"state": entry} if entry else {})
    entry["git"] = mode
    projects[key] = entry
    cli._consent_save(projects)


def mode(project) -> str:
    """What the engine may do here: the project's answer, capped by the `git` setting.
    "" = not asked yet."""
    cap = settings.get("git")
    if cap == "off":
        return "off"
    d = decision(project)
    if not d:
        return ""
    return min(d, cap, key=_ORDER.index)


# --- one-time setup: the folder becomes a worktree of the records branch -----------

def setup(project, want: str, out) -> bool:
    """Make <project>/agent-records a worktree of `agent-records`, carrying over the
    records already there (snapshot first, byte-checked copy). If the code branch
    tracked the folder, one commit there stops tracking it — refused if the person
    has other changes staged, so nothing of theirs is swept in."""
    project = Path(project).resolve()
    recs = project / FOLDER
    say = lambda s: out.write(f"automemory: {s}\n")
    if not is_repo(project):
        say("not a git repository — nothing to commit to.")
        return False
    if is_records_worktree(recs):
        save_decision(project, want)
        return True
    lock = recs / ".run.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime < 30 * 60:
        say("a recording run is writing to this folder right now — try again in a minute.")
        return False
    tracked = _git(project, "ls-files", "--", FOLDER).stdout.strip()
    if tracked and not _ok(project, "diff", "--cached", "--quiet"):
        say("you have staged changes. Commit or unstage them first — moving the records "
            "needs one commit on your branch and must not sweep yours in.")
        return False

    aside = None
    if recs.exists() and any(recs.iterdir()):
        snap = record.snapshot(recs, label="records-branch")
        say(f"snapshot of your records kept at {snap}")
        aside = project / f".agent-records-moving-{int(time.time())}"
        recs.rename(aside)
    elif recs.exists():
        recs.rmdir()

    def restore(why):
        if recs.exists() and not any(recs.iterdir()):
            recs.rmdir()
        if aside is not None and not recs.exists():
            aside.rename(recs)
        say(f"records branch not set up: {why}. Your records are where they were.")
        return False

    has_remote = bool(remote_url(project))
    if has_remote:
        _git(project, "fetch", "-q", "origin", BRANCH, timeout=60)
    local = _ok(project, "rev-parse", "--verify", "-q", f"refs/heads/{BRANCH}")
    remote = has_remote and _ok(project, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{BRANCH}")
    if local:
        r = _git(project, "worktree", "add", "-q", str(recs), BRANCH)
    elif remote:
        r = _git(project, "worktree", "add", "-q", "--track", "-b", BRANCH, str(recs), f"origin/{BRANCH}")
    else:
        r = _git(project, "worktree", "add", "-q", "--orphan", "-b", BRANCH, str(recs))
    if r.returncode != 0:
        return restore(r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "git worktree failed")

    if aside is not None:
        for src in aside.rglob("*"):
            rel = src.relative_to(aside)
            if src.is_dir() or rel.name == ".run.lock":
                continue
            dst = recs / rel
            if dst.exists() and not filecmp.cmp(src, dst, shallow=False):
                _git(project, "worktree", "remove", "--force", str(recs))
                return restore(f"{rel} differs between your folder and the existing "
                               f"{BRANCH} branch")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        bad = [p for p in aside.rglob("*") if p.is_file() and p.name != ".run.lock"
               and not filecmp.cmp(p, recs / p.relative_to(aside), shallow=False)]
        if bad:
            _git(project, "worktree", "remove", "--force", str(recs))
            return restore(f"copy check failed for {bad[0].name}")
        shutil.rmtree(aside)  # verified copy in place + snapshot kept outside the project

    gi = recs / ".gitignore"
    if not gi.exists():
        gi.write_text(".run.lock\n", encoding="utf-8")
    from . import cli
    cli._ensure_local_ignored(project, FOLDER + "/",
                              note=f"records live on the {BRANCH} branch (a worktree here)")
    if tracked:
        _git(project, "rm", "-r", "-q", "--cached", "--", FOLDER)
        c = _git(project, "commit", "-q", "-m",
                 f"automemory: records move to the {BRANCH} branch {TAG}")
        branch = _git(project, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if c.returncode == 0:
            say(f"committed on {branch}: {FOLDER}/ is no longer tracked there (not pushed — "
                f"it goes up with your next push)")
    save_decision(project, want)
    say(f"{FOLDER}/ is now the {BRANCH} branch")
    line = sync(project, recs)
    if line:
        out.write(line + "\n")
    return True


# --- every run: commit (and push) what changed ------------------------------------

def sync(project, records_dir) -> str:
    """Commit the records folder; push its branch if allowed. Returns one line for the
    run summary ("" when there was nothing to do). Never raises, never touches the
    person's code branches."""
    project = Path(project).resolve()
    m = mode(project)
    if m in ("", "off") or Path(records_dir).resolve() != project / FOLDER:
        return ""
    recs = project / FOLDER
    if not is_records_worktree(recs):
        return "automemory: records not committed — the records branch isn't set up here."
    _git(recs, "add", "-A", ".")
    changed = not _ok(recs, "diff", "--cached", "--quiet")
    if changed:
        n = len([l for l in _git(recs, "diff", "--cached", "--name-only").stdout.splitlines()
                 if l.endswith(".md")])
        c = _git(recs, "commit", "-q", "-m", f"records: {n} file(s) updated {TAG}")
        if c.returncode != 0:
            return f"automemory: records commit failed — {(c.stderr or c.stdout).strip()[:200]}"
    if m != "push" or not remote_url(project):
        return f"automemory: records committed on {BRANCH}." if changed else ""
    ahead = _git(recs, "rev-list", "--count", f"origin/{BRANCH}..HEAD")
    if ahead.returncode == 0 and ahead.stdout.strip() == "0":
        return ""  # nothing unpushed
    p = _git(recs, "push", "-q", "-u", "origin", BRANCH, timeout=90)
    if p.returncode != 0:
        # Someone else pushed records first (another machine): replay ours on top. The
        # branch holds only records, so this never touches code.
        if _ok(recs, "pull", "-q", "--rebase", "origin", BRANCH, timeout=90):
            p = _git(recs, "push", "-q", "-u", "origin", BRANCH, timeout=90)
        else:
            _git(recs, "rebase", "--abort")
    if p.returncode != 0:
        why = (p.stderr or p.stdout).strip().splitlines()
        return (f"automemory: records committed but NOT pushed — "
                f"{why[-1][:200] if why else 'push failed'}. The next run tries again.")
    return f"automemory: records pushed to origin/{BRANCH}."


# --- the one question, at a terminal ------------------------------------------------

def ask(project, out, input_fn=input) -> str:
    """Ask once whether to commit and push this project's records. Returns the mode
    chosen ("" if this isn't a place to ask)."""
    project = Path(project).resolve()
    cap = settings.get("git")
    if cap == "off" or decision(project) or not is_repo(project):
        return ""
    url = remote_url(project)
    vis = visibility(url) if url else ""
    out.write(f"\nautomemory can commit this project's records to their own branch, "
              f"`{BRANCH}`, separate from your code")
    out.write(f", and push it to:\n  origin = {url}  ({vis})\n" if url and cap == "push"
              else ".\n")
    if vis == "public":
        out.write("  ⚠ This repository is PUBLIC: anyone could read your session records.\n")
    elif url and vis == "unknown":
        out.write("  (couldn't tell whether this repository is public)\n")
    out.write("Records summarise your sessions. Your code branches are never touched.\n")
    can_push = bool(url) and cap == "push"
    q = "Commit and push records? [y/N, c = commit only] " if can_push else "Commit records? [y/N] "
    try:
        a = input_fn(q).strip().lower()
    except EOFError:
        a = ""
    if a in ("y", "yes"):
        want = "push" if can_push else "commit"
    elif a == "c" and can_push:
        want = "commit"
    else:
        save_decision(project, "off")
        out.write("automemory: records stay uncommitted here (change later: "
                  "automemory git push).\n")
        return "off"
    return want if setup(project, want, out) else ""
