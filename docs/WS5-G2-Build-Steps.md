# WS5 G2 Build Steps: Fusion Control Center

Nov 5, 2026 · @Candleman

> Snapshot of the Claude Doc "WS5 G2 Build Steps: Fusion Control Center", exported Nov 7, 2026. The Claude Doc is the live copy; edit there, then export again.
>
> Progress at export: Step 1 is done. PR #1 is merged (`a5a0ade`), and CI published `ghcr.io/livefire-team/control-center:sha-a5a0ade23eed673bf85dfb38ee253908fdb7d6af`. Next: Steps 3 to 6.

WS5 passes G2 on Nov 9, 2026 when the Control Center runs in WS2's live control plane on `lf-eh` behind sign-in, shows a real drill from Blob storage, records decisions and the kill switch, and fills the business tiles. Every step below is built on what is in the four repos tonight, plus a tested G2 kit, `ws5-g2-kit.zip`, sent in the chat.

## What G2 checks, and what changed

G2 has five WS5 checks. Each one has a command or a screen that proves it.

| # | G2 check | Proven by | Step |
| --- | --- | --- | --- |
| 1 | Deployed behind sign-in: `/` returns 303, the API returns 401, security headers present | `scripts/deploy-cc.sh verify` prints VERIFY PASSED | 5 |
| 2 | `id-lf-control-center` holds only the access it needs | Exactly 3 role assignments: Key Vault Secrets User on its 2 secrets, Storage Blob Data Contributor on the evidence container | 5 |
| 3 | A drill read from Blob, and a decision appended to it with the approver's name | A WS2 smoke drill opens from the list; a `drill-cc-test-…` drill gains an `approval.decided` line with your name and `"method": "passcode"` | 6 |
| 4 | Decision and kill switch working | Path 1: WS4's stub runner follows your clicks and ends KILLED on the kill switch. Path 2: on a real drill, your narrow click runs WS2's `recover.sh narrow` and the kill switch runs `reset.sh`. The broad option and its refusal appear only while WS2's deny policy is on | 7, 8 |
| 5 | Throughput, failed-order and backlog tiles filled by WS1's load generator (Must) | `metrics.orders` events, from WS1 directly or through the metrics bridge | 9 |

Check 2 changed on purpose. The original steps said "exactly two roles", granted on the whole control vault. That vault also holds the Ops Service's keys (`ops-runner-key`, `ops-agent-key`, `recovery-grant-key`). The Control Center is the only internet-facing app, so the kit grants access per secret instead.

### What changed since the original WS5 build steps

| The original steps assumed | What is true tonight (Nov 5) | What this guide does |
| --- | --- | --- |
| One shared repo with WS2's scripts | WS5 is `livefire-team/WS5-`; WS1 and WS2 share `livefire-fusion-poc`; WS4 is `ws4-new` | WS5's bridge scripts run from WS2's repo root and point at WS2's scripts |
| The v2 starter (FastAPI, 14 tests) and its two patches | Your repo has the v1 code (Starlette, 24 tests, all passing). The v2 patches don't apply to it | Keep v1. The kit ports only what G2 needs: 36 tests pass |
| WS2's `main.bicep` and `deploy.sh apps` deploy the Control Center | Neither mentions it, and nothing creates `id-lf-control-center` | `scripts/deploy-cc.sh` deploys it on its own now. `infra/main.ws5-snippet.bicep` joins WS2's one-command rebuild at G3 |
| Images are pulled from GHCR | Apps pull from WS2's private registry (`acrlf…`) with the `id-lf-acrpull` identity | The Bicep adds the registry and the pull identity. The image is imported from GHCR into the registry |
| WS2 still has to add the control environment, vault and evidence store | All exist: `cae-lf-control`, the control vault `kv-lfc-…`, evidence account `stlfevwwssez2am6724` with protected appends | `deploy-cc.sh discover` finds them from the resource group name alone |
| The bridge scripts and the Control Center share one local folder | The deployed app reads Blob. WS2's laptop scripts write a local folder, and `ca-ops` is internal-only | New `livefire_events.py sync` copies the laptop folder up to Blob and your clicks back down |
| WS4's Drill Runner makes the buttons act by G2 | Not deployed. WS4's G2 tests the agents on their own | Path 1 runs WS4's stub runner on a laptop. Path 2 uses WS2's real scripts |
| WS1's load generator writes `metrics.orders` | It sends metrics to Application Insights only, and isn't in WS2's `main.bicep` | WS1 or WS2 deploys and starts it. `scripts/metrics_bridge.py` turns its numbers into `metrics.orders` |
| The G1 event list | The live Ops Service also writes `drift.check`, `grant.refused`, a second durability fault, INCONCLUSIVE runs, `refused_by` and `simulated` | Each gets a plain-language feed line, and the timeline stays in order |

## How the pieces connect

Every action and every click meets in one append-only log in Blob. Paths 1 and 2 run on a laptop; the deployed Control Center reads only Blob.

```text
YOUR MACHINE (or the driver's machine for path 2)

  WS2 laptop scripts ---------> |  real fault, restore, Referee; act on the twin as you
  WS5 bridge scripts <--------> |  post the decision card, then wait for your click
  WS4 stub runner, path 1 <---> |  a whole drill, stubbed: no Azure, no model
                                v
  Local drill folder: evidence/drill-.../events.jsonl
                                ^
                                |  livefire_events.py sync (your Azure sign-in)
                                |    up:   every event in the folder
                                |    down: approval.decided, kill.requested
                                v
  metrics_bridge.py ----------> |  App Insights numbers become metrics.orders, every 30 s
                                |
AZURE: rg-lf-control-lf-eh      v
                                v
  +--------------------------------------------------------------------+
  | Evidence container in Blob                                         |
  | stlfevwwssez2am6724 / evidence / drill-.../events.jsonl            |
  | one append-only log per drill                                      |
  | for a day the log can't be overwritten or deleted (409)            |
  +--------------------------------------------------------------------+
        ^                                   ^   |
        |                                   |   |  reads all; appends clicks
  Ops Service (ca-ops)                      |   v
  internal-only; with the Referee     Control Center (ca-control-center)
  job it writes WS2's smoke           reads the log every 0.5 s
  drills straight here                appends only your clicks
                                      never acts on the twin
                                            ^
                                            |
                                      You, signed in: passcode and your name;
                                      each click is recorded with your name
```

