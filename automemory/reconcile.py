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

from . import dispatch, memory_mirror, progress, providers, record, redact, transcript

DEFAULT_CAP = 5
# The scribe model is NOT a constant — see providers.py. `DEFAULT_MODEL = "haiku"`
# used to live here and broke every opencode backfill (its model namespace rejects
# "haiku"). Callers must go through dispatch.default_model(provider).
PROVIDER_ENV = "AUTOMEMORY_PROVIDER"
# Inside the package, so every install (editable or not) ships it.
_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "record-author.md"


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
    """Split authored markdown into {heading: body}; a leading `**Summary:** …`
    line becomes {"Summary": …}."""
    secs, cur, buf = {}, None, []
    m = re.search(r"(?m)^\*\*Summary:\*\*\s*(.+)$", md.split("## ", 1)[0])
    if m:
        secs["Summary"] = m.group(1).strip()
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
        return "norecord"   # may carry a reason: "NO-RECORD: <why>"
    if "## Done" in t and t.split("## Done", 1)[1].strip():
        return "ok"
    return "bad"


def _transcript_meta(path: Path) -> dict:
    """Best-effort envelope metadata from the transcript (never raises)."""
    tss, model, first_prompt = [], "", ""
    custom, ai = "", ""  # the user's rename beats the auto title; the LATEST of each wins
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
            if t in ("custom-title", "ai-title"):
                # Claude Code writes {"type":"ai-title","aiTitle":...} — the key this
                # once missed, so every record fell back to the first prompt's words.
                got = (r.get("customTitle") or r.get("aiTitle") or r.get("title")
                       or r.get("content") or r.get("text") or "").strip()
                if got and t == "custom-title":
                    custom = got
                elif got:
                    ai = got
            m = r.get("message")
            cand = (m.get("model") if isinstance(m, dict) else None) or r.get("model")
            if cand and not str(cand).startswith("<"):  # skip "<synthetic>"
                model = cand  # LAST used wins: the frontmatter shows current state
            if not first_prompt and t == "user":
                m = r.get("message")
                c = m.get("content") if isinstance(m, dict) else None
                if isinstance(c, str):
                    first_prompt = c
    except OSError:
        pass
    occurred = min(tss)[:10] if tss else "0000-00-00"
    last_active = max(tss)[:10] if tss else occurred
    title = custom or ai
    if not title:
        title = " ".join(first_prompt.split()[:8]) or f"session {path.stem[:8]}"
    return {"occurred": occurred, "last_active": last_active, "title": title[:120],
            "custom_title": custom[:120], "model": model,
            "started_ts": min(tss) if tss else "", "last_active_ts": max(tss) if tss else ""}


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

    def is_scribe(self, ref: str, cwd: str = None) -> bool:  # pragma: no cover
        """True if this session is the engine's own dispatch output (skip it)."""
        return False

    def has_content(self, ref: str, cwd: str = None) -> bool:  # pragma: no cover
        """True if the session has anything a scribe could summarise."""
        return True

    def scribe_models(self, ref: str, cwd: str = None) -> list:
        """Models to try for this session's scribe, in order. The engine never
        names a model itself: the user's configured choice, else the provider's
        own default (None = don't pass --model)."""
        return [dispatch.default_model(self.name)]


# --- cheapest scribe, without naming a model in code -------------------------------
# Owner, 2026-11-05: use each tool's cheapest model for scribing, but never by naming a
# model (names get retired; a hardcoded one already killed opencode once). Only a tool
# that EXPOSES a cheap tier can be asked for it; every chain then ends in models that
# can't be dead — the session's own model, then the tool's default.
_CHEAP_WORDS = ("affordable", "efficient", "cheap", "mini", "lite", "small")


