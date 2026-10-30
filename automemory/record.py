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
    "title", "source", "model", "session_started", "last_active", "turns",
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
    return _declined_load(records_dir).get(session_ref)


# --- declined sessions (no record file, so a sidecar remembers them) ----------


def _declined_load(records_dir) -> dict:
    try:
        return json.loads((Path(records_dir) / DECLINED_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def mark_declined(records_dir, session_ref: str, turns: int) -> None:
    """The scribe said NO-RECORD up to `turns`. Re-checked only if it grows."""
    records_dir = Path(records_dir)
    records_dir.mkdir(parents=True, exist_ok=True)
    data = _declined_load(records_dir)
    data[session_ref] = int(turns)
    (records_dir / DECLINED_NAME).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                                             encoding="utf-8")


# --- body assembly --------------------------------------------------------------


def _sections(done: str, learnings: str, outstanding: str, level: str = "##") -> list:
    parts = [f"{level} Done", done.strip(), ""]
    if learnings and learnings.strip():
        parts += [f"{level} Learnings & Gotchas", learnings.strip(), ""]
    if outstanding and outstanding.strip():
        parts += [f"{level} Outstanding", outstanding.strip(), ""]
    return parts


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
                 session_started: str, last_active: str = "", turns: int = 0,
                 done: str, learnings: str = "", outstanding: str = "",
                 captured: str = "") -> Path:
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
        "turns": turns, "captured": captured or now_iso(), "subject": slug(subject),
        "status": "open",
    }
    body = "\n".join(["", *_sections(done, learnings, outstanding), "## Edits", "none", ""])
    _guarded_write(path, _fm_dump(fields) + "\n" + body)
    return path


# --- write: continue an existing record ----------------------------------------


def append_update(path, *, turns_from: int, turns_to: int, title: str,
                  custom_title: str = "", model: str, last_active: str,
                  provider_tag: str, session_ref: str, done: str = "",
                  learnings: str = "", outstanding: str = "", date: str = "",
                  captured: str = "") -> Path:
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
        header = f"## Update — {date} · turns {turns_from}–{turns_to}"
        block = "\n".join([header, "", *_sections(done, learnings, outstanding, "###")])
        body = _insert_before_tail(body, block)

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
            if p.name == index_path.name:
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