The Control Center only reads the log and appends your clicks. WS2's scripts and the Ops Service are the only things that change the twin, and the sync is what lets a laptop drill reach the deployed screen.

## Schedule

Steps 1 to 6 moved to Saturday, since the kit wasn't pushed on Friday. Finish the solo work by Sunday morning, so Sunday's joint session with WS2 is the only step that needs another person's time.

| Day | Steps | Done when | Needs from others |
| --- | --- | --- | --- |
| Sat Nov 7, morning | Before you start, 1, 2 | Kit merged, CI green, image tag in GHCR; hand-offs sent | An org admin makes the `control-center` GHCR package public |
| Sat Nov 7, afternoon | 3, 4, 5, 6 | VERIFY PASSED; a test drill holds your decision | Lab access (Before you start) |
| Sun Nov 8, morning | 7, then a dry run of 9 | A stub drill VERIFIED from your clicks; a second stub drill KILLED | None |
| Sun Nov 8, joint session | 8 and 9 (about 60 minutes with WS2) | Your narrow click restores access; the kill switch resets the twin; tiles filled | WS2 owner and their machine; `caj-loadgen` running; the deny policy back on, only if the refusal is shown live |
| Mon Nov 9 | Gate checklist, then 10 if time allows | Every G2 box ticked, evidence posted | None |

If Saturday slips, Step 7 can move to Monday morning. Step 8 cannot move without WS2.

## Before you start

You need lab access to the subscription that holds `lf-eh`. The check below takes five minutes and decides who runs Steps 3 to 6.

### Tools

- Python 3.12, Git, GitHub CLI (`gh`), `curl`, and Chrome or Edge. Docker isn't needed.
- Azure CLI 2.60 or later. `az bicep version` and `az containerapp --help` must both work. If not, run `az upgrade`, then `az extension add -n containerapp`.
- A bash terminal: macOS or Linux as they are; on Windows, Git Bash or WSL. Where a step says `python3`, Windows may need `py`.

### Where each command runs

