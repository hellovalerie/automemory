"""transcript.py — provider transcripts -> readable text.

Two store adapters, ONE rendering vocabulary:

  - Claude Code: JSONL at ~/.claude/projects/<slug>/<uuid>.jsonl
    (stdlib-only port of system/scripts/transcript-to-text.sh — removes the jq dep)
  - opencode:    SQLite at ~/.local/share/opencode/opencode.db
    (tables session/message/part; ids look like `ses_…`)

Both emit user/assistant *text* turns plus CONDENSED tool markers interleaved in
order. Raw tool payloads are NEVER emitted (they can be huge and can contain
secrets); thinking/reasoning blocks and internal bookkeeping records are dropped.

Markers (identical across providers, so the scribe prompt is provider-neutral):
  [DISPATCH: <desc / first ~15 words of prompt>]   Task/Agent tool_use
  [AGENT RETURNED: <first ~25 words>]              Task/Agent tool_result
  [BASH: <cmd ~80ch> -> <first output line | error exit N>]
  [EDIT: <path>] / [WRITE: <path>]                 path only
  [TOOL <name>]                                    any other tool
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from pathlib import Path

_WS = re.compile(r"[\s\x00-\x1f]+")

# opencode part types that carry no dialogue: reasoning/thinking is dropped by
# policy (same as Claude Code's thinking blocks); the rest are bookkeeping.
_OC_SKIP_TYPES = {"step-start", "step-finish", "reasoning", "snapshot", "patch", "file"}

OPENCODE_DB_ENV = "AUTOMEMORY_OPENCODE_DB"


def clean(v) -> str:
    """Collapse whitespace/control runs to one space; trim. Mirrors jq `clean`."""
    s = "" if v is None else str(v)
    return _WS.sub(" ", s).strip()


def clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + "…"


def words(s: str, n: int) -> str:
    w = s.split()
    return " ".join(w[:n]) + (" …" if len(w) > n else "")


def _dialogue_text(content) -> str:
    """String content as-is; array -> join its text blocks (drop thinking/tool)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n\n".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def render(lines) -> str:
    """Render an iterable of JSONL strings to readable text."""
    state: dict = {}  # tool_use id -> {"name", "cmd"}
    out: list = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue  # skip malformed lines rather than abort

        rtype = r.get("type")
        msg = r.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None

        # -- state: remember each tool_use so its later tool_result can be labeled
        if rtype == "assistant" and isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    inp = b.get("input") or {}
                    state[b.get("id")] = {
                        "name": b.get("name"),
                        "cmd": clip(clean(inp.get("command", "")), 80),
                    }

        # -- dialogue (text turns only; thinking + tool blocks never rendered) --
        if rtype in ("user", "assistant") and isinstance(msg, dict):
            role = msg.get("role") or rtype
            text = _dialogue_text(content)
            if text:
                out.append(f"### {role}\n\n{text}\n\n")

        # -- assistant tool_use markers (Bash waits for its result) --
        if rtype == "assistant" and isinstance(content, list):
            for b in content:
                if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                    continue
                name = b.get("name")
                inp = b.get("input") or {}
                if name in ("Task", "Agent"):
                    desc = inp.get("description") or words(inp.get("prompt", ""), 15)
                    marker = f"[DISPATCH: {clean(desc)}]"
                elif name == "Bash":
                    continue  # emitted at result time, with its output
                elif name in ("Edit", "MultiEdit", "NotebookEdit"):
                    path = inp.get("file_path") or inp.get("notebook_path") or "?"
                    marker = f"[EDIT: {clean(path)}]"
                elif name == "Write":
                    marker = f"[WRITE: {clean(inp.get('file_path') or '?')}]"
                else:
                    marker = f"[TOOL {clean(name)}]"
                out.append(clip(marker, 200) + "\n\n")

        # -- user tool_result markers --
        if rtype == "user" and isinstance(content, list):
            for b in content:
                if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                    continue
                u = state.get(b.get("tool_use_id"), {})
                c = b.get("content")
                if isinstance(c, str):
                    res_out = c
                elif isinstance(c, list):
                    res_out = "\n".join(
                        x.get("text", "")
                        for x in c
                        if isinstance(x, dict) and x.get("type") == "text"
                    )
                else:
                    res_out = ""

                name = u.get("name")
                if name in ("Task", "Agent"):
                    marker = f"[AGENT RETURNED: {words(clean(res_out), 25)}]"
                elif name == "Bash":
                    line1 = clean(res_out.split("\n")[0] if res_out else "")
                    if b.get("is_error") is True:
                        m = re.match(r"^Exit code (\d+)", line1)
                        res = f"error exit {m.group(1)}" if m else "error: " + clip(line1, 80)
                    elif not line1:
                        res = "(no output)"
                    else:
                        res = line1
                    marker = f"[BASH: {u.get('cmd', '')} -> {res}]"
                else:
                    continue  # unknown/other tool result — no marker
                out.append(clip(marker, 200) + "\n\n")

    return "".join(out)


