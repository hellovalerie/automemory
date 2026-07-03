#!/usr/bin/env bash
# capture-session.sh — Engine A SessionEnd hook (thin launcher).
#
# Hooks run inside a bounded execution window; the capture pipeline (two
# headless model calls + git push) takes minutes. So this script only saves
# the payload and detaches capture-worker.sh, returning to Claude Code
# immediately. The worker survives the hook (and the session) via nohup.
#
# Design contract: never disrupt the user's session; always exit 0 fast.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG="$REPO_ROOT/.capture.log"

PAYLOAD_FILE="$(mktemp -t automemory-payload.XXXXXX)" || exit 0
cat >"$PAYLOAD_FILE" 2>/dev/null

printf '%s launcher: detaching worker (payload %s)\n' \
  "$(date '+%Y-%m-%dT%H:%M:%S')" "$PAYLOAD_FILE" >>"$LOG" 2>/dev/null

nohup "$SCRIPT_DIR/capture-worker.sh" "$PAYLOAD_FILE" </dev/null >/dev/null 2>&1 &

exit 0
