#!/usr/bin/env bash
# reconcile-sessions.sh — Engine A SessionStart hook (the reconciler).
#
# Capture is event-driven today: if SessionEnd never fires (crash, force-quit,
# `/clear`less exit, hook timeout), a transcript is never captured. This script
# makes capture state-driven instead: on every session start it diffs the
# transcripts Claude Code has on disk against the raws already committed to
# this repo, and backfills anything missed by detaching capture-worker.sh for
# it — same detach pattern as capture-session.sh. SessionEnd stays as the
# freshness optimization; this is the at-least-once guarantee.
#
# Design contract: must be FAST (<1s), no network, no agents run inline. Its
# stdout becomes context the session's Claude sees, so it also doubles as a
# trust signal: proof, every session, that the vault is (or isn't) caught up.
#
# Never exits nonzero for "behind" states — only on genuine script failure —
# since a nonzero exit here would block session start.

set +e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG="$REPO_ROOT/.capture.log"

log() { printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$*" >>"$LOG" 2>/dev/null || true; }

# Multi-project scan (issue #8: global capture). By default every project dir
# under ~/.claude/projects is scanned. AUTOMEMORY_TRANSCRIPTS_DIR overrides to
# a single directory (test harness hook — preserved).
PROJECTS_PARENT="$HOME/.claude/projects"
RAW_DIR="$REPO_ROOT/ops/exports/claude-code-export/raw"
mkdir -p "$RAW_DIR" 2>/dev/null

# ---------------------------------------------------------------------------
# Partitions (folder ignore list). Folders listed in system/config/capture-ignore.txt
# — and everything under them — are never crawled. Claude Code names each project
# dir after its cwd with every non-alphanumeric char replaced by '-'
# (/Users/x/Sites/myproject -> -Users-x-Sites-myproject), so a folder path is matched by
# slugifying it the same way and prefix-matching the project dir's basename. The
# '-*' boundary keeps `foo` from matching a sibling `foobar` (only `foo` and
# `foo-<child>` match). AUTOMEMORY_IGNORE_FILE overrides the path (test hook).
# ---------------------------------------------------------------------------
IGNORE_FILE="${AUTOMEMORY_IGNORE_FILE:-$REPO_ROOT/system/config/capture-ignore.txt}"
IGNORE_SLUGS=()
if [ -f "$IGNORE_FILE" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%%#*}"                        # strip trailing comment
    line="${line#"${line%%[![:space:]]*}"}"   # ltrim
    line="${line%"${line##*[![:space:]]}"}"   # rtrim
    [ -z "$line" ] && continue
    case "$line" in
      "~")     line="$HOME" ;;
      "~/"*)   line="$HOME/${line#\~/}" ;;
      '$HOME') line="$HOME" ;;
      '$HOME/'*) line="$HOME/${line#\$HOME/}" ;;
    esac
    line="${line%/}"                          # optional trailing slash
    slug="$(printf '%s' "$line" | sed 's/[^a-zA-Z0-9]/-/g')"
    [ -n "$slug" ] && IGNORE_SLUGS+=("$slug")
  done < "$IGNORE_FILE"
fi

# is_partitioned <project-dir-basename> — true if the dir is an ignored folder
# or lives under one.
is_partitioned() {
  local base="$1" slug
  for slug in "${IGNORE_SLUGS[@]}"; do
    [ "$base" = "$slug" ] && return 0
    case "$base" in "$slug"-*) return 0 ;; esac
  done
  return 1
}

