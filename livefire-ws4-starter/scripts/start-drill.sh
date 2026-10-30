#!/usr/bin/env bash
# Starts one drill as one execution of job-drill-runner, then prints where to watch it.
#   scripts/start-drill.sh                       Supervised, T-01
#   scripts/start-drill.sh "Autonomous Twin"     evaluation and repeatability runs
#   scripts/start-drill.sh Supervised T-00       baseline only
set -euo pipefail
set -a; eval "$(azd env get-values)"; set +a
MODE="${1:-Supervised}"
SCENARIO="${2:-T-01}"
DRILL_ID="drill-$(date -u +%Y%m%d-%H%M%S)"
JOB=job-drill-runner

running=$(az containerapp job execution list -n "$JOB" -g "$CONTROL_RG" \
  --query "length([?properties.status=='Running'])" -o tsv)
[ "$running" = "0" ] || { echo "refused: a drill is already running; one at a time" >&2; exit 2; }

job_id=$(az containerapp job show -n "$JOB" -g "$CONTROL_RG" --query id -o tsv)
# A start request replaces the container's whole env list, secret references included,
# so read the template's list and send it back with this drill's three values.
body=$(az containerapp job show -n "$JOB" -g "$CONTROL_RG" --query "properties.template.containers[0]" -o json |
  jq --arg d "$DRILL_ID" --arg m "$MODE" --arg s "$SCENARIO" '{containers: [
      .env |= ([.[] | select(.name != "DRILL_ID" and .name != "MODE" and .name != "SCENARIO")]
               + [{name: "DRILL_ID", value: $d}, {name: "MODE", value: $m}, {name: "SCENARIO", value: $s}])]}')
az rest --method post --url "https://management.azure.com${job_id}/start?api-version=2024-03-01" --body "$body" -o none

echo "$DRILL_ID started: $MODE, $SCENARIO"
echo "Watch it: ${CONTROL_CENTER_URL:-<control center>}/drills/$DRILL_ID"
echo "Logs:     az containerapp job logs show -n $JOB -g $CONTROL_RG --container drill-runner --follow"
