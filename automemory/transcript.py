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
#
# ── TWO SCHEMAS ───────────────────────────────────────────────────────────────
# v1 (<=1.19): session/message/part. Parts in their own table; tool parts carry
#     `tool` and `state.output`. Session ids look like `ses_ec…`.
# v2 (2.x):   session_v2/session_message. NO parts table — content blocks are
#     inlined into the message (`content: [...]`); tool blocks carry `name` and
#     `state.content[]`. Session ids use a different prefix (`ses_eb…`).
#
# v2 FREEZES the v1 tables rather than dropping them (verified 2026-10-16: the v1
# tables stop receiving rows at the moment of upgrade — part/message newest row
# stayed at the upgrade instant while session_message kept growing). An upgraded
# install therefore has both, with live content only in v2. We read both and
# render either into the same vocabulary, which is why the renderer was factored
# to a neutral shape: old sessions stay backfillable across the upgrade.


def opencode_db_path(db=None) -> Path:
    """Path to opencode's store. AUTOMEMORY_OPENCODE_DB wins (tests/overrides)."""
    env = os.environ.get(OPENCODE_DB_ENV)
    return Path(db or env or (Path.home() / ".local" / "share" / "opencode" / "opencode.db"))


def _oc_connect(db=None):
    p = opencode_db_path(db)
    # mode=ro: refuse to create/modify. opencode may be running concurrently.
    return sqlite3.connect(f"file:{p}?mode=ro", uri=True)


def _has_table(con, name: str) -> bool:
    return con.execute(
        "select 1 from sqlite_master where type='table' and name=?", (name,)
    ).fetchone() is not None


def opencode_session_ids(directory, db=None) -> list:
    """Session ids for a project directory, newest-first, across BOTH schemas.

    Ordered by LAST ACTIVITY (time_updated), not creation time. Creation order is
    wrong for backfill priority: a long-running session is current work but looks
    ancient. Measured 2026-10-16 — a session open since Oct 15 sat at position 52
    of 54 and would never be reached under a cap, purely because it started
    early. Never raises; duplicates impossible."""
    try:
        con = _oc_connect(db)
    except sqlite3.Error:
        return []
    out = []
    try:
        if _has_table(con, "session_v2"):
            out += list(con.execute(
                "select id, coalesce(time_updated, time_created) from session_v2 "
                "where directory = ?",
                (str(directory),)))
        if _has_table(con, "session"):
            out += list(con.execute(
                "select id, coalesce(time_updated, time_created) from session "
                "where directory = ?",
                (str(directory),)))
    except sqlite3.Error:
        return [s for s, _ in out]
    finally:
        con.close()
    out.sort(key=lambda p: p[1] or 0, reverse=True)
    seen, uniq = set(), []
    for s, _ in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


def _oc_output(state: dict) -> str:
    """Tool output text, tolerating both schemas and both output conventions."""
    c = state.get("content")
    if isinstance(c, list):
        joined = "\n".join(
            b.get("text", "") for b in c
            if isinstance(b, dict) and b.get("type") == "text"
        )
        if joined:
            return joined
    out = state.get("output")
    if isinstance(out, str):
        return out
    md = state.get("metadata") or {}
    return md.get("output") if isinstance(md.get("output"), str) else ""


def _oc_path(inp: dict) -> str:
    """The path argument, tolerating every key v2 actually uses. Observed in one
    store, in the same schema: `path`, `filePath` and `file_path` all appear for
    read/edit/write. Assuming one key loses the marker for the others."""
    for k in ("filePath", "file_path", "path", "notebook_path", "notebookPath"):
        v = inp.get(k)
        if v:
            return str(v)
    return "?"


def _oc_tool_marker(block: dict) -> str:
    """One condensed marker for an opencode tool block. Mirrors the Claude Code
    marker table. Handles both schemas: v1 (`tool` + `state.output`) and v2
    (`name` + `state.content[]`, and `shell` alongside `bash`)."""
    st = block.get("state") or {}
    inp = st.get("input") or {}
    name = (block.get("name") or block.get("tool") or "").lower()

    if name == "task":
        desc = inp.get("description") or words(inp.get("prompt", ""), 15)
        return f"[DISPATCH: {clean(desc)}]"

    if name in ("bash", "shell"):  # v2 renamed bash -> shell for some tools
        raw = _oc_output(st)
        line1 = clean(raw.split("\n")[0] if raw else "")
        if st.get("status") == "error":
            res = "error: " + clip(line1, 80) if line1 else "error"
        else:
            res = line1 or "(no output)"
        return f"[BASH: {clip(clean(inp.get('command','')), 80)} -> {res}]"

    if name in ("edit", "patch", "multiedit"):
        return f"[EDIT: {clean(_oc_path(inp))}]"
    if name == "write":
        return f"[WRITE: {clean(_oc_path(inp))}]"
    return f"[TOOL {clean(name)}]"


