# LiveFire event catalog (v1)

Owner: WS5 (Control Center). Changes need the lead's approval and a version bump.
Send to: WS4 (workflow), WS2 (referee, fault, Ops Service), WS1 (load generator), WS3 (MCP).

The Control Center reads exactly one thing: a drill's `events.jsonl`. It never calls
another service. If you write events in this format, they show up on screen.

## 1. Line format

One JSON object per line, UTF-8, each line ending in `\n`:

```json
{"ts": "2026-10-26T09:30:05.000Z", "drill_id": "drill-...", "action": "fault.inject", "result": "ok", "details": {}}
```

| Field      | Rule |
|------------|------|
| `ts`       | ISO 8601 UTC with `Z`, millisecond precision |
| `drill_id` | Must match `^drill-[A-Za-z0-9-]{1,64}$`, and the folder or blob it lives in |
| `action`   | Lower-case, dot-separated, from the tables below |
| `result`   | String, from the tables below |
| `details`  | JSON object; `{}` when empty |

Where the file lives:

* Local / G1: `evidence/<drill-id>/events.jsonl`
* Lab / G2+: Blob container `evidence`, **append blob** `<drill-id>/events.jsonl`
  (append blobs are what allow protected-append immutability).

Always append, never rewrite. The easiest way to get all of this right is to write through
the shared module, `src/shared/livefire_events.py`:

```python
from livefire_events import store_from_env, make_event
store = store_from_env()   # reads EVENT_SOURCE=dir:<path> or blob:<account-url>/<container>
store.append("drill-abc", make_event("drill-abc", "fault.inject", "ok", {"model": "rbac", "entries_after": 0}))
```

From a shell script:

```bash
python src/shared/livefire_events.py append "$DRILL_ID" fault.inject ok '{"model":"rbac","entries_after":0}'
```

## 2. Events WS2's scripts already write

| action | result | details the UI uses |
|---|---|---|
| `fault.inject`, `fault.restore` | `ok` | `model`, `entries_after` |
| `wait.forbidden`, `wait.ok` | `ok` or `timeout` | `seconds` |
| `referee.<label>`, e.g. `referee.baseline`, `referee.during-fault`, `referee.after-restore` | `PASS` or `FAIL` | `last_step`, `error`, `orders_persisted` |
| `recover.broad` | `denied` or `UNEXPECTED_ALLOW` | `code` |
| `recover.narrow` | `ok` | none |
| `reset` | `ok` | none |

## 3. Events proposed for WS4, WS3 and WS1

The UI already handles all of these. WS4 can rename any of them, as long as this table is
updated the same day and the sample drills are regenerated.

| action | Emitted by | result | details |
|---|---|---|---|
| `drill.start`, `drill.end` | WS4 workflow | `ok` | `mode` (`supervised`, `autonomous`, `advisory`) |
| `planner.tool_call`, `engineer.tool_call` | WS4, from MCP spans | `allow` or `deny` | `tool`, `endpoint`, `decision`, `summary`, optional `trace_url` |
| `planner.options` | WS4 | `ok` | `options`: list of `{id, label}`; add `"broad": true` to the over-broad option |
| `approval.requested` | WS4 gate | `ok` | `request_id`, `options` (same shape as above) |
| `approval.decided` | **Control Center** | `ok` | `request_id`, `option_id`, `option_label`, `approver`, `method`, `latency_s` |
| `planner.brief` | WS4 | `ok` | `summary` |
| `engineer.change_report` | WS4 | `ok` | `branch` |
| `ci.result` | WS4 | `pass` or `fail` | `run_url` |
| `deploy.revision` | WS2 Ops | `ok` | `image` |
| `verdict` | WS4 | `VERIFIED`, `FALSE_GREEN` or `ESCALATED_*` | none |
| `usage` | WS4 | `ok` | `tokens`, `est_cost_usd` |
| `metrics.orders` | WS1 load generator, every 30 s | `ok` | `orders_per_min`, `failed_total`, `backlog` |
| `kill.requested` | **Control Center** | `ok` | `by`, `method` |
| `package.ready` (G3) | WS4 | `ok` | `evidence_url`, `pr_url` |

Links (`trace_url`, `run_url`, `evidence_url`, `pr_url`) are shown only if they start with an
allowed prefix (default: `https://ai.azure.com/`, `https://portal.azure.com/`,
`https://github.com/`). Set `LINK_PREFIXES` on the Control Center to change the list.

## 4. The approval API

1. The workflow (or the G2 bridge script) appends `approval.requested`.
2. The Control Center shows the options. A click sends
   `POST /drills/{drill_id}/approvals` with `{"request_id": "...", "option_id": "..."}`.
3. If that request is still open, the Control Center appends `approval.decided` with the
   signed-in person's name and returns **202**.
4. If no request with that ID is open (already decided, or never asked), it returns **409**.
   An option that wasn't offered returns **400**.
5. The Control Center **never carries out the action.** WS4's gate waits for
   `approval.decided`, then asks the Ops Service, and Azure Policy has the final say. That is
   why choosing the broad option produces a visible `recover.broad denied`.

WS4 to agree with WS5: how long the gate waits before escalating (`ESCALATED_TIMEOUT`).

## 5. The kill switch

`POST /drills/{drill_id}/kill` appends `kill.requested` (`by`, `method`) and returns 202.
WS4's workflow (from G3) or the G2 watcher script reacts by running WS2's reset.

## 6. Events to stages

| Stage | Events |
|---|---|
| Baseline | `drill.start`, `referee.baseline`, and `reset` or `wait.ok` before any fault |
| Fault | `fault.inject`, `wait.forbidden`, `referee.during-fault` |
| Investigate | `planner.tool_call` before the False Green, `planner.options` |
| Decide | `approval.*`, `recover.broad` |
| Restore | `fault.restore`, `recover.narrow`, `wait.ok` after the fault |
| False Green | `referee.after-restore` with `FAIL` |
| Repair | `planner.*` after the False Green, `engineer.*`, `ci.result` |
| Verify | `deploy.revision`, later referee runs, `reset` after the False Green |
| Evidence | `verdict`, `package.ready`, `drill.end` |

`metrics.orders`, `usage` and `kill.requested` don't move the stage.

## 7. Banner rules

1. A `verdict` event always wins.
2. Otherwise, infrastructure green while the latest customer journey fails: **FALSE GREEN**.
3. A failing journey alone: **CUSTOMERS CAN'T ORDER**.
4. A passing journey after a fault: **CUSTOMERS ORDERING AGAIN**.
5. Otherwise: **HEALTHY**.

Only the workflow's verdict can say VERIFIED. A recovery by restart is honest progress, not a
verified fix.
