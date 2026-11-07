#!/usr/bin/env bash
# G2 bridge: makes the kill switch act before WS4's workflow exists.
# Waits for a kill.requested event in the drill, then runs WS2's reset script once.
# From G3, WS4's workflow owns this reaction; stop running this script then.
#
# Run it from the root of WS2's repo, in its own terminal on the driver's machine, next to the sync:
#   export DRILL_ID=<the drill in progress>
#   export EVENT_SOURCE=dir:$PWD/evidence
#   RESET_SH=./scripts/reset.sh <ws5-repo>/scripts/kill-watcher.sh
#
# Optional: RESET_SH (default: scripts/reset.sh, WS2's script).
set -euo pipefail

: "${DRILL_ID:?Set DRILL_ID to the drill in progress}"
: "${EVENT_SOURCE:?Set EVENT_SOURCE (dir:<path> or blob:<account-url>/<container>)}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESET_SH="${RESET_SH:-$REPO/scripts/reset.sh}"

[[ -x "$RESET_SH" ]] || { echo "WS2's reset script not found or not executable: $RESET_SH (set RESET_SH)" >&2; exit 1; }

echo "Watching $DRILL_ID for the kill switch (Ctrl+C to stop)…" >&2
if event="$(python3 "$REPO/src/shared/livefire_events.py" wait-action "$DRILL_ID" kill.requested)"; then
  echo "Kill switch pressed: $event" >&2
  echo "Running WS2's reset…" >&2
  "$RESET_SH"
fi
