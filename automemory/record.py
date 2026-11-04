"""Record core — the envelope every session record is written into.

Spec: dev/design/RECORD-CORE.md. Plain markdown, stdlib only (no YAML
dependency — the frontmatter is a controlled flat `key: value` block).

Identity is the SESSION REF (`source: <provider>:<ref>`) — immutable, one file
per session forever. The filename is a human label and may be renamed; nothing
ever looks a record up by filename.

Records are SACRED user data. This module is the only code that writes them, and
it can only:
  - create a new record file,
  - rewrite the frontmatter (the current-state label) while keeping every body
    line, in order (`_guarded_write` refuses anything else),
  - rename a file, never onto an existing one.
There is deliberately no delete.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

INDEX_NAME = "INDEX.md"
DECLINED_NAME = ".declined.json"

# --- helpers ------------------------------------------------------------------


def slug(text: str) -> str:
    """Lowercase; runs of non-alphanumerics collapse to one hyphen; trimmed."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "untitled"


def short(session_ref: str) -> str:
    """A short, filename-safe token for a session ref.

    Prefixed ids (opencode `ses_ec1e5bf2…`) skip the constant scheme prefix —
    taking the leading run would return "ses" for every opencode session.
    Bare UUIDs (Claude Code `9f2c1a44-3b7e-…`) keep their 8-char prefix.
    """
    ref = (session_ref or "").strip()
    # UUIDv7 (Codex) starts with a timestamp: sessions begun in the same minute share
    # their first 8 characters (measured 2026-11-04: two `01a1a593…` sessions). Use the
    # random tail instead. Random UUIDs (Claude Code, v4) keep their historical prefix.
    if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[0-9a-f]{4}-[0-9a-f]{12}", ref, re.I):
        return ref[-8:]
    runs = re.findall(r"[A-Za-z0-9]+", ref)
    if not runs:
        return "unknown"
    if len(runs) == 1:
        return runs[0][:8]
    if len(runs[0]) <= 4:
        return runs[1][:8] or runs[0][:8]
    return runs[0][:8]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# --- frontmatter (controlled flat format; no YAML dep) ------------------------

# Fixed order so records read consistently. The frontmatter is the CURRENT
# STATE of the session (latest title, last-used model); history of how it got
# there lives in the body's `## Changes`.
_ORDER = (
    "title", "source", "model", "session_started", "last_active", "turns", "seen_chars",
    "list_written",
    "captured", "subject", "status", "superseded_by",
)


def _fm_dump(fields: dict) -> str:
    lines = ["---"]
    for key in _ORDER:
        if key in fields and fields[key] not in (None, ""):
            lines.append(f"{key}: {fields[key]}")
    for key, val in fields.items():  # unknown keys survive, after the known ones
        if key not in _ORDER and val not in (None, ""):
            lines.append(f"{key}: {val}")
    lines.append("---")
    return "\n".join(lines)


def _split(text: str):
    """(frontmatter dict, body text). Body is everything after the closing fence."""
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return {}, text
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm, text[m.end():]


def _fm_parse(text: str) -> dict:
    return _split(text)[0]


def is_record(fm: dict) -> bool:
    return bool(fm.get("source"))


def is_legacy(fm: dict) -> bool:
    """Pre-2026-10-30 format: `occurred`/`id`, no `session_started`."""
    return is_record(fm) and "session_started" not in fm and "occurred" in fm


# --- the guard: append-only on the body ---------------------------------------


def _lines_kept_in_order(old: str, new: str) -> bool:
    """True if every non-blank line of `old` appears in `new`, in the same order.
    Blank lines carry no content; guarding them would only block re-spacing."""
    it = iter(new.splitlines())
    return all(any(o == n for n in it) for o in old.splitlines() if o.strip())


class AppendOnlyViolation(RuntimeError):
    pass


def _guarded_write(path: Path, new_text: str) -> None:
    """Write `new_text` over `path` ONLY if no body line of the existing file is
    lost. The frontmatter may change (it's the current-state label); the body
    may only grow."""
    if path.exists():
        _, old_body = _split(path.read_text(encoding="utf-8"))
        _, new_body = _split(new_text)
        if not _lines_kept_in_order(old_body, new_body):
            raise AppendOnlyViolation(f"refusing to drop record content in {path}")
    path.write_text(new_text, encoding="utf-8")


