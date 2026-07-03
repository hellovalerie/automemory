#!/usr/bin/env python3
"""
Parse a Claude.ai `conversations.json` export into (a) a lossless JSONL record
that is your source of truth, and (b) book-readable Markdown — WITHOUT any
caller needing to read the raw file into a model's context.

WHAT CHANGED vs. the first draft
--------------------------------
1. NO silent truncation. The first draft sliced attachment text to 1000 chars
   in the transcript (`extracted_content[:1000]`). That quietly curated the
   book-facing file. Removed. Full content goes everywhere. (Set
   --attach-threshold N to instead emit a short stub + a pointer to the JSONL
   for very long pasted documents, if you WANT them out of the prose — but it
   is now an explicit, visible choice, never silent.)

2. Dialogue and thinking are SEPARATE streams. The agent's `thinking` blocks no
   longer interrupt the conversation. The transcript is clean dialogue; the
   reflections go to their own file, each tagged with the turn number it
   preceded, so the two read as parallel tracks you can cross-reference.

3. Optional --linearize. The export can carry off-path branches from messages
   you edited or responses you regenerated. With --linearize, the script
   follows `parent_message_uuid` to reconstruct the single canonical path
   actually displayed in the UI (the most-recently-active branch), and REPORTS
   any superseded branches it dropped to stderr so nothing vanishes silently.
   Off by default (preserves the export's own ordering).

4. Lighter layout. No `### USER` header + horizontal rule after every single
   turn. Speaker names are configurable (--you-name / --agent-name) for a book.

5. Empty reflection dividers are suppressed. A conversation with no `thinking`
   blocks no longer emits a lonely "# <title> — reflections" header with
   nothing under it. Content is never dropped — only empty headers are.

6. Optional --split DIR. Writes ONE Markdown file per conversation into DIR
   (e.g. "01 - Example Chat.md"), plus a paired ".reflections.md" only when
   that chat actually has reflections. Makes it easy to trace a reflection back
   to its own chat. The combined --transcript / --reflections / --jsonl outputs
   still work independently; --jsonl always stays a single combined file.

THE EXPORT SCHEMA (unchanged understanding from the first draft)
----------------------------------------------------------------
A JSON array of conversation objects. Each has `chat_messages`. Each message:
  - sender: "human" | "assistant"
  - content: list of typed blocks
  - attachments: TEXT uploads, with extracted_content inline  (RECOVERABLE)
  - files:       IMAGE uploads, metadata only, no bytes        (NOT recoverable)

BLOCK TYPES kept: text, thinking, flag (safety block).
Tool blocks (tool_use / tool_result) are dropped by default; --keep-tools keeps
a visible stub so you know a tool turn happened.

Usage:
  python3 parse_conversations-opus-edit.py conversations.json \
      --jsonl messages.jsonl \
      --transcript transcript.md \
      --reflections reflections.md \
      --split output \
      --linearize \
      --you-name "Me" --agent-name "Claude"
All outputs are optional; omit them all to just print the summary.
"""
# NEW-DEV NOTE: `from __future__ import annotations` lets us write modern type
# hints (e.g. `str | None`) even on Python 3.9, where that syntax isn't native.
# argparse = command-line flag parsing; json = read/write JSON; sys = stderr;
# os = make folders / build file paths; re = tidy up unsafe filename characters.
from __future__ import annotations
import argparse
import json
import os
import re
import sys


# NEW-DEV NOTE: a `set` of block types we skip unless --keep-tools is passed.
# Membership tests (`x in DROP_BLOCK_TYPES`) are what a set is for — fast lookup.
DROP_BLOCK_TYPES = {"tool_use", "tool_result"}


# ---------------------------------------------------------------- normalize ---

