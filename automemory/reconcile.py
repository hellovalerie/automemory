"""reconcile.py — the SessionStart reconciler (Engine A keystone).

Runs from the SessionStart hook (hook payload on stdin). Three jobs:
  1. Handover-in: print this project's map (agent-records/INDEX.md) + a fast-path
     reminder to stdout -> injected into the starting session's context.
  2. Find prior sessions in THIS project with a transcript but no record.
  3. Backfill the newest N (default 5): transcript -> redact -> author (dispatch)
     -> validate (retry once) -> write record -> rebuild INDEX. Defer the rest.

Records live per-project at <cwd>/agent-records/. Transcripts are read from Claude
Code's store ~/.claude/projects/<slug>/. A hook is non-interactive, so backfill is
bounded (cap) and the rest is deferred to `automemory process` (a terminal).

Core functions take their dirs and a dispatch_fn injected, so they are testable
without real model calls; main() wires the real paths + real dispatch.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from . import dispatch, record, redact, transcript

DEFAULT_CAP = 5
DEFAULT_MODEL = "haiku"  # the cheap backfill scribe (record.model = the session's model)
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


# --- core (testable) ----------------------------------------------------------


def find_missing(transcripts_dir: Path, records_dir: Path, current_session: str):
    """Transcripts with no record yet (excluding the live session), newest-first."""
    out = []
    if not Path(transcripts_dir).is_dir():
        return out
    for p in Path(transcripts_dir).glob("*.jsonl"):
        if p.stem == current_session:
            continue
        if record.record_exists_for_session(records_dir, p.stem):
            continue
        out.append(p)
    out.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return out


def backfill_one(transcript_path, records_dir, project, prompt, model, dispatch_fn):
    """Render -> redact -> author (1 retry on malformed) -> write. Returns an
    outcome dict; never raises."""
    uuid = Path(transcript_path).stem
    text = transcript.render_file(transcript_path)
    if not text.strip():
        return {"uuid": uuid, "status": "empty", "cost": 0.0}
    scrubbed, _ = redact.redact(text)

    result = None
    for _ in (1, 2):  # author, retry once on malformed
        result = dispatch_fn(scrubbed, prompt, model=model)
        if result.is_error:
            return {"uuid": uuid, "status": "dispatch-error", "error": result.error,
                    "cost": result.cost_usd}
        verdict = _valid_or_norecord(result.text)
        if verdict == "norecord":
            return {"uuid": uuid, "status": "no-record", "cost": result.cost_usd}
        if verdict == "ok":
            break
    if _valid_or_norecord(result.text) != "ok":
        return {"uuid": uuid, "status": "malformed", "cost": result.cost_usd}

    meta = _transcript_meta(Path(transcript_path))
    secs = _split_sections(result.text)
    path = record.write_record(
        records_dir,
        provider="claude-code", session_ref=uuid, model=meta["model"],
        subject=project, title=meta["title"], occurred=meta["occurred"],
        done=secs.get("Done", ""),
        learnings=secs.get("Learnings & Gotchas", ""),
        outstanding=secs.get("Outstanding", ""),
    )
    return {"uuid": uuid, "status": "ok", "cost": result.cost_usd, "path": str(path)}


def run(payload: dict, *, claude_projects_root=None, dispatch_fn=None,
        cap: int = DEFAULT_CAP, out=None) -> dict:
    """Orchestrate handover-in + bounded backfill. Returns a summary dict."""
    out = out or sys.stdout
    cwd = payload.get("cwd") or os.getcwd()
    current = payload.get("session_id") or ""
    project = Path(cwd).name or "project"
    records_dir = Path(cwd) / "agent-records"
    projects_root = Path(claude_projects_root or (Path.home() / ".claude" / "projects"))
    transcripts_dir = projects_root / _slug_for_cwd(cwd)
    dispatch_fn = dispatch_fn or dispatch.author
    prompt = _load_prompt()

    # 1. handover-in
    idx = records_dir / "INDEX.md"
    if idx.exists():
        out.write("=== automemory: session map ===\n")
        out.write(idx.read_text(encoding="utf-8").rstrip() + "\n\n")
    out.write("automemory: at a natural checkpoint, write/refresh this session's record "
              "(## Done required, else skip) into agent-records/.\n")

    # 2. find missing
    missing = find_missing(transcripts_dir, records_dir, current)
    todo, deferred = missing[:cap], missing[cap:]

    # 3. backfill
    total_cost, wrote = 0.0, 0
    outcomes = []
    for tp in todo:
        r = backfill_one(tp, records_dir, project, prompt, DEFAULT_MODEL, dispatch_fn)
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

    return {"missing": len(missing), "processed": len(todo), "wrote": wrote,
            "deferred": len(deferred), "cost": total_cost, "outcomes": outcomes}


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}
    run(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
