"""reconcile.py — the SessionStart reconciler (Engine A keystone).

Runs from the SessionStart hook (hook payload on stdin). Three jobs:
  1. Handover-in: print this project's map (agent-records/INDEX.md) + a fast-path
     reminder to stdout -> injected into the starting session's context.
  2. Find prior sessions in THIS project with a transcript but no record.
  3. Backfill the newest N (default 5): transcript -> redact -> author (dispatch)
     -> validate (retry once) -> write record -> rebuild INDEX. Defer the rest.

Records live per-project at <cwd>/agent-records/. Transcripts come from a
PROVIDER ADAPTER (see `_Source` below): Claude Code's JSONL store, or opencode's
SQLite store. A hook is non-interactive, so backfill is bounded (cap) and the
rest is deferred to `automemory process` (a terminal).

Core functions take their source, dirs and a dispatch_fn injected, so they are
testable without real model calls; main() wires the real paths + real dispatch.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import dispatch, providers, record, redact, transcript

DEFAULT_CAP = 5
# The scribe model is NOT a constant — see providers.py. `DEFAULT_MODEL = "haiku"`
# used to live here and broke every opencode backfill (its model namespace rejects
# "haiku"). Callers must go through dispatch.default_model(provider).
PROVIDER_ENV = "AUTOMEMORY_PROVIDER"
_ENGINE_ROOT = Path(__file__).resolve().parents[1]
_PROMPT_PATH = _ENGINE_ROOT / "system" / "engine" / "prompts" / "record-author.md"


def _slug_for_cwd(cwd: str) -> str:
    """Claude Code's project-store dir name: every non-alphanumeric char -> '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def _load_prompt() -> str:
    try:
        return _PROMPT_PATH.read_text(encoding="utf-8")
    except OSError:
        return ("From the session transcript output '## Done' (required; else exactly "
                "'NO-RECORD'), plus optional '## Learnings & Gotchas' / '## Outstanding'. "
                "Facts only, from the transcript.")


def _split_sections(md: str) -> dict:
    """Split authored markdown into {heading: body}."""
    secs, cur, buf = {}, None, []
    for line in md.splitlines():
        m = re.match(r"^##\s+(.*)", line)
        if m:
            if cur is not None:
                secs[cur] = "\n".join(buf).strip()
            cur, buf = m.group(1).strip(), []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        secs[cur] = "\n".join(buf).strip()
    return secs


def _valid_or_norecord(text: str) -> str:
    """'norecord' (write-gate said no) | 'ok' (has a non-empty Done) | 'bad'."""
    t = (text or "").strip()
    if t.upper().startswith("NO-RECORD"):
        return "norecord"
    if "## Done" in t and t.split("## Done", 1)[1].strip():
        return "ok"
    return "bad"