def _guarded_rename(src: Path, dst: Path) -> Path:
    if dst.exists():
        raise FileExistsError(f"refusing to overwrite {dst}")
    src.rename(dst)
    return dst


# --- filenames ----------------------------------------------------------------


def filename_for(records_dir, *, started: str, provider_tag: str, session_ref: str,
                 custom_title: str = "") -> str:
    """`<session_started>-<provider>-<rename-slug | short id>.md`.

    The user's rename wins; never-renamed sessions use the short session id.
    If another session already holds that name, append the short id.
    """
    label = slug(custom_title) if custom_title else short(session_ref)
    name = f"{started}-{provider_tag}-{label}.md"
    existing = Path(records_dir) / name
    if existing.exists() and not _belongs_to(existing, session_ref):
        name = f"{started}-{provider_tag}-{label}-{short(session_ref)}.md"
    return name


def _belongs_to(path: Path, session_ref: str) -> bool:
    try:
        src = _fm_parse(path.read_text(encoding="utf-8")).get("source", "")
    except OSError:
        return False
    return src.endswith(f":{session_ref}") or src == session_ref


# --- lookup (identity = session ref, never filename) --------------------------


def find_record(records_dir, session_ref: str):
    """The record file for a session, or None. If legacy duplicates exist, the
    first by name wins — they never grow (their sessions are finished)."""
    records_dir = Path(records_dir)
    if not records_dir.is_dir():
        return None
    for p in sorted(records_dir.glob("*.md")):
        if p.name != INDEX_NAME and _belongs_to(p, session_ref):
            return p
    return None


def record_exists_for_session(records_dir, session_ref: str) -> bool:
    return find_record(records_dir, session_ref) is not None


def covered_turns(records_dir, session_ref: str):
    """Turns already handled for a session — by its record, else by a decline.
    None = never seen."""
    p = find_record(records_dir, session_ref)
    if p is not None:
        try:
            return int(_fm_parse(p.read_text(encoding="utf-8")).get("turns") or 0)
        except ValueError:
            return 0
    return declined_turns(records_dir, session_ref)


def covered_chars(records_dir, session_ref: str) -> int:
    """How much rendered transcript was seen when the session was last handled (0 if
    unknown) — catches a turn that kept growing after it was checked."""
    p = find_record(records_dir, session_ref)
    if p is not None:
        try:
            return int(_fm_parse(p.read_text(encoding="utf-8")).get("seen_chars") or 0)
        except ValueError:
            return 0
    e = _declined_load(records_dir).get(session_ref)
    return int(e.get("chars") or 0) if e else 0


# --- ignored sessions (never recorded) -------------------------------------------
# Revived from Engine B's capture-ignore-sessions.txt (2026-11-03): sessions that
# must never become records — e.g. evaluation runs, whose records would leak one
# test's answers into the next. One session id per line; `#` comments allowed.
IGNORE_NAME = ".ignore-sessions"


def ignored_sessions(records_dir) -> set:
    try:
        lines = (Path(records_dir) / IGNORE_NAME).read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    return {l.split("#", 1)[0].strip() for l in lines if l.split("#", 1)[0].strip()}


def ignore_session(records_dir, session_ref: str, note: str = "") -> bool:
    """Add a session to the ignore list. False if it was already there."""
    if session_ref in ignored_sessions(records_dir):
        return False
    p = Path(records_dir) / IGNORE_NAME
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(f"{session_ref}" + (f"  # {note}" if note else "") + "\n")
    return True


# --- declined sessions (no record file, so a sidecar remembers them) ----------