def codex_cheapest(home=None):
    """The model Codex itself DESCRIBES as affordable/efficient, newest-ranked first (its
    `priority`), from ~/.codex/models_cache.json. A heuristic over Codex's own words —
    there is no price field — so it re-derives itself as Codex's list changes. None if
    nothing qualifies."""
    try:
        root = Path(home or os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
        models = json.loads((root / "models_cache.json").read_text(encoding="utf-8"))["models"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    cands = [m for m in models if m.get("visibility") == "list" and m.get("supported_in_api")
             and any(w in (m.get("description") or "").lower() for w in _CHEAP_WORDS)]
    cands.sort(key=lambda m: m.get("priority", 999))
    return cands[0]["slug"] if cands else None


def opencode_small_model():
    """opencode's own `small_model` setting, if the user set one (its cheap tier)."""
    for name in ("opencode.json", "opencode.jsonc"):
        p = Path.home() / ".config" / "opencode" / name
        try:
            text = re.sub(r"(?m)^\s*//.*$", "", p.read_text(encoding="utf-8"))
            v = json.loads(text).get("small_model")
            if v:
                return v
        except (OSError, ValueError):
            continue
    return None


def _chain(*models) -> list:
    out = []
    for m in models:
        if m not in out:
            out.append(m)
    return [m for m in out if m] + [None]   # None = the tool's own default, always last


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

    def has_content(self, ref: str, cwd: str = None) -> bool:
        p = self._path(ref, cwd)
        try:
            return p.stat().st_size > 0
        except OSError:
            return False

    def scribe_models(self, ref: str, cwd: str = None) -> list:
        """Configured → Claude's small-tier alias (`haiku`, kept current by Anthropic) →
        the session's own model → Claude's default. The fallbacks mean a retired alias
        degrades the cost, never the recording."""
        return _chain(dispatch.default_model(self.name), providers.scribe_model(self.name),
                      self.meta(ref, cwd).get("model"))


class _OpencodeSource(_Source):
    """opencode: one SQLite store at ~/.local/share/opencode/opencode.db, rows
    keyed by session id, scoped by the session's recorded directory."""

    name = "opencode"

    def __init__(self, db=None):
        self.db = db
        self._available = None  # `opencode models`, fetched once per run

    def _available_models(self):
        """What opencode can route right now, or None if we can't tell. A retired
        model can HANG for the full dispatch timeout rather than erroring
        (measured 2026-10-29), so dead candidates are dropped before any call."""
        if self._available is None:
            try:
                import subprocess
                p = subprocess.run(["opencode", "models"], capture_output=True,
                                   text=True, timeout=60)
                listed = {l.strip() for l in p.stdout.splitlines() if "/" in l}
                self._available = listed if (p.returncode == 0 and listed) else set()
            except (OSError, subprocess.SubprocessError):
                self._available = set()
        return self._available or None

    def list_ids(self, cwd: str) -> list:
        return transcript.opencode_session_ids(cwd, db=self.db)

    def render(self, ref: str, cwd: str = None) -> str:
        return transcript.render_opencode_session(ref, db=self.db)

    def meta(self, ref: str, cwd: str = None) -> dict:
        m = dict(transcript.opencode_session_meta(ref, db=self.db))
        last = transcript.opencode_session_model_ref(ref, db=self.db)
        if last:
            m["model"] = last.split("/", 1)[-1]  # last used, not most frequent
        m["last_active_ts"] = transcript.opencode_session_last_active(ref, db=self.db)
        m["last_active"] = m["last_active_ts"][:10] or m.get("occurred", "")
        # opencode's session title can't be told apart from a user rename, so the
        # filename falls back to the session id (custom_title stays empty).
        m.setdefault("custom_title", "")
        return m

    def scribe_models(self, ref: str, cwd: str = None) -> list:
        """Configured → opencode's own `small_model` (its cheap tier, if the user set
        one) → the model the session ran on → the model the user is on now. Candidates
        opencode no longer lists are dropped first: a retired model can HANG for the
        full timeout instead of erroring (measured 2026-10-29). Not "omit --model":
        opencode then picks the head of its recently-used list, which can be a retired
        model — and with the scribe agent that hangs."""
        out = [m for m in _chain(dispatch.default_model(self.name), opencode_small_model(),
                                 transcript.opencode_session_model_ref(ref, db=self.db),
                                 transcript.opencode_latest_model_ref(db=self.db)) if m]
        avail = self._available_models()
        live = [m for m in out if avail is None or m in avail]
        return live or out  # can't verify any? try them anyway, errors are logged

    def is_scribe(self, ref: str, cwd: str = None) -> bool:
        # cwd is accepted for interface parity with the Claude source, which needs
        # it to locate the file. opencode session ids are global and ignore it.
        return transcript.opencode_is_scribe_session(ref, dispatch.SCRIBE_SENTINEL,
                                                    db=self.db)

    def has_content(self, ref: str, cwd: str = None) -> bool:
        """True if the session carries at least one RENDERABLE message.

        Row count is too coarse: a session can hold `system`/`idle` rows and
        render to nothing at all (measured 2026-10-17 — three such sessions, two
        rows each, zero characters out). Those still consumed cap slots before
        this check existed. Deliberately not a render — rendering every session
        to decide the cap would cost as much as the backfill itself.
        """
        try:
            con = transcript._oc_connect(self.db)
        except Exception:
            return True
        try:
            if transcript._has_table(con, "session_message"):
                row = con.execute(
                    "select count(*) from session_message "
                    "where session_id = ? and type in ('user','assistant')",
                    (ref,)).fetchone()
                if row and row[0]:
                    return True
            if transcript._has_table(con, "message"):
                row = con.execute(
                    "select count(*) from message where session_id = ? "
                    "and json_extract(data,'$.role') in ('user','assistant')",
                    (ref,)).fetchone()
                return bool(row and row[0])
            return True
        except Exception:
            return True
        finally:
            try:
                con.close()
            except Exception:
                pass


class _CodexSource(_Source):
    """Codex: one JSONL per session under ~/.codex/sessions/ (and archived_sessions/),
    scoped by the session's recorded cwd."""

    name = "codex"

    def __init__(self, home=None):
        self.home = home
        self._files = {}

    def _path(self, ref, cwd=None):
        if ref not in self._files and cwd:
            self._files.update(transcript.codex_session_files(cwd, home=self.home))
        return self._files.get(ref)

    def list_ids(self, cwd: str) -> list:
        self._files = transcript.codex_session_files(cwd, home=self.home)
        return list(self._files)

    def render(self, ref: str, cwd: str = None) -> str:
        p = self._path(ref, cwd)
        return transcript.render_codex_session(p) if p else ""

    def meta(self, ref: str, cwd: str = None) -> dict:
        p = self._path(ref, cwd)
        return transcript.codex_session_meta(p, home=self.home) if p else {
            "occurred": "0000-00-00", "last_active": "", "title": f"session {ref[:8]}",
            "custom_title": "", "model": ""}

    def is_scribe(self, ref: str, cwd: str = None) -> bool:
        # The scribe runs --ephemeral, so its sessions are never stored; this is the
        # belt to that brace.
        first = next((t for _, role, t, _ in transcript._codex_items(self._path(ref, cwd) or Path("/nonexistent"))
                      if role == "user"), "")
        return dispatch.SCRIBE_SENTINEL in first

    def has_content(self, ref: str, cwd: str = None) -> bool:
        return any(role == "user" for _, role, _, _ in
                   transcript._codex_items(self._path(ref, cwd) or Path("/nonexistent")))

    def scribe_models(self, ref: str, cwd: str = None) -> list:
        """Configured → the model Codex itself describes as affordable (heuristic over its
        own model list; no price exists to read) → the session's own model → Codex's
        default."""
        return _chain(dispatch.default_model(self.name), codex_cheapest(self.home),
                      self.meta(ref, cwd).get("model"))


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
        if p.name == "codex":
            return _CodexSource(home=kw.get("codex_home"))

    oc = _OpencodeSource(db=kw.get("opencode_db"))
    if oc.list_ids(cwd):
        return oc
    # Neither store has sessions here yet (a new project): the default provider —
    # not opencode, which made `init` wire nothing in a fresh folder (found 2026-11-03).
    return _ClaudeCodeSource(projects_root=kw.get("claude_projects_root"))


def resolve_sources(cwd: str, provider: str = None, **kw) -> list:
    """EVERY provider store that holds sessions for this cwd — the reconciler's
    entry point. A project driven by both Claude Code and opencode must have both
    captured; `resolve_source` above picks ONE and is only for trigger wiring
    (init/status), which genuinely is per-harness.

    An explicit provider (arg or AUTOMEMORY_PROVIDER) still narrows to one, but
    run() says so out loud — a silent pin is how an alias once hid 54 sessions.
    """
    if provider or os.environ.get(PROVIDER_ENV):
        return [resolve_source(cwd, provider=provider, **kw)]
    found = [s for s in (_ClaudeCodeSource(projects_root=kw.get("claude_projects_root")),
                         _OpencodeSource(db=kw.get("opencode_db")),
                         _CodexSource(home=kw.get("codex_home")))
             if s.list_ids(cwd)]
    return found or [_ClaudeCodeSource(projects_root=kw.get("claude_projects_root"))]


def _display_name(src, ref: str, cwd: str) -> str:
    """Short human label for progress lines: the session title, else its id."""
    try:
        t = src.meta(ref, cwd).get("title") or ""
    except Exception:
        t = ""
    t = redact.redact(t)[0]
    return (t[:48] + "…") if len(t) > 49 else (t or record.short(ref))


# --- core (testable) ----------------------------------------------------------


def split_turns(text: str) -> list:
    """Rendered transcript -> one chunk per user turn (`### user` + what follows).
    Anything before the first user turn rides with the first chunk. A turn is one
    message from the person; that count is the record's progress marker."""
    parts = re.split(r"(?m)^(?=### user\s*$)", text or "")
    chunks = [p for p in parts if p.strip()]
    if len(chunks) > 1 and not chunks[0].lstrip().startswith("### user"):
        chunks[1] = chunks[0] + chunks[1]
        chunks = chunks[1:]
    return chunks


# --- usage: what scribe calls actually cost YOU (learned, never a price table) ----
# Prices change and differ per account and model; a hardcoded table would rot like the
# hardcoded model names did. Every scribe call already reports its cost and tokens, so
# log them, and estimate a backlog from the user's own history.
USAGE_LOG = Path(os.environ.get("AUTOMEMORY_USAGE_LOG")
                 or (Path.home() / ".local" / "state" / "automemory" / "usage.jsonl"))
CHARS_PER_TOKEN = 4  # rough rule of thumb for English text + code; refined by the log


def log_usage(provider: str, model, chars_in: int, result) -> None:
    try:
        USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        u = result.usage or {}
        tok_in = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens",
                                                   "cache_creation_input_tokens", "input"))
        tok_out = int(u.get("output_tokens") or u.get("output") or 0)
        with USAGE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": record.now_iso(), "provider": provider,
                                 "model": result.model or (model if isinstance(model, str) else ""),
                                 "chars_in": chars_in, "tokens_in": tok_in, "tokens_out": tok_out,
                                 "cost_usd": round(float(result.cost_usd or 0), 6)}) + "\n")
    except Exception:
        pass  # usage logging must never break recording