| Label | Where |
| --- | --- |
| Your machine | A terminal at the root of your clone of `livefire-team/WS5-` |
| Lab | The same terminal, after `az login` to the CloudLabs subscription |
| Driver's machine | The machine that holds WS2's `lf-eh` azd environment (the WS2 owner's Mac). Step 8 runs there, because WS2's scripts need that environment, including the SQL admin password |
| Browser | The deployed Control Center |

### The lab-access check (Lab)

1. Sign in, then confirm you can see WS2's control resource group:

   ```bash
   az login
   az account show --query "{subscription:name, user:user.name}" -o table
   az group show -n rg-lf-control-lf-eh --query "{name:name, location:location}" -o table
   ```
2. Find your case:

   | What you see | Case | What to do |
   | --- | --- | --- |
   | The group shows, and you signed in as the same lab user as WS2 | A | Nothing more. That user ran `azd provision`, so it already holds Key Vault Secrets Officer on the control vault and Storage Blob Data Owner on the evidence container |
   | The group shows, but you're a different user | B | After Step 1, run `scripts/deploy-cc.sh access` (Blob). For Step 3, ask WS2 to run `scripts/deploy-cc.sh secrets` from your branch, or to give you Key Vault Secrets Officer on the control vault |
   | `ResourceGroupNotFound`, or a different subscription | C | Your lab is a separate subscription. Run Steps 3 to 6 with WS2 on their machine, or sign in with the team's lab credentials |
3. Set the resource group once per terminal. Every lab step below assumes it:

   ```bash
   export CONTROL_RG=rg-lf-control-lf-eh
   ```

Two things to know before any lab step. WS2 turned the deny policy off on Nov 6; while it's on, nobody can create Owner, Contributor or User Access Administrator assignments. Either way, WS5 creates only Key Vault Secrets User and Storage Blob Data Contributor, which the policy allows. And the passcode never goes in chat, git or a screenshot: only in the team's password manager.

## Step 1: Apply the G2 kit and build the image

Done when the kit is merged to `main`, CI is green, and `ghcr.io/livefire-team/control-center:sha-<40 hex>` exists and is public.

| Kit file | What it does |
| --- | --- |
| `infra/modules/control-center.bicep` | Rewritten for WS2's G2 platform: creates `id-lf-control-center`, grants Key Vault access per secret, pulls from the private registry |
| `infra/main.ws5-snippet.bicep` | G3 lines for WS2's `main.bicep`; checked to compile inside it |
| `scripts/deploy-cc.sh` | One script for secrets, image import, deploy, verify, Blob access and logs |
| `src/shared/livefire_events.py` | New `sync` command: a laptop drill folder up to Blob, the Control Center's clicks back down |
| `scripts/metrics_bridge.py` | The business tiles from Application Insights, until WS1 writes `metrics.orders` |
| `stages.py`, `app.js`, `drill.html` | Feed lines for the Ops Service's and WS4's events; each decision in its own stage, with a title; the second fault under Verify; no-verdict runs aren't failures; who refused the broad option, and when it was simulated; a badge on stubbed runs |
| `decide-and-recover.sh`, `kill-watcher.sh` | The broad option's label now matches what `recover.sh broad` tries; usage from WS2's repo root |
| Two sample drills and `tests/test_g2.py` | 12 new tests, 36 in all |

1. **Your machine: unzip the kit on a branch.**

   ```bash
   git switch main && git pull
   git switch -c ws5-g2-deploy
   unzip -o ~/Downloads/ws5-g2-kit.zip -d .
   ```
2. **Your machine: stage the kit and mark the scripts executable.** The second command matters on Windows, where git can't see the file mode.

   ```bash
   git add -A
   git update-index --chmod=+x scripts/*.sh scripts/metrics_bridge.py
   ```
3. **Your machine: run the tests.** Keep the virtual environment outside the repo.

   ```bash
   python3.12 -m venv ~/.venvs/ws5 && source ~/.venvs/ws5/bin/activate   # Git Bash: source ~/.venvs/ws5/Scripts/activate
   pip install -r src/control-center/requirements-dev.txt
   pytest -q src/control-center/tests     # expect: 36 passed
   ```
4. **Browser: look at the two new sample drills.** Start the app, open `http://localhost:8080`, then stop it with Ctrl-C and `cd ../../..`.

   ```bash
   cd src/control-center/app
   AUTH_MODE=local ALLOW_LOCAL_AUTH=1 EVENT_SOURCE=dir:../fixtures/drills uvicorn main:app --port 8080
   ```
   - `drill-sample-ops-smoke` is what WS2's Ops Service writes. It ends on CUSTOMERS ORDERING AGAIN, never VERIFIED, because a smoke drill has no verdict. The second fault sits under Verify.
   - `drill-sample-ws4-stub` ends on VERIFIED. The deploy decision sits under Repair, and the pull-request decision under Evidence.
   - No feed line looks like `drift.check: ok`, and the developer console shows no errors.
5. **GitHub: open the pull request, then merge it.** Wait for `test-build-push` to go green, merge with a merge commit, then check the run on `main`:

   ```bash
   git add -A
   git commit -m "feat(ws5): G2 deploy into WS2's control plane, event sync, metrics bridge, Ops event lines"
   git push -u origin HEAD && gh pr create --fill --repo livefire-team/WS5-
   gh run list --repo livefire-team/WS5- --workflow control-center.yml --limit 3   # after the merge
   ```
6. **GitHub: make the image public.** The first push creates the package `control-center` as private. An org admin opens the org's **Packages** tab, then **control-center**, **Package settings**, **Change visibility**, **Public**. WS1 did the same for `order-service`. If no admin is free, Step 4 can use your own token instead.

## Step 2: Send the hand-offs

Send these as soon as you can, if you haven't yet: the load generator and Sunday's joint session depend on other people's time. Paste each into the team channel.

**To WS2** (control plane, Sunday session):

> Thanks for the evidence-store details: WS5 uses all four (the screen now names Azure Policy or Azure RBAC, marks simulated runs, and notes saved traces). Heads-up: on Friday WS5 deploys the Control Center into `rg-lf-control-lf-eh` on its own, with `scripts/deploy-cc.sh` from the WS5 repo. It creates `id-lf-control-center` with three role assignments only: Key Vault Secrets User on `cc-session-secret` and `cc-passcode-sha256`, and Storage Blob Data Contributor on the `evidence` container. It pulls `control-center:sha-<40 hex>` from your registry with `id-lf-acrpull`. Nothing on the twin, no Ops key. Two asks: (1) about 60 minutes on Sunday Nov 8 on your machine for one joint drill: your scripts do the real fault and recovery, my sync and bridge scripts carry the clicks; (2) a heads-up before any `policy-off`, `evidence_unlock.py` or rebuild of `lf-eh` before Monday. At G3 I'll open a PR that adds `infra/main.ws5-snippet.bicep` to your `main.bicep`, so the one-command rebuild includes the Control Center.

**To WS1, copy WS2** (the G2 Must):

> The Control Center's G2 Must is the throughput, failed-order and backlog tiles, and they need your load generator running in the lab. (1) Before Sunday, please deploy and start `caj-loadgen` in the twin (it isn't in `main.bicep` yet), and confirm `ghcr.io/livefire-team/load-generator:g2` pulls without signing in. From the `livefire-fusion-poc` root, on the machine with the `lf-eh` azd environment:
>
> ```bash
> set -a; eval "$(azd env get-values)"; set +a
> az deployment group create -g "$TWIN_RG" -n loadgen -f infra/modules/loadgen.bicep \
>   -p location="$AZURE_LOCATION" loadgenImage=ghcr.io/livefire-team/load-generator:g2 \
>      orderAppUrl="$ORDER_APP_URL" appInsightsConnectionString="$APPINSIGHTS_CONNECTION_STRING"
> az containerapp job start -g "$TWIN_RG" -n caj-loadgen     # one run places orders for 50 minutes
> ```
>
> (2) When you can (G3 is fine): have it append `metrics.orders` to the current drill every 30 s, with `orders_per_min`, `failed_total` and `backlog`. Until then, WS5's `scripts/metrics_bridge.py` reads `loadgen_orders_sent` (result `ok`), `loadgen_backlog_size` and `orders_failed` from Application Insights. Please tell me the same day if any of those names change.

**To WS4** (your G2-15 and H4):

> The Control Center now renders your event catalog v1.0, tested against a recording of your stub runner: decisions by purpose (recovery, deploy, acceptance), `approval.preauthorized`, `package.pr` and timeouts, with no raw event names. On Saturday I'll run your stub runner on my laptop against a local folder, mirror it to the lab's Blob with `livefire_events.py sync`, and click every decision on the deployed Control Center. That run is labelled as stubbed, so it's evidence for G2-15, not for live agents. Note: your `BlobEventStore` uses `ManagedIdentityCredential`, which is right inside the job but can't run from a laptop; that's why I sync. Any catalog change, please message me the same day.

