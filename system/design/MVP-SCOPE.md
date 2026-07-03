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

## Open before code

- **[BLOCKING]** hook→live-agent authoring mechanism (research in flight).
- Which stream/dir Engine A records live in (records/ vs fieldnotes/ — "fieldnotes" connotes
  Engine B's blind output).
- MVP secret-scrub decision (above).
