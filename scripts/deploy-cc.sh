#!/usr/bin/env bash
# Deploys the Control Center into WS2's G2 control plane, on its own (G2). From G3 the same module runs from
# WS2's infra/main.bicep instead (infra/main.ws5-snippet.bicep).
#
# Run from the WS5 repo root, signed in to the lab with `az login`, with the control resource group set:
#   export CONTROL_RG=rg-lf-control-<azd env name>        # e.g. rg-lf-control-lf-eh
#
#   scripts/deploy-cc.sh discover            print what was found in the control resource group (no changes)
#   scripts/deploy-cc.sh secrets             create cc-session-secret and cc-passcode-sha256 if missing;
#                                            prints a new passcode once (put it in the password manager)
#   scripts/deploy-cc.sh rotate-passcode     a new passcode; restart the app 30 minutes later (see the guide)
#   scripts/deploy-cc.sh image [sha]         import ghcr.io/<owner>/control-center:sha-<40 hex> into the registry
#   scripts/deploy-cc.sh deploy [sha]        deploy infra/modules/control-center.bicep with that image
#   scripts/deploy-cc.sh verify              roles, revision, sign-in guard and security headers
#   scripts/deploy-cc.sh access [object-id]  Storage Blob Data Contributor on the evidence container for you
#                                            (or that object ID), for test drills and the sync bridge
#   scripts/deploy-cc.sh logs                the app's last 50 console lines
#   scripts/deploy-cc.sh all [sha]           secrets, image, deploy, verify
#
# [sha] defaults to the last commit on origin/main that changed the Control Center: the tag CI pushed to GHCR.
# Optional: GHCR_OWNER (default livefire-team), GHCR_USER and GHCR_TOKEN (only if the GHCR package is private),
# CC_AUTH_MODE (passcode or easyauth), CC_ENTRA_CLIENT_ID (easyauth), CC_ALLOWED_IPS (comma-separated CIDRs).
set -euo pipefail
cd "$(dirname "$0")/.."
RG="${CONTROL_RG:?export CONTROL_RG=rg-lf-control-<azd env name> first}"
GHCR_OWNER="${GHCR_OWNER:-livefire-team}"
AUTH_MODE="${CC_AUTH_MODE:-passcode}"
APP=ca-control-center
say() { printf '\n== %s\n' "$*"; }
fail() { echo "STOP: $*" >&2; exit 1; }

sha256() { python3 -c 'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'; }
random() { python3 -c 'import secrets,sys; print(secrets.token_urlsafe(int(sys.argv[1])))' "$1"; }

discover() {
  az group show -n "$RG" -o none 2>/dev/null || fail "resource group $RG not found: wrong subscription or name (az account show)"
  ENV_ID="$(az containerapp env show -g "$RG" -n cae-lf-control --query id -o tsv)"
  ACR="$(az acr list -g "$RG" --query '[0].name' -o tsv)"
  ACR_SERVER="$(az acr list -g "$RG" --query '[0].loginServer' -o tsv)"
  PULL_ID="$(az identity show -g "$RG" -n id-lf-acrpull --query id -o tsv)"
  KV="$(az keyvault list -g "$RG" --query "[?starts_with(name, 'kv-lfc-')].name | [0]" -o tsv)"
  EVIDENCE="$(az storage account list -g "$RG" --query "[?starts_with(name, 'stlfev')].name | [0]" -o tsv)"
  for v in ENV_ID ACR ACR_SERVER PULL_ID KV EVIDENCE; do
    [ -n "${!v}" ] || fail "$v not found in $RG: has WS2 run scripts/deploy.sh infra?"
  done
}

print_found() {
  say "found in $RG"
  printf '%-12s %s\n' environment "$ENV_ID" registry "$ACR_SERVER" pull-id "$PULL_ID" \
    vault "$KV" evidence "$EVIDENCE"
  echo "EVENT_SOURCE for scripts: blob:https://$EVIDENCE.blob.core.windows.net/evidence"
}