def normalize_block(block: dict) -> dict:
    """Take ONE raw content block from the export and return a clean, uniform dict.

    A message's `content` is a list of blocks, each with a different `type`
    ("text", "thinking", "flag", ...). Each type stores its payload under a
    different key. This function flattens that variety into a predictable shape
    so the rest of the code never has to re-learn the export's quirks.
    NEW-DEV NOTE: `.get("x")` reads a key but returns None (not a crash) if it's
    absent — safer than `block["x"]` when the export may omit fields.
    """
    btype = block.get("type")
    out = {
        "type": btype,
        "start": block.get("start_timestamp"),
        "stop": block.get("stop_timestamp"),
    }
    if btype == "text":
        out["text"] = block.get("text", "")
    elif btype == "thinking":
        out["thinking"] = block.get("thinking", "")
        out["truncated"] = bool(block.get("truncated"))
        out["cut_off"] = bool(block.get("cut_off"))
    elif btype == "flag":
        out["flag"] = block.get("flag")
        out["helpline"] = block.get("helpline")
    else:  # unknown future type: keep raw so nothing is silently dropped
        out["raw"] = block
    return out


def normalize_message(msg: dict, keep_tools: bool) -> dict:
    """Turn ONE raw message into our clean record: metadata + normalized blocks
    + attachments + files.

    This is where each message gets its `role` ("user"/"model"), its list of
    cleaned content blocks, and its uploads sorted into text (recoverable) vs.
    images (metadata only). Called once per message by iter_conversations().
    NEW-DEV NOTE: the three `[... for x in ...]` constructs below are list
    comprehensions — a compact way to build a new list by looping. The `if`
    at the end of the first one filters items out.
    """
    # If --keep-tools was passed, drop nothing; otherwise skip tool blocks.
    drop = set() if keep_tools else DROP_BLOCK_TYPES
    blocks = [
        normalize_block(b)
        for b in (msg.get("content") or [])   # `or []` guards against content being null
        if b.get("type") not in drop
    ]
    attachments = [
        {
            "file_name": a.get("file_name"),
            "file_type": a.get("file_type"),
            "file_size": a.get("file_size"),
            "extracted_content": a.get("extracted_content", ""),  # FULL, never sliced
        }
        for a in (msg.get("attachments") or [])
    ]
    files = [
        {
            "file_name": f.get("file_name"),
            "file_uuid": f.get("file_uuid"),
            "recoverable": False,  # image bytes are not in the export
        }
        for f in (msg.get("files") or [])
    ]
    return {
        "uuid": msg.get("uuid"),
        "sender": msg.get("sender"),
        "role": "user" if msg.get("sender") == "human" else "model",
        "created_at": msg.get("created_at"),
        "parent_message_uuid": msg.get("parent_message_uuid"),
        "blocks": blocks,
        "attachments": attachments,
        "files": files,
    }


# ------------------------------------------------------------- linearization ---