def _declined_load(records_dir) -> dict:
    """{session ref: {"turns", "chars", "reason", "at"}} — older files stored a bare
    turn count; read both."""
    try:
        raw = json.loads((Path(records_dir) / DECLINED_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: (v if isinstance(v, dict) else {"turns": int(v)}) for k, v in raw.items()}


def declined_turns(records_dir, session_ref: str):
    e = _declined_load(records_dir).get(session_ref)
    return e.get("turns") if e else None


def mark_declined(records_dir, session_ref: str, turns: int, chars: int = 0,
                  reason: str = "") -> None:
    """The scribe said NO-RECORD for what it saw (`turns`, `chars`). Re-checked if the
    session grows — more turns, OR the last turn grew (work that finished after the
    check: measured 2026-11-04, a session declined mid-task whose task then completed
    inside the same turn would never have been looked at again)."""
    records_dir = Path(records_dir)
    records_dir.mkdir(parents=True, exist_ok=True)
    data = _declined_load(records_dir)
    data[session_ref] = {"turns": int(turns), "chars": int(chars),
                         "reason": (reason or "").strip()[:200], "at": now_iso()}
    (records_dir / DECLINED_NAME).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                                             encoding="utf-8")


# --- body assembly --------------------------------------------------------------


def _sections(done: str, learnings: str, outstanding: str, level: str = "##",
              summary: str = "", bugs: str = "") -> list:
    parts = [f"**Summary:** {summary.strip()}", ""] if summary and summary.strip() else []
    parts += [f"{level} Done", done.strip(), ""]
    for head, text in (("Bugs & Fixes", bugs), ("Learnings & Gotchas", learnings),
                       ("Outstanding", outstanding)):
        if text and text.strip():
            parts += [f"{level} {head}", text.strip(), ""]
    return parts


def update_block(date: str, turns_from: int, turns_to: int, *, done: str,
                 summary: str = "", bugs: str = "", learnings: str = "",
                 outstanding: str = "") -> str:
    """Every batch of a session — the first one too — has this one shape."""
    header = f"## Update — {date} · turns {turns_from}–{turns_to}"
    return "\n".join([header, "", *_sections(done, learnings, outstanding, "###",
                                              summary=summary, bugs=bugs)]).rstrip("\n")


def latest_outstanding(body: str) -> list:
    """The current open list: the newest update's `### Outstanding` bullets, minus
    `✓ closed:` lines. (Each update restates every still-open item, so the newest
    one alone is the current state.) Legacy shape: the top-level `## Outstanding`."""
    for sec in _top_sections(body):
        if _UPDATE.match(sec) or sec.startswith("## Outstanding"):
            m = (re.search(r"(?ms)^### Outstanding\s*\n(.*?)(?=^#{2,3} |\Z)", sec)
                 if _UPDATE.match(sec) else re.match(r"(?ms)^## Outstanding\s*\n(.*)", sec))
            if _UPDATE.match(sec) and not m:
                return []  # newest update says nothing is open
            if m:
                # Closed = the line STARTS with ✓ closed. An open item may quote
                # the marker (found by the 2026-11-03 grader: "Check the next update
                # for `✓ closed:` lines" was hidden from session start).
                return [l.rstrip() for l in m.group(1).splitlines()
                        if l.strip().startswith(("-", "*"))
                        and not l.strip().lstrip("-* ").startswith("✓ closed")]
    return []


_TAIL = ("## Changes", "## Edits")


def _insert_before_tail(body: str, block: str, tail=_TAIL) -> str:
    """Insert `block` just before the record's closing run of `tail` sections
    (normally `## Changes` then `## Edits` — Edits always last). Legacy records
    carry hand-written sections after `## Edits`; there is no clean tail, so the
    block is appended at the end rather than wedged into the middle of history."""
    lines = body.rstrip("\n").split("\n")
    tops = [i for i, l in enumerate(lines) if l.startswith("## ")]
    start = None
    for i in reversed(tops):
        if lines[i].strip() in tail:
            start = i
        else:
            break
    if start is None:
        return "\n".join(lines) + "\n\n" + block.rstrip("\n") + "\n"
    return "\n".join(lines[:start] + block.rstrip("\n").split("\n") + [""] + lines[start:]) + "\n"