resolve_sha() {
  local sha="${1:-}"
  if [ -z "$sha" ]; then
    git fetch -q origin
    sha="$(git log -1 --first-parent --format=%H origin/main -- src/control-center src/shared \
      .github/workflows/control-center.yml)"
  fi
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || fail "need the full 40-character commit SHA, got '$sha'"
  echo "$sha"
}

secret_exists() { az keyvault secret show --vault-name "$KV" -n "$1" --query id -o tsv >/dev/null 2>&1; }

set_passcode() {
  local passcode; passcode="$(random 18)"
  az keyvault secret set --vault-name "$KV" -n cc-passcode-sha256 \
    --value "$(printf '%s' "$passcode" | sha256)" -o none
  echo "New passcode (shown once; put it in the team password manager, never in chat or git):"
  echo "  $passcode"
}

phase_secrets() {
  say "secrets in $KV"
  if secret_exists cc-session-secret; then echo "cc-session-secret: already there"
  else az keyvault secret set --vault-name "$KV" -n cc-session-secret --value "$(random 48)" -o none
       echo "cc-session-secret: created"; fi
  if [ "$AUTH_MODE" = easyauth ]; then
    secret_exists cc-entra-client-secret || fail "easyauth needs cc-entra-client-secret (see the guide, Easy Auth)"
    echo "cc-entra-client-secret: already there"
  elif secret_exists cc-passcode-sha256; then echo "cc-passcode-sha256: already there (rotate-passcode makes a new one)"
  else set_passcode; fi
}

phase_image() {
  local sha tag src
  sha="$(resolve_sha "${1:-}")"; tag="sha-$sha"; src="ghcr.io/$GHCR_OWNER/control-center:$tag"
  say "image $ACR_SERVER/control-center:$tag"
  if az acr repository show -n "$ACR" --image "control-center:$tag" -o none 2>/dev/null; then
    echo "already in the registry"; return
  fi
  local creds=()
  [ -z "${GHCR_TOKEN:-}" ] || creds=(--username "${GHCR_USER:?set GHCR_USER with GHCR_TOKEN}" --password "$GHCR_TOKEN")
  az acr import -n "$ACR" --source "$src" --image "control-center:$tag" ${creds[@]+"${creds[@]}"} -o none \
    || fail "import failed: did CI push $src, and is the GHCR package public (or GHCR_USER/GHCR_TOKEN set)?"
  echo "imported $src"
}

phase_deploy() {
  local sha image ips
  sha="$(resolve_sha "${1:-}")"; image="$ACR_SERVER/control-center:sha-$sha"
  az acr repository show -n "$ACR" --image "control-center:sha-$sha" -o none 2>/dev/null \
    || fail "$image is not in the registry: run scripts/deploy-cc.sh image $sha first"
  secret_exists cc-session-secret || fail "run scripts/deploy-cc.sh secrets first"
  if [ "$AUTH_MODE" = easyauth ]; then secret_exists cc-entra-client-secret || fail "cc-entra-client-secret is missing"
  else secret_exists cc-passcode-sha256 || fail "run scripts/deploy-cc.sh secrets first"; fi
  ips="$(python3 -c 'import json,sys; print(json.dumps([x.strip() for x in sys.argv[1].split(",") if x.strip()]))' \
    "${CC_ALLOWED_IPS:-}")"
  say "deploying $APP ($AUTH_MODE) with $image"
  az deployment group create -g "$RG" -n control-center -f infra/modules/control-center.bicep \
    -p environmentId="$ENV_ID" image="$image" registryServer="$ACR_SERVER" pullIdentityId="$PULL_ID" \
       keyVaultName="$KV" evidenceAccountName="$EVIDENCE" authMode="$AUTH_MODE" \
       entraClientId="${CC_ENTRA_CLIENT_ID:-}" allowedIpRanges="$ips" \
    --query properties.outputs.url.value -o tsv
}

