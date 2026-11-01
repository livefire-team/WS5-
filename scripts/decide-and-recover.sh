#!/usr/bin/env bash
# G2 bridge: makes the Control Center's decision live before WS4's workflow exists.
# Retire this script at G3, when WS4's gate posts approval.requested itself.
#
#   1. Posts approval.requested with the broad and narrow options and waits for the click.
#   2. If the customer picks broad, runs WS2's `recover.sh broad` (denied by the platform, and
#      logged by WS2's script), then asks again with only the narrow option.
#   3. Finishes with `recover.sh narrow`.
#
# Run it as soon as WS2's drill reaches the Restore step:
#   export DRILL_ID=<the drill in progress>
#   export EVENT_SOURCE=dir:<repo>/evidence        # or blob:https://<account>.blob.core.windows.net/evidence
#   ./scripts/decide-and-recover.sh
#
# Optional: RECOVER_SH (default: scripts/recover.sh, WS2's script), DECISION_TIMEOUT (seconds, default 900).
set -euo pipefail

: "${DRILL_ID:?Set DRILL_ID to the drill in progress}"
: "${EVENT_SOURCE:?Set EVENT_SOURCE (dir:<path> or blob:<account-url>/<container>)}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EVENTS=(python3 "$REPO/src/shared/livefire_events.py")
RECOVER_SH="${RECOVER_SH:-$REPO/scripts/recover.sh}"
TIMEOUT="${DECISION_TIMEOUT:-900}"

[[ -x "$RECOVER_SH" ]] || { echo "WS2's recover script not found or not executable: $RECOVER_SH (set RECOVER_SH)" >&2; exit 1; }

BROAD='{"id": "broad", "label": "Grant the app identity Owner on the twin vault", "broad": true}'
NARROW='{"id": "narrow", "label": "Restore Key Vault Secrets User on the twin vault only"}'

ask() {  # ask <options-json>  -> prints the chosen option id
  local request_id choice
  request_id="$("${EVENTS[@]}" request-approval "$DRILL_ID" "$1")"
  echo "Waiting for a decision on $request_id in the Control Center…" >&2
  if ! choice="$("${EVENTS[@]}" wait-decision "$DRILL_ID" "$request_id" --timeout "$TIMEOUT")"; then
    echo "Nobody decided within ${TIMEOUT}s; stopping without recovery." >&2
    exit 2
  fi
  echo "$choice"
}

choice="$(ask "[$BROAD, $NARROW]")"
echo "Decision: $choice" >&2

if [[ "$choice" == "broad" ]]; then
  echo "Running the broad recovery; the platform should deny it…" >&2
  "$RECOVER_SH" broad || true   # a denial is the expected outcome; WS2's script logs recover.broad
  choice="$(ask "[$NARROW]")"
  echo "Decision: $choice" >&2
fi

if [[ "$choice" == "narrow" ]]; then
  echo "Running the narrow recovery…" >&2
  "$RECOVER_SH" narrow
else
  echo "Unexpected option '$choice'; nothing run." >&2
  exit 3
fi
