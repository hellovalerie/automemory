"""Record core — the shared envelope both engines write into.

Spec: system/design/RECORD-CORE.md. Plain markdown + git, stdlib only (no YAML
dependency — the frontmatter format is a controlled flat `key: value` block).

Two jobs live here and nothing else (the core is the envelope, not the contents):
  - write_record(): persist one record (envelope + body) as a per-session file.
  - rebuild_index(): regenerate INDEX.md as a derived view from frontmatter.
Plus record_exists_for_session() for the reconciler's by-UUID idempotency.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

# --- helpers ------------------------------------------------------------------


def slug(text: str) -> str:
    """Lowercase; runs of non-alphanumerics collapse to one hyphen; trimmed."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "untitled"


def short(session_ref: str) -> str:
    """A short, collision-resistant, filename-safe token for a session ref.

    Prefixed ids (opencode `ses_ec1e5bf2…`) must merge the prefix with the first
    id segment: taking only the leading alphanumeric run would return the
    CONSTANT "ses" for every opencode session, and two same-day sessions on one
    subject would overwrite each other's record. (ENGINE-A.md relies on the
    session ref guaranteeing filename uniqueness.)

    Bare UUIDs (Claude Code `9f2c1a44-3b7e-…`) keep their historical 8-char
    prefix — merging is a no-op there because the first segment is already 8+.
    """
    ref = (session_ref or "").strip()
    runs = re.findall(r"[A-Za-z0-9]+", ref)
    if not runs:
        return "unknown"
    if len(runs) == 1:
        return runs[0][:8]
    # A SHORT leading run is a scheme prefix (`ses_…`), constant across every
    # session — it carries no discriminating power and would eat the token
    # budget, so skip it. A long one is already the id (Claude Code UUIDs).
    if len(runs[0]) <= 4:
        return runs[1][:8] or runs[0][:8]
    return runs[0][:8]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- frontmatter (controlled flat format; no YAML dep) ------------------------

# Envelope field order is fixed so records read consistently. `subject` and the
# optional lifecycle fields sit last; body follows the closing fence.
_ENVELOPE_ORDER = (
    "id", "source", "model", "occurred", "captured", "title", "status",
    "superseded_by", "subject",
)


def _fm_dump(fields: dict) -> str:
    lines = ["---"]
    for key in _ENVELOPE_ORDER:
        if key not in fields:
            continue
        val = fields[key]
        lines.append(f"{key}:" if val in (None, "") else f"{key}: {val}")
    lines.append("---")
    return "\n".join(lines)


def _fm_parse(text: str) -> dict:
    """Parse the leading `---` fenced block into a flat dict (tolerant)."""
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out


# --- write --------------------------------------------------------------------


def write_record(
    records_dir,
    *,
    provider: str,
    session_ref: str,
    model: str,
    subject: str,
    title: str,
    occurred: str,
    done: str,
    learnings: str = "",
    outstanding: str = "",
    captured: str = "",
) -> Path:
    """Write one session record. Returns the file Path.

    `done` is mandatory — it IS the write-gate ("Done is the minimum"). A caller
    with nothing concrete for Done should not call this at all.
    """
    if not (done and done.strip()):
        raise ValueError("record requires a non-empty Done (write-gate)")

    records_dir = Path(records_dir)
    records_dir.mkdir(parents=True, exist_ok=True)

    subj = slug(subject)
    stem = f"{occurred}-{subj}-{short(session_ref)}"
    path = records_dir / f"{stem}.md"

    fields = {
        "id": stem,
        "source": f"{provider}:{session_ref}",
        "model": model or "",
        "occurred": occurred,
        "captured": captured or _now_iso(),
        "title": title,
        "status": "open",
        "subject": subj,
    }

    parts = [_fm_dump(fields), "", "## Done", done.strip(), ""]
    if learnings and learnings.strip():
        parts += ["## Learnings & Gotchas", learnings.strip(), ""]
    if outstanding and outstanding.strip():
        parts += ["## Outstanding", outstanding.strip(), ""]
    parts += ["## Edits", "none", ""]

    path.write_text("\n".join(parts), encoding="utf-8")
    return path


# --- idempotency (reconciler uses this: "does this session have a record?") ---


def record_exists_for_session(records_dir, session_ref: str) -> bool:
    records_dir = Path(records_dir)
    if not records_dir.is_dir():
        return False
    for p in records_dir.glob("*.md"):
        src = _fm_parse(p.read_text(encoding="utf-8")).get("source", "")
        if src.endswith(f":{session_ref}") or src == session_ref:
            return True
    return False


# --- INDEX (derived view — rebuilt from frontmatter, never hand-edited) -------


def rebuild_index(records_dir, index_path, records_link_prefix: str = "agent-records") -> Path:
    """Regenerate INDEX.md from every record's frontmatter, newest-first."""
    records_dir = Path(records_dir)
    index_name = Path(index_path).name
    entries = []
    if records_dir.is_dir():
        for p in sorted(records_dir.glob("*.md")):
            if p.name == index_name:
                continue  # the index lives among the records; never index itself
            fm = _fm_parse(p.read_text(encoding="utf-8"))
            if not fm.get("id"):
                continue  # skip non-record markdown (no envelope frontmatter)
            entries.append(fm)
    entries.sort(
        key=lambda f: (f.get("occurred", ""), f.get("captured", "")), reverse=True
    )

    lines = [
        "# automemory — index",
        "",
        "One line per record, newest first. Derived from frontmatter — do not hand-edit.",
        "",
    ]
    if not entries:
        lines.append("_(empty — records appear here automatically as sessions are captured.)_")
    else:
        for f in entries:
            title = f.get("title", "(untitled)")
            rid = f.get("id", "")
            occurred = f.get("occurred", "")
            subj = f.get("subject", "")
            tail = " · ".join(x for x in (occurred, subj) if x)
            link = f"{records_link_prefix}/{rid}.md" if records_link_prefix else f"{rid}.md"
            lines.append(f"- [{title}]({link}) — {tail}")

    index_path = Path(index_path)
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return index_path
