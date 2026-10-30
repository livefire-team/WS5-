# Event catalog, v1.0

Every line of `evidence/<drill_id>/events.jsonl` is `{"ts","drill_id","action","result","details"}`.
Writers keep the events they already write: WS2's Ops Service (`reset`, `fault.inject`, `fault.restore`,
`wait.ok`, `wait.forbidden`, `recover.broad`, `referee.<label>`, `deploy.revision`), WS3's MCP server
(`planner.tool_call`, `engineer.tool_call`), the Control Center (`approval.decided`, `kill.requested`) and
WS1's load generator (`metrics.orders`).

Referee labels: `baseline`, `during-fault`, `after-restore`, `after-fix`, `after-refault` (new: the durability
check), `after-restart` (G1 manual drills only).

Events the Drill Runner writes:

| Action | Result | Details |
| --- | --- | --- |
| drill.start | ok | mode, scenario, policy, agents (name:version), prompt_sha256, model_deployment, contract_version, runner_version |
| planner.options | ok | options [{id, label}], recommended, confidence, hypothesis, response_id |
| approval.requested | ok | request_id (apr-N), purpose (recovery, deploy, acceptance), options [{id, label}] |
| approval.preauthorized | ok | action, mode, policy_version |
| approval.decided | timeout | only when nobody chose in time: request_id, option_id "timeout" |
| planner.brief | ok | summary, confidence, acceptance_criteria (count), response_id |
| engineer.change_report | ok | branch, attempt, commits, changed_paths (the branch's real diff), response_id |
| ci.result | pass, fail | attempt, branch, commit (full SHA), run_url, failed [{job, steps}] |
| usage | ok | agent, tokens, input_tokens, output_tokens, tool_calls, est_cost_usd, response_id |
| verdict | terminal state | reason, inputs, attempts, tokens, est_cost_usd |
| package.ready | ok, error | path, sha256, events_sha256 |
| package.pr | ok, rejected, no_decision, error | url, branch, approver |
| drill.end | ok, cleanup_failed | terminal, cleanup, pr_url |
