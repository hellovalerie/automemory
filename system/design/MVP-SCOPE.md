# MVP scope — Engine A (`learnings`) first slice (DRAFT)

Status: **DRAFT**, 2026-08-12. The bounded first build. `BUILD-SPEC.md` is the whole
product (both strategies, full CLI, tiers, secret-scrub); this is the minimal shippable
slice that proves Engine A end-to-end. Governed by BUILD-SPEC's fixed commitments (Python 3
stdlib-only, filesystem canonical, git optional, Claude-Code-only v1, no personal content
ever ships).

## Goal

A frictionless self-scribe: at a session boundary the live agent writes a factual session
record; the next session starts oriented from the map. End-to-end, one strategy
(`learnings` / our "Engine A"), on the shared record core.

## IN scope (the slice)

1. **Record core (minimal):** write an entry conforming to the `RECORD-CORE.md` envelope;
   file-per-session-UUID (no locks needed); `subject` tag; the three-part body
   (Done / Learnings&Gotchas / Outstanding, Done required).
2. **INDEX (the map):** generate/append one line per record from frontmatter; newest-first.
3. **Write trigger (Claude Code adapter):** the mechanism by which the LIVE agent authors
   its record at session boundary (+ cadence). ⛔ **BLOCKED pending hook-mechanics research**
   (agent dispatched 2026-08-12). Do not build until resolved.
4. **SessionStart handover-in:** hook injects the map/orientation into the starting session.
5. **`automemory init`:** idempotently wire the hook(s) into Claude Code `settings.json`
   (merge, never clobber — see A8/open concern) + scaffold config. Zero-dependency.
6. **Minimal read CLI:** `status` (health/last capture) + `peep` (index/titles). Enough to
   see it working.

## OUT of scope (deferred to full BUILD-SPEC)

- Engine B / `automemory-capture` (blind observer + extractor) — the whole texture pipeline.
- Capture tiers & live cost meter (§6) — Engine A is the Local/~free tier by definition; the
  tier *chooser* is post-MVP.
- Secret-scrubbing pass (§7) — **caveat below.**
- Detached-worker daemonization (§3, A2) — Engine A is synchronous by design; no daemon.
- Full CLI (`partition/parse/purge/protect/package/port/process/peer`) — post-MVP.
- Multi-surface adapters (Codex/ChatGPT), brew tap polish, migration tooling.

## Caveats to hold (don't let MVP-thinning violate a product value)

- **Secret-scrubbing (§7) is default-deny in the full product.** Engine A is self-authored
  (the agent writes prose, not a raw transcript dump), so the exposure surface is smaller —
  but "self-authored" is not "safe by construction." Decide before ship: does the MVP need a
  minimal redaction pass, or is a stated honest limit acceptable for the self-scribe path?
  (Flagged, not resolved.)
- **`init` editing a user-hand-edited settings.json (A8):** merge semantics + a protect step,
  not clobber. Load-bearing for "never overwrite user data."

## Resolved before code (2026-08-12)

- **Trigger:** Option 2 (non-disruptive live fast-path) + SessionStart reconciler backstop.
  Stop-block forced authoring (Option 1) killed on lived experience. See ENGINE-A.md.
- **Records dir:** `agent-records/` (ships empty).
- **Secret-scrub:** minimal heuristic pass now, on the backfill read. Built = `redact.py`.

## Build status — RESUME POINT (2026-08-12, end of day)

Working tree clean; git history clean (rebuilt to July-3 anchor, force-pushed); all pushed
to origin. Capture pipeline still QUIESCED (hooks disabled) — leave off until MVP works.

Modules (in `automemory/`):
- ✅ ① `record.py`    — envelope write + derived INDEX. Verified.
- ✅ ② `transcript.py`— JSONL→text (stdlib port of transcript-to-text.sh). Verified (synthetic + real).
- ✅ ③ `redact.py`    — heuristic secret-scrub, precision-first. Verified (positives + provenance-survives).
- ⬜ ④ `dispatch.py`  — **NEXT.** Headless `claude -p` to author a record from a redacted
  transcript. ⚠ Has a genuine external unknown (CLI invocation/flags/IO/errors) — VERIFY the
  mechanism first (research pass or tiny probe), like the hook research, before writing.
- ⬜ ⑤ `reconcile.py` — SessionStart: handover-in + fast-path reminder + backfill (uses ①②③④).
- ⬜ ⑥ `cli.py`       — `init` (merge-not-clobber hooks) / `status` / `peep`.

Deferred purge tail (unchanged): Gate 5 (GitHub Support blob-purge — user's call, anytime);
destroy this session's transcript (true end of operation); re-enable capture (Gate 6, after MVP).

## MVP COMPLETE (2026-08-20)

All six modules built + verified against real behavior; installable + runnable end-to-end.

- ① record.py · ② transcript.py · ③ redact.py · ④ dispatch.py · ⑤ reconcile.py · ⑥ cli.py
- pyproject.toml → `pip/brew install` gives the `automemory` console command.
- Verified installed flow: `automemory init` (merge-not-clobber hook + scaffold), `status`,
  `peep`, `reconcile` (SessionStart handover-in + bounded backfill).

Finishing TODOs (post-MVP, not blocking):
- Packaging: ship engine prompts as package_data (non-editable installs); brew formula.
- Full CLI (§9): partition/parse/purge/protect/package/port/process/peer.
- Deferred purge tail: Gate 5 (GitHub Support blob-purge, user's call); destroy this
  session's transcript (true end); re-enable capture (install + un-disable hooks = dogfood).
- Doc debt: FOR-THE-NEXT-CLAUDE Engine-A creed; heavy-doc recalibration anchors.
- Engine B (blind scribe / automemory-capture) reassembly.