def _transcript_meta(path: Path) -> dict:
    """Best-effort envelope metadata from the transcript (never raises)."""
    tss, title, model, first_prompt = [], "", "", ""
    try:
        for line in path.open(encoding="utf-8", errors="replace"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("timestamp"):
                tss.append(r["timestamp"])
            t = r.get("type")
            if t in ("custom-title", "ai-title") and not title:
                title = (r.get("title") or r.get("customTitle") or r.get("content")
                         or r.get("text") or "").strip()
            if not model:
                m = r.get("message")
                cand = (m.get("model") if isinstance(m, dict) else None) or r.get("model")
                if cand:
                    model = cand
            if not first_prompt and t == "user":
                m = r.get("message")
                c = m.get("content") if isinstance(m, dict) else None
                if isinstance(c, str):
                    first_prompt = c
    except OSError:
        pass
    occurred = min(tss)[:10] if tss else "0000-00-00"
    if not title:
        title = " ".join(first_prompt.split()[:8]) or f"session {path.stem[:8]}"
    return {"occurred": occurred, "title": title[:120], "model": model}


# --- provider adapters ---------------------------------------------------------
# The reconciler's only provider-specific surface. Everything above this line
# deals in opaque session refs; everything below knows where a provider keeps
# its transcripts and what shape they are.


@dataclass
class _Source:
    """A provider's transcript store. Three ops, all best-effort, never raising."""

    name: str

    def list_ids(self, cwd: str) -> list:  # pragma: no cover - interface
        """Session ids for a project directory, newest-first."""
        raise NotImplementedError

    def render(self, ref: str, cwd: str = None) -> str:  # pragma: no cover
        """Session transcript -> the shared readable-text vocabulary."""
        raise NotImplementedError

    def meta(self, ref: str, cwd: str = None) -> dict:  # pragma: no cover
        """{occurred, title, model} best-effort."""
        raise NotImplementedError

    def is_scribe(self, ref: str) -> bool:  # pragma: no cover
        """True if this session is the engine's own dispatch output (skip it)."""
        return False


class _ClaudeCodeSource(_Source):
    """Claude Code: one JSONL file per session under ~/.claude/projects/<slug>/.

    The project slug is derived from the session's cwd, so render/meta need it
    (opencode session ids are global and ignore it)."""

    name = "claude-code"

    def __init__(self, projects_root=None):
        self.projects_root = Path(
            projects_root or (Path.home() / ".claude" / "projects")
        )

    def _path(self, ref: str, cwd: str = None) -> Path:
        return self.projects_root / _slug_for_cwd(cwd or "") / f"{ref}.jsonl"

    def list_ids(self, cwd: str) -> list:
        d = self.projects_root / _slug_for_cwd(cwd)
        if not d.is_dir():
            return []
        ids = [p.stem for p in d.glob("*.jsonl")]
        # Newest-first by file mtime, matching the reconciler's ordering.
        return sorted(ids, key=lambda s: (self._path(s, cwd)).stat().st_mtime,
                      reverse=True)

    def render(self, ref: str, cwd: str = None) -> str:
        return transcript.render_file(self._path(ref, cwd))

    def meta(self, ref: str, cwd: str = None) -> dict:
        return _transcript_meta(self._path(ref, cwd))

    def is_scribe(self, ref: str, cwd: str = None) -> bool:
        """Claude Code's neutral-cwd isolation usually keeps the scribe's own
        transcripts out of the scanned project entirely, so this is belt-and-
        braces — but the guard is cheap and the failure it prevents (the engine
        silently authoring records about itself) is expensive."""
        p = self._path(ref, cwd)
        try:
            with p.open(encoding="utf-8", errors="replace") as fh:
                return dispatch.SCRIBE_SENTINEL in fh.read(200_000)
        except OSError:
            return False


class _OpencodeSource(_Source):
    """opencode: one SQLite store at ~/.local/share/opencode/opencode.db, rows
    keyed by session id, scoped by the session's recorded directory."""

    name = "opencode"

    def __init__(self, db=None):
        self.db = db

    def list_ids(self, cwd: str) -> list:
        return transcript.opencode_session_ids(cwd, db=self.db)

    def render(self, ref: str, cwd: str = None) -> str:
        return transcript.render_opencode_session(ref, db=self.db)

    def meta(self, ref: str, cwd: str = None) -> dict:
        return transcript.opencode_session_meta(ref, db=self.db)

    def is_scribe(self, ref: str) -> bool:
        return transcript.opencode_is_scribe_session(ref, dispatch.SCRIBE_SENTINEL,
                                                    db=self.db)


def resolve_source(cwd: str, provider: str = None, **kw) -> _Source:
    """Pick a provider adapter. Explicit provider > AUTOMEMORY_PROVIDER >
    auto-detect (whichever store actually has sessions for this cwd)."""
    requested = provider or os.environ.get(PROVIDER_ENV) or ""
    if requested:
        # Only short-circuit when the caller actually named one. Falling through
        # providers.resolve("") here would return DEFAULT and make auto-detect
        # unreachable — which is exactly the bug this guard prevents.
        p = providers.resolve(requested)
        if p.name == "claude-code":
            return _ClaudeCodeSource(projects_root=kw.get("claude_projects_root"))
        if p.name == "opencode":
            return _OpencodeSource(db=kw.get("opencode_db"))

    oc = _OpencodeSource(db=kw.get("opencode_db"))
    if oc.list_ids(cwd):
        return oc
    cc = _ClaudeCodeSource(projects_root=kw.get("claude_projects_root"))
    return cc if cc.list_ids(cwd) else oc


# --- core (testable) ----------------------------------------------------------


def find_missing(source: _Source, records_dir: Path, cwd: str, current_session: str):
    """Session refs with a transcript but no record yet (excluding the live
    session and the engine's own scribe sessions), newest-first. Idempotency
    key is the session ref."""
    out = []
    for ref in source.list_ids(cwd):
        if ref == current_session:
            continue
        if source.is_scribe(ref, cwd):
            continue
        if record.record_exists_for_session(records_dir, ref):
            continue
        out.append(ref)
    return out


def backfill_one(source: _Source, ref: str, records_dir: Path, project: str,
                 prompt: str, model: str, dispatch_fn, cwd: str = None):
    """Render -> redact -> author (1 retry on malformed) -> write. Returns an
    outcome dict; never raises."""
    text = source.render(ref, cwd)
    if not text.strip():
        return {"uuid": ref, "status": "empty", "cost": 0.0}
    scrubbed, _ = redact.redact(text)

    result = None
    # Two distinct retries, because two distinct failures:
    #  - malformed output from the scribe (weak-model stochasticity)
    #  - a DISPATCH error, which is often transient. Observed 2026-10-15: the
    #    opencode free tier 403s intermittently under repeated calls — the same
    #    payload succeeded on one run and failed the next four. A provider that
    #    drops the occasional request shouldn't cost the session its record.
    # "not found" and malformed-binary errors are permanent; don't hammer those.
    _PERMANENT = ("not found", "no such file")
    dispatch_tries, max_dispatch_tries = 0, 3
    for _ in (1, 2):  # author, retry once on malformed
        result = dispatch_fn(scrubbed, prompt, model=model)
        while result.is_error and dispatch_tries < max_dispatch_tries:
            if any(p in result.error.lower() for p in _PERMANENT):
                break
            dispatch_tries += 1
            time.sleep(min(2 ** dispatch_tries, 8))
            result = dispatch_fn(scrubbed, prompt, model=model)
        if result.is_error:
            return {"uuid": ref, "status": "dispatch-error", "error": result.error,
                    "cost": result.cost_usd}
        verdict = _valid_or_norecord(result.text)
        if verdict == "norecord":
            return {"uuid": ref, "status": "no-record", "cost": result.cost_usd}
        if verdict == "ok":
            break
    if _valid_or_norecord(result.text) != "ok":
        return {"uuid": ref, "status": "malformed", "cost": result.cost_usd}

    meta = source.meta(ref, cwd)
    secs = _split_sections(result.text)
    path = record.write_record(
        records_dir,
        provider=source.name, session_ref=ref, model=meta["model"],
        subject=project, title=meta["title"], occurred=meta["occurred"],
        done=secs.get("Done", ""),
        learnings=secs.get("Learnings & Gotchas", ""),
        outstanding=secs.get("Outstanding", ""),
    )
    return {"uuid": ref, "status": "ok", "cost": result.cost_usd, "path": str(path)}


def records_dir_for(cwd: str) -> Path:
    """Where this project's records live.

    Default is <project>/agent-records/. A project may redirect them elsewhere
    (consent entry `records_dir`) — used when a project's history should be
    durable in a private repo rather than gitignored out of a public one. Same
    lookup as the plugin's consent gate, so the two can't disagree.
    """
    default = Path(cwd) / "agent-records"
    try:
        cfg = Path(os.environ.get("AUTOMEMORY_CONFIG")
                   or (Path.home() / ".config" / "opencode" / "automemory.json"))
        entry = json.loads(cfg.read_text(encoding="utf-8")).get("projects", {}) \
            .get(str(Path(cwd).resolve()), {})
        if isinstance(entry, dict) and entry.get("records_dir"):
            return Path(entry["records_dir"]) / "agent-records"
    except (OSError, ValueError, KeyError):
        pass
    return default


def run(payload: dict, *, provider: str = None, claude_projects_root=None,
        opencode_db=None, dispatch_fn=None, cap: int = DEFAULT_CAP, out=None) -> dict:
    """Orchestrate handover-in + bounded backfill. Returns a summary dict."""
    out = out or sys.stdout
    cwd = payload.get("cwd") or os.getcwd()
    current = payload.get("session_id") or ""
    project = Path(cwd).name or "project"
    records_dir = records_dir_for(cwd)
    source = resolve_source(cwd, provider=provider,
                            claude_projects_root=claude_projects_root,
                            opencode_db=opencode_db)
    dispatch_fn = dispatch_fn or dispatch.DISPATCHERS.get(
        source.name, dispatch.author)
    prompt = _load_prompt()

    # 1. handover-in
    idx = records_dir / "INDEX.md"
    if idx.exists():
        out.write("=== automemory: session map ===\n")
        out.write(idx.read_text(encoding="utf-8").rstrip() + "\n\n")
    out.write("automemory: at a natural checkpoint, write/refresh this session's record "
              "(## Done required, else skip) into agent-records/.\n")

    # 2. find missing
    missing = find_missing(source, records_dir, cwd, current)
    todo, deferred = missing[:cap], missing[cap:]

    # 3. backfill
    total_cost, wrote = 0.0, 0
    outcomes = []
    for ref in todo:
        r = backfill_one(source, ref, records_dir, project, prompt,
                         dispatch.default_model(source.name), dispatch_fn, cwd)
        outcomes.append(r)
        total_cost += r.get("cost", 0.0)
        if r["status"] == "ok":
            wrote += 1
    if wrote:
        record.rebuild_index(records_dir, records_dir / "INDEX.md", records_link_prefix="")
        out.write(f"automemory: backfilled {wrote} session(s), ~${total_cost:.2f}.\n")
    if deferred:
        out.write(f"automemory: {len(deferred)} older session(s) pending — "
                  f"run `automemory process` to catch up.\n")

    return {"provider": source.name, "missing": len(missing), "processed": len(todo),
            "wrote": wrote, "deferred": len(deferred), "cost": total_cost,
            "outcomes": outcomes}


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}
    run(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
