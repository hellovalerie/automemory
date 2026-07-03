#!/usr/bin/env bash
# capture-worker.sh — Engine A v0 (automemory), detached worker.
#
# Launched by capture-session.sh (the thin SessionEnd hook) with the payload
# file as $1. Runs outside the hook's execution window — hook timeouts killed
# the extractor when this work ran inline ("Hook cancelled", 2026-07-05).
# For non-trivial sessions:
#   1. archives the raw transcript (insurance vs. Claude Code's ~30-day cleanup),
#   2. spawns a blind observer (Haiku, no repo access) -> field notes,
#   3. spawns an extractor (Sonnet, repo access) -> texture entries + INDEX,
#   4. commits and pushes the result to the private automemory remote.
#
# Field notes are keyed by session UUID in frontmatter, not by filename
# (issue #9): before writing, we look up fieldnotes/claude-code/ for a note
# whose frontmatter carries this session's uuid. No match -> NEW note (date-slug
# filename, uuid-suffix stopgap kept as a last-resort backstop). Match ->
# UPDATE-APPEND: the observer writes an addendum for the transcript lines past
# the note's `lines:` watermark, appended as a dated "## Update" section before
# the Edits ledger. Wholesale regeneration of an existing note is BANNED —
# notes accumulate human/merge edits and an Edits ledger.
#
# Design contract: this must NEVER disrupt the user's session. Every failure is
# non-fatal — we log to .capture.log (gitignored) and exit 0 no matter what.

# Resolve the repo root from this script's location, not $cwd (the hook's cwd is
# the *ended* session's project, which is usually some other repo).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG="$REPO_ROOT/.capture.log"

log() { printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$*" >>"$LOG" 2>/dev/null || true; }

# From here on, no error should ever propagate to the user's session.
set +e

# --- frontmatter helpers (first `---` block only) ---------------------------
# fm_get <file> <key> — value of a frontmatter key, empty if absent
fm_get() {
  awk -v key="$2" '
    NR==1 { if ($0 != "---") exit; next }
    /^---$/ { exit }
    $0 ~ "^"key":" { sub("^"key":[ \t]*", ""); sub("[ \t]*$", ""); print; exit }
  ' "$1"
}

# fm_set <file> <key> <value> — upsert one key inside the frontmatter block:
# rewritten in place if present, inserted before the closing `---` if absent
# (backfill-on-recapture of keys older notes predate, e.g. title:/model:).
fm_set() {
  local tmp
  tmp="$(mktemp -t automemory-fm.XXXXXX)" || return 1
  awk -v key="$2" -v val="$3" '
    NR==1 && $0=="---" { infm=1; print; next }
    infm && /^---$/    { if (!done) print key": "val; infm=0; print; next }
    infm && $0 ~ "^"key":" { print key": "val; done=1; next }
    { print }
  ' "$1" >"$tmp" && mv "$tmp" "$1"
}

# note_body <file> — file content minus the leading frontmatter block
note_body() {
  awk '
    NR==1 && $0=="---" { infm=1; next }
    infm && /^---$/    { infm=0; next }
    !infm { print }
  ' "$1"
}

# Gate v2 (issue #24) — supersedes obs_valid's 40-line ruler (2026-07-08; the
# ruler both rejected good compact notes on the "healthy" new-note path —
# e1970a49, twice — and passed a 71-line torso with no narrative at all,
# b845a056). Two checks, both operational:
#   1. WORD floor, not lines (the #20 lesson, now on both paths);
#   2. STRUCTURE: at least one section beyond the skeleton (Session shape /
#      Dispatches / Steering / Edits / Unverified / Update) — the chronological
#      narrative the observer prompt mandates, without demanding any single
#      heading name (no retroactive format drift, #28).
# The Edits ledger is GUARANTEED by the worker (ensure_note_ledger below),
# no longer demanded of the scribe — same contract the addendum path got in #20.
NOTE_MIN_WORDS=150
# note_valid <file> [source_word_count] — the floor is PROPORTIONAL when the
# source is known (2026-07-08 trial: a fixed 150 floor rejected honest 95-word
# notes on a 91-word transcript — a note cannot out-weigh its source; that was
# e1970a49's real story). floor = min(150, source/2), never below 30 (a pure
# pointer-dodge is ~15 words).
note_valid() {
  local floor="$NOTE_MIN_WORDS" prop
  if [ -n "${2:-}" ] && [ "${2:-0}" -gt 0 ] 2>/dev/null; then
    prop=$(( $2 / 2 ))
    [ "$prop" -lt "$floor" ] && floor="$prop"
    [ "$floor" -lt 30 ] && floor=30
  fi
  [ -s "$1" ] || return 1
  [ "$(wc -w <"$1" | tr -d ' ')" -ge "$floor" ] || return 1
  grep -E '^##+[[:space:]]' "$1" \
    | grep -ivE '^##+[[:space:]]+(session shape|dispatches|steering|edits|unverified|update)' \
    | grep -q .
}
# Appended to the prompt for the single retry after a validation failure.
OBS_RETRY_LINE="Your previous attempt was rejected: too short, or missing its chronological narrative sections. Produce the COMPLETE notes — chronological sections covering the material, ending with the Edits ledger — written to the deliverable file named in your instructions."

# Addendum validation (issue #20). The update-append path replaced the old
# line-count ruler with a WORD-count floor: a substance measure that does not
# punish long-line prose (in a 20-run test, 10 good addenda scored <40 lines but
# 554+ words). The Edits ledger is GUARANTEED by the worker (honest backstop
# below), not demanded of the scribe, so a dropped ledger (~10% of runs) no
# longer rejects a good note. (The new-note path's old obs_valid ruler was
# retired for Gate v2 above, 2026-07-08 — it destroyed e1970a49's note twice.)
ADDENDUM_MIN_WORDS=40
addendum_has_substance() {
  [ -s "$1" ] || return 1
  [ "$(wc -w <"$1" | tr -d ' ')" -ge "$ADDENDUM_MIN_WORDS" ]
}
# Lenient ledger detection: accept ## or ### (or more), any case, trailing text.
ledger_present() { grep -iEq '^##+[[:space:]]+edits' "$1"; }

# ensure_note_ledger <file> — new-note twin of the addendum backstop (#20/#24):
# present -> normalize the heading to the canonical '## Edits'; absent -> append
# an HONEST placeholder that never claims the scribe verified quote-fidelity.
ensure_note_ledger() {
  local ltmp
  if ledger_present "$1"; then
    ltmp="$(mktemp -t automemory-ledger.XXXXXX)" || return 0
    awk 'BEGIN{done=0}
         /^##+[[:space:]]+[Ee]dits/ && !done { print "## Edits"; done=1; next }
         { print }' "$1" >"$ltmp" && mv "$ltmp" "$1"
  else
    printf '\n## Edits\n\n_none recorded; ledger auto-supplied by the worker (scribe emitted none), so quote-fidelity is unverified for this note._\n' >>"$1"
    log "backstop: worker supplied missing Edits ledger (new note)"
  fi
}

# ---------------------------------------------------------------------------
# Room and board (issue #25 — "command over nature", PLAN.md). The scribe works
# in a STABLE engine-owned room, not a per-run tent: one harness identity, one
# memory drawer, nothing orphans (issue #23's root cause, removed by
# construction). The prompt+transcript arrive on stdin (guarantees the whole
# transcript is in context — a file the scribe must Read risks partial reads);
# the DELIVERABLE IS A FILE the scribe writes in the room — the sanctioned
# outlet for the file-writing instinct plan mode used to fight. Per-run
# filenames keep concurrent scribes collision-free (fan out per actor, #22's
# principle). acceptEdits replaces plan mode: no plan-file door exists at all.
# Blindness is preserved by geography — the room holds only what the engine
# brought. Harvest order: deliverable file, else stdout (fallback, logged).
# After harvest: desk swept; drawer harvested to ops/logs/scribe-drawer/ —
# observe-first holding place (#25/#26 interim: fresh witnesses per run,
# nothing designed atop the material until material exists).
# ---------------------------------------------------------------------------
SCRIBE_ROOM="${AUTOMEMORY_SCRIBE_ROOM:-$HOME/.automemory/scribe-room}"

harvest_drawer() {
  local slug drawer hold
  slug="$(printf '%s' "$SCRIBE_ROOM" | sed 's|[/.]|-|g')"
  drawer="$HOME/.claude/projects/$slug/memory"
  if [ -d "$drawer" ] && [ -n "$(ls -A "$drawer" 2>/dev/null)" ]; then
    hold="$REPO_ROOT/ops/logs/scribe-drawer/$(date '+%Y-%m-%d')-${SESSION_ID:-run}"
    mkdir -p "$hold" 2>/dev/null
    mv "$drawer"/* "$hold"/ 2>>"$LOG" && log "scribe drawer harvested -> ${hold#$REPO_ROOT/}"
  fi
  # The room's project dir accumulates engine-run transcripts (marker-skipped
  # by the reconciler); sweep ones older than a day so it never balloons.
  find "$HOME/.claude/projects/$slug" -maxdepth 1 -name '*.jsonl' -mtime +1 -delete 2>/dev/null
}

# run_scribe <input_file> <harvest_dest> <deliverable_filename>
run_scribe() {
  local in="$1" out="$2" dfile="$3" stdout_tmp
  mkdir -p "$SCRIBE_ROOM" 2>/dev/null
  stdout_tmp="$(mktemp -t automemory-scribe-stdout.XXXXXX)" || stdout_tmp="$out.stdout"
  rm -f "$SCRIBE_ROOM/$dfile" 2>/dev/null
  ( cd "$SCRIBE_ROOM" && claude -p \
      --model haiku \
      --permission-mode acceptEdits ) <"$in" >"$stdout_tmp" 2>>"$LOG"
  if [ -s "$SCRIBE_ROOM/$dfile" ]; then
    mv "$SCRIBE_ROOM/$dfile" "$out" 2>>"$LOG"
    log "scribe delivered via file ($dfile)"
  else
    mv "$stdout_tmp" "$out" 2>>"$LOG"
    log "scribe delivered via stdout fallback (no $dfile)"
  fi
  rm -f "$stdout_tmp" "$SCRIBE_ROOM/$dfile" 2>/dev/null
  harvest_drawer
}

# DELIVERABLE_NOTE <filename> — the per-run instruction block naming the file.
deliverable_note() {
  printf 'DELIVERABLE: write the complete field notes to a file named `%s` in your current working directory (create it; overwrite if it exists). The FILE is the deliverable — after writing it, reply with just the word DONE.' "$1"
}

# ---------------------------------------------------------------------------
# Failure surfacing (issue #20, the owner's design 2026-07-07): a terminal
# capture failure leaves a VISIBLE breadcrumb, not just a log line — one JSON
# per uuid under ops/logs/failures/ (fan out per actor; a shared appended log would
# re-create the #22 race). The rejected output is preserved inside the record
# (stop deleting rejected work silently). The reconciler's session-start status
# line — the one surface the user reads every session — counts these files.
# Cleared automatically when this uuid's next capture succeeds.
# ---------------------------------------------------------------------------
FAIL_DIR="$REPO_ROOT/ops/logs/failures"
write_failure() {  # <path-kind> <reason> <rejected_output_file>
  mkdir -p "$FAIL_DIR" 2>/dev/null
  jq -n --arg uuid "${SESSION_ID:-unknown}" --arg slug "$SLUG" --arg at "$CAPTURED_TS" \
        --arg kind "$1" --arg reason "$2" --arg raw "$RAW_REL" \
        --rawfile output "$3" \
        '{uuid:$uuid, project:$slug, at:$at, path:$kind, reason:$reason, raw:$raw, rejected_output:$output}' \
        >"$FAIL_DIR/${SESSION_ID:-unknown}.json" 2>>"$LOG" \
    && log "failure recorded: ops/logs/failures/${SESSION_ID:-unknown}.json ($1: $2)" \
    || log "WARN: could not write failure record for ${SESSION_ID:-unknown}"
}
clear_failure() {
  if [ -n "$SESSION_ID" ] && [ -f "$FAIL_DIR/$SESSION_ID.json" ]; then
    rm -f "$FAIL_DIR/$SESSION_ID.json" 2>/dev/null
    log "failure record cleared for $SESSION_ID (capture succeeded)"
  fi
}

# ---------------------------------------------------------------------------
# Git push lock (issue #22): the pull/commit/push sequence is the one shared
# step among concurrent workers — funnel it through a single serialization
# point. Wait up to 120s, break stale locks after 600s; on timeout proceed
# unserialized with a warning (a capture must never be lost to a lock).
# ---------------------------------------------------------------------------
GIT_LOCK_DIR=""
GIT_LOCK_HELD=""
git_lock() {
  GIT_LOCK_DIR="$INFLIGHT_DIR/git-push.lock"
  local waited=0 age
  mkdir -p "$INFLIGHT_DIR" 2>/dev/null
  while ! mkdir "$GIT_LOCK_DIR" 2>/dev/null; do
    age=$(( $(date +%s) - $(stat -f %m "$GIT_LOCK_DIR" 2>/dev/null || stat -c %Y "$GIT_LOCK_DIR" 2>/dev/null || echo 0) ))
    if [ "$age" -gt 600 ]; then
      log "breaking stale git lock (${age}s old)"
      rmdir "$GIT_LOCK_DIR" 2>/dev/null
      continue
    fi
    if [ "$waited" -ge 120 ]; then
      log "WARN: git lock still held after ${waited}s — proceeding unserialized"
      return 1
    fi
    sleep 3
    waited=$((waited + 3))
  done
  GIT_LOCK_HELD=1
  return 0
}

# derive_title_slug <transcript_path> <fallback_cwd> — a content-derived slug
# for the NEW-note filename (issue: every session in this project was named
# "automemory", including e.g. a git-push chat — indistinguishable in the
# folder). Order of preference:
#   (a) a "summary"-type transcript record's title text, if the transcript
#       carries one (none observed in this project's transcripts to date —
#       this branch is forward-compatible, not exercised by current data);
#   (b) first ~6 words of the first real user message — skipping isMeta
#       records (local-command caveats) and raw <command-name>/
#       <local-command-*> markup lines, which are not things the user typed;
#   (c) the session cwd's basename (old behavior), if (a)/(b) find nothing.
# Output is lowercased, hyphenated, stripped to [a-z0-9-], capped at 40 chars.
derive_title_slug() {
  local transcript="$1" fallback_cwd="$2" raw=""

  raw="$(jq -r 'select(.type=="summary") | (.summary // .text // empty)' \
    "$transcript" 2>/dev/null | tail -1)"

  if [ -z "$raw" ]; then
    raw="$(jq -r '
      select(.type=="user" and (.isMeta != true)) |
      .message.content as $c |
      (if ($c|type) == "string" then $c
       elif ($c|type) == "array" then ([$c[]? | select(.type=="text") | .text] | join(" "))
       else "" end) as $t |
      ($t | gsub("\n"; " "))
    ' "$transcript" 2>/dev/null | grep -vE '^[[:space:]]*(<command-|<local-command|[[:space:]]*$)' | head -1)"
  fi

  [ -z "$raw" ] && raw="$(basename "${fallback_cwd:-session}")"

  raw="$(printf '%s' "$raw" | awk '{for(i=1;i<=6 && i<=NF;i++) printf "%s ", $i}')"
  printf '%s' "$raw" | tr '[:upper:]' '[:lower:]' | tr -s '[:space:]' '-' \
    | tr -cd 'a-z0-9-' | sed -E 's/-+/-/g; s/^-//; s/-$//' | cut -c1-40 | sed -E 's/-$//'
}

# yaml_quote <string> — double-quote a value for a YAML frontmatter line
yaml_quote() {
  printf '"%s"' "$(printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g')"
}

# derive_title <transcript_path> [session_uuid] — the human-readable session
# title for `title:` frontmatter (SCHEMA.md identity block). Order:
#   (a) the last custom-title record (the owner's own rename — wins over the
#       harness's generated one), filtered to this session's uuid when known;
#   (b) the last ai-title record (harness-generated), same filter;
#   (c) the derive_title_slug source text: first real user message, first
#       ~8 words, plain text (NOT slugified).
# Empty output → the caller omits the key.
derive_title() {
  local transcript="$1" uuid="${2:-}" t=""
  t="$(jq -r --arg u "$uuid" '
    select(.type=="custom-title") |
    select($u=="" or .sessionId==$u or (.sessionId==null)) |
    .customTitle // empty
  ' "$transcript" 2>/dev/null | tail -1)"
  if [ -z "$t" ]; then
    t="$(jq -r --arg u "$uuid" '
      select(.type=="ai-title") |
      select($u=="" or .sessionId==$u or (.sessionId==null)) |
      .aiTitle // empty
    ' "$transcript" 2>/dev/null | tail -1)"
  fi
  if [ -z "$t" ]; then
    t="$(jq -r '
      select(.type=="user" and (.isMeta != true)) |
      .message.content as $c |
      (if ($c|type) == "string" then $c
       elif ($c|type) == "array" then ([$c[]? | select(.type=="text") | .text] | join(" "))
       else "" end) as $t |
      ($t | gsub("\n"; " "))
    ' "$transcript" 2>/dev/null \
      | grep -vE '^[[:space:]]*(<command-|<local-command|[[:space:]]*$)' | head -1 \
      | awk '{for(i=1;i<=8 && i<=NF;i++) printf "%s%s", (i>1?" ":""), $i}')"
  fi
  printf '%s' "$t"
}

# derive_model <transcript_path> — the most frequent .message.model across
# assistant records, mapped to a short label for `model:` frontmatter
# (claude-fable-* -> fable, *opus* -> opus, *sonnet* -> sonnet,
# *haiku* -> haiku; unknown ids pass through raw). Synthetic placeholder
# ids (<synthetic>) are ignored. Empty output → the caller omits the key.
derive_model() {
  local id
  id="$(jq -r 'select(.type=="assistant") | .message.model // empty' "$1" 2>/dev/null \
    | grep -v '^<' | sort | uniq -c | sort -rn | awk 'NR==1{print $2}')"
  case "$id" in
    "")             ;;
    claude-fable-*) printf 'fable' ;;
    *opus*)         printf 'opus' ;;
    *sonnet*)       printf 'sonnet' ;;
    *haiku*)        printf 'haiku' ;;
    *)              printf '%s' "$id" ;;
  esac
}

# ---------------------------------------------------------------------------
# Parse the SessionEnd payload (fields per Claude Code hooks docs):
#   { session_id, transcript_path, cwd, hook_event_name, reason }
# ---------------------------------------------------------------------------
PAYLOAD_FILE="$1"
if [ -z "$PAYLOAD_FILE" ] || [ ! -f "$PAYLOAD_FILE" ]; then
  log "skip: worker called without payload file"
  exit 0
fi
PAYLOAD="$(cat "$PAYLOAD_FILE")"
rm -f "$PAYLOAD_FILE"
TRANSCRIPT="$(printf '%s' "$PAYLOAD" | jq -r '.transcript_path // empty' 2>/dev/null)"
SESSION_ID="$(printf '%s' "$PAYLOAD" | jq -r '.session_id // empty' 2>/dev/null)"
SESSION_CWD="$(printf '%s' "$PAYLOAD" | jq -r '.cwd // empty' 2>/dev/null)"

# (a) Bail quietly on trivial or missing sessions.
if [ -z "$TRANSCRIPT" ] || [ ! -f "$TRANSCRIPT" ]; then
  log "skip: no transcript_path (got '$TRANSCRIPT')"
  exit 0
fi
LINES="$(wc -l <"$TRANSCRIPT" 2>/dev/null | tr -d ' ')"
LINES="${LINES:-0}"
if [ "$LINES" -lt 20 ]; then
  log "skip: trivial session ($LINES lines) $TRANSCRIPT"
  exit 0
fi

# SESSION EXCLUSION: specific sessions listed by uuid in
# system/config/capture-ignore-sessions.txt are never captured, regardless of
# project — exact match, checked here so the SessionEnd path honors it too
# (reconcile-sessions.sh checks the same file for the backfill/grown path).
SESSION_IGNORE_FILE="${AUTOMEMORY_IGNORE_SESSIONS_FILE:-$REPO_ROOT/system/config/capture-ignore-sessions.txt}"
if [ -n "$SESSION_ID" ] && [ -f "$SESSION_IGNORE_FILE" ] \
  && grep -vE '^[[:space:]]*#|^[[:space:]]*$' "$SESSION_IGNORE_FILE" \
     | sed -E 's/[[:space:]]*#.*$//; s/[[:space:]]+$//' \
     | grep -qxF "$SESSION_ID"; then
  log "skip: session excluded by capture-ignore-sessions.txt ($SESSION_ID)"
  exit 0
fi

# FOLDER PARTITION: folders listed in system/config/capture-ignore.txt are
# never captured — same file reconcile-sessions.sh checks for the
# backfill/grown path, but until now nothing enforced it on the normal
# SessionEnd path, so a session ending inside a partitioned folder was
# captured anyway (found 2026-07-19: two ~/Sites/writings sessions leaked in
# despite the folder being partitioned since 2026-07-17). Matching mirrors
# reconcile-sessions.sh's is_partitioned: slugify the folder path the same way
# Claude Code slugifies cwd, then prefix-match with a '-' boundary.
FOLDER_IGNORE_FILE="${AUTOMEMORY_IGNORE_FILE:-$REPO_ROOT/system/config/capture-ignore.txt}"
if [ -n "$SESSION_CWD" ] && [ -f "$FOLDER_IGNORE_FILE" ]; then
  CWD_SLUG="$(printf '%s' "$SESSION_CWD" | sed 's/[^a-zA-Z0-9]/-/g')"
  PARTITIONED_CWD=0
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%%#*}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [ -z "$line" ] && continue
    case "$line" in
      "~")     line="$HOME" ;;
      "~/"*)   line="$HOME/${line#\~/}" ;;
      '$HOME') line="$HOME" ;;
      '$HOME/'*) line="$HOME/${line#\$HOME/}" ;;
    esac
    line="${line%/}"
    SLUG="$(printf '%s' "$line" | sed 's/[^a-zA-Z0-9]/-/g')"
    [ -z "$SLUG" ] && continue
    case "$CWD_SLUG" in
      "$SLUG"|"$SLUG"-*) PARTITIONED_CWD=1; break ;;
    esac
  done < "$FOLDER_IGNORE_FILE"
  if [ "$PARTITIONED_CWD" -eq 1 ]; then
    log "skip: cwd partitioned by capture-ignore.txt ($SESSION_CWD)"
    exit 0
  fi
fi

# ENGINE GUARD: never capture the capturer. The engine's own headless agents
# (observer/extractor, `claude -p` with cwd inside this project) leave
# transcripts in the same projects dir; capturing them is recursive
# self-capture (observed 2026-07-05: aafedf15 = the extractor run for
# d2a113db's capture, itself captured as a "session"). Engine prompts open
# with the AUTOMEMORY-ENGINE-RUN sentinel; legacy engine transcripts match on
# distinctive prompt text in the first user message region.
if head -c 4000 "$TRANSCRIPT" 2>/dev/null | grep -q 'AUTOMEMORY-ENGINE-RUN\|automemory record, per\|It is your only input'; then
  log "skip: engine-run transcript (self-capture guard) $TRANSCRIPT"
  exit 0
fi

DATE="$(date '+%Y-%m-%d')"
CAPTURED_TS="$(date '+%Y-%m-%dT%H:%M:%S%z')"
# Slug from the ended session's project directory — a readable, stable label.
SLUG="$(basename "${SESSION_CWD:-session}" | tr '[:upper:] ' '[:lower:]-' | tr -cd 'a-z0-9-')"
[ -z "$SLUG" ] && SLUG="session"
SHORT_ID="$(printf '%s' "$SESSION_ID" | cut -c1-8)"

log "start: $DATE slug=$SLUG session=$SESSION_ID lines=$LINES cwd=$SESSION_CWD"

# ---------------------------------------------------------------------------
# Per-uuid in-flight guard (issue #9). reconcile-sessions.sh writes
# $INFLIGHT_DIR/<uuid> file markers at dispatch; the worker holds a companion
# mkdir lock (atomic) for its whole run so a SessionEnd worker and a
# reconciler-dispatched worker for the same uuid never run concurrently
# (observed race: one worker committed an empty note file, another filled it).
# Marker + lock are removed on exit; stale after 1h (crashed holder).
# ---------------------------------------------------------------------------
INFLIGHT_DIR="${AUTOMEMORY_INFLIGHT_DIR:-/tmp/automemory-inflight}"
STALE_SECS=3600
if [ -n "$SESSION_ID" ]; then
  mkdir -p "$INFLIGHT_DIR" 2>/dev/null
  UUID_LOCK="$INFLIGHT_DIR/$SESSION_ID.lock"
  if [ -d "$UUID_LOCK" ]; then
    LOCK_AGE=$(( $(date +%s) - $(stat -f %m "$UUID_LOCK" 2>/dev/null || stat -c %Y "$UUID_LOCK" 2>/dev/null || echo 0) ))
    if [ "$LOCK_AGE" -le "$STALE_SECS" ]; then
      log "skip: another worker in flight for $SESSION_ID (lock ${LOCK_AGE}s old)"
      exit 0
    fi
    log "breaking stale uuid lock for $SESSION_ID (${LOCK_AGE}s old)"
    rmdir "$UUID_LOCK" 2>/dev/null
  fi
  if ! mkdir "$UUID_LOCK" 2>/dev/null; then
    log "skip: lost uuid-lock race for $SESSION_ID"
    exit 0
  fi
  MARKER="$INFLIGHT_DIR/$SESSION_ID"
  : >"$MARKER" 2>/dev/null   # create/refresh the dispatch marker the reconciler checks
fi
# Single EXIT trap for every lock this worker can hold (uuid lock, dispatch
# marker, git push lock) — a crashed worker must never leave a lock behind.
trap '[ -n "$UUID_LOCK" ] && rmdir "$UUID_LOCK" 2>/dev/null; [ -n "$MARKER" ] && rm -f "$MARKER" 2>/dev/null; [ -n "$GIT_LOCK_HELD" ] && rmdir "$GIT_LOCK_DIR" 2>/dev/null' EXIT

# ---------------------------------------------------------------------------
# (b) Archive the raw transcript verbatim. Re-captures of a known session keep
#     the SAME raw filename (raw is verbatim source — safe to overwrite with
#     the fuller transcript; the filename's date prefix stays the first
#     capture's date).
# ---------------------------------------------------------------------------
RAW_DIR="$REPO_ROOT/ops/exports/claude-code-export/raw"
mkdir -p "$RAW_DIR"
RAW_DEST="$RAW_DIR/${DATE}-${SESSION_ID:-$SHORT_ID}.jsonl"
if [ -n "$SESSION_ID" ]; then
  for existing_raw in "$RAW_DIR"/*-"$SESSION_ID".jsonl; do
    [ -e "$existing_raw" ] && RAW_DEST="$existing_raw"
    break
  done
fi
cp "$TRANSCRIPT" "$RAW_DEST" 2>>"$LOG" && log "archived raw -> $RAW_DEST" || log "WARN: raw copy failed"
RAW_REL="ops/exports/claude-code-export/raw/$(basename "$RAW_DEST")"

# ---------------------------------------------------------------------------
# Lookup by uuid, never by filename (issue #9): find an existing field note
# whose frontmatter uuid matches this session. Filenames are display labels;
# the uuid is identity — a renamed note must still be found here.
# ---------------------------------------------------------------------------
NOTES_DIR="$REPO_ROOT/fieldnotes/claude-code"
mkdir -p "$NOTES_DIR"
EXISTING_NOTE=""
if [ -n "$SESSION_ID" ]; then
  while IFS= read -r f; do
    [ -e "$f" ] || continue
    if [ "$(fm_get "$f" uuid)" = "$SESSION_ID" ]; then
      EXISTING_NOTE="$f"
      break
    fi
  done < <(find "$NOTES_DIR" -name '*.md' -type f 2>/dev/null)
fi

# Readable transcript text, shared by both agents. Kept in scratch (never committed).
TXT="$(mktemp -t automemory-transcript.XXXXXX)" || TXT="$REPO_ROOT/.capture-transcript.tmp"
"$SCRIPT_DIR/transcript-to-text.sh" "$TRANSCRIPT" >"$TXT" 2>>"$LOG"
if [ ! -s "$TXT" ]; then
  log "skip: transcript produced no readable text"
  rm -f "$TXT"
  exit 0
fi
TXT_WORDS="$(wc -w <"$TXT" | tr -d ' ')"

OBS_PROMPT="$REPO_ROOT/system/engine/prompts/observer.md"
# Work-mode addendum for claude-code sessions (all this worker handles):
# appended right after observer.md in both observer prompt constructions.
OBS_WORK_PROMPT="$REPO_ROOT/system/engine/prompts/observer-work.md"
if [ ! -f "$OBS_WORK_PROMPT" ]; then
  log "WARN: observer-work.md missing ($OBS_WORK_PROMPT) — proceeding with base observer prompt only"
  OBS_WORK_PROMPT=""
fi
RUN_EXTRACTOR=1

if [ -z "$EXISTING_NOTE" ]; then
  # -------------------------------------------------------------------------
  # (c) NEW note. Observer — Haiku, BLIND. No repo access: it runs from a
  #     throwaway cwd and receives only the transcript text + observer.md
  #     instruction via stdin. Its whole world is the transcript.
  # -------------------------------------------------------------------------
  TITLE_SLUG="$(derive_title_slug "$TRANSCRIPT" "$SESSION_CWD")"
  [ -z "$TITLE_SLUG" ] && TITLE_SLUG="session"
  NOTE_TITLE="$(derive_title "$TRANSCRIPT" "$SESSION_ID")"
  NOTE_MODEL="$(derive_model "$TRANSCRIPT")"
  PROJECT_DIR="$NOTES_DIR/$SLUG"
  mkdir -p "$PROJECT_DIR"
  OBS_OUT="$PROJECT_DIR/${DATE}-${SLUG}-${TITLE_SLUG}.md"
  # Last-resort backstop (pre-#9 stopgap): with uuid lookup above, a re-capture
  # of the same session lands in the UPDATE branch, so this should only fire
  # for two DIFFERENT same-day sessions in the same project. Never write onto
  # an existing note from a different session — suffix with the session uuid.
  if [ -e "$OBS_OUT" ] && [ -n "$SESSION_ID" ]; then
    OBS_OUT="$PROJECT_DIR/${DATE}-${SLUG}-${TITLE_SLUG}-${SESSION_ID:0:8}.md"
  fi
  OBS_TMP="$(mktemp -t automemory-observer-out.XXXXXX)" || OBS_TMP="$REPO_ROOT/.capture-observer.tmp"
  OBS_IN="$(mktemp -t automemory-observer-in.XXXXXX)" || OBS_IN="$REPO_ROOT/.capture-observer-in.tmp"
  DELIVERABLE="notes-${SESSION_ID:-$SHORT_ID}.md"

  {
    printf 'AUTOMEMORY-ENGINE-RUN — internal capture agent; this marker lets the engine recognize its own sessions.\n\n'
    cat "$OBS_PROMPT"
    [ -n "$OBS_WORK_PROMPT" ] && { printf '\n\n'; cat "$OBS_WORK_PROMPT"; }
    printf '\n\n%s\n' "$(deliverable_note "$DELIVERABLE")"
    printf '\n---\n\nBelow is the entire transcript. It is your only input.\n\n'
    cat "$TXT"
  } >"$OBS_IN"
  run_scribe "$OBS_IN" "$OBS_TMP" "$DELIVERABLE"

  # Gate v2 (issue #24): word floor + narrative structure. One retry.
  if ! note_valid "$OBS_TMP" "$TXT_WORDS"; then
    log "WARN: observer note failed Gate v2 ($(wc -w <"$OBS_TMP" 2>/dev/null | tr -d ' ') words) — retrying once"
    printf '\n\n%s\n' "$OBS_RETRY_LINE" >>"$OBS_IN"
    run_scribe "$OBS_IN" "$OBS_TMP" "$DELIVERABLE"
  fi

  if note_valid "$OBS_TMP" "$TXT_WORDS"; then
    ensure_note_ledger "$OBS_TMP"
    {
      printf -- '---\n'
      printf 'uuid: %s\n' "${SESSION_ID:-unknown}"
      printf 'source: claude-code\n'
      printf 'date: %s\n' "$DATE"
      printf 'raw: %s\n' "$RAW_REL"
      printf 'captured: %s\n' "$CAPTURED_TS"
      printf 'lines: %s\n' "$LINES"
      [ -n "$NOTE_TITLE" ] && printf 'title: %s\n' "$(yaml_quote "$NOTE_TITLE")"
      [ -n "$NOTE_MODEL" ] && printf 'model: %s\n' "$NOTE_MODEL"
      printf -- '---\n\n'
      cat "$OBS_TMP"
    } >"$OBS_OUT"
    log "observer wrote $OBS_OUT ($(wc -w <"$OBS_OUT" | tr -d ' ') words)"
    clear_failure
  else
    write_failure "new-note" "failed Gate v2 after retry ($(wc -w <"$OBS_TMP" 2>/dev/null | tr -d ' ') words)" "$OBS_TMP"
    log "WARN: observer output failed Gate v2 after retry — NOT writing a note for ${SESSION_ID:-unknown}; rejected output preserved in ops/logs/failures/ and surfaced at next session start."
  fi
  rm -f "$OBS_TMP" "$OBS_IN"
else
  # -------------------------------------------------------------------------
  # (c') UPDATE-APPEND (issue #9). Never rewrite: delta = transcript lines
  #      beyond the note's `lines:` watermark. Observer sees its own prior
  #      note (continuity of style/turn-numbering — its own product, so
  #      blindness holds) plus the full transcript with the new region marked,
  #      and produces ONLY an addendum. Appended as a dated "## Update"
  #      section (SCHEMA.md, "Field-note updates"), inserted BEFORE the
  #      Unverified section (issue #11) when present, else before the Edits
  #      ledger — tail matter (Unverified, then Edits) stays last.
  # -------------------------------------------------------------------------
  PREV_LINES="$(fm_get "$EXISTING_NOTE" lines)"
  PREV_LINES="${PREV_LINES:-0}"
  case "$PREV_LINES" in (*[!0-9]*) PREV_LINES=0 ;; esac

  # Title refresh-on-recapture (SCHEMA.md, "Field-note updates": renames must
  # never fork a note — uuid is the key, title is a display label that follows
  # the owner's renames). Backfill when absent; when present but the transcript
  # now evidences a different title (a rename since last capture), update it
  # and log old -> new (rename history stays recoverable in the raw
  # transcript's title records). model: stays backfill-only.
  NOTE_TITLE="$(derive_title "$TRANSCRIPT" "$SESSION_ID")"
  if [ -n "$NOTE_TITLE" ]; then
    PREV_TITLE="$(fm_get "$EXISTING_NOTE" title)"
    NEW_TITLE_Q="$(yaml_quote "$NOTE_TITLE")"
    if [ -z "$PREV_TITLE" ]; then
      fm_set "$EXISTING_NOTE" title "$NEW_TITLE_Q" 2>>"$LOG"
    elif [ "$PREV_TITLE" != "$NEW_TITLE_Q" ]; then
      fm_set "$EXISTING_NOTE" title "$NEW_TITLE_Q" 2>>"$LOG"
      log "title refreshed on $EXISTING_NOTE: $PREV_TITLE -> $NEW_TITLE_Q"
    fi
  fi
  if [ -z "$(fm_get "$EXISTING_NOTE" model)" ]; then
    NOTE_MODEL="$(derive_model "$TRANSCRIPT")"
    [ -n "$NOTE_MODEL" ] && fm_set "$EXISTING_NOTE" model "$NOTE_MODEL" 2>>"$LOG"
  fi

  if [ "$LINES" -le "$PREV_LINES" ]; then
    # No delta: raw copy already refreshed above; just touch captured.
    fm_set "$EXISTING_NOTE" captured "$CAPTURED_TS" 2>>"$LOG"
    log "update: no delta for $EXISTING_NOTE (lines $LINES <= watermark $PREV_LINES) — raw refreshed, captured touched"
    RUN_EXTRACTOR=0
  else
    log "update: $EXISTING_NOTE has delta (lines $LINES > watermark $PREV_LINES)"
    OLD_TXT="$(mktemp -t automemory-oldtxt.XXXXXX)"
    MARKED="$(mktemp -t automemory-marked.XXXXXX)"
    head -n "$PREV_LINES" "$TRANSCRIPT" | "$SCRIPT_DIR/transcript-to-text.sh" >"$OLD_TXT" 2>>"$LOG"
    OLD_SIZE="$(wc -c <"$OLD_TXT" | tr -d ' ')"
    if [ "$OLD_SIZE" -gt 0 ] && head -c "$OLD_SIZE" "$TXT" | cmp -s - "$OLD_TXT"; then
      {
        cat "$OLD_TXT"
        printf '\n<<< NEW MATERIAL BEGINS HERE — everything below this line is not covered by your prior field note >>>\n\n'
        tail -c +"$((OLD_SIZE + 1))" "$TXT"
      } >"$MARKED"
    else
      log "WARN: watermarked prefix does not match current transcript text — passing full transcript with an unlocalized marker note"
      {
        printf '<<< NOTE: the new material is the LATER portion of this transcript, beyond what your prior field note already covers; the exact boundary could not be marked >>>\n\n'
        cat "$TXT"
      } >"$MARKED"
    fi

    ADDENDUM="$(mktemp -t automemory-addendum.XXXXXX)"
    OBS_IN="$(mktemp -t automemory-observer-in.XXXXXX)" || OBS_IN="$REPO_ROOT/.capture-observer-in.tmp"
    DELIVERABLE="addendum-${SESSION_ID:-$SHORT_ID}.md"
    {
      printf 'AUTOMEMORY-ENGINE-RUN — internal capture agent; this marker lets the engine recognize its own sessions.\n\n'
      cat "$OBS_PROMPT"
      [ -n "$OBS_WORK_PROMPT" ] && { printf '\n\n'; cat "$OBS_WORK_PROMPT"; }
      printf '\n\n---\n\nUPDATE MODE. Below is your own prior field note on this session'"'"'s earlier turns, provided for continuity of style and turn-numbering — do NOT rewrite or restate it.\n\n'
      note_body "$EXISTING_NOTE"
      printf '\n\n---\n\nBelow is the ENTIRE transcript; it and your prior note above are your only inputs. The marker line "<<< NEW MATERIAL" shows where material not yet covered by your prior note begins. Produce ONLY an addendum covering that new material: chronological notes in the same form, continuing the turn numbering. Do not repeat the header, and do not restate notes on earlier turns. End the addendum with a "### Edits (this update)" subsection — this nested addendum ledger uses ### (the "## Edits" form mentioned earlier applies only to a whole new note) — listing any surgical edits you made to quotes as verbatim → as printed, or the single word none.\n\n'
      printf '%s\n\n' "$(deliverable_note "$DELIVERABLE")"
      cat "$MARKED"
    } >"$OBS_IN"
    run_scribe "$OBS_IN" "$ADDENDUM" "$DELIVERABLE"

    # Validate substance (issue #20): a real addendum, not a pointer/stub.
    # Word-floor, not line-count. One retry.
    if ! addendum_has_substance "$ADDENDUM"; then
      log "WARN: observer addendum below word floor ($(wc -w <"$ADDENDUM" 2>/dev/null | tr -d ' ') words) — retrying once"
      printf '\n\n%s\n' "$OBS_RETRY_LINE" >>"$OBS_IN"
      run_scribe "$OBS_IN" "$ADDENDUM" "$DELIVERABLE"
    fi
    rm -f "$OBS_IN"

    if addendum_has_substance "$ADDENDUM"; then
      # Guarantee the Edits ledger (issue #20). Present -> normalize its heading
      # to the canonical nested form. Absent -> the worker supplies an HONEST
      # placeholder that never claims the scribe verified quote-fidelity (it did
      # not), and logs the backstop so misses stay countable (#21).
      if ledger_present "$ADDENDUM"; then
        LTMP="$(mktemp -t automemory-ledger.XXXXXX)"
        awk 'BEGIN{done=0}
             /^##+[[:space:]]+[Ee]dits/ && !done { print "### Edits (this update)"; done=1; next }
             { print }' "$ADDENDUM" >"$LTMP" && mv "$LTMP" "$ADDENDUM"
      else
        printf '\n### Edits (this update)\n\n_none recorded; ledger auto-supplied by the worker (scribe emitted none), so quote-fidelity is unverified for this addendum._\n' >>"$ADDENDUM"
        log "backstop: worker supplied missing Edits ledger for $EXISTING_NOTE"
      fi
      # Splice, then advance the watermark ONLY if the splice conclusively
      # succeeded (issue #20 'librarian' fix). Otherwise the note stays unchanged
      # but the watermark would jump, silently orphaning the delta forever.
      TMP_NOTE="$(mktemp -t automemory-note.XXXXXX)"
      if awk -v add="$ADDENDUM" -v date="$DATE" '
        function emit_update(   line) {
          print "---"; print ""
          print "## Update — " date; print ""
          while ((getline line < add) > 0) print line
          close(add); print ""
        }
        !inserted && /^## (Unverified|Edits)([ \t]|$)/ { emit_update(); inserted=1 }
        { print }
        END { if (!inserted) { print ""; emit_update() } }
      ' "$EXISTING_NOTE" >"$TMP_NOTE" && mv "$TMP_NOTE" "$EXISTING_NOTE"; then
        fm_set "$EXISTING_NOTE" captured "$CAPTURED_TS" 2>>"$LOG"
        fm_set "$EXISTING_NOTE" lines "$LINES" 2>>"$LOG"
        log "observer appended update to $EXISTING_NOTE ($(wc -w <"$ADDENDUM" | tr -d ' ') words); watermark $PREV_LINES -> $LINES"
        clear_failure
      else
        rm -f "$TMP_NOTE"
        log "WARN: splice failed for $EXISTING_NOTE — note unchanged, watermark NOT advanced (will retry next capture)"
        RUN_EXTRACTOR=0
      fi
    else
      write_failure "addendum" "below word floor after retry ($(wc -w <"$ADDENDUM" 2>/dev/null | tr -d ' ') words)" "$ADDENDUM"
      log "WARN: observer addendum below word floor after retry — note left untouched (watermark NOT advanced); rejected output preserved in ops/logs/failures/"
      RUN_EXTRACTOR=0
    fi
    rm -f "$OLD_TXT" "$MARKED" "$ADDENDUM"
  fi
fi

# ---------------------------------------------------------------------------
# (d) Extractor — Sonnet, WITH repo access. Runs with cwd = repo so it can read
#     system/design/*.md and write entries + INDEX.md lines itself. acceptEdits lets it
#     Read/Write within the repo non-interactively; the extractor prompt already
#     forbids reading fieldnotes/ (observer independence) and agent-notes/
#     (agent-side privacy — must never reach her desk). Skipped when an
#     update-capture had no new material.
# ---------------------------------------------------------------------------
if [ "$RUN_EXTRACTOR" -eq 1 ]; then
  EXT_PROMPT="$REPO_ROOT/system/engine/prompts/extractor.md"
  EXT_INPUT="$(mktemp -t automemory-extractor.XXXXXX)" || EXT_INPUT="$REPO_ROOT/.capture-extractor.tmp"
  {
    printf 'AUTOMEMORY-ENGINE-RUN — internal capture agent; this marker lets the engine recognize its own sessions.\n\n'
    cat "$EXT_PROMPT"
    printf '\n\n---\n\nSession metadata: source "claude-code: %s", session uuid %s, date %s. Use these facts verbatim in entry frontmatter source fields; do not embellish or guess beyond them (e.g. never infer which model or persona the session was).\n' "$SLUG" "${SESSION_ID:-unknown}" "$DATE"
    printf 'The transcript follows; it is your only source. Do not read fieldnotes/ or agent-notes/.\n\n'
    cat "$TXT"
  } >"$EXT_INPUT"

  ( cd "$REPO_ROOT" && claude -p "$(cat "$EXT_INPUT")" \
      --model sonnet \
      --permission-mode acceptEdits ) >>"$LOG" 2>>"$LOG"
  log "extractor completed"
  rm -f "$EXT_INPUT"
fi
rm -f "$TXT"

# ---------------------------------------------------------------------------
# (f) Commit and push. Pull --rebase first; never force-push; tolerate offline.
# ---------------------------------------------------------------------------
cd "$REPO_ROOT" || { log "ERROR: cannot cd repo"; exit 0; }

# Serialize the shared git step across concurrent workers (issue #22).
git_lock

git pull --rebase >>"$LOG" 2>&1 || log "WARN: pull --rebase failed (continuing)"
# Scope the add to capture outputs only. `git add -A` swept unrelated
# in-progress working-tree edits (design docs, mid-edit scripts) into capture
# commits twice on 2026-07-05 — a capture commit must contain only what the
# capture produced: vault streams + raws + INDEX + failure/drawer records.
# Never system/scripts/, system/engine/, system/design/, .claude/.
git add ops/exports/ fieldnotes/ wins/ practitioner/ research/ losses/ ops/sweeps/ ops/logs/ INDEX.md >>"$LOG" 2>&1

if git diff --cached --quiet 2>/dev/null; then
  log "nothing to commit; done"
  exit 0
fi

git commit -m "automemory: capture session $DATE" \
  -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>" >>"$LOG" 2>&1 \
  || { log "WARN: commit failed"; exit 0; }

if git push >>"$LOG" 2>&1; then
  log "pushed OK"
else
  log "WARN: push failed (offline?) — commit left locally, will push next run"
fi

exit 0