def _add_changes(body: str, entries: list) -> str:
    """Append dated lines to `## Changes`, creating the section (before
    `## Edits`) the first time anything changes."""
    if not entries:
        return body
    new_lines = "\n".join(f"- {e}" for e in entries)
    lines = body.rstrip("\n").split("\n")
    if "## Changes" in (l.strip() for l in lines):
        i = max(j for j, l in enumerate(lines) if l.strip() == "## Changes")
        k = i + 1
        while k < len(lines) and not lines[k].startswith("## "):
            k += 1
        while k > i + 1 and not lines[k - 1].strip():
            k -= 1
        return "\n".join(lines[:k] + new_lines.split("\n") + lines[k:]) + "\n"
    return _insert_before_tail(body, "## Changes\n" + new_lines + "\n", tail=("## Edits",))


# --- write: new record ----------------------------------------------------------


def write_record(records_dir, *, provider: str, provider_tag: str, session_ref: str,
                 model: str, subject: str, title: str, custom_title: str = "",
                 session_started: str, last_active: str = "", turns: int = 0, seen_chars: int = 0,
                 done: str, learnings: str = "", outstanding: str = "",
                 summary: str = "", bugs: str = "", captured: str = "") -> Path:
    """Create a session's record. `done` is mandatory — it IS the write-gate.
    A session that already has a record must use `append_update` instead."""
    if not (done and done.strip()):
        raise ValueError("record requires a non-empty Done (write-gate)")
    records_dir = Path(records_dir)
    records_dir.mkdir(parents=True, exist_ok=True)
    if find_record(records_dir, session_ref) is not None:
        raise FileExistsError(f"{session_ref} already has a record; append an update")

    path = records_dir / filename_for(records_dir, started=session_started,
                                      provider_tag=provider_tag, session_ref=session_ref,
                                      custom_title=custom_title)
    fields = {
        "title": title, "source": f"{provider}:{session_ref}", "model": model,
        "session_started": session_started, "last_active": last_active or session_started,
        "turns": turns, "seen_chars": seen_chars or "", "list_written": captured or now_iso(),
        "captured": captured or now_iso(), "subject": slug(subject), "status": "open",
    }
    block = update_block(session_started if not last_active else last_active, 1, turns,
                         done=done, summary=summary, bugs=bugs, learnings=learnings,
                         outstanding=outstanding)
    body = "\n" + block + "\n\n## Edits\nnone\n"
    _guarded_write(path, _fm_dump(fields) + "\n" + body)
    return path


# --- write: continue an existing record ----------------------------------------


def append_update(path, *, turns_from: int, turns_to: int, title: str, seen_chars: int = 0,
                  custom_title: str = "", model: str, last_active: str,
                  provider_tag: str, session_ref: str, done: str = "",
                  learnings: str = "", outstanding: str = "", date: str = "",
                  summary: str = "", bugs: str = "", captured: str = "") -> Path:
    """Continue a record exactly where it left off.

    - A dated `## Update — <date> · turns A–B` block, only if the scribe found
      something (`done`); a declined stretch adds no block but still advances
      `turns`.
    - `## Changes` gets one line per frontmatter value that changed (title,
      model, filename) — the frontmatter itself always shows the latest.
    - The file is renamed if the user renamed the session.
    Returns the (possibly new) path.
    """
    path = Path(path)
    date = date or today()
    fm, body = _split(path.read_text(encoding="utf-8"))

    if done and done.strip():
        block = update_block(date, turns_from, turns_to, done=done, summary=summary,
                             bugs=bugs, learnings=learnings, outstanding=outstanding)
        # Freshest first: a new update goes straight under the frontmatter.
        body = "\n" + block + "\n\n" + body.lstrip("\n")
        fm["list_written"] = captured or now_iso()  # this update now holds the project's list

    changes = []
    if title and title != fm.get("title"):
        changes.append(f'{date} · title: "{fm.get("title", "")}" → "{title}"')
        fm["title"] = title
    if model and model != fm.get("model"):
        changes.append(f"{date} · model: {fm.get('model') or '(unknown)'} → {model}")
        fm["model"] = model

    want = filename_for(path.parent, started=fm.get("session_started", ""),
                        provider_tag=provider_tag, session_ref=session_ref,
                        custom_title=custom_title)
    if want != path.name:
        changes.append(f"{date} · filename: {path.name} → {want}")

    body = _add_changes(body, changes)
    fm["turns"] = turns_to
    if seen_chars:
        fm["seen_chars"] = seen_chars
    fm["last_active"] = last_active or date
    fm["captured"] = captured or now_iso()
    _guarded_write(path, _fm_dump(fm) + "\n" + body)
    if want != path.name:
        path = _guarded_rename(path, path.parent / want)
    return path