def _usage_rates(provider: str, last: int = 50):
    """(cost per char, tokens per char, n) from the user's own recent calls, or None."""
    try:
        rows = [json.loads(l) for l in USAGE_LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
    except (OSError, ValueError):
        return None
    rows = [r for r in rows if r.get("provider") == provider and r.get("chars_in")][-last:]
    if not rows:
        return None
    chars = sum(r["chars_in"] for r in rows)
    cost = sum(r.get("cost_usd") or 0 for r in rows)
    toks = sum((r.get("tokens_in") or 0) + (r.get("tokens_out") or 0) for r in rows)
    return (cost / chars if cost else None, toks / chars if toks else None, len(rows))


def backlog_estimate(pending: list, records_dir, cwd: str) -> str:
    """One line for the backlog question: tokens, and cost from the user's history."""
    per = {}
    for src, ref, cov in pending:
        try:
            turns = split_turns(src.render(ref, cwd))
        except Exception:
            continue
        has_rec = cov is not None and record.find_record(records_dir, ref) is not None
        per[src.name] = per.get(src.name, 0) + len("".join(turns[cov:] if has_rec else turns))
    parts, total_cost, known = [], 0.0, True
    for prov, chars in per.items():
        rates = _usage_rates(prov)
        tpc = (rates[1] if rates and rates[1] else 1 / CHARS_PER_TOKEN)
        parts.append(f"~{int(chars * tpc / 1000)}k tokens ({prov})")
        if rates and rates[0]:
            total_cost += chars * rates[0]
        else:
            known = False
    cost = (f"about ${total_cost:.2f}, judging by your recent runs" if known and total_cost
            else "cost not known yet — it's learned from your first few runs")
    return f"{', '.join(parts) or 'nothing to send'}; {cost}"


def _pending_order_key(src, ref: str, cov, records_dir, cwd) -> str:
    """When the uncovered work began, as a sortable string: a continued session's new
    stretch began after its record was last captured; a new session at its start.
    Processing in this order lets ONE open list move forward the way the work did."""
    if cov is not None:
        p = record.find_record(records_dir, ref)
        if p is not None:
            return record._fm_parse(p.read_text(encoding="utf-8")).get("captured", "")
    try:
        m = src.meta(ref, cwd)
        return m.get("started_ts") or m.get("occurred", "")
    except Exception:
        return ""


def find_pending(source: _Source, records_dir: Path, cwd: str, current_session: str):
    """Sessions with turns nobody has looked at yet, newest-first, as
    (ref, covered) — covered = turns already handled (by a record or a decline),
    None if the session was never seen.

    A session is pending when it has no record/decline and has content, OR it has
    grown past what was covered (resumed/continued). Empty sessions are dropped
    here, before the cap, so they can't eat it (measured 2026-10-17).
    """
    out = []
    ignored = record.ignored_sessions(records_dir)
    for ref in source.list_ids(cwd):
        if ref == current_session or source.is_scribe(ref, cwd) or ref in ignored:
            continue
        covered = record.covered_turns(records_dir, ref)
        if covered is None:
            if source.has_content(ref, cwd):
                out.append((ref, None))
            continue
        text = source.render(ref, cwd)
        turns = len(split_turns(text))
        seen = record.covered_chars(records_dir, ref)
        if turns > covered:
            out.append((ref, covered))
        elif (turns == covered and covered > 0 and not seen
              and record.find_record(records_dir, ref) is None):
            # A decline from before declines remembered how much they saw: growth can't
            # be detected, so look once more (one scribe call per old decline).
            out.append((ref, covered - 1))
        elif turns == covered and seen and len(text) > seen and covered > 0:
            # Same number of turns, but the last one grew after it was checked (work
            # that finished later — measured 2026-11-04). Re-cover that last turn.
            out.append((ref, covered - 1))
    return out


def find_missing(source: _Source, records_dir: Path, cwd: str, current_session: str):
    """Back-compat: refs only."""
    return [ref for ref, _ in find_pending(source, records_dir, cwd, current_session)]


_PROJECT_LIST = (
    "\n\nThis project already has open items from earlier sessions (possibly in other "
    "tools), listed between BEGIN/END OPEN ITEMS with their ids. In `## Outstanding`, "
    "close each one this session resolved (`✓ closed: [oN] <exact wording> — <how>`), "
    "carry forward each one it doesn't mention exactly as given (id included), and add "
    "this session's new items without an id."
)

_CONTINUE = (
    "\n\nThis session ALREADY HAS a record. The transcript you are given is ONLY the "
    "new turns since then. Write only what is new in them — do not repeat earlier work. "
    "The items still open before these turns are listed between BEGIN/END OPEN ITEMS: "
    "in `## Outstanding`, close each one these turns resolved (`✓ closed: <item> — "
    "<how>`), carry forward each one they don't mention, and add new ones. Same "
    "write-gate: if the new turns produced nothing durable, output exactly NO-RECORD. "
    "Confirming an open item finished or only partly done IS durable: record it, never "
    "decline it."
)


_MODEL_GONE = ("model is unavailable", "model unavailable", "no-route",
               "invalid model", "unknown model", "model not found",
               "timed out")  # a dead model can hang instead of erroring


def _dispatch_first_working(dispatch_fn, text: str, prompt: str, models: list):
    """Try each candidate model; move on only when the MODEL is the problem
    (retired, unrouted, unknown). Any other result — success or a different
    error — is returned as-is."""
    result = None
    for m in models or [None]:
        result = dispatch_fn(text, prompt, model=m)
        try:  # name the model that answered; None = the tool's own default
            result.model = getattr(result, "model", "") or m or "default"
        except AttributeError:
            pass
        if not (result.is_error and any(g in (result.error or "").lower()
                                        for g in _MODEL_GONE)):
            return result
    return result


def backfill_one(source: _Source, ref: str, records_dir: Path, project: str,
                 prompt: str, model, dispatch_fn, cwd: str = None, covered=None):
    """One session: render -> slice to the turns not yet covered -> redact ->
    author (1 retry on malformed) -> create the record, or continue it in place,
    or remember the decline. Returns an outcome dict; never raises.

    covered=None  : never seen -> the whole transcript; a record is created.
    covered=N     : turns 1..N already handled -> only turns N+1.. are sent. If a
                    record exists it is CONTINUED (`## Update`), never rewritten;
                    if N came from a decline, the whole transcript is re-read
                    (the earlier part had nothing alone, but may give context).
    """
    full = source.render(ref, cwd)
    turns = split_turns(full)
    total = len(turns)
    if not full.strip() or total == 0:
        return {"uuid": ref, "status": "empty", "cost": 0.0}
    existing = record.find_record(records_dir, ref) if covered is not None else None

    # The PROJECT's open list (one list across sessions and tools), with ids — never
    # the whole old record: stale text handed to the scribe spreads (measured
    # 2026-11-03 — a never-closed "not built" item merged with new turns into a false
    # claim). Items without an id yet get one now, so the scribe sees and keeps them.
    listed, _, _ = record.project_open_list(records_dir)
    given_items, next_id = record.with_ids(listed, record.max_item_id(records_dir) + 1)
    open_block = ("\n\n--- BEGIN OPEN ITEMS ---\n" + ("\n".join(given_items) or "(none)")
                  + "\n--- END OPEN ITEMS ---")
    if existing is not None:
        text = "".join(turns[covered:])
        instruction = prompt + _CONTINUE + open_block
    else:
        text, instruction = full, prompt + (_PROJECT_LIST + open_block if given_items else "")
    scrubbed, _ = redact.redact(text)
    chars_in = len(scrubbed) + len(instruction)

    result = None
    # Two distinct retries, because two distinct failures:
    #  - malformed output from the scribe (weak-model stochasticity)
    #  - a DISPATCH error, which is often transient. Observed 2026-10-15: the
    #    opencode free tier 403s intermittently under repeated calls.
    # Permanent errors (missing CLI, unavailable model) aren't retried.
    _PERMANENT = ("not found", "no such file", "unavailable", "invalid model")
    dispatch_tries, max_dispatch_tries = 0, 3
    models = model if isinstance(model, list) else [model]
    call = lambda: _dispatch_first_working(dispatch_fn, scrubbed, instruction, models)
    verdict = "bad"
    for _ in (1, 2):  # author, retry once on malformed
        result = call()
        if not result.is_error:
            log_usage(source.name, model, chars_in, result)
        while result.is_error and dispatch_tries < max_dispatch_tries:
            if any(p in result.error.lower() for p in _PERMANENT):
                break
            dispatch_tries += 1
            time.sleep(min(2 ** dispatch_tries, 8))
            result = call()
        if result.is_error:
            return {"uuid": ref, "status": "dispatch-error", "error": result.error,
                    "cost": result.cost_usd}
        verdict = _valid_or_norecord(result.text)
        if verdict in ("norecord", "ok"):
            break
    if verdict == "bad":
        return {"uuid": ref, "status": "malformed", "cost": result.cost_usd}

    meta = source.meta(ref, cwd)
    # Dates and the update's time are LOCAL to this machine, taken from the stretch's
    # last turn (transcripts store UTC).
    day, clock = record.local_stamp(meta.get("last_active_ts", ""))
    if day:
        meta = dict(meta, last_active=day)
    sday, _ = record.local_stamp(meta.get("started_ts", ""))
    if sday:
        meta = dict(meta, occurred=sday)
    tag = providers.resolve(source.name).file_tag or source.name
    # Second net: scrub what the scribe WROTE as well as what it read, so a
    # secret that slipped past the first pass still never lands in a file.
    secs = ({k: redact.redact(v)[0] for k, v in _split_sections(result.text).items()}
            if verdict == "ok" else {})
    scribe = getattr(result, "model", "") or ""
    dropped = []
    if verdict == "ok":
        # The engine's rules on the list: new items get ids; every given item comes
        # back open or closed — any the scribe left out is carried forward, marked.
        secs["Outstanding"], dropped, _ = record.settle_outstanding(
            given_items, secs.get("Outstanding", ""), next_id)
    # Titles often come from the first message, which can hold full paths — never put
    # the home folder (a username) into a record (found 2026-11-05 in a Codex title).
    home = str(Path.home())
    meta = dict(meta, title=redact.redact(meta.get("title", ""))[0].replace(home, "~"),
                custom_title=redact.redact(meta.get("custom_title", ""))[0].replace(home, "~"))

    if existing is not None:
        # Continue in place. A declined stretch adds no block but still advances
        # `turns` and records any title/model/filename change — and WHY it was declined,
        # in `## Changes` (found 2026-11-04: a declined check-in left no trace).
        reason = ""
        if verdict == "norecord":
            reason = result.text.strip()[len("NO-RECORD"):].lstrip(" :—-").strip()
            reason = redact.redact(reason.splitlines()[0] if reason else "")[0][:200]
        path = record.append_update(
            existing, turns_from=covered + 1, turns_to=total, seen_chars=len(full),
            title=meta["title"], custom_title=meta.get("custom_title", ""),
            model=meta["model"], last_active=meta.get("last_active", ""),
            provider_tag=tag, session_ref=ref, date=meta.get("last_active", ""),
            done=secs.get("Done", ""), learnings=secs.get("Learnings & Gotchas", ""),
            outstanding=secs.get("Outstanding", ""), summary=secs.get("Summary", ""),
            bugs=secs.get("Bugs & Fixes", ""), scribe=scribe, time=clock,
            declined=verdict == "norecord", declined_reason=reason)
        status = "updated" if verdict == "ok" else "no-record"
        return {"uuid": ref, "status": status, "cost": result.cost_usd, "path": str(path),
                "given": len(given_items), "dropped": dropped, "scribe": scribe,
                "reason": reason}

    if verdict == "norecord":
        reason = result.text.strip()[len("NO-RECORD"):].lstrip(" :—-").strip()
        record.mark_declined(records_dir, ref, total, chars=len(full),
                             reason=redact.redact(reason.splitlines()[0] if reason else "")[0])
        return {"uuid": ref, "status": "no-record", "cost": result.cost_usd, "scribe": scribe,
                "reason": record._declined_load(records_dir).get(ref, {}).get("reason", "")}

    path = record.write_record(
        records_dir, provider=source.name, provider_tag=tag, session_ref=ref,
        model=meta["model"], subject=project, title=meta["title"],
        custom_title=meta.get("custom_title", ""), session_started=meta["occurred"],
        last_active=meta.get("last_active", ""), turns=total, seen_chars=len(full),
        done=secs.get("Done", ""), learnings=secs.get("Learnings & Gotchas", ""),
        outstanding=secs.get("Outstanding", ""), summary=secs.get("Summary", ""),
        bugs=secs.get("Bugs & Fixes", ""), scribe=scribe, time=clock)
    return {"uuid": ref, "status": "ok", "cost": result.cost_usd, "path": str(path),
            "given": len(given_items), "dropped": dropped, "scribe": scribe}


def _summary_line(outcomes: list, cost: float) -> str:
    """One line per run: checked / written / declined / failed. `declined` = the
    scribe's write-gate said NO-RECORD; `empty` = nothing renderable (no call)."""
    if not outcomes:
        return "automemory: run — nothing new to check."
    n = lambda *st: sum(1 for o in outcomes if o["status"] in st)
    parts = [f"{n('ok')} written"]
    if n("updated"):
        parts.append(f"{n('updated')} continued")
    parts.append(f"{n('no-record')} declined")
    if n("empty"):
        parts.append(f"{n('empty')} empty")
    failed = n("dispatch-error", "malformed")
    if failed:
        parts.append(f"{failed} FAILED")
    return (f"automemory: run — checked {len(outcomes)}: {', '.join(parts)} "
            f"(~${cost:.2f}).")


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


def handover_text(records_dir: Path) -> str:
    """What a starting session is shown: the project's session map. (The old
    "write your own record at a natural checkpoint" fast-path reminder was
    retired 2026-10-30 — undefined, uncheckable, and it bypassed the format.)"""
    idx = Path(records_dir) / record.INDEX_NAME
    if not idx.exists():
        return ""
    busy = ""
    if (Path(records_dir) / ".run.lock").exists():
        # E.g. exit then an immediate resume: the end-of-session run is still writing
        # (measured 2026-11-03 — the map showed 45 turns while 46–49 were being added).
        busy = "(a recording run is in progress — this map may be a minute behind)\n"
    return ("=== automemory: session map ===\n" + busy
            + idx.read_text(encoding="utf-8").rstrip() + "\n\n"
            + open_items_text(records_dir))


OPEN_LINES = 15  # keep the start-of-session context small
def open_items_text(records_dir: Path) -> str:
    """The project's open list — the newest one written anywhere, across sessions and
    tools — so the live agent can act on it and the next update can close items.
    Items for the person first. Full list and recent closures: STATUS.md."""
    items, src, date = record.project_open_list(records_dir)
    if not items:
        return ""
    items = ([i for i in items if "**You:**" in i] + [i for i in items if "**You:**" not in i])
    out = [f"=== automemory: open items (project · as of {date} · {src}) ==="]
    out += items[:OPEN_LINES]
    if len(items) > OPEN_LINES:
        out.append(f"  … {len(items) - OPEN_LINES} more — agent-records/STATUS.md")
    return "\n".join(out) + "\n\n"


class _ProjectLock:
    """One run per project at a time. End-of-session for one session and
    start-of-session for the next can fire seconds apart; two concurrent runs
    would both see the same uncovered turns and append the same update twice.
    A lock older than 30 minutes is treated as stale (a crashed run)."""

    STALE_S = 30 * 60

    def __init__(self, records_dir: Path):
        self.path = Path(records_dir) / ".run.lock"
        self.held = False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if time.time() - self.path.stat().st_mtime > self.STALE_S:
                self.path.unlink()
        except OSError:
            pass
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()}\n".encode())
            os.close(fd)
            self.held = True
        except FileExistsError:
            self.held = False
        return self

    def __exit__(self, *exc):
        if self.held:
            try:
                self.path.unlink()
            except OSError:
                pass
        return False