def linearize(records: list, report) -> list:
    """Follow parent_message_uuid to the single canonical (most recent) path.

    At any branch caused by an edit/regeneration, choose the child whose subtree
    contains the most recent activity, and report the branch(es) we drop.

    The subtree-latest values are computed ITERATIVELY (children-before-parents),
    so depth is irrelevant — a 1,000+ message linear chain will not overflow the
    Python call stack the way a recursive walk would.

    NEW-DEV NOTE: mentally, the messages form a TREE. Each message points at its
    `parent_message_uuid`; editing/regenerating creates a fork (a parent with 2+
    children). This function's job: from that tree, return the single list of
    messages on the newest branch. It works in four phases, labeled below.
    """
    # PHASE 1 — index the tree: look up any message by id, and list each
    # message's children. `setdefault(k, []).append(v)` means "make an empty
    # list for this parent the first time we see it, then add this child."
    by_uuid = {r["uuid"]: r for r in records if r.get("uuid")}
    children: dict = {}
    for r in records:
        children.setdefault(r.get("parent_message_uuid"), []).append(r)

    # PHASE 2 — find the starting point(s). A "root" is a message whose parent
    # is not another message here (i.e. the top of a chain).
    roots = [r for r in records if r.get("parent_message_uuid") not in by_uuid]
    if not roots:
        report("  ! no root found; leaving order untouched")
        return records

    # Fast path: a conversation with no forks has nothing to linearize. Skip the
    # whole tree-walk and keep the export's own ordering. (The common case.)
    has_branch = any(len(kids) > 1 for kids in children.values())
    if not has_branch and len(roots) == 1:
        return records

    # PHASE 3 — for each message, find the newest timestamp anywhere beneath it.
    # We need this to decide, at a fork, which branch is the "live" one.
    # subtree_max[uuid] = latest created_at anywhere in that node's subtree.
    # Compute it iteratively: order nodes by depth, then fold leaves up into
    # parents. No recursion, so no depth limit.
    # NEW-DEV NOTE (BFS = breadth-first search): starting from the roots, we
    # visit every node level-by-level using a queue, recording them in `order`.
    # Then we walk `order` backwards — deepest nodes first — so a parent's
    # children are always computed before the parent itself.
    order = []                       # nodes in top-down (BFS) order
    queue = list(roots)
    seen_bfs = set()
    while queue:
        node = queue.pop(0)
        u = node.get("uuid")
        if u in seen_bfs:            # cycle guard (shouldn't happen)
            continue
        seen_bfs.add(u)
        order.append(node)
        queue.extend(children.get(u, []))

    subtree_max: dict = {}
    for node in reversed(order):     # children appear after parents in `order`,
        u = node.get("uuid")         # so reversed() visits children first
        best = node.get("created_at") or ""
        for ch in children.get(u, []):
            best = max(best, subtree_max.get(ch.get("uuid"), ""))
        subtree_max[u] = best

    def latest(node):
        return subtree_max.get(node.get("uuid"), node.get("created_at") or "")

    # PHASE 4 — build the winning path. Start at the newest root, and at every
    # fork step into the child with the newest subtree; report the losers.
    # Walk from the most-recent root down the most-recent child each step.
    roots.sort(key=latest, reverse=True)
    path = []
    node = roots[0]
    while node is not None:
        path.append(node)
        kids = children.get(node.get("uuid"), [])
        if not kids:
            break
        kids_sorted = sorted(kids, key=latest, reverse=True)
        chosen = kids_sorted[0]
        for dropped in kids_sorted[1:]:
            report(f"  ~ dropped superseded branch at {dropped.get('uuid')} "
                   f"(created {dropped.get('created_at')})")
        node = chosen
    return path


# ---------------------------------------------------- claude-code sessions ---
# A Claude Code session export (e.g. ops/exports/claude-code-export/raw/*.jsonl)
# is a DIFFERENT schema from the claude.ai conversations.json export above: one
# JSON object per line, mixing real dialogue (type "user"/"assistant") with
# session plumbing (type "mode", "system", "attachment", "ai-title", ...) that
# isn't conversation content. This section parses THAT format into the same
# dialogue/reflections shape the rest of the script renders.

CC_NON_DIALOGUE_TYPES = {
    "last-prompt", "mode", "permission-mode", "attachment",
    "file-history-snapshot", "ai-title", "system",
}


def cc_normalize_block(block: dict, keep_tools: bool) -> dict | None:
    """Claude Code content blocks reuse the same `type` vocabulary (text,
    thinking, tool_use, tool_result) as the claude.ai export, so this mirrors
    normalize_block() above. Returns None to drop a block entirely."""
    btype = block.get("type")
    if btype == "text":
        return {"type": "text", "text": block.get("text", "")}
    if btype == "thinking":
        return {"type": "thinking", "thinking": block.get("thinking", "")}
    if btype in ("tool_use", "tool_result"):
        if not keep_tools:
            return None
        return {"type": btype, "raw": block}
    return {"type": "raw", "raw": block}  # unknown future type: keep, never drop silently


