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

from . import dispatch, memory_mirror, providers, record, redact, transcript

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
            "custom_title": custom[:120], "model": model}


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
        m["last_active"] = (transcript.opencode_session_last_active(ref, db=self.db)
                            or m.get("occurred", ""))
        # opencode's session title can't be told apart from a user rename, so the
        # filename falls back to the session id (custom_title stays empty).
        m.setdefault("custom_title", "")
        return m

    def scribe_models(self, ref: str, cwd: str = None) -> list:
        """User's configured model, then the model the session itself ran on,
        then the model the user is on NOW (latest session anywhere) — so an old
        session whose model has since been retired still gets a scribe. NOT
        "omit --model": opencode then picks the head of its recently-used
        list, which can be a retired model (measured 2026-10-29 — it was), and
        with the scribe agent that hangs instead of erroring."""
        out = []
        for m in (dispatch.default_model(self.name),
                  transcript.opencode_session_model_ref(ref, db=self.db),
                  transcript.opencode_latest_model_ref(db=self.db)):
            if m and m not in out:
                out.append(m)
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
                         _OpencodeSource(db=kw.get("opencode_db")))
             if s.list_ids(cwd)]
    return found or [_ClaudeCodeSource(projects_root=kw.get("claude_projects_root"))]


def _interleave(lists: list) -> list:
    """Round-robin merge, so a shared cap can't be spent entirely on one provider."""
    out, i = [], 0
    while any(i < len(l) for l in lists):
        out.extend(l[i] for l in lists if i < len(l))
        i += 1
    return out


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


def find_pending(source: _Source, records_dir: Path, cwd: str, current_session: str):
    """Sessions with turns nobody has looked at yet, newest-first, as
    (ref, covered) — covered = turns already handled (by a record or a decline),
    None if the session was never seen.

    A session is pending when it has no record/decline and has content, OR it has
    grown past what was covered (resumed/continued). Empty sessions are dropped
    here, before the cap, so they can't eat it (measured 2026-10-17).
    """
    out = []
    for ref in source.list_ids(cwd):
        if ref == current_session or source.is_scribe(ref, cwd):
            continue
        covered = record.covered_turns(records_dir, ref)
        if covered is None:
            if source.has_content(ref, cwd):
                out.append((ref, None))
            continue
        if len(split_turns(source.render(ref, cwd))) > covered:
            out.append((ref, covered))
    return out


def find_missing(source: _Source, records_dir: Path, cwd: str, current_session: str):
    """Back-compat: refs only."""
    return [ref for ref, _ in find_pending(source, records_dir, cwd, current_session)]


_CONTINUE = (
    "\n\nThis session ALREADY HAS a record (below, between BEGIN/END RECORD). "
    "Write ONLY what is new in the transcript you are given — the new turns since "
    "that record. Do not repeat anything the record already says. Same sections, "
    "same write-gate: if the new turns produced nothing durable, output exactly "
    "NO-RECORD."
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

    if existing is not None:
        _, prior = record._split(existing.read_text(encoding="utf-8"))
        text = "".join(turns[covered:])
        instruction = prompt + _CONTINUE + f"\n\n--- BEGIN RECORD ---\n{prior.strip()}\n--- END RECORD ---"
    else:
        text, instruction = full, prompt
    scrubbed, _ = redact.redact(text)

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
    tag = providers.resolve(source.name).file_tag or source.name
    secs = _split_sections(result.text) if verdict == "ok" else {}

    if existing is not None:
        # Continue in place. A declined stretch adds no block but still advances
        # `turns` and records any title/model/filename change.
        path = record.append_update(
            existing, turns_from=covered + 1, turns_to=total,
            title=meta["title"], custom_title=meta.get("custom_title", ""),
            model=meta["model"], last_active=meta.get("last_active", ""),
            provider_tag=tag, session_ref=ref, date=meta.get("last_active", ""),
            done=secs.get("Done", ""), learnings=secs.get("Learnings & Gotchas", ""),
            outstanding=secs.get("Outstanding", ""))
        status = "updated" if verdict == "ok" else "no-record"
        return {"uuid": ref, "status": status, "cost": result.cost_usd, "path": str(path)}

    if verdict == "norecord":
        record.mark_declined(records_dir, ref, total)
        return {"uuid": ref, "status": "no-record", "cost": result.cost_usd}

    path = record.write_record(
        records_dir, provider=source.name, provider_tag=tag, session_ref=ref,
        model=meta["model"], subject=project, title=meta["title"],
        custom_title=meta.get("custom_title", ""), session_started=meta["occurred"],
        last_active=meta.get("last_active", ""), turns=total,
        done=secs.get("Done", ""), learnings=secs.get("Learnings & Gotchas", ""),
        outstanding=secs.get("Outstanding", ""))
    return {"uuid": ref, "status": "ok", "cost": result.cost_usd, "path": str(path)}


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


def run(payload: dict, *, provider: str = None, claude_projects_root=None,
        opencode_db=None, dispatch_fn=None, cap: int = DEFAULT_CAP, out=None) -> dict:
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

    # 1. handover-in
    idx = records_dir / "INDEX.md"
    if idx.exists():
        out.write("=== automemory: session map ===\n")
        out.write(idx.read_text(encoding="utf-8").rstrip() + "\n\n")
    out.write("automemory: at a natural checkpoint, write/refresh this session's record "
              "(## Done required, else skip) into agent-records/.\n")

    # 2. find missing — per provider, then one shared cap across all of them
    per_source = [[(src, ref, cov) for ref, cov in find_pending(src, records_dir, cwd, current)]
                  for src in sources]
    missing = _interleave(per_source)
    todo, deferred = missing[:cap], missing[cap:]

    # 3. backfill — each session is authored by its own provider's dispatcher
    total_cost, wrote = 0.0, 0
    outcomes = []
    for src, ref, cov in todo:
        fn = dispatch_fn or dispatch.DISPATCHERS.get(src.name, dispatch.author)
        r = backfill_one(src, ref, records_dir, project, prompt,
                         src.scribe_models(ref, cwd), fn, cwd, covered=cov)
        r["provider"] = src.name
        outcomes.append(r)
        total_cost += r.get("cost", 0.0)
        if r["status"] in ("ok", "updated"):
            wrote += 1
    if any(o["status"] in ("ok", "updated", "no-record") for o in outcomes):
        record.rebuild_index(records_dir)
    # ALWAYS report, even when nothing was written: a run that checked sessions and
    # declined them all must not look the same as a run that never happened (#5).
    out.write(_summary_line(outcomes, total_cost) + "\n")
    for o in outcomes:
        if o["status"] in ("dispatch-error", "malformed"):
            out.write(f"automemory:   {o['status']} {o.get('provider', '')}:{o['uuid']}"
                      f"{' — ' + o['error'].strip()[:200] if o.get('error') else ''}\n")
    if deferred:
        out.write(f"automemory: {len(deferred)} older session(s) pending — "
                  f"run `automemory process` to catch up.\n")

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


def main() -> int:
    # A hook pipes a JSON payload in. A person typing `automemory reconcile` has a
    # keyboard on stdin — reading it would hang until Ctrl-D (hence the old
    # `</dev/null` workaround). No payload = no live session to exclude, which is
    # exactly the manual "capture everything, including now" behaviour.
    if sys.stdin is None or sys.stdin.isatty():
        payload = {}
    else:
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except json.JSONDecodeError:
            payload = {}
    run(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