def run(payload: dict, *, provider: str = None, claude_projects_root=None,
        opencode_db=None, dispatch_fn=None, cap: int = DEFAULT_CAP, out=None,
        handover: bool = True, all_pending: bool = False) -> dict:
    """Orchestrate handover-in + bounded backfill across EVERY provider with
    sessions in this project. Returns a summary dict."""
    out = out or sys.stdout
    cwd = payload.get("cwd") or os.getcwd()
    current = payload.get("session_id") or ""
    project = Path(cwd).name or "project"
    records_dir = records_dir_for(cwd)
    sources = resolve_sources(cwd, provider=provider,
                              claude_projects_root=claude_projects_root,
                              opencode_db=opencode_db)
    prompt = _load_prompt()

    pinned = provider or os.environ.get(PROVIDER_ENV)
    if pinned:
        out.write(f"automemory: WARNING — provider pinned to {sources[0].name} "
                  f"({PROVIDER_ENV}); other providers' sessions are NOT captured.\n")

    # 1. handover-in — for the live agent's context. Skipped in background runs,
    # whose output goes to a log, not to an agent.
    if handover:
        out.write(handover_text(records_dir))

    # One run per project at a time (end-of-session + next start can overlap).
    lock = _ProjectLock(records_dir).__enter__()
    if not lock.held:
        out.write("automemory: another run is already working on this project — skipped.\n")
        return {"providers": [s.name for s in sources], "missing": 0, "processed": 0,
                "wrote": 0, "deferred": 0, "cost": 0.0, "outcomes": [], "skipped": "locked"}
    try:
        # 2. find missing — per provider, then one shared cap across all of them
        with progress.Spinner("checking sessions"):
            per_source = [[(src, ref, cov) for ref, cov in find_pending(src, records_dir, cwd, current)]
                          for src in sources]
        # Oldest first, across every provider: one open list moves forward in the
        # order the work happened (a Claude session Monday, a Codex one Tuesday).
        missing = sorted((x for xs in per_source for x in xs),
                         key=lambda t: _pending_order_key(t[0], t[1], t[2], records_dir, cwd))
        if len(missing) > cap and (all_pending or (handover and progress.interactive()
                                                   and sys.stdin is not None and sys.stdin.isatty())):
            # A backlog, run by a person: say what it will take and let them choose.
            if all_pending:
                cap = len(missing)
            else:
                with progress.Spinner("estimating"):
                    est = backlog_estimate(missing, records_dir, cwd)
                sys.stderr.write(
                    f"\nautomemory: {len(missing)} sessions are waiting to be recorded "
                    f"(oldest first): {est}. One model call each, roughly a minute apiece.\n")
                try:
                    ans = input(f"Record all {len(missing)} now? [y/N] (N records the oldest {cap}) ")
                except EOFError:
                    ans = ""
                if ans.strip().lower() in ("y", "yes"):
                    cap = len(missing)
        todo, deferred = missing[:cap], missing[cap:]

        # 3. backfill — each session is authored by its own provider's dispatcher
        total_cost, wrote = 0.0, 0
        outcomes = []
        _VERB = {"ok": "✓ wrote", "updated": "✓ continued", "no-record": "· declined",
                 "empty": "· empty", "dispatch-error": "✗ FAILED", "malformed": "✗ FAILED"}
        for i, (src, ref, cov) in enumerate(todo, 1):
            fn = dispatch_fn or dispatch.DISPATCHERS.get(src.name, dispatch.author)
            name = _display_name(src, ref, cwd)
            what = "continuing" if cov is not None and record.find_record(records_dir, ref) else "writing"
            with progress.Spinner(f"{what} {i}/{len(todo)} · {src.name} · {name}"):
                r = backfill_one(src, ref, records_dir, project, prompt,
                                 src.scribe_models(ref, cwd), fn, cwd, covered=cov)
            why = f" — {r['reason']}" if r.get("reason") else ""
            why += f" · scribe {r['scribe']}" if r.get("scribe") else ""
            progress.note(f"  {_VERB.get(r['status'], r['status'])} {name}{why}")
            r["provider"] = src.name
            outcomes.append(r)
            total_cost += r.get("cost", 0.0)
            if r["status"] in ("ok", "updated"):
                wrote += 1
        if any(o["status"] in ("ok", "updated", "no-record") for o in outcomes):
            record.rebuild_index(records_dir)
            record.rebuild_status(records_dir)
        # ALWAYS report, even when nothing was written: a run that checked sessions and
        # declined them all must not look the same as a run that never happened (#5).
        out.write(_summary_line(outcomes, total_cost) + "\n")
        for o in outcomes:
            if o.get("dropped"):
                # Exact, by id: the scribe left these out; the engine carried them
                # forward (marked) so nothing vanished. Still worth knowing how often.
                out.write(f"automemory:   {len(o['dropped'])} of {o['given']} open items not "
                          f"mentioned by the scribe — carried forward by the engine: "
                          + " | ".join(d.strip()[:80] for d in o["dropped"]) + "\n")
        for o in outcomes:
            if o["status"] == "no-record" and o.get("reason"):
                out.write(f"automemory:   declined {o.get('provider', '')}:{record.short(o['uuid'])}"
                          f" — {o['reason']}\n")
        for o in outcomes:
            if o["status"] in ("dispatch-error", "malformed"):
                out.write(f"automemory:   {o['status']} {o.get('provider', '')}:{o['uuid']}"
                          f"{' — ' + o['error'].strip()[:200] if o.get('error') else ''}\n")
        stale = [p.name for p in Path(records_dir).glob("*.md") if p.name != record.INDEX_NAME
                 and record.is_record(record._fm_parse(p.read_text(encoding="utf-8")))
                 and (record.is_legacy(record._fm_parse(p.read_text(encoding="utf-8")))
                      or record.needs_reshape(record._split(p.read_text(encoding="utf-8"))[1]))]
        if stale:
            # Never reshaped silently: a format change rewrites sacred data, so the
            # user decides (migrate snapshots first).
            out.write(f"automemory: {len(stale)} record(s) in an older format — "
                      f"run `automemory migrate` to update them (snapshot first).\n")
        if deferred:
            out.write(f"automemory: {len(deferred)} more session(s) waiting — "
                      f"run `automemory reconcile --all` to record them all now.\n")

        # 4. mirror the agent's own memory. Claude Code only (opencode has no such dir),
        # but keyed on the STORE, not on which provider won detection — a project that
        # also has opencode sessions still has a Claude memory dir to mirror.
        # Runs every time: memory can change without any new session.
        mem = memory_mirror.MirrorResult()
        cc = next((s for s in sources if isinstance(s, _ClaudeCodeSource)), None)
        if cc is None and not pinned:
            cc = _ClaudeCodeSource(projects_root=claude_projects_root)
        if cc is not None:
            mem = memory_mirror.sync(
                memory_mirror.memory_dir_for(cc.projects_root, _slug_for_cwd(cwd)),
                memory_mirror.mirror_dir_for(records_dir))
            if mem.touched():
                bits = [f"{n} {w}" for n, w in
                        ((mem.new, "new"), (mem.superseded, "superseded"), (mem.retired, "retired")) if n]
                out.write(f"automemory: mirrored memory ({', '.join(bits)}).\n")

        return {"providers": [s.name for s in sources], "missing": len(missing),
                "processed": len(todo), "wrote": wrote, "deferred": len(deferred),
                "cost": total_cost, "outcomes": outcomes,
                "memory": {"new": mem.new, "superseded": mem.superseded,
                           "retired": mem.retired, "unchanged": mem.unchanged,
                           "skipped": mem.skipped}}
    finally:
        lock.__exit__()


def read_payload() -> dict:
    """A hook's JSON payload from stdin, or {} for a person / a silent pipe.

    A person at a terminal has a keyboard on stdin; an IDE task, cron, CI or an
    agent's shell can leave a pipe open and silent (measured 2026-10-30: read()
    hung 5+ min). A hook writes its payload at once, so wait briefly and treat
    silence as "no payload" — the manual capture-everything run."""
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    import select
    try:
        ready, _, _ = select.select([sys.stdin], [], [], 3.0)
    except (OSError, ValueError):
        ready = [sys.stdin]  # not selectable (e.g. a file): just read it
    if not ready:
        return {}
    try:
        return json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return {}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    run(read_payload(), handover="--background" not in argv, all_pending="--all" in argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
