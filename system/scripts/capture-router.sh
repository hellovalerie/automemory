#!/usr/bin/env bash
# capture-router.sh — user-level hook entry point (issue #8: global capture).
#
# Wired into ~/.claude/settings.json for BOTH SessionStart and SessionEnd, so
# EVERY Claude Code project on this machine feeds the automemory vault. It is
# deliberately thin: branch on hook_event_name and hand the untouched payload
# to the existing project scripts (capture-session.sh / reconcile-sessions.sh).
#
# automemory's own sessions are excluded here: the repo's project-level hooks
# (.claude/settings.json) already run the same scripts, and user + project
# hooks BOTH fire in Claude Code — without this cwd check every automemory
# session would be captured twice.
#
# Design contract: failure-silent, always exit 0, never disrupt any session.
# This is a user-level script (no CLAUDE_PROJECT_DIR), so the repo path is
# derived from the script's own location — settings.json invokes it by absolute
# path, so $0 resolves the repo root without hardcoding a username. (Was once
# hardcoded to /Users/<name>/...; a machine move to a new home dir silently
# broke capture — issue #30. Same $0-based idiom reconcile-sessions.sh uses.)
AUTOMEMORY_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

set +e

LOG="$AUTOMEMORY_ROOT/.capture.log"
SCRIPTS="$AUTOMEMORY_ROOT/system/scripts"

PAYLOAD="$(cat 2>/dev/null)"

CWD="$(printf '%s' "$PAYLOAD" | jq -r '.cwd // empty' 2>/dev/null)"
EVENT="$(printf '%s' "$PAYLOAD" | jq -r '.hook_event_name // empty' 2>/dev/null)"

# No-op for automemory itself (exact match or any path inside it): the
# project's own hooks handle those sessions; double-firing must not happen.
case "$CWD" in
  "$AUTOMEMORY_ROOT" | "$AUTOMEMORY_ROOT"/*) exit 0 ;;
esac

SLUG="$(basename "${CWD:-unknown}" | tr '[:upper:] ' '[:lower:]-' | tr -cd 'a-z0-9-')"
[ -z "$SLUG" ] && SLUG="unknown"

printf '%s router: dispatch %s for %s\n' \
  "$(date '+%Y-%m-%dT%H:%M:%S')" "${EVENT:-unknown-event}" "$SLUG" >>"$LOG" 2>/dev/null

case "$EVENT" in
  SessionEnd)
    # Same stdin contract as the project hook: capture-session.sh cats stdin
    # into a payload file and detaches the worker.
    printf '%s' "$PAYLOAD" | "$SCRIPTS/capture-session.sh" 2>/dev/null
    ;;
  SessionStart)
    # Reconciler reads the payload from stdin too; its stdout is session
    # context for the starting session, so pass it through untouched.
    printf '%s' "$PAYLOAD" | "$SCRIPTS/reconcile-sessions.sh"
    # agent-notes reminder (global, 2026-07-06 — the user: "I have other
    # projects, I want their notes"). Permission-framed by design: a demanded
    # entry is a performed entry. Mirrors the wording in automemory's own
    # project hook.
    printf 'agent-notes: the automemory repo (%s/agent-notes/) keeps a stream in the agent'"'"'s own hand — read its README before writing. If something comes up for YOU this session (an assumption breaks, you notice a pull), a note there before the session ends is yours to write: signed model + version, then commit and push it in that repo (pull/rebase first, never force). "Nothing came up" is the usual truth and needs NO entry; never write one to satisfy this reminder.\n' "$AUTOMEMORY_ROOT"
    ;;
esac

exit 0