def _render_v2(con, session_id) -> str:
    """v2: content blocks are inlined into session_message rows, ordered by seq."""
    out: list = []
    rows = con.execute(
        "select type, data from session_message where session_id = ? order by seq",
        (session_id,),
    ).fetchall()
    for mtype, mdata in rows:
        try:
            d = json.loads(mdata)
        except json.JSONDecodeError:
            continue
        if mtype == "user":
            text = (d.get("text") or "").strip()
            if text:
                out.append(f"### user\n\n{text}\n\n")
        elif mtype == "assistant":
            for b in d.get("content") or []:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt == "text":
                    text = (b.get("text") or "").strip()
                    if text:
                        out.append(f"### assistant\n\n{text}\n\n")
                elif bt == "tool":
                    out.append(clip(_oc_tool_marker(b), 200) + "\n\n")
                # reasoning / step markers carry nothing durable — dropped by policy
    return "".join(out)


def _render_v1(con, session_id) -> str:
    """v1: parts live in their own table, joined to messages."""
    out: list = []
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
        for (pdata,) in con.execute(
            "select data from part where message_id = ? order by time_created, id",
            (_mid,),
        ).fetchall():
            try:
                pd = json.loads(pdata)
            except json.JSONDecodeError:
                continue
            ptype = pd.get("type")
            if ptype == "text":
                text = (pd.get("text") or "").strip()
                if text:
                    out.append(f"### {role}\n\n{text}\n\n")
            elif ptype == "tool":
                out.append(clip(_oc_tool_marker(pd), 200) + "\n\n")
    return "".join(out)


def render_opencode_session(session_id, db=None) -> str:
    """Render one opencode session to the shared text vocabulary. Never raises.
    Prefers v2 and falls back to v1 — a session lives in exactly one of them."""
    try:
        con = _oc_connect(db)
    except sqlite3.Error:
        return ""
    out = ""
    try:
        if _has_table(con, "session_message"):
            out = _render_v2(con, session_id)
        if not out and _has_table(con, "message"):
            out = _render_v1(con, session_id)
    except sqlite3.Error:
        return out
    finally:
        con.close()
    return out


def opencode_first_user_text(session_id, db=None) -> str:
    """First user message of a session ("" if none). Handles both schemas."""
    try:
        con = _oc_connect(db)
    except sqlite3.Error:
        return ""
    try:
        if _has_table(con, "session_message"):
            row = con.execute(
                "select data from session_message where session_id = ? "
                "and type = 'user' order by seq limit 1",
                (session_id,),
            ).fetchone()
            if row:
                try:
                    return (json.loads(row[0]).get("text") or "").strip()
                except json.JSONDecodeError:
                    return ""
        if _has_table(con, "part"):
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
        row = None
        if _has_table(con, "session_v2"):
            row = con.execute(
                "select title, time_created from session_v2 where id = ?",
                (session_id,),
            ).fetchone()
        if row is None and _has_table(con, "session"):
            row = con.execute(
                "select title, time_created from session where id = ?", (session_id,)
            ).fetchone()

        # Most frequent model across assistant messages (they carry it; user
        # messages do not). v2 stores model as {id, providerID}; v1 as a flat
        # string on the message.
        models = []
        if _has_table(con, "session_message"):
            for (m,) in con.execute(
                "select data from session_message where session_id = ? and type = 'assistant'",
                (session_id,),
            ):
                try:
                    mid = (json.loads(m).get("model") or {}).get("id")
                except json.JSONDecodeError:
                    mid = None
                if mid:
                    models.append(mid)
        if not models and _has_table(con, "message"):
            for (d,) in con.execute(
                "select data from message where session_id = ?", (session_id,)
            ):
                try:
                    md = json.loads(d)
                except json.JSONDecodeError:
                    continue
                if md.get("modelID"):
                    models.append(md["modelID"])
        model = max(set(models), key=models.count) if models else ""
    except sqlite3.Error:
        return blank
    finally:
        con.close()

    title, created = (row[0], row[1]) if row else ("", None)
    if not title:
        first = opencode_first_user_text(session_id, db=db)
        title = first.strip()

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