def cc_extract_user_blocks(content, keep_tools: bool) -> list:
    """User messages in a Claude Code export are either a plain string (real
    prose, OR a synthetic `<local-command-*>` wrapper from running a slash
    command) or a block list (e.g. a tool_result being handed back). Clean up
    the synthetic wrappers into short readable stubs instead of raw XML tags.
    """
    if isinstance(content, str):
        text = content
        if text.strip().startswith("<local-command-caveat>"):
            return []  # boilerplate that precedes every slash-command turn; pure noise
        m = re.search(r"<command-name>(.*?)</command-name>", text, re.S)
        if m:
            return [{"type": "text", "text": f"_[ran {m.group(1).strip()}]_"}]
        m = re.search(r"<local-command-stdout>(.*?)</local-command-stdout>", text, re.S)
        if m:
            out = m.group(1).strip()
            return [{"type": "text", "text": f"_[command output]_\n\n```\n{out}\n```"}] if out else []
        return [{"type": "text", "text": text}]
    blocks = []
    for b in (content or []):
        nb = cc_normalize_block(b, keep_tools)
        if nb:
            blocks.append(nb)
    return blocks


def parse_cc_session(path: str, keep_tools: bool):
    """Read one Claude Code session .jsonl and return (session_id, ai_title,
    records), where records is a list of (role, blocks) — the same shape
    build_conversation() below expects."""
    session_id = None
    ai_title = None
    records = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            session_id = obj.get("sessionId") or session_id
            t = obj.get("type")
            if t == "ai-title" and not ai_title:
                ai_title = obj.get("aiTitle")
            if t in CC_NON_DIALOGUE_TYPES or t not in ("user", "assistant"):
                continue
            msg = obj.get("message") or {}
            role = msg.get("role")
            content = msg.get("content")
            if role == "user":
                blocks = cc_extract_user_blocks(content, keep_tools)
            else:
                blocks = [b for b in (cc_normalize_block(b, keep_tools) for b in (content or [])) if b]
            records.append({"role": role, "blocks": blocks})
    return session_id, ai_title, records


def render_cc_dialogue(rec, idx, you, agent) -> str | None:
    speaker = you if rec["role"] == "user" else agent
    parts = []
    for b in rec["blocks"]:
        t = b["type"]
        if t == "text":
            if b["text"].strip():
                parts.append(demote_headers(b["text"].rstrip()))
        elif t == "thinking":
            continue  # goes to the reflections stream
        elif t == "tool_use":
            raw = b["raw"]
            parts.append(f"_[tool call: {raw.get('name')}]_")
        elif t == "tool_result":
            content = b["raw"].get("content")
            if isinstance(content, list):
                content = "\n".join(
                    c.get("text", "") if isinstance(c, dict) else str(c) for c in content
                )
            parts.append(f"_[tool result]_\n\n```\n{content}\n```")
        else:
            parts.append(f"_[{b['raw'].get('type')} block]_")
    if not parts:
        return None
    return f"## {speaker} · turn {idx}\n\n" + "\n\n".join(parts)


def render_cc_reflection(rec, idx, agent) -> str | None:
    refs = [b["thinking"].rstrip() for b in rec["blocks"]
            if b["type"] == "thinking" and b["thinking"].strip()]
    if not refs:
        return None
    return f"### before turn {idx} — {agent}'s reflection\n\n" + "\n\n".join(refs)


def build_cc_conversation(records, you, agent):
    dialogue_parts, reflection_parts = [], []
    for idx, rec in enumerate(records, start=1):
        d = render_cc_dialogue(rec, idx, you, agent)
        if d:
            dialogue_parts.append(d)
        r = render_cc_reflection(rec, idx, agent)
        if r:
            reflection_parts.append(r)
    return dialogue_parts, reflection_parts


# --------------------------------------------------------------- iteration ---

def iter_conversations(data, keep_tools, do_linearize, report):
    """Loop over every conversation and hand back (metadata, cleaned records).

    This is the bridge between raw parsing and output writing. For each
    conversation it normalizes all messages, optionally linearizes them, and
    stamps each record with its conversation's metadata.
    NEW-DEV NOTE: `yield` (instead of `return`) makes this a GENERATOR — it
    produces conversations one at a time as the caller loops, rather than
    building one giant list in memory. `report` is a function passed in from
    main() so this code can log without knowing where the log goes.
    """
    for conv in data:
        meta = {
            "conversation_uuid": conv.get("uuid"),
            "conversation_name": conv.get("name"),
            "created_at": conv.get("created_at"),
        }
        recs = [normalize_message(m, keep_tools) for m in conv.get("chat_messages", [])]
        if do_linearize and recs:
            report(f"[linearize] {meta.get('conversation_name') or meta['conversation_uuid']}")
            recs = linearize(recs, report)
        for r in recs:
            r.update(meta)
        yield meta, recs


