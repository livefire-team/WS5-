# Presenter notes

At most three sentences per segment. Say the **bold line** word for word.

**Open with both honesty lines:**
- This is a controlled identity disruption that rehearses a cyber-recovery control, not a simulated attack.
- The verdict covers this scenario and this journey only.

## 0–5 · Baseline (HEALTHY)
**"We measure recovery in customer transactions."** The left panel is what dashboards show; the
right panel is an independent referee placing a real order. Right now both agree, and the mode is
Supervised, so nothing changes without a human.

## 5–10 · Fault (CUSTOMERS CAN'T ORDER)
We've removed one permission: the app's access to its Key Vault, inside a disposable twin. The
replicas still say Running, but orders per minute drop to zero and the backlog climbs.

## 10–17 · Investigate
The Planner investigates through read-only tools: telemetry, the identity snapshot, past incidents,
the runbook and commit history. Each call is checked on the server; you just saw one cross-role call
refused. It comes back with two options and the evidence for each.

## 17–22 · Decide
You choose. If you pick the broad option, watch what happens: **"Policy, not the model, decides
what runs."** The narrow option restores exactly one role.

## 22–27 · False Green
Every dashboard is green again. The referee still can't place an order, and the database shows no
order saved. **"This is the False Green."**

## 27–35 · Repair
The Planner revises its view and writes a brief; the Engineer works only on a candidate branch,
with a regression test and a runbook change. It can't touch the acceptance test; that call was
just denied.

## 35–40 · Verify (VERIFIED)
The candidate deploys as a new revision, and the same unchanged journey places an order. **"Evidence,
not the agent, declares recovery."** VERIFIED comes from a deterministic rule in code, not from a model.

## 40–45 · Evidence
The scorecard: verified recovery time, how long the False Green lasted, decision time, denials,
tool calls, tokens and cost. The pull request stays unmerged; a human reviews it.

## If something goes wrong
- Stall over 2 minutes: "Access changes in Azure can take a few minutes to propagate; the workflow
  polls rather than guessing, and you can see it waiting on the left."
- Switching to replay: "The lab has stopped, so here is the run we recorded on <date>. Same evidence,
  replayed at five times speed." The badge on screen says Replay.

## Q&A: five answers, under 30 seconds each
1. **Does this touch production?** No. Only a disposable twin, and no production identity exists.
2. **What if the agent is wrong?** An independent referee decides, budgets cap the attempts, and a
   human reviews the pull request.
3. **What stops it giving itself admin rights?** Azure Policy, a limited RBAC role and separate MCP
   scopes. You just watched the broad option get denied.
4. **Would it work on our application?** It needs a readiness check: telemetry, a repeatable
   customer journey, isolation and repository access.
5. **What does a drill cost?** Point to the token and cost tiles on the scorecard.
