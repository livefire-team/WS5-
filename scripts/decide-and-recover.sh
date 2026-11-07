#!/usr/bin/env bash
# G2 bridge: makes the Control Center's decision live before WS4's workflow exists.
# Retire this script at G3, when WS4's gate posts approval.requested itself.
#
#   1. Posts approval.requested with the broad and narrow options and waits for the click.
#   2. If the customer picks broad, runs WS2's `recover.sh broad` (denied by the platform, and
#      logged by WS2's script), then asks again with only the narrow option.
#   3. Finishes with `recover.sh narrow`.
#
# The broad option is offered only while WS2's deny policy (lf-deny-broad-roles) is assigned. Without it,
# `recover.sh broad` run by an Owner really grants Contributor on the whole subscription before removing it,
# so the card then offers only the narrow option. To show the refusal live, WS2 runs `scripts/deploy.sh policy`
# first (it waits until Azure Policy refuses, up to 30 minutes) and `scripts/deploy.sh policy-off` afterwards.
#
# Run it from the root of WS2's repo (livefire-fusion-poc) as soon as the drill reaches the Restore step, on
# the machine that holds the lab's azd environment. WS2's scripts write to the local evidence folder, and
# `livefire_events.py sync` (in its own terminal) copies that folder to Blob and the Control Center's clicks back:
#   export DRILL_ID=<the drill in progress>
#   export EVENT_SOURCE=dir:$PWD/evidence
#   RECOVER_SH=./scripts/recover.sh <ws5-repo>/scripts/decide-and-recover.sh
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

BROAD='{"id": "broad", "label": "Grant the app identity Contributor on the whole subscription", "broad": true}'
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

policy_on() {  # true only when az can see the deny policy assigned on the current subscription
  [[ "$(az policy assignment list --query "[?name=='lf-deny-broad-roles'].name" -o tsv 2>/dev/null)" \
     == "lf-deny-broad-roles" ]]
}

if policy_on; then
  choice="$(ask "[$BROAD, $NARROW]")"
else
  echo "The deny policy lf-deny-broad-roles is not assigned, so the broad option is NOT offered:" >&2
  echo "run as an Owner, it would really grant Contributor on the subscription. Offering only the narrow option." >&2
  echo "To show the refusal live, ask WS2 to run scripts/deploy.sh policy first (up to 30 minutes)." >&2
  choice="$(ask "[$NARROW]")"
fi
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
