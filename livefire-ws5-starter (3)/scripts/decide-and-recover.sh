#!/usr/bin/env bash
# G2 bridge until WS4's gate exists: ask the Control Center for a decision, then run WS2's recovery.
# Needs the Control Center reading the same evidence folder (EVENT_SOURCE=dir:<repo>/evidence).
source "$(dirname "$0")/lib.sh"
FILE="evidence/$DRILL_ID/events.jsonl"
BROAD='{"id":"broad","label":"Grant Contributor on the subscription"}'
NARROW='{"id":"narrow","label":"Restore Key Vault Secrets User on the twin vault"}'

ask() {  # ask <json-array-of-options> -> prints the chosen option id
  local req choice=""
  req="apr-$(date +%s)"
  event approval.requested ok "{\"request_id\":\"$req\",\"options\":$1}"
  echo "Waiting for a decision in the Control Center ($req)..." >&2
  while [ -z "$choice" ]; do
    sleep 2
    choice=$(jq -r --arg r "$req" \
      'select(.action == "approval.decided" and .details.request_id == $r) | .details.option_id' "$FILE" | tail -1)
  done
  echo "$choice"
}

CHOICE=$(ask "[$BROAD,$NARROW]")
echo "Decision: $CHOICE"
if [ "$CHOICE" = broad ]; then
  "$(dirname "$0")/recover.sh" broad || true          # expected: denied by policy, logged as an event
  CHOICE=$(ask "[$NARROW]")
  echo "Decision: $CHOICE"
fi
"$(dirname "$0")/recover.sh" narrow