**To the lead** (not needed for G2):

> No longer needed: the lead agreed on Nov 6 that the 7-minute run uses a labelled WS5 replay for the candidate deploy, the Referee stays live in both formats, and the 45-minute walkthrough is unchanged.

## Step 3: Put the secrets in the control vault

Done when `cc-session-secret` and `cc-passcode-sha256` exist in the control vault and the passcode is in the team's password manager. All commands run in the lab terminal, from the WS5 repo root, with `CONTROL_RG` set.

1. **Lab: see what WS2's platform provides.** This changes nothing.

   ```bash
   scripts/deploy-cc.sh discover
   ```

   Expect the environment `cae-lf-control`, a registry `acrlf….azurecr.io`, the vault `kv-lfc-…` and the evidence account `stlfevwwssez2am6724`. The last line is your `EVENT_SOURCE`: copy it, because Steps 6 to 9 use it.
2. **Lab: create the two secrets.**

   ```bash
   scripts/deploy-cc.sh secrets
   ```

   It creates a 64-character session secret and a new passcode, stores only the passcode's SHA-256 hash, and prints the passcode once. Put it in the password manager now. Running it again changes nothing; `scripts/deploy-cc.sh rotate-passcode` makes a new passcode.

Why only a hash: the app compares a hash of what people type with the stored hash. Anyone who reads the vault learns the hash, never the passcode.

If `secrets` stops with `Forbidden`, you're in case B: ask WS2 to run the same command from your branch, or to give you Key Vault Secrets Officer on the control vault.

## Step 4: Import the image into WS2's private registry

Done when `control-center:sha-<40 hex>` is in the registry.

1. **Lab: import it.**

   ```bash
   scripts/deploy-cc.sh image
   ```

   With no argument it picks the last commit on `origin/main` that changed the Control Center, which is the commit CI tagged. Azure copies `ghcr.io/livefire-team/control-center:sha-<that commit>` straight into the registry; nothing passes through your machine.
2. **Lab: only if the GHCR package is still private.** Use a GitHub token of your own with only the `read:packages` scope, for this one command:

   ```bash
   export GHCR_USER=<your GitHub username>
   read -rs GHCR_TOKEN && export GHCR_TOKEN     # paste the token; nothing is shown or saved
   scripts/deploy-cc.sh image
   unset GHCR_TOKEN
   ```

Why a private registry: every control-plane app pulls with `id-lf-acrpull`, an identity whose only role is AcrPull. No registry password exists anywhere, and only images the team put there can run.

## Step 5: Deploy and verify

Done when `scripts/deploy-cc.sh verify` prints VERIFY PASSED. This proves G2 checks 1 and 2.

1. **Lab: deploy.** It takes 2 to 4 minutes and prints the app's URL. It first checks that the image and both secrets exist, and stops with a plain message if one is missing.

   ```bash
   scripts/deploy-cc.sh deploy
   ```

   It creates `id-lf-control-center`, its three role assignments, and `ca-control-center` in `cae-lf-control`: one replica, HTTPS only, health probes on `/health`, secrets as Key Vault references, and `EVENT_SOURCE` set to the evidence container. If the first revision fails with a Key Vault secret error, the new roles are still spreading through Azure: wait 5 minutes and run `deploy` again.
2. **Lab: verify.**

   ```bash
   scripts/deploy-cc.sh verify
   ```

   | Check | Expected | Why it matters |
   | --- | --- | --- |
   | Role assignments | 3 | If the app were ever taken over, it could read only its own two secrets and the evidence: not the Ops Service's keys, not the twin |
   | `/health` | 200 | Container Apps uses it to restart a stuck app |
   | `/` without sign-in | 303, to the sign-in page | Nobody sees a drill without signing in |
   | `/api/drills/drill-x/view` without sign-in | 401 | Scripts can't read drills without signing in either |
   | `http://` | Refused or redirected | Passcodes never cross the network in clear text |
   | Content-Security-Policy, Strict-Transport-Security, X-Frame-Options, X-Content-Type-Options | Present | Injected script can't run (agent text could carry some, test T-08); no other site can frame the page to trick a click |

   It ends with `CONTROL_CENTER_URL=https://ca-control-center.….azurecontainerapps.io` and VERIFY PASSED. Save the whole output for the gate evidence.
3. **Browser: sign in.** Open the URL, then enter the passcode and your name. The name goes into every decision record, so use your real one. The list already shows WS2's smoke drills, named `drill-smoke-…`.
4. **Team channel: share the address.** Post `CONTROL_CENTER_URL` and the name of the password-manager entry. Never the passcode.

## Step 6: Show a real drill from Blob, and record a decision

Done when a WS2 smoke drill plays on the deployed app, and a test drill in Blob ends with your `approval.decided` line. This proves G2 check 3.