def demote_headers(text: str, shift: int = 3) -> str:
    """Push down any ATX markdown headers (`#`..`######`) found INSIDE a turn's
    prose by `shift` levels, capped at H6. Turn markers are rendered as real
    headers (see render_dialogue / render_cc_dialogue) so they read as the
    biggest thing on the page; without this, a `##` in the model's own reply
    outranks the turn marker and the transcript loses its rhythm.
    """
    def _shift(m):
        hashes, rest = m.group(1), m.group(2)
        return "#" * min(6, len(hashes) + shift) + rest
    return re.sub(r"(?m)^(#{1,6})(\s)", _shift, text)


# ----------------------------------------------------------------- render ----

def render_attachment(a: dict, threshold: int | None, ref: str) -> str:
    """Format one text attachment for the Markdown transcript.

    Normally inlines the full document in a code fence. But if a threshold is
    set and the document is longer than it, emit a short stub pointing at the
    JSONL record instead — so huge pasted docs don't drown the prose. The full
    text is never lost; it always remains in the JSONL.
    """
    name = a.get("file_name") or "(unnamed)"
    body = a.get("extracted_content") or ""
    if threshold is not None and len(body) > threshold:
        return (f"[attached document **{name}** — {len(body)} chars, full text in "
                f"JSONL record {ref}]")
    return f"[attached document **{name}**]\n\n```\n{body}\n```"


def render_dialogue(rec, idx, you, agent, attach_threshold) -> str | None:
    """One turn as clean prose. Returns None if the turn has no spoken content."""
    speaker = you if rec["role"] == "user" else agent
    parts = []
    for b in rec["blocks"]:
        t = b["type"]
        if t == "text":
            if b["text"].strip():
                parts.append(demote_headers(b["text"].rstrip()))
        elif t == "flag":
            parts.append(f"_[safety flag: {b['flag']}]_")
        elif t == "thinking":
            continue  # goes to the reflections stream, not the dialogue
        else:
            parts.append(f"_[{t} block]_")
    for a in rec["attachments"]:
        parts.append(render_attachment(a, attach_threshold, rec.get("uuid", "?")))
    for f in rec["files"]:
        parts.append(f"_[image: {f['file_name']} — not in export, unrecoverable]_")
    if not parts:
        return None
    body = "\n\n".join(parts)
    return f"## {speaker} · turn {idx}\n\n{body}"


def render_reflection(rec, idx, agent) -> str | None:
    """One turn's thinking, or None if this turn had none.

    NEW-DEV NOTE: `thinking` blocks only ever appear on the AGENT's messages,
    never the user's — so this stream is exclusively the model's reasoning.
    """
    refs = [b for b in rec["blocks"] if b["type"] == "thinking" and b["thinking"].strip()]
    if not refs:
        return None
    chunks = []
    for b in refs:
        tag = " *(truncated in export)*" if b.get("truncated") or b.get("cut_off") else ""
        chunks.append(f"{b['thinking'].rstrip()}{tag}")
    joined = "\n\n".join(chunks)
    return f"### before turn {idx} — {agent}'s reflection\n\n{joined}"


# --------------------------------------------------------- per-conversation ---

def build_conversation(recs, you, agent, attach_threshold):
    """Render one conversation's records into two lists of Markdown blocks:
    (dialogue_parts, reflection_parts).

    Returns them separately so the caller can decide where each stream goes and,
    crucially, SKIP writing a header when a stream is empty (the fix for the
    lonely "# (untitled) — reflections" dividers).
    """
    dialogue_parts, reflection_parts = [], []
    turn = 0
    for rec in recs:
        turn += 1
        d = render_dialogue(rec, turn, you, agent, attach_threshold)
        if d:
            dialogue_parts.append(d)
        r = render_reflection(rec, turn, agent)
        if r:
            reflection_parts.append(r)
    return dialogue_parts, reflection_parts


