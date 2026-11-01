# WS5 hand-offs: copy-paste messages

## To WS4 (lead) and WS2: the event catalog

> The WS5 event catalog is in `contracts/event-catalog.md`. It's the only thing the Control Center
> needs from you. Please write every event through `src/shared/livefire_events.py`
> (`store.append(drill_id, make_event(...))`, or `python src/shared/livefire_events.py append ...`
> from shell scripts). That gives us one format and one validator, and the screen picks events up
> with no changes on my side.
>
> WS4: you can rename any proposed event, as long as you update the catalog the same day. Two things
> to agree with me: (1) how long your approval gate waits before escalating, and (2) that your gate
> waits for `approval.decided` and never treats the Control Center as the one acting.
>
> WS2: in Blob mode, events go to the `evidence` container as **append blobs** named
> `<drill-id>/events.jsonl`. The shared module creates them that way. Please set the container's
> immutability policy to allow **protected append writes**, or decisions can't be recorded.

## To WS2: the control.bicep request

> For G2, WS5 needs four things in `control.bicep`:
> 1. The control Container Apps environment (the Ops Service needs it too)
> 2. A user-assigned identity `id-lf-control-center`
> 3. The control Key Vault (RBAC permission model)
> 4. The evidence storage account with its `evidence` container (protected appends allowed)
>
> Then call the module:
>
> ```bicep
> module controlCenter 'modules/control-center.bicep' = {
>   name: 'control-center'
>   params: {
>     location: location
>     environmentId: controlEnv.id
>     image: controlCenterImage            // ghcr.io/<org>/control-center:sha-<commit>
>     identityId: ccIdentity.id
>     identityClientId: ccIdentity.properties.clientId
>     identityPrincipalId: ccIdentity.properties.principalId
>     authMode: 'passcode'                 // 'easyauth' if the probe allowed app registrations
>     keyVaultName: controlVault.name
>     evidenceAccountName: evidence.name
>     allowedIpRanges: []                  // e.g. ['203.0.113.0/24'] on the day
>   }
> }
> ```
>
> The module gives the identity only Key Vault Secrets User on the control vault and Storage Blob
> Data Contributor on the evidence container. No rights on the twin, no route to the Ops Service.

## To WS1: business numbers

> Please have the load generator append a `metrics.orders` event to the current drill every
> 30 seconds: `{"orders_per_min": <float>, "failed_total": <int>, "backlog": <int>}`. Use
> `src/shared/livefire_events.py`. That fills the orders-per-minute, failed-orders and backlog tiles.

## To the lead: deck corrections and roles

> Deck corrections are in `docs/walkthrough/deck-corrections.md` (8 items from the plan).
> Proposed roles for the day are in `docs/walkthrough/run-of-show.md`: Presenter, Driver,
> Backup, Customer. Can we name people at the first rehearsal?

## Secrets for the lab (do this before deploying)

```bash
PASSCODE="$(openssl rand -base64 18)"          # share out of band, on the day only
az keyvault secret set --vault-name <control-vault> --name cc-passcode-sha256 \
  --value "$(printf '%s' "$PASSCODE" | sha256sum | cut -d' ' -f1)"
az keyvault secret set --vault-name <control-vault> --name cc-session-secret \
  --value "$(openssl rand -base64 48)"
```

Never commit either value. Rotate the passcode after every walkthrough.