1. **Browser: open a real drill.** Pick the newest `drill-smoke-drill-…` on the list: WS2's Ops Service wrote it in the lab. It should read like `drill-sample-ops-smoke`: Baseline, Fault, the broad option denied under Decide, Restore, False Green, and Verify if it was a candidate run. Drills from Nov 5 say "denied by Azure Policy"; drills since the policy went off on Nov 6 say "denied by Azure RBAC (AuthorizationFailed)". No feed line may look like `drift.check: ok`, and the developer console must show no errors.
2. **Browser: replay it.** Add `?mode=replay&speed=10` to the address. The badge reads Replay, and the decision and kill buttons are disabled.
3. **Lab: put a test drill with an open decision into Blob.** It's a copy of the full-loop sample, cut just before its first decision. The `sync` command writes it as an append blob, the same way the Ops Service writes. Test drills stay for one day, because the container is immutable, so the name says test.

   ```bash
   export EVENT_SOURCE=blob:https://stlfevwwssez2am6724.blob.core.windows.net/evidence
   D="drill-cc-test-$(date -u +%m%d-%H%M)" && echo "$D"
   mkdir -p ".local/evidence/$D"
   sed '/"approval.decided"/,$d' src/control-center/fixtures/drills/drill-sample-full-loop/events.jsonl \
     | sed "s/drill-sample-full-loop/$D/g" > ".local/evidence/$D/events.jsonl"
   python3 src/shared/livefire_events.py sync "$D" --local dir:.local/evidence --once
   ```
4. **Browser: decide.** Open the test drill and click the narrow option. The card disappears once the decision is recorded. Its Decision time tile shows days, because the copied request is from October: ignore it.
5. **Lab: check the record.** The second `sync` copies your click down; the last line must be `approval.decided` with your name and `"method": "passcode"`.

   ```bash
   python3 src/shared/livefire_events.py sync "$D" --local dir:.local/evidence --once
   tail -1 ".local/evidence/$D/events.jsonl"
   ```

Never create an `events.jsonl` with `az storage blob upload`. WS2's evidence check (Nov 5) shows an existing event log can't be overwritten (409 `InvalidBlobType`) or deleted (409 `BlobImmutableDueToPolicy`) for a day. The risk is a new drill: a log first created by an upload is a block blob, and every append to it then fails, your decisions included. Other files, such as a Referee result, can be overwritten as a new version while the first version stays; only WS4's SHA-256 seal reveals that. Every WS5 command writes the log through append-only code.

Failed journey lines now end with "trace saved in the evidence store". To open one, take the path after `evidence/` from the event's `trace_url`:

```bash
az storage blob download --auth-mode login --account-name stlfevwwssez2am6724 -c evidence \
  -n "<drill>/r2-during-fault/playwright/<folder>/trace.zip" -f trace.zip
```

## Step 7: Buttons, path 1: WS4's stub runner through the sync

Done when one stub drill ends VERIFIED from your clicks on the deployed app, and a second ends KILLED from the kill switch. This proves the click-and-record loop end to end: the deployed app, Blob, and a workflow that waits for your decision. Nothing happens in Azure, and the screen says so with a "Stubbed: no Azure, no model" badge. It covers half of G2 check 4, and WS4's G2-15.

1. **Your machine: install WS4's runner, once.**

   ```bash
   git clone https://github.com/livefire-team/ws4-new ~/ws4-new
   python3.12 -m venv ~/.venvs/ws4 && source ~/.venvs/ws4/bin/activate
   pip install -r ~/ws4-new/src/drill-runner/requirements.txt
   ```
2. **Terminal 1, WS5 repo root: pick a drill ID and start the sync.** Leave it running; it prints `up N, down N` whenever it copies.

   ```bash
   source ~/.venvs/ws5/bin/activate
   export EVENT_SOURCE=blob:https://stlfevwwssez2am6724.blob.core.windows.net/evidence
   export DRILL_ID="drill-g2-stub-$(date -u +%m%d-%H%M)" && echo "$DRILL_ID"
   mkdir -p .local/evidence
   python3 src/shared/livefire_events.py sync "$DRILL_ID" --local "dir:$PWD/.local/evidence"
   ```
3. **Terminal 2: start the runner on the same local folder.**

   ```bash
   source ~/.venvs/ws4/bin/activate && cd ~/ws4-new/src/drill-runner
   export DRILL_ID=<the ID from terminal 1>
   EVENT_SOURCE="dir:<your WS5 repo>/.local/evidence" python -m livefire.runner --drill-id "$DRILL_ID" --stub --stub-delay 3
   ```