expect() {  # expect <label> <wanted> <got>
  if [ "$2" = "$3" ]; then printf '  ok    %-44s %s\n' "$1" "$3"; else printf '  FAIL  %-44s wanted %s, got %s\n' "$1" "$2" "$3"; BAD=1; fi
}

phase_verify() {
  local url pid n h
  BAD=0
  url="https://$(az containerapp show -g "$RG" -n "$APP" --query properties.configuration.ingress.fqdn -o tsv)"
  for _ in $(seq 36); do   # a new revision can take a minute or two to answer
    [ "$(curl -s -o /dev/null -w '%{http_code}' "$url/health")" = 200 ] && break
    sleep 5
  done
  say "revision"
  az containerapp revision list -g "$RG" -n "$APP" \
    --query "[?properties.active].{name:name, health:properties.healthState, running:properties.runningState, replicas:properties.replicas}" -o table
  say "roles of id-lf-control-center (exactly 3: two secrets, one container)"
  pid="$(az identity show -g "$RG" -n id-lf-control-center --query principalId -o tsv)"
  az role assignment list --assignee "$pid" --all --query "[].{role:roleDefinitionName, scope:scope}" -o table
  n="$(az role assignment list --assignee "$pid" --all --query 'length(@)' -o tsv)"
  expect "role assignments" 3 "$n"
  say "sign-in guard and headers on $url"
  expect "/health" 200 "$(curl -s -o /dev/null -w '%{http_code}' "$url/health")"
  if [ "$AUTH_MODE" = passcode ]; then
    expect "/ without sign-in (to the sign-in page)" 303 "$(curl -s -o /dev/null -w '%{http_code}' "$url/")"
    expect "/api/drills/drill-x/view without sign-in" 401 \
      "$(curl -s -o /dev/null -w '%{http_code}' "$url/api/drills/drill-x/view")"
  else
    expect "/ without sign-in (to Microsoft Entra)" 302 "$(curl -s -o /dev/null -w '%{http_code}' "$url/")"
  fi
  expect "http:// is refused or redirected" yes \
    "$(c="$(curl -s -o /dev/null -w '%{http_code}' "http://${url#https://}/health")"; case "$c" in 200) echo no ;; *) echo yes ;; esac)"
  h="$(curl -sI "$url/health" | tr -d '\r' | tr 'A-Z' 'a-z')"
  for header in content-security-policy strict-transport-security x-frame-options x-content-type-options; do
    expect "header $header" yes "$(grep -q "^$header:" <<<"$h" && echo yes || echo no)"
  done
  echo
  echo "CONTROL_CENTER_URL=$url"
  [ "$BAD" = 0 ] && echo "VERIFY PASSED" || { echo "VERIFY FAILED"; exit 1; }
}

phase_access() {
  local who="${1:-}" scope
  [ -n "$who" ] || who="$(az ad signed-in-user show --query id -o tsv)"
  scope="$(az storage account show -g "$RG" -n "$EVIDENCE" --query id -o tsv)/blobServices/default/containers/evidence"
  say "Storage Blob Data Contributor on the evidence container for $who"
  az role assignment create --assignee "$who" --role "Storage Blob Data Contributor" --scope "$scope" -o none
  echo "done; it can take a few minutes to apply"
}

case "${1:-}" in
  discover) discover; print_found ;;
  secrets) discover; phase_secrets ;;
  rotate-passcode) discover; say "new passcode in $KV"; set_passcode ;;
  image) discover; phase_image "${2:-}" ;;
  deploy) discover; phase_deploy "${2:-}" ;;
  verify) phase_verify ;;
  access) discover; phase_access "${2:-}" ;;
  logs) az containerapp logs show -g "$RG" -n "$APP" --tail 50 --format text ;;
  all) discover; print_found; phase_secrets; phase_image "${2:-}"; phase_deploy "${2:-}"; phase_verify ;;
  *) sed -n '2,23p' "$0"; exit 2 ;;
esac