def render_file(path) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return render(fh)


# --- opencode adapter ----------------------------------------------------------
# opencode keeps transcripts in SQLite, not JSONL. Opened READ-ONLY and never
# written: opencode owns this file and may have it open (WAL) at the same time.
# Schema: session(id, directory, title, time_created) -> message(session_id,
# data{role,modelID,...}) -> part(message_id, data{type:text|tool|...}).
# A tool call and its result are ONE part (state.input + state.output), so there
# is no tool_use/tool_result pairing to stitch here.


def opencode_db_path(db=None) -> Path:
    """Path to opencode's store. AUTOMEMORY_OPENCODE_DB wins (tests/overrides)."""
    env = os.environ.get(OPENCODE_DB_ENV)
    return Path(db or env or (Path.home() / ".local" / "share" / "opencode" / "opencode.db"))


def _oc_connect(db=None):
    p = opencode_db_path(db)
    # mode=ro: refuse to create/modify. opencode may be running concurrently.
    return sqlite3.connect(f"file:{p}?mode=ro", uri=True)


def opencode_session_ids(directory, db=None) -> list:
    """Session ids recorded for a project directory, newest-first. Never raises."""
    try:
        con = _oc_connect(db)
    except sqlite3.Error:
        return []
    try:
        rows = con.execute(
            "select id from session where directory = ? order by time_created desc",
            (str(directory),),
        ).fetchall()
        return [r[0] for r in rows]
    except sqlite3.Error:
        return []
    finally:
        con.close()


def _oc_tool_marker(part: dict) -> str:
    """One condensed marker for an opencode tool part. Mirrors the Claude Code
    marker table; opencode tool names are lowercase."""
    st = part.get("state") or {}
    inp = st.get("input") or {}
    name = (part.get("tool") or "").lower()

    if name == "task":
        desc = inp.get("description") or words(inp.get("prompt", ""), 15)
        return f"[DISPATCH: {clean(desc)}]"

    if name == "bash":
        cmd = clip(clean(inp.get("command", "")), 80)
        out = st.get("output")
        if not isinstance(out, str):
            out = ((st.get("metadata") or {}).get("output")) or ""
        line1 = clean(out.split("\n")[0] if out else "")
        if st.get("status") == "error":
            res = "error: " + clip(line1, 80) if line1 else "error"
        else:
            res = line1 or "(no output)"
        return f"[BASH: {cmd} -> {res}]"

    if name in ("edit", "patch"):
        return f"[EDIT: {clean(inp.get('filePath') or inp.get('file_path') or '?')}]"

    if name == "write":
        return f"[WRITE: {clean(inp.get('filePath') or inp.get('file_path') or '?')}]"

    return f"[TOOL {clean(name)}]"