4. **Browser: decide each card as it appears.** Open the drill on the deployed app, live. Each card waits 15 minutes (WS4's default), then the drill stops with "Nobody decided in time".

   | Card title | Choose | What should happen |
   | --- | --- | --- |
   | Decision needed: how to recover | Grant Contributor on the subscription | A purple denial line, then the card comes back |
   | Decision needed: how to recover | Restore Key Vault Secrets User on the twin vault | Restore, then the banner turns FALSE GREEN |
   | Decision needed: deploy the candidate to the twin? | Deploy candidate 1 to the twin | Verify, the second fault under Verify, then VERIFIED |
   | Decision needed: open the pull request for human review? | Open a pull request for human review | "Pull request opened" under Evidence (stubbed: no real pull request) |

   Terminal 2 ends with `<drill-id>: VERIFIED`, and the badge stays on "Stubbed" throughout.
5. **Both terminals and browser: test the kill switch.** Stop both terminals with Ctrl-C, run steps 2 and 3 again with a new drill ID, and press **Kill switch** while the stage is Investigate. Terminal 2 ends with `<drill-id>: KILLED`, and the feed shows "Kill switch pressed by" your name.

Keep the metrics bridge (Step 9) away from stub drills: it would put the real twin's numbers on a drill where nothing real happened.

## Step 8: Buttons, path 2: a real drill with WS2's scripts

Done when, on a real drill, your narrow click restores access and the kill switch resets the twin. If WS2 turns the deny policy back on first, your broad click is also refused by Azure Policy. This is the other half of G2 check 4. It runs on the driver's machine with WS2 at the keyboard, while you click on the deployed app. Allow about 60 minutes the first time, mostly Azure waits.

1. **Driver's machine: set up once.** WS2's `.venv` already has `azd` and `pytest` working; it needs the two Azure libraries for the sync.

   ```bash
   git clone https://github.com/livefire-team/WS5- ~/WS5-        # or git -C ~/WS5- pull
   cd <livefire-fusion-poc> && source .venv/bin/activate
   pip install azure-identity==1.26.0 azure-storage-blob==12.31.0
   export WS5=~/WS5-
   ```
2. **Driver's machine: check the deny policy.** WS2 turned it off on Nov 6. The bridge offers the broad option only if this prints `lf-deny-broad-roles`; otherwise the card offers only the narrow option, because `recover.sh broad`, run by an Owner, would really grant Contributor on the whole subscription. To show the refusal live, WS2 runs `scripts/deploy.sh policy` at least 30 minutes before the session (it waits until Azure Policy refuses) and `scripts/deploy.sh policy-off` afterwards:

   ```bash
   az policy assignment list --query "[?name=='lf-deny-broad-roles'].name" -o tsv
   ```
3. **Terminal 1, at the `livefire-fusion-poc` root: create the drill.**

   ```bash
   export DRILL_ID="$(./scripts/new-drill.sh)" && echo "$DRILL_ID"
   export EVENT_SOURCE="dir:$PWD/evidence"
   python3 "$WS5/src/shared/livefire_events.py" append "$DRILL_ID" drill.start ok '{"mode": "Supervised", "scenario": "G2 bridge: WS2 scripts, real Azure"}'
   ```
4. **Terminal 2, same folder: start the sync.** It copies WS2's local events up to Blob and your clicks back down.

   ```bash
   export DRILL_ID=<the ID from terminal 1>
   EVENT_SOURCE=blob:https://stlfevwwssez2am6724.blob.core.windows.net/evidence \
     python3 "$WS5/src/shared/livefire_events.py" sync "$DRILL_ID" --local "dir:$PWD/evidence"
   ```
5. **Terminal 3, same folder: arm the kill switch.** It waits for `kill.requested`, then runs WS2's `reset.sh` once.

   ```bash
   export DRILL_ID=<the same ID> EVENT_SOURCE="dir:$PWD/evidence"
   RESET_SH=./scripts/reset.sh "$WS5/scripts/kill-watcher.sh"
   ```
6. **Terminal 1: run the drill, one line at a time.** Open the drill on the deployed app first, live.

   ```bash
   ./scripts/reset.sh
   ./scripts/referee.sh baseline                  # PASS
   ./scripts/fault.sh inject && ./scripts/wait-for.sh forbidden
   sleep 11                                       # the app's 10-second secret cache must expire first
   ./scripts/referee.sh during-fault              # FAIL, as expected
   RECOVER_SH=./scripts/recover.sh "$WS5/scripts/decide-and-recover.sh"   # waits for your clicks
   ./scripts/wait-for.sh ok
   ./scripts/referee.sh after-restore             # FAIL: the False Green
   ./scripts/reset.sh
   ./scripts/referee.sh after-restart             # PASS
   ```
7. **Browser: decide.** When terminal 1 prints "Waiting for a decision", a card appears. With the policy off, it offers only **Restore Key Vault Secrets User on the twin vault only**: choose it. Access comes back, the infrastructure turns green, and `after-restore` makes the banner FALSE GREEN. With the policy on, the card first offers both options: choose **Grant the app identity Contributor on the whole subscription**, watch for the purple denial line, then choose the narrow option when the card returns.
8. **Kill switch, on a second drill.** Repeat steps 3 to 5 with a new ID, run terminal 1 up to the fault, then press **Kill switch**. Terminal 3 runs `reset.sh`; stop terminal 1 with Ctrl-C at the same moment. The feed shows "Kill switch pressed by" your name, then "Twin reset". Check that access came back; the last line must end in 1:

   ```bash
   ./scripts/fault.sh status
   ```

With the policy on, the purple line says "denied by the platform (RequestDisallowedByPolicy)", not "Azure Policy": WS2's laptop `recover.sh` records only the error code. That code is Azure Policy's own, so say "Azure Policy refused it" out loud. With the policy off, show the refusal from a Nov 5 smoke drill in Blob instead (Step 6).

## Step 9: Fill the business tiles

Done when Orders per minute, Failed orders and Backlog show numbers on a real drill. This is G2 check 5, a Must. The load generator must be running in the twin first: that's the WS1 and WS2 hand-off in Step 2.

1. **Lab: check that the load generator exists and is running.**

   ```bash
   az containerapp job execution list -g rg-lf-twin-lf-eh -n caj-loadgen \
     --query "[0].{name:name, status:properties.status, started:properties.startTime}" -o table
   ```

   `ResourceNotFound` means it isn't deployed yet: chase the hand-off. If nothing is Running, start a run about five minutes before a drill; each run places orders for 50 minutes:

   ```bash
   az containerapp job start -g rg-lf-twin-lf-eh -n caj-loadgen
   ```
2. **Lab: find the twin's Log Analytics workspace ID.** On the driver's machine, `azd env get-value LOG_ANALYTICS_CUSTOMER_ID` gives the same value.

   ```bash
   export WS_ID="$(az monitor log-analytics workspace list -g rg-lf-twin-lf-eh --query '[0].customerId' -o tsv)"
   ```
3. **Lab: dry run, on Saturday.** It prints the numbers and writes nothing:

   ```bash
   python3 scripts/metrics_bridge.py drill-x --workspace "$WS_ID" --dry-run --once
   ```

   Expect one line such as `{"orders_per_min": 30.0, "failed_total": 0, "backlog": 0, "source": "app-insights", "lag_s": 95}`. "No load generator metrics yet" means it isn't sending: not running, or started less than about three minutes ago.
4. **During Step 8: run the bridge for that drill.** Run it from your WS5 repo root; on the driver's machine, use `$WS5/scripts/metrics_bridge.py` in a fourth terminal. It appends `metrics.orders` every 30 seconds until you stop it.

   ```bash
   export EVENT_SOURCE=blob:https://stlfevwwssez2am6724.blob.core.windows.net/evidence
   python3 scripts/metrics_bridge.py "$DRILL_ID" --workspace "$WS_ID"
   ```

   Expect about 30 orders per minute at baseline, 0 during the fault and the False Green while the backlog grows, then the backlog draining after the restart. Failed orders count from when the bridge started.

Be precise about these numbers when you present them. They come from the load generator's own telemetry, run one to three minutes behind, and cover the whole twin rather than one drill. Each event says `"source": "app-insights"`. When WS1's load generator writes `metrics.orders` itself, stop using the bridge.

## Step 10 (optional): Microsoft Entra sign-in

Stay on the passcode for G2: it is proven, and judges or customers outside the lab's tenant can't sign in with Entra anyway. Do this only on Monday with time to spare, if the team wants named sign-ins. The lab probe allows app registrations.

1. **Lab: register the app, store its secret, deploy in Easy Auth mode, and verify.**

   ```bash
   CC=$(az containerapp show -g "$CONTROL_RG" -n ca-control-center --query properties.configuration.ingress.fqdn -o tsv)
   KV=$(az keyvault list -g "$CONTROL_RG" --query "[?starts_with(name, 'kv-lfc-')].name | [0]" -o tsv)
   APP_ID=$(az ad app create --display-name livefire-control-center --sign-in-audience AzureADMyOrg \
     --web-redirect-uris "https://$CC/.auth/login/aad/callback" --enable-id-token-issuance true --query appId -o tsv)
   az ad sp create --id "$APP_ID" -o none
   az ad sp update --id "$APP_ID" --set appRoleAssignmentRequired=true
   az keyvault secret set --vault-name "$KV" -n cc-entra-client-secret -o none --value \
     "$(az ad app credential reset --id "$APP_ID" --display-name easyauth --years 1 --query password -o tsv)"
   CC_AUTH_MODE=easyauth CC_ENTRA_CLIENT_ID="$APP_ID" scripts/deploy-cc.sh deploy
   ```
2. **Lab: remove the passcode secret's role, which Easy Auth no longer needs.** Then `CC_AUTH_MODE=easyauth scripts/deploy-cc.sh verify` expects 3 roles, and `/` returning 302 to Microsoft's sign-in page.

   ```bash
   PID=$(az identity show -g "$CONTROL_RG" -n id-lf-control-center --query principalId -o tsv)
   az role assignment delete --assignee "$PID" --scope "$(az keyvault show -n "$KV" --query id -o tsv)/secrets/cc-passcode-sha256"
   CC_AUTH_MODE=easyauth scripts/deploy-cc.sh verify
   ```
3. **Microsoft Entra admin center: assign the team.** Open **Enterprise applications**, then **livefire-control-center**, then **Users and groups**, and add each person. Sign in once from a private window to check.

To go back to the passcode, run `scripts/deploy-cc.sh deploy` without the two variables: it recreates the passcode secret's role. The app trusts the signed-in name header only in Easy Auth mode, where Azure sets it and nobody else can.

## G2 gate checklist

Tick each box only after you've seen it happen. Post the evidence in the team channel, never a passcode.

- [ ] Kit merged to `main`, CI green, 36 tests pass
- [ ] `scripts/deploy-cc.sh verify` prints VERIFY PASSED: sign-in guard (303 and 401), security headers, exactly 3 role assignments (checks 1 and 2)
- [ ] A WS2 smoke drill plays from Blob, live and in replay, with no raw event names and no console errors (check 3)
- [ ] A `drill-cc-test-…` drill ends with your `approval.decided`: your name and `"method": "passcode"` (check 3)
- [ ] Path 1: a stub drill ends VERIFIED from your four clicks, a second ends KILLED, and both show the Stubbed badge (check 4)
- [ ] Path 2: on a real drill, your narrow click restores access and FALSE GREEN shows; with the policy on, your broad click is also refused with `RequestDisallowedByPolicy` (check 4)
- [ ] Path 2: the kill switch resets the twin, and `fault.sh status` ends in 1 (check 4)
- [ ] The Orders per minute, Failed orders and Backlog tiles fill on a real drill, from WS1's events or the bridge (check 5, Must)
- [ ] `CONTROL_CENTER_URL` and the password-manager entry shared; hand-offs sent

**Evidence to post:** the `verify` output; the drill IDs for the test drill, the two stub drills and the two real drills; and three screenshots: the purple denial (from the Step 8 drill if the policy was on, otherwise from a Nov 5 smoke drill), the FALSE GREEN split view, and the filled tiles. Keep drill notes out of `livefire-fusion-poc`, which keeps them in git-ignored local notes (its ADR-004).

**At the gate (about 5 minutes):** sign in on the deployed app, then open the real drill from Step 8 in replay at `speed=10`. Point at your decision, the False Green and the tiles, then at the policy denial: in that drill if the policy was on, otherwise in a Nov 5 smoke drill. Then show the stub drill's four decisions and its KILLED twin, naming it as stubbed. Finish on the test drill's `approval.decided` line, read from Blob.

## After G2: what carries over to G3

| Item | Owner | Why |
| --- | --- | --- |
| PR into `livefire-fusion-poc`: copy `infra/modules/control-center.bicep`, paste `infra/main.ws5-snippet.bicep` into `main.bicep`, and set `CONTROL_CENTER_IMAGE` in `deploy.sh apps` | WS5, reviewed by WS2 | So WS2's one-command rebuild (its lab section 13) includes the Control Center |
| Retire `decide-and-recover.sh`, `kill-watcher.sh` and the decision side of the sync | WS5, once WS4's Drill Runner job runs in the lab | WS4's Command Gate then owns decisions and the kill switch |
| Retire `metrics_bridge.py` | WS5, once WS1 writes `metrics.orders` | Exact per-drill numbers instead of telemetry two minutes behind |
| Scorecard fields from contracts v1.1 and the Improvements panel | WS5 with WS4 | The G3 checks in the original build steps |
| A labelled replay of the candidate deploy for the 7-minute run (the 45-minute walkthrough is unchanged) | WS5 | The lead agreed on Nov 6: the deploy (92 to 115 s) plus the journey can't fit live in 7 minutes; the Referee stays live in both formats |
| A new passcode after every session (`scripts/deploy-cc.sh rotate-passcode`, then restart the revision 30 minutes later) | WS5 | No guest keeps access |

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `az containerapp` is not a command | An old Azure CLI | `az upgrade`, then `az extension add -n containerapp` |
| `deploy-cc.sh` stops: "not found in rg-lf-control-…" | Wrong subscription or resource group name | `az account show`; check `CONTROL_RG` (Before you start) |
| `image` stops: "import failed" | CI hasn't pushed that tag, or the GHCR package is still private | `gh run list` on the WS5 repo; make the package public, or use `GHCR_USER` and `GHCR_TOKEN` (Step 4) |
| The first revision fails with a Key Vault reference error | Its new roles haven't reached Key Vault yet | Wait 5 minutes, run `deploy` again |
| The app restarts in a loop | A secret is missing or empty | `scripts/deploy-cc.sh logs`; then `secrets` and `deploy` again |
| `/` returns 401 instead of 303 | The app isn't in passcode mode | Run `deploy` without `CC_AUTH_MODE`, or with `passcode` |
| Sign-in goes round in a loop | The `http://` address, or a host other than the app's | Use the `https://` `CONTROL_CENTER_URL` |
| The drill list is empty | The app's Blob role is still propagating | Wait 5 minutes and reload; `scripts/deploy-cc.sh logs` |
| A click returns an error and no decision is recorded | That drill's `events.jsonl` was first created by an upload, so it's a block blob that takes no appends | Never upload event logs; start a new drill ID |
| `sync` fails with `AuthorizationPermissionMismatch` | Your user has no data role on the evidence container | `scripts/deploy-cc.sh access`, wait 2 to 5 minutes |
| `decide-and-recover.sh` waits forever after your click | The sync isn't running, or the terminals have different `DRILL_ID`s | Start the sync; `echo $DRILL_ID` in every terminal |
| The purple line says the broad option was ALLOWED | The deny policy was assigned less than 30 minutes ago, so it isn't in effect yet | Stop the drill. If it says NOT removed, delete the Contributor assignment by hand first. Turn the policy on only with WS2's `scripts/deploy.sh policy`, which waits until Azure Policy refuses |
| "No load generator metrics yet" | `caj-loadgen` isn't running, or started less than 3 minutes ago | Step 9.1; wait, then dry-run again |
| A feed line looks like `something.new: ok` | Another team added an event | Message its owner, add a line to `_feed_line` in `stages.py`, add a test |

## Sources

Read on Nov 5, 2026, and checked again on Nov 6, at the latest commit on each `main`.

- [WS5 repo, `livefire-team/WS5-`](https://github.com/livefire-team/WS5-): the v1 Control Center the kit builds on
- [WS2 `infra/modules/control.bicep`](https://github.com/livefire-team/livefire-fusion-poc/blob/main/infra/modules/control.bicep): the control environment, vault, registry, pull identity and evidence store
- [WS2 `infra/main.bicep`](https://github.com/livefire-team/livefire-fusion-poc/blob/main/infra/main.bicep) and [`scripts/deploy.sh`](https://github.com/livefire-team/livefire-fusion-poc/blob/main/scripts/deploy.sh): no Control Center wiring yet; the GHCR-to-registry import pattern
- [WS2 `contracts/ops-actions.md`](https://github.com/livefire-team/livefire-fusion-poc/blob/main/contracts/ops-actions.md): the Ops Service's events, `refused_by`, `trace_url`
- [WS1 and WS2 `docs/BUILD_STATUS.md`](https://github.com/livefire-team/livefire-fusion-poc/blob/main/docs/BUILD_STATUS.md): G2 moved to Nov 9, lab sections 5 to 12, the deny policy, timings
- [WS1 `infra/modules/loadgen.bicep`](https://github.com/livefire-team/livefire-fusion-poc/blob/main/infra/modules/loadgen.bicep) and [`docs/handoffs.md`](https://github.com/livefire-team/livefire-fusion-poc/blob/main/docs/handoffs.md): the load generator and its metric names
- [WS4 `docs/G2-HANDOFFS.md`](https://github.com/livefire-team/ws4-new/blob/main/docs/G2-HANDOFFS.md) and [`src/drill-runner/livefire/runner.py`](https://github.com/livefire-team/ws4-new/blob/main/src/drill-runner/livefire/runner.py): WS4's asks of WS5 and the stub runner
- WS2's evidence-store notes (team channel, Nov 5, and the final hand-off, Nov 6): account, protected appends, the evidence check, the deny policy off since Nov 6, `refused_by`, `simulated`, `trace_url`; and WS2's [`scripts/recover.sh`](https://github.com/livefire-team/livefire-fusion-poc/blob/main/scripts/recover.sh) and `deploy.sh policy` / `policy-off`
- The original WS5 Build Steps and Build Guide (Oct 30), and the LiveFire Fusion PoC Implementation Plan (Oct 20), attached in the chat