# --- snapshot (`protect`): a read-only copy before any format change ----------


def snapshot(records_dir, label: str = "") -> Path:
    """Copy the whole records dir to ~/.local/share/automemory/snapshots/<ts>/,
    OUTSIDE the project (never committed, never rinsed), and verify the copy
    byte-for-byte before returning. Raises if the copy doesn't match."""
    import filecmp
    import shutil
    records_dir = Path(records_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    import os
    base = Path(os.environ.get("AUTOMEMORY_SNAPSHOT_DIR")
                or (Path.home() / ".local" / "share" / "automemory" / "snapshots"))
    dest = base / f"{stamp}-{slug(label or records_dir.parent.name)}"
    shutil.copytree(records_dir, dest)
    for src in records_dir.rglob("*"):
        if src.is_file():
            twin = dest / src.relative_to(records_dir)
            if not (twin.is_file() and filecmp.cmp(src, twin, shallow=False)):
                raise RuntimeError(f"snapshot mismatch at {src}; nothing migrated")
    return dest


# --- migration of pre-2026-10-30 records ----------------------------------------


def migrate_legacy(path, *, provider_tag: str, session_ref: str, title: str,
                   custom_title: str = "", model: str = "", turns: int,
                   date: str = "") -> Path:
    """Rewrite a legacy record's frontmatter into the current format and rename
    it. Body untouched except an appended `## Changes` noting every change."""
    path = Path(path)
    date = date or today()
    fm, body = _split(path.read_text(encoding="utf-8"))
    started = fm.pop("occurred", "") or fm.get("captured", "")[:10]
    old_id = fm.pop("id", "")
    new = {k: v for k, v in fm.items()}
    new["session_started"] = started
    new["last_active"] = (fm.get("captured", "")[:10] or started)
    new["turns"] = turns
    changes = [f"{date} · format: migrated to the 2026-10-30 record format "
               f"(occurred → session_started; added last_active, turns; dropped id `{old_id}` — "
               f"identity is `source`)"]
    if title and title != fm.get("title"):
        changes.append(f'{date} · title: "{fm.get("title", "")}" → "{title}"')
        new["title"] = title
    if model and model != fm.get("model"):
        changes.append(f"{date} · model: {fm.get('model') or '(unknown)'} → {model}")
        new["model"] = model
    want = filename_for(path.parent, started=started, provider_tag=provider_tag,
                        session_ref=session_ref, custom_title=custom_title)
    if want != path.name and (path.parent / want).exists():
        # Legacy duplicate: two old records for ONE session. Keep both (nothing is
        # deleted); the second keeps its old trailing token to stay distinct.
        want = want[:-3] + "-" + path.stem.rsplit("-", 1)[-1] + ".md"
    if want != path.name:
        changes.append(f"{date} · filename: {path.name} → {want}")
    body = _add_changes(body, changes)
    _guarded_write(path, _fm_dump(new) + "\n" + body)
    if want != path.name:
        path = _guarded_rename(path, path.parent / want)
    return path


_UPDATE = re.compile(r"^## Update — .*turns (\d+)")


def _top_sections(body: str) -> list:
    """Split a body at its `## ` headings: [preamble, section, section, …]."""
    return re.split(r"(?m)^(?=## )", body)


def _non_blank(text: str) -> list:
    return sorted(l for l in text.splitlines() if l.strip())


def updates_out_of_order(body: str) -> bool:
    """True if `## Update — …` blocks aren't already first, newest first."""
    secs = [s for s in _top_sections(body) if s.strip()]
    tags = [_UPDATE.match(s) for s in secs]
    n = sum(1 for t in tags if t)
    turns = [int(t.group(1)) for t in tags[:n] if t]
    return n > 0 and (len(turns) != n or turns != sorted(turns, reverse=True))


_ORIGINAL = ("## Done", "## Bugs & Fixes", "## Learnings & Gotchas", "## Outstanding")


_DATED = re.compile(r"^## Update[ —]+(\d{4}-\d{2}-\d{2})")


def _sort_key(sec: str):
    """Updates newest first: by date, then by turn number when both are present."""
    m = _DATED.match(sec)
    t = _UPDATE.match(sec)
    return (m.group(1) if m else "", int(t.group(1)) if t else 0)


def _canonical(secs: list) -> list:
    """Updates (auto and hand-written) newest first, then anything else as found,
    then `## Changes`, then `## Edits` — always last."""
    ups = sorted((x for x in secs if _DATED.match(x)), key=_sort_key, reverse=True)
    other = [x for x in secs if not _DATED.match(x) and not x.startswith(("## Changes", "## Edits"))]
    return ups + other + [x for x in secs if x.startswith("## Changes")] \
        + [x for x in secs if x.startswith("## Edits")]


def needs_reshape(body: str) -> bool:
    """True if the first batch still sits as top-level `## Done` etc., or the
    sections aren't in canonical order (updates newest first, Edits last)."""
    secs = [x for x in _top_sections(body) if x.strip()]
    if any(x.startswith(_ORIGINAL) for x in secs):
        return True
    return [x.strip() for x in secs] != [x.strip() for x in _canonical(secs)]


def reshape_first_batch(path, date: str = "") -> bool:
    """Wrap a record's original top-level sections into `## Update — <started> ·
    turns 1–N` (N = the turn before the earliest update, or all turns), with the
    headings demoted to `###`. Runs only from `automemory migrate`, after its
    snapshot; its own check: every non-blank line survives, apart from the
    demoted headings and the one new heading."""
    path = Path(path)
    date = date or today()
    fm, body = _split(path.read_text(encoding="utf-8"))
    if not needs_reshape(body):
        return False
    secs = [s for s in _top_sections(body) if s.strip()]
    orig = [s for s in secs if s.startswith(_ORIGINAL)]
    first_from = [int(m.group(0).split("turns ")[1].split("–")[0])
                  for m in (re.match(r"^## Update — .*turns \d+", s) for s in secs) if m]
    last = (min(first_from) - 1) if first_from else int(fm.get("turns") or 0)
    inner = "\n".join(("#" + s) for s in orig)  # "## Done" -> "### Done"
    block = (f"## Update — {fm.get('session_started', '')} · turns 1–{last}\n\n"
             + inner.strip("\n") + "\n")
    out, placed = [], False
    for s in secs:
        if s.startswith(_ORIGINAL):
            if not placed:
                # The first batch is the OLDEST: it goes after every update.
                placed = True
            continue
        out.append(s.strip("\n") + "\n")
    has_orig = bool(orig)
    if has_orig:
        out.append(block)
    new = "\n" + "\n".join(x.strip("\n") + "\n" for x in _canonical(out))
    norm = lambda t: sorted(l.lstrip("#") for l in t.splitlines()
                            if l.strip() and not _UPDATE.match(l))
    if norm(new) != norm(body):
        raise AppendOnlyViolation(f"reshape would change content in {path}")
    new = _add_changes(new, [(f"{date} · format: first batch wrapped as an `## Update` "
                              f"block (turns 1–{last}) like every later batch")
                             if has_orig else
                             f"{date} · format: sections put in order (updates newest first, Edits last)"])
    path.write_text(_fm_dump(fm) + "\n" + new, encoding="utf-8")
    return True


def reorder_updates(path, date: str = "") -> bool:
    """Move a record's `## Update — …` blocks to the top, newest first. Runs only
    from `automemory migrate`, after its verified snapshot: moving text is what
    `_guarded_write` forbids, so this has its own check — every non-blank line
    must survive, only the order may change. Returns True if it moved anything."""
    path = Path(path)
    date = date or today()
    fm, body = _split(path.read_text(encoding="utf-8"))
    if not updates_out_of_order(body):
        return False
    secs = [s for s in _top_sections(body) if s.strip()]
    ups = [s for s in secs if _UPDATE.match(s)]
    rest = [s for s in secs if not _UPDATE.match(s)]
    ups.sort(key=lambda s: int(_UPDATE.match(s).group(1)), reverse=True)
    new = "\n" + "\n".join(s.strip("\n") + "\n" for s in ups + rest)
    if _non_blank(new) != _non_blank(body):
        raise AppendOnlyViolation(f"reorder would change content in {path}")
    new = _add_changes(new, [f"{date} · format: updates moved to the top, newest first"])
    path.write_text(_fm_dump(fm) + "\n" + new, encoding="utf-8")
    return True


# --- the project's open list (one list, across sessions and providers) ---------
# Owner's design, 2026-11-04: one open list for the whole project. Every scribe is
# handed the project's current list; its update closes what that stretch finished,
# carries the rest, adds what's new. The newest list written anywhere IS the project's
# current list. Items carry engine-assigned ids (`[o12]`) so an item opened in one
# session and closed in another — even by another tool — stays traceable, and a
# dropped item is detected exactly rather than by matching reworded text.

ITEM_ID = re.compile(r"\[o(\d+)\]")
STATUS_NAME = "STATUS.md"


def _records(records_dir):
    for p in Path(records_dir).glob("*.md"):
        if p.name in (INDEX_NAME, STATUS_NAME):
            continue
        fm, body = _split(p.read_text(encoding="utf-8"))
        if is_record(fm):
            yield p, fm, body


def max_item_id(records_dir) -> int:
    """Highest item id used anywhere in the project's records (0 if none)."""
    top = 0
    for _, _, body in _records(records_dir):
        for m in ITEM_ID.finditer(body):
            top = max(top, int(m.group(1)))
    return top


def _top_update_date(body: str) -> str:
    m = re.search(r"(?m)^## Update — (\d{4}-\d{2}-\d{2})", body)
    return m.group(1) if m else ""


def project_open_list(records_dir):
    """(items, record filename, date) — the newest open list written anywhere in the
    project. Newest = the record whose list was written last (`list_written`, else
    `captured` for records from before this existed)."""
    best = None
    for p, fm, body in _records(records_dir):
        key = fm.get("list_written") or fm.get("captured") or ""
        if best is None or key > best[0]:
            best = (key, p.name, body)
    if best is None:
        return [], "", ""
    return latest_outstanding(best[2]), best[1], _top_update_date(best[2])


def with_ids(items: list, next_id: int):
    """Give each open item without an id the next one. Returns (items, next_id)."""
    out = []
    for it in items:
        if not ITEM_ID.search(it):
            it = re.sub(r"^(\s*[-*]\s*)", lambda m: f"{m.group(1)}[o{next_id}] ", it, count=1)
            next_id += 1
        out.append(it)
    return out, next_id


def _item_text(line: str) -> str:
    """An item's wording, comparable: no bullet, id, markup, engine note or case."""
    t = ITEM_ID.sub("", line).strip().lstrip("-* ")
    t = t.split(" _(carried by the engine", 1)[0]
    t = re.sub(r"[*`_]", "", t).lower()
    return re.sub(r"\s+", " ", t).strip()


def settle_outstanding(given: list, returned: str, next_id: int):
    """Apply the engine's rules to the scribe's Outstanding:
    - new open items get ids;
    - every GIVEN item must come back either open or `✓ closed:` (by id); any that
      doesn't is carried forward by the engine, marked, so nothing disappears silently.
    Returns (outstanding text, dropped items, next_id)."""
    lines = [l.rstrip() for l in (returned or "").splitlines() if l.strip()]
    bullets = [l for l in lines if l.strip().startswith(("-", "*"))]
    closed = [l for l in bullets if l.strip().lstrip("-* ").startswith("✓ closed")]
    opened = [l for l in bullets if l not in closed]
    # A scribe that carries or closes an item but forgets its id would otherwise make
    # a duplicate (and a false "carried by the engine"). Match by wording, reuse the id.
    by_text = {_item_text(g): ITEM_ID.search(g).group(0) for g in given if ITEM_ID.search(g)}
    def _adopt(l, closing):
        if ITEM_ID.search(l):
            return l
        body = l.strip().lstrip("-* ")
        core = body.split("closed:", 1)[1].split(" — ", 1)[0] if closing and "closed:" in body else body
        tag = by_text.get(_item_text(core))
        if not tag:
            return l
        if closing:
            return l.replace("closed:", f"closed: {tag}", 1)
        return re.sub(r"^(\s*[-*]\s*)", lambda m: f"{m.group(1)}{tag} ", l, count=1)
    opened = [_adopt(l, False) for l in opened]
    closed = [_adopt(l, True) for l in closed]
    opened, next_id = with_ids(opened, next_id)
    seen = {m.group(1) for l in opened + closed for m in ITEM_ID.finditer(l)}
    dropped = [g for g in given
               if (m := ITEM_ID.search(g)) and m.group(1) not in seen]
    carried = [g.rstrip() + " _(carried by the engine: this stretch didn't mention it)_"
               for g in dropped]
    return "\n".join(opened + carried + closed), dropped, next_id


def rebuild_status(records_dir, recent: int = 15) -> Path:
    """STATUS.md — generated, never hand-edited: the project's open list and the most
    recently closed items, each closure linked to the record that closed it."""
    records_dir = Path(records_dir)
    items, src, date = project_open_list(records_dir)
    closures = []
    for p, fm, body in _records(records_dir):
        for sec in _top_sections(body):
            m = re.match(r"^## Update — (\d{4}-\d{2}-\d{2})", sec)
            if not m:
                continue
            for l in sec.splitlines():
                if l.strip().lstrip("-* ").startswith("✓ closed"):
                    closures.append((m.group(1), fm.get("source", "").split(":", 1)[0],
                                     p.name, l.strip().lstrip("-* ")))
    closures.sort(key=lambda c: c[0], reverse=True)
    out = ["# automemory — status", "",
           "Generated from the records — do not hand-edit.", "",
           f"## Open ({len(items)})" + (f" — as of {date}, from [{src}]({src})" if src else ""), ""]
    out += items or ["_(nothing open)_"]
    out += ["", f"## Recently closed", ""]
    out += [f"- {d} · {prov} · [{name}]({name}) — {txt}" for d, prov, name, txt in closures[:recent]] \
        or ["_(nothing closed yet)_"]
    path = records_dir / STATUS_NAME
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return path


# --- INDEX (derived view — rebuilt from frontmatter, never hand-edited) -------


def _cell(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ")


def rebuild_index(records_dir, index_path=None, records_link_prefix: str = "") -> Path:
    """Regenerate INDEX.md as a table, most recently active first."""
    records_dir = Path(records_dir)
    index_path = Path(index_path) if index_path else records_dir / INDEX_NAME
    rows = []
    if records_dir.is_dir():
        for p in records_dir.glob("*.md"):
            if p.name in (index_path.name, STATUS_NAME):
                continue
            fm = _fm_parse(p.read_text(encoding="utf-8"))
            if is_record(fm):
                rows.append((p.name, fm))
    rows.sort(key=lambda r: (r[1].get("last_active") or r[1].get("occurred", ""),
                             r[1].get("captured", "")), reverse=True)

    lines = [
        "# automemory — index",
        "",
        "One row per session, most recently active first. Generated from the records — "
        "do not hand-edit.",
        "",
    ]
    if not rows:
        lines.append("_(empty — records appear here automatically as sessions are captured.)_")
    else:
        lines += ["| Title | Provider | Started | Last active | Model | Turns |",
                  "|---|---|---|---|---|---|"]
        for name, f in rows:
            link = f"{records_link_prefix}/{name}" if records_link_prefix else name
            provider = f.get("source", "").split(":", 1)[0]
            started = f.get("session_started") or f.get("occurred", "")
            lines.append(
                f"| [{_cell(f.get('title') or '(untitled)')}]({link}) | {_cell(provider)} "
                f"| {started} | {f.get('last_active', '')} | {_cell(f.get('model', ''))} "
                f"| {f.get('turns', '')} |")
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return index_path