# ---------------------------------------------------------------------------
# Per-session exclusions (system/config/capture-ignore-sessions.txt): specific
# session UUIDs that are never captured, regardless of project. Exact match,
# unlike the folder partition's prefix match above.
# ---------------------------------------------------------------------------
SESSION_IGNORE_FILE="${AUTOMEMORY_IGNORE_SESSIONS_FILE:-$REPO_ROOT/system/config/capture-ignore-sessions.txt}"
IGNORE_SESSION_UUIDS=()
if [ -f "$SESSION_IGNORE_FILE" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%%#*}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [ -z "$line" ] && continue
    IGNORE_SESSION_UUIDS+=("$line")
  done < "$SESSION_IGNORE_FILE"
fi

is_session_ignored() {
  local uuid="$1" s
  for s in "${IGNORE_SESSION_UUIDS[@]}"; do
    [ "$uuid" = "$s" ] && return 0
  done
  return 1
}

# ---------------------------------------------------------------------------
# Read the SessionStart hook payload from stdin (same fields as SessionEnd's,
# per capture-worker.sh's comment: session_id, transcript_path, cwd,
# hook_event_name, ...). We only need session_id, to know which transcript is
# "current" (it's mid-session — its own SessionEnd will capture it).
# ---------------------------------------------------------------------------
PAYLOAD="$(cat 2>/dev/null)"
CURRENT_SESSION_ID="$(printf '%s' "$PAYLOAD" | jq -r '.session_id // empty' 2>/dev/null)"

# ---------------------------------------------------------------------------
# Concurrency guard: whole-script lock via atomic mkdir. Stale locks (crashed
# holder) older than 1h are broken. If we can't get the lock, another
# reconcile is already in flight — exit clean, no double dispatch.
# ---------------------------------------------------------------------------
LOCK_DIR="${AUTOMEMORY_RECONCILE_LOCK:-/tmp/automemory-reconcile.lock}"
STALE_SECS=3600

if [ -d "$LOCK_DIR" ]; then
  LOCK_AGE=$(( $(date +%s) - $(stat -f %m "$LOCK_DIR" 2>/dev/null || stat -c %Y "$LOCK_DIR" 2>/dev/null || echo 0) ))
  if [ "$LOCK_AGE" -gt "$STALE_SECS" ]; then
    log "reconcile: breaking stale lock (${LOCK_AGE}s old)"
    rmdir "$LOCK_DIR" 2>/dev/null
  fi
fi

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  log "reconcile: lock held, another reconcile in flight — skipping"
  echo "automemory: reconcile already in progress — skipping this check"
  exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null' EXIT

# Per-uuid in-flight guard, separate from the whole-script lock: two
# reconciles that run back-to-back (first releases its lock before its
# detached worker has had a chance to write the raw file) must not both
# dispatch a worker for the same uuid. capture-worker.sh creates/respects the
# same marker (plus its own <uuid>.lock mkdir lock) since issue #9, so a
# SessionEnd worker and a reconciler-dispatched worker never run concurrently.
INFLIGHT_DIR="${AUTOMEMORY_INFLIGHT_DIR:-/tmp/automemory-inflight}"
mkdir -p "$INFLIGHT_DIR" 2>/dev/null

# inflight_guard <uuid> — returns 0 (and creates the marker) if a worker may
# be dispatched for this uuid; returns 1 if one is already in flight.
inflight_guard() {
  local marker="$INFLIGHT_DIR/$1" marker_age
  if [ -e "$marker" ]; then
    marker_age=$(( $(date +%s) - $(stat -f %m "$marker" 2>/dev/null || stat -c %Y "$marker" 2>/dev/null || echo 0) ))
    if [ "$marker_age" -le "$STALE_SECS" ]; then
      log "reconcile: $1 already in flight (marker ${marker_age}s old) — skipping dispatch"
      return 1
    fi
    log "reconcile: stale in-flight marker for $1 (${marker_age}s) — retrying dispatch"
  fi
  : >"$marker" 2>/dev/null
  return 0
}

# dispatch_worker <uuid> <transcript> <reason> — detach capture-worker.sh with
# a synthetic hook-shaped payload (same {session_id, transcript_path, cwd}
# shape capture-session.sh writes for a real SessionEnd). cwd is recovered
# straight out of the transcript itself (each record carries the cwd it was
# run from).
dispatch_worker() {
  local uuid="$1" transcript="$2" reason="$3" synth_cwd payload_file
  synth_cwd="$(jq -r 'select(.cwd != null) | .cwd' "$transcript" 2>/dev/null | head -1)"
  [ -z "$synth_cwd" ] && synth_cwd="$REPO_ROOT"

  payload_file="$(mktemp -t automemory-payload.XXXXXX)"
  [ -n "$payload_file" ] || return 1
  jq -n --arg t "$transcript" --arg s "$uuid" --arg c "$synth_cwd" --arg r "$reason" \
    '{transcript_path: $t, session_id: $s, cwd: $c, hook_event_name: "SessionStart", reason: $r}' \
    >"$payload_file" 2>/dev/null

  log "reconcile: dispatching worker for $uuid ($reason) via $payload_file"
  nohup "$SCRIPT_DIR/capture-worker.sh" "$payload_file" </dev/null >/dev/null 2>&1 &
  disown 2>/dev/null
}

LIVE_SECS=1800   # transcript mtime younger than this = presumed still running

# is_engine_session <transcript> — the capture engine's own headless agents
# (observer/extractor, `claude -p`) create transcripts in this same project
# dir; capturing them makes the engine capture itself, recursively (observed
# 2026-07-05: session aafedf15 was the extractor run for d2a113db's capture,
# and its capture spawned further agent sessions). Engine prompts now open
# with an AUTOMEMORY-ENGINE-RUN sentinel; legacy engine transcripts are
# matched on distinctive prompt text within the first user message region.
is_engine_session() {
  head -c 4000 "$1" 2>/dev/null | grep -q 'AUTOMEMORY-ENGINE-RUN\|automemory record, per\|It is your only input'
}

TRACKED=0
GROWN=0
TRIVIAL=0
LIVE=0
ENGINE=0
PARTITIONED=0
SESSION_EXCLUDED=0
PROJECTS_SCANNED=0
MISSED_UUIDS=()
GROWN_UUIDS=()
LIVE_UUIDS=()

shopt -s nullglob

# Build the list of project transcript dirs to scan. Directories named
# -private-var-folders-* are the engine's own headless agents' scratch cwds
# (mktemp dirs under /private/var/folders) — skipped entirely, their
# transcripts counted as engine.
SCAN_DIRS=()
if [ -n "${AUTOMEMORY_TRANSCRIPTS_DIR:-}" ]; then
  SCAN_DIRS=("$AUTOMEMORY_TRANSCRIPTS_DIR")
else
  for PROJECT_DIR in "$PROJECTS_PARENT"/*/; do
    PROJECT_DIR="${PROJECT_DIR%/}"
    case "$(basename "$PROJECT_DIR")" in
      -private-var-folders-*)
        SCRATCH_TRANSCRIPTS=("$PROJECT_DIR"/*.jsonl)
        ENGINE=$((ENGINE + ${#SCRATCH_TRANSCRIPTS[@]}))
        continue
        ;;
    esac
    # Partitioned folders (system/config/capture-ignore.txt) are never crawled.
    if is_partitioned "$(basename "$PROJECT_DIR")"; then
      PARTITIONED=$((PARTITIONED + 1))
      continue
    fi
    SCAN_DIRS+=("$PROJECT_DIR")
  done
fi

for TRANSCRIPTS_DIR in "${SCAN_DIRS[@]}"; do
  DIR_TRANSCRIPTS=("$TRANSCRIPTS_DIR"/*.jsonl)
  [ -e "${DIR_TRANSCRIPTS[0]:-}" ] || continue
  PROJECTS_SCANNED=$((PROJECTS_SCANNED + 1))
for TRANSCRIPT in "${DIR_TRANSCRIPTS[@]}"; do
  UUID="$(basename "$TRANSCRIPT" .jsonl)"

  # EXCLUDED: this specific session is listed in capture-ignore-sessions.txt.
  if is_session_ignored "$UUID"; then
    SESSION_EXCLUDED=$((SESSION_EXCLUDED + 1))
    continue
  fi

  # CURRENT: this session's own transcript — its SessionEnd will capture it.
  if [ -n "$CURRENT_SESSION_ID" ] && [ "$UUID" = "$CURRENT_SESSION_ID" ]; then
    continue
  fi

  # ENGINE: our own capture agents' transcripts — never capture the capturer.
  if is_engine_session "$TRANSCRIPT"; then
    ENGINE=$((ENGINE + 1))
    continue
  fi

  # LIVE: another session running in this project right now looks identical
  # to a missed one — the hook payload only names OUR session. A warm mtime is
  # the only tell. Leave it alone (dispatching would race its own SessionEnd
  # capture); a later reconcile picks it up once it has gone cold.
  MTIME_AGE=$(( $(date +%s) - $(stat -f %m "$TRANSCRIPT" 2>/dev/null || stat -c %Y "$TRANSCRIPT" 2>/dev/null || echo 0) ))
  if [ "$MTIME_AGE" -lt "$LIVE_SECS" ]; then
    LIVE=$((LIVE + 1))
    LIVE_UUIDS+=("${UUID:0:8}")
    continue
  fi

  LINES="$(wc -l <"$TRANSCRIPT" 2>/dev/null | tr -d ' ')"
  LINES="${LINES:-0}"

  # TRIVIAL: match capture-worker.sh's own skip condition (fewer than 20
  # lines total — it has no separate user-message-count check).
  if [ "$LINES" -lt 20 ]; then
    TRIVIAL=$((TRIVIAL + 1))
    continue
  fi

  # nullglob (enabled below, for the transcripts loop) means a non-matching
  # glob expands to zero words rather than the literal pattern — so this must
  # be checked via array expansion, never a bare `ls <glob>` (which, with no
  # match, becomes `ls` with no args and silently lists the cwd instead).
  RAW_CANDIDATES=("$RAW_DIR"/*-"$UUID".jsonl)
  RAW_MATCH=""
  if [ -e "${RAW_CANDIDATES[0]:-}" ]; then
    RAW_MATCH="${RAW_CANDIDATES[0]}"
  fi

  if [ -n "$RAW_MATCH" ]; then
    RAW_LINES="$(wc -l <"$RAW_MATCH" 2>/dev/null | tr -d ' ')"
    RAW_LINES="${RAW_LINES:-0}"
    if [ "$LINES" -gt "$RAW_LINES" ]; then
      # GROWN: transcript has outgrown its committed raw. Since issue #9 the
      # worker update-appends to the uuid-keyed field note, so recapture is
      # safe — dispatch it, same detach + in-flight marker path as MISSED.
      if inflight_guard "$UUID"; then
        GROWN=$((GROWN + 1))
        GROWN_UUIDS+=("${UUID:0:8}")
        dispatch_worker "$UUID" "$TRANSCRIPT" "reconcile-grown"
      else
        TRACKED=$((TRACKED + 1))
      fi
    else
      TRACKED=$((TRACKED + 1))
    fi
    continue
  fi

  # MISSED: no raw file for this uuid. Backfill unless already in flight.
  if ! inflight_guard "$UUID"; then
    TRACKED=$((TRACKED + 1))
    continue
  fi
  MISSED_UUIDS+=("${UUID:0:8}")
  dispatch_worker "$UUID" "$TRANSCRIPT" "reconcile-backfill"
done
done

# ---------------------------------------------------------------------------
# Report. Kept to 1-3 lines; this is what the session's Claude sees.
# ---------------------------------------------------------------------------
MISSED_COUNT=${#MISSED_UUIDS[@]}

LIVE_NOTE=""
if [ "$LIVE" -gt 0 ]; then
  LIVE_LIST="$(IFS=,; echo "${LIVE_UUIDS[*]}")"
  LIVE_NOTE="; ${LIVE} live session(s) [${LIVE_LIST}] left alone"
fi

# Failure alarm (issue #20, the owner's design): terminal capture failures leave
# one JSON each under ops/logs/failures/; this line is the one surface she reads
# every session, so the count belongs here. Cleared when recaptures succeed.
FAIL_NOTE=""
FAIL_COUNT="$(ls "$REPO_ROOT/ops/logs/failures/"*.json 2>/dev/null | wc -l | tr -d ' ')"
if [ "${FAIL_COUNT:-0}" -gt 0 ]; then
  FAIL_NOTE="; ⚠ ${FAIL_COUNT} capture failure(s) — see ops/logs/failures/"
fi
LIVE_NOTE="${LIVE_NOTE}${FAIL_NOTE}"

# Partition transparency: never drop folders silently — the reconcile line is
# the one surface the user reads each session, so a honoured partition is shown.
if [ "$PARTITIONED" -gt 0 ]; then
  LIVE_NOTE="${LIVE_NOTE}; ${PARTITIONED} folder(s) partitioned"
fi
if [ "$SESSION_EXCLUDED" -gt 0 ]; then
  LIVE_NOTE="${LIVE_NOTE}; ${SESSION_EXCLUDED} session(s) excluded"
fi

if [ "$MISSED_COUNT" -eq 0 ] && [ "$GROWN" -eq 0 ]; then
  echo "automemory: vault current — ${TRACKED} sessions tracked across ${PROJECTS_SCANNED} projects, ${TRIVIAL} trivial skipped, ${ENGINE} engine-run(s) ignored${LIVE_NOTE}"
else
  MSG="automemory:"
  if [ "$MISSED_COUNT" -gt 0 ]; then
    MISSED_LIST="$(IFS=,; echo "${MISSED_UUIDS[*]}")"
    MSG="$MSG backfilling ${MISSED_COUNT} missed session(s) [${MISSED_LIST}] (detached)"
  fi
  if [ "$GROWN" -gt 0 ]; then
    GROWN_LIST="$(IFS=,; echo "${GROWN_UUIDS[*]}")"
    [ "$MISSED_COUNT" -gt 0 ] && MSG="$MSG;"
    MSG="$MSG recapturing ${GROWN} grown [${GROWN_LIST}] (detached)"
  fi
  echo "${MSG}${LIVE_NOTE}"
fi

log "reconcile: done — projects=$PROJECTS_SCANNED tracked=$TRACKED grown=$GROWN trivial=$TRIVIAL live=$LIVE engine=$ENGINE partitioned=$PARTITIONED session_excluded=$SESSION_EXCLUDED missed=$MISSED_COUNT"

exit 0