def render_opencode_session(session_id, db=None) -> str:
    """Render one opencode session to the shared text vocabulary. Never raises."""
    try:
        con = _oc_connect(db)
    except sqlite3.Error:
        return ""
    out: list = []
    try:
        msgs = con.execute(
            "select id, data from message where session_id = ? order by time_created, id",
            (session_id,),
        ).fetchall()
        for _mid, mdata in msgs:
            try:
                md = json.loads(mdata)
            except json.JSONDecodeError:
                continue
            role = md.get("role") or "user"
            parts = con.execute(
                "select data from part where message_id = ? order by time_created, id",
                (_mid,),
            ).fetchall()
            for (pdata,) in parts:
                try:
                    pd = json.loads(pdata)
                except json.JSONDecodeError:
                    continue
                ptype = pd.get("type")
                if ptype in _OC_SKIP_TYPES:
                    continue
                if ptype == "text":
                    text = (pd.get("text") or "").strip()
                    if text:
                        out.append(f"### {role}\n\n{text}\n\n")
                elif ptype == "tool":
                    out.append(clip(_oc_tool_marker(pd), 200) + "\n\n")
    except sqlite3.Error:
        return "".join(out)  # partial render beats none
    finally:
        con.close()
    return "".join(out)


def opencode_first_user_text(session_id, db=None) -> str:
    """The first user text part of a session (empty string if none). Never raises."""
    try:
        con = _oc_connect(db)
    except sqlite3.Error:
        return ""
    try:
        row = con.execute(
            "select p.data from part p join message m on m.id = p.message_id "
            "where p.session_id = ? and json_extract(m.data,'$.role') = 'user' "
            "  and json_extract(p.data,'$.type') = 'text' "
            "order by p.time_created, p.id limit 1",
            (session_id,),
        ).fetchone()
    except sqlite3.Error:
        return ""
    finally:
        con.close()
    if not row:
        return ""
    try:
        return (json.loads(row[0]).get("text") or "").strip()
    except json.JSONDecodeError:
        return ""


def opencode_is_scribe_session(session_id, sentinel: str, db=None) -> bool:
    """True if this session is one of the engine's own dispatch runs.

    Needed because a headless `opencode run` records its session against the
    running server's project rather than its launch cwd, so scribe sessions
    appear in the very project list the reconciler scans.

    Matched on the FIRST user message only, and by prefix — the scribe prompt
    begins with the sentinel, whereas a human session that merely *discusses*
    the sentinel mentions it mid-text. A substring LIKE anywhere in the
    transcript false-positives on exactly those sessions (verified: it caught a
    real work session that was talking about this very guard). The leading
    quote/whitespace strip is needed because `opencode run` persists the argv
    message with a leading '"' (verified 1.19.0). Never raises."""
    first = opencode_first_user_text(session_id, db=db).lstrip("\"' \t\r\n")
    return first.startswith(f"[{sentinel}")


def opencode_session_meta(session_id, db=None) -> dict:
    """Envelope metadata for one opencode session (never raises)."""
    import datetime as _dt

    blank = {"occurred": "0000-00-00", "title": "", "model": ""}
    try:
        con = _oc_connect(db)
    except sqlite3.Error:
        return blank
    try:
        row = con.execute(
            "select title, time_created from session where id = ?", (session_id,)
        ).fetchone()
        # The most frequent modelID across assistant messages (they carry it;
        # user messages do not).
        models = [
            m for (m,) in con.execute(
                "select json_extract(data,'$.modelID') from message "
                "where session_id = ? and json_extract(data,'$.modelID') is not null",
                (session_id,),
            ) if m
        ]
        model = max(set(models), key=models.count) if models else ""
        first_prompt = con.execute(
            "select p.data from part p join message m on m.id = p.message_id "
            "where p.session_id = ? and json_extract(m.data,'$.role') = 'user' "
            "  and json_extract(p.data,'$.type') = 'text' "
            "order by p.time_created, p.id limit 1",
            (session_id,),
        ).fetchone()
    except sqlite3.Error:
        return blank
    finally:
        con.close()

    title, created = (row[0], row[1]) if row else ("", None)
    if first_prompt:
        try:
            title = title or (json.loads(first_prompt[0]).get("text") or "").strip()
        except json.JSONDecodeError:
            pass

    occurred = ""
    if created:
        occurred = _dt.datetime.fromtimestamp(
            created / 1000, _dt.timezone.utc).strftime("%Y-%m-%d")
    if not title:
        title = f"session {session_id[:8]}"
    return {"occurred": occurred or "0000-00-00", "title": title[:120], "model": model}


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "/dev/stdin"
    sys.stdout.write(render_file(src))
