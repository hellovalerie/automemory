"""transcript.py — Claude Code transcript JSONL -> readable text.

Stdlib-only port of system/scripts/transcript-to-text.sh (removes the jq dep).
Emits user/assistant *text* turns plus CONDENSED tool markers interleaved in
order. Raw tool payloads are NEVER emitted (they can be huge and can contain
secrets); thinking blocks and internal bookkeeping records are dropped.

Markers:
  [DISPATCH: <desc / first ~15 words of prompt>]   Task/Agent tool_use
  [AGENT RETURNED: <first ~25 words>]              Task/Agent tool_result
  [BASH: <cmd ~80ch> -> <first output line | error exit N>]
  [EDIT: <path>] / [WRITE: <path>]                 path only
  [TOOL <name>]                                    any other tool
"""
from __future__ import annotations

import json
import re
import sys

_WS = re.compile(r"[\s\x00-\x1f]+")


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


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "/dev/stdin"
    sys.stdout.write(render_file(src))