def safe_filename(name: str, idx: int) -> str:
    """Turn a chat title into a filesystem-safe, sortable basename.

    Prefix with a zero-padded index so files sort in conversation order and two
    same-named (or both-untitled) chats never overwrite each other.
    NEW-DEV NOTE: the regex strips characters that are illegal or awkward in a
    filename (slashes, colons, etc.); everything else — including emoji — stays.
    """
    base = (name or "").strip() or "(untitled)"
    base = re.sub(r'[/\\:*?"<>|\x00-\x1f]', "-", base)   # illegal on common filesystems
    base = re.sub(r"\s+", " ", base).strip()[:60].strip()
    return f"{idx:02d} - {base}"


# ------------------------------------------------------------------- main ----

def main(argv=None):
    """Entry point: parse command-line flags, then stream every message through
    normalize -> (optional) linearize -> the requested output files.

    Reads the flags, opens whichever output files were requested, loops once
    over all conversations writing JSONL / transcript / reflections / split
    files as it goes, tallies statistics, and prints a summary to stderr.
    NEW-DEV NOTE: this is the one function that touches the disk and the flags.
    Everything above it is pure data-shuffling with no I/O — easier to reason
    about and test. Reading this function top-to-bottom traces a full run.
    """
    # --- 1. Describe the command-line interface. Each add_argument = one flag.
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", help="path to conversations.json")
    ap.add_argument("--cc-session", action="append", metavar="PATH",
                    help="parse a Claude Code session .jsonl export (different schema from "
                         "conversations.json; repeatable). Writes <parsed-dir>/<session-uuid>.md "
                         "(+ .reflections.md if the session has thinking blocks).")
    ap.add_argument("--parsed-dir", default="parsed", metavar="DIR",
                    help="output dir for --cc-session files (default: parsed)")
    ap.add_argument("--jsonl", help="lossless one-record-per-line output (source of truth)")
    ap.add_argument("--transcript", help="ONE combined dialogue file (Markdown)")
    ap.add_argument("--reflections", help="ONE combined reflections file (Markdown)")
    ap.add_argument("--split", metavar="DIR",
                    help="write one Markdown file per conversation into DIR "
                         "(+ a paired .reflections.md only when that chat has reflections)")
    ap.add_argument("--linearize", action="store_true",
                    help="follow parent_message_uuid to the canonical path; report dropped branches")
    ap.add_argument("--keep-tools", action="store_true", help="keep visible stubs for tool turns")
    ap.add_argument("--attach-threshold", type=int, default=None,
                    help="if set, attachments longer than N chars become a stub + JSONL pointer "
                         "in the transcript (full text always stays in JSONL)")
    ap.add_argument("--you-name", default="Me")
    ap.add_argument("--agent-name", default="Claude")
    args = ap.parse_args(argv)

    def report(line):  # branch / linearize notes go to stderr, never into a file
        print(line, file=sys.stderr)

    # --- 1b. --cc-session is a separate mode: different schema, no conversations.json
    # needed. Handle it and exit before touching the claude.ai export logic below.
    if args.cc_session:
        os.makedirs(args.parsed_dir, exist_ok=True)
        written = 0
        for path in args.cc_session:
            session_id, ai_title, records = parse_cc_session(path, args.keep_tools)
            if not session_id:
                report(f"  ! no sessionId found in {path}; skipping")
                continue
            dlg, refl = build_cc_conversation(records, args.you_name, args.agent_name)
            if not dlg:
                report(f"  ! no dialogue content in {path} (session {session_id})")
                continue
            md_path = os.path.join(args.parsed_dir, f"{session_id}.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(f"# {session_id}\n\n" + "\n\n".join(dlg) + "\n")
            written += 1
            report(f"  wrote {md_path}" + (f"  (ai-title: {ai_title})" if ai_title else ""))
            if refl:
                refl_path = os.path.join(args.parsed_dir, f"{session_id}.reflections.md")
                with open(refl_path, "w", encoding="utf-8") as f:
                    f.write(f"# {session_id} — reflections\n\n" + "\n\n".join(refl) + "\n")
                written += 1
                report(f"  wrote {refl_path}")
        report(json.dumps({"cc_sessions_parsed": len(args.cc_session), "files_written": written}))
        return 0

    if not args.path:
        ap.error("path (to conversations.json) is required unless --cc-session is used")

    # --- 2. Load the whole export into memory (it's a few MB — fine to hold).
    with open(args.path, encoding="utf-8") as fh:
        data = json.load(fh)

    # --- 3. Running tallies for the end-of-run summary.
    counts = {"conversations": len(data), "messages": 0, "user": 0, "model": 0}
    block_counts: dict = {}
    attach_chars = 0
    images_lost = 0
    split_files_written = 0

    # --- 4. Open only the combined outputs the user asked for; the rest stay
    # None and are quietly skipped below. If --split was given, make the folder.
    jsonl_fh = open(args.jsonl, "w", encoding="utf-8") if args.jsonl else None
    md_fh = open(args.transcript, "w", encoding="utf-8") if args.transcript else None
    refl_fh = open(args.reflections, "w", encoding="utf-8") if args.reflections else None
    if args.split:
        os.makedirs(args.split, exist_ok=True)

    # --- 5. The main loop: one conversation at a time.
    for idx, (meta, recs) in enumerate(iter_conversations(
            data, args.keep_tools, args.linearize, report), start=1):
        title = meta.get("conversation_name") or "(untitled)"

        # 5a. Statistics + the lossless JSONL (streamed, one line per message).
        for rec in recs:
            counts["messages"] += 1
            counts["user" if rec["role"] == "user" else "model"] += 1
            for b in rec["blocks"]:
                block_counts[b["type"]] = block_counts.get(b["type"], 0) + 1
            for a in rec["attachments"]:
                attach_chars += len(a.get("extracted_content") or "")
            images_lost += len(rec["files"])
            if jsonl_fh:
                jsonl_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

        # 5b. Render this conversation's two Markdown streams once, reuse below.
        dlg, refl = build_conversation(recs, args.you_name, args.agent_name,
                                       args.attach_threshold)

        # 5c. Combined transcript — write header only if there's dialogue.
        if md_fh and dlg:
            md_fh.write(f"# {title}\n\n" + "\n\n".join(dlg) + "\n\n")

        # 5d. Combined reflections — write header ONLY if this chat reflected.
        #     (This is the fix: no more empty "# (untitled) — reflections".)
        if refl_fh and refl:
            refl_fh.write(f"# {title} — reflections\n\n" + "\n\n".join(refl) + "\n\n")

        # 5e. Split mode — one file per chat, paired reflections only if present.
        if args.split and dlg:
            base = safe_filename(title, idx)
            with open(os.path.join(args.split, base + ".md"), "w", encoding="utf-8") as f:
                f.write(f"# {title}\n\n" + "\n\n".join(dlg) + "\n")
            split_files_written += 1
            if refl:
                with open(os.path.join(args.split, base + ".reflections.md"),
                          "w", encoding="utf-8") as f:
                    f.write(f"# {title} — reflections\n\n" + "\n\n".join(refl) + "\n")
                split_files_written += 1

    # --- 6. Close whatever combined files we opened (skip the None placeholders).
    for fh in (jsonl_fh, md_fh, refl_fh):
        if fh:
            fh.close()

    # --- 7. Print the run summary to stderr so it never pollutes a piped file.
    summary = {
        "counts": counts,
        "block_counts": block_counts,
        "attachment_chars_preserved": attach_chars,
        "images_unrecoverable": images_lost,
        "linearized": args.linearize,
        "split_files_written": split_files_written,
    }
    print(json.dumps(summary, indent=2), file=sys.stderr)
    return 0


# NEW-DEV NOTE: this block runs ONLY when the file is executed directly
# (`python3 parse_conversations-opus-edit.py ...`), not when it's imported by
# another module. `SystemExit(main())` exits with main()'s return code (0 = OK),
# which is the convention command-line tools use to signal success/failure.
if __name__ == "__main__":
    raise SystemExit(main())
