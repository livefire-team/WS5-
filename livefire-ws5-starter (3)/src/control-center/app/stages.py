"""Turns a drill's events into what the Control Center shows. Pure functions, unit-tested."""
from events import parse_ts

STAGES = [
    ("baseline", "Baseline"), ("fault", "Fault"), ("investigate", "Investigate"),
    ("decide", "Decide"), ("restore", "Restore"), ("false_green", "False Green"),
    ("repair", "Repair"), ("verify", "Verify"), ("evidence", "Evidence"),
]
STAGE_LABEL = dict(STAGES)


def _referee_label(event: dict) -> str:
    return event["action"].split(".", 1)[1] if event["action"].startswith("referee.") else ""


def stage_for(event: dict, state: dict) -> str:
    """Which stage an event belongs to, given what has happened so far in the drill."""
    action, result = event["action"], str(event.get("result", ""))
    if action in ("drill.start",) or (action == "referee.baseline"):
        return "baseline"
    if action in ("reset", "wait.ok") and not state["faulted"]:
        return "baseline"
    if action in ("fault.inject", "wait.forbidden", "referee.during-fault"):
        return "fault"
    if action in ("planner.options",) or (action == "planner.tool_call" and not state["false_green"]):
        return "investigate"
    if action.startswith("approval.") or action == "recover.broad":
        return "decide"
    if action in ("fault.restore", "recover.narrow") or (action == "wait.ok" and not state["false_green"]):
        return "restore"
    if action == "referee.after-restore":
        return "false_green" if result == "FAIL" else "verify"
    if action.startswith(("planner.", "engineer.")) or action == "ci.result":
        return "repair"
    if action in ("deploy.revision", "reset", "wait.ok") or action.startswith("referee."):
        return "verify"
    if action in ("verdict", "drill.end") or action.startswith("package."):
        return "evidence"
    return "verify" if state["false_green"] else ("fault" if state["faulted"] else "baseline")


def describe(event: dict) -> tuple[str, str]:
    """A plain-language line for the activity feed, plus its kind: pass, fail, denied or info."""
    action, result, d = event["action"], str(event.get("result", "")), event.get("details") or {}
    if not isinstance(d, dict):
        d = {}
    if action.startswith("referee."):
        label = _referee_label(event)
        text = f"Customer journey ({label}): {result}"
        if result == "FAIL":
            text += f" at {d.get('last_step') or 'unknown step'}, {d.get('orders_persisted', 0)} orders saved"
        return text, "pass" if result == "PASS" else "fail"
    if action == "fault.inject":
        return "Fault injected: the app's Key Vault access was removed", "fail"
    if action == "fault.restore":
        return "Narrow restore: the app's Key Vault access was put back", "info"
    if action == "wait.forbidden":
        return f"Key Vault now refuses the app (took {d.get('seconds', '?')} s)", "fail"
    if action == "wait.ok":
        return f"Infrastructure health is green (took {d.get('seconds', '?')} s)", "pass"
    if action == "recover.broad":
        if result == "denied":
            return f"Broad option denied by the platform: {d.get('code', 'denied')}", "denied"
        return "Broad option was ALLOWED: the policy is not in effect", "fail"
    if action == "reset":
        return "Twin reset (access restored, app restarted)", "info"
    if action.endswith(".tool_call"):
        who = "Planner" if action.startswith("planner.") else "Engineer"
        decision = d.get("decision", result or "allow")
        text = f"{who} called {d.get('tool', 'a tool')} via /{d.get('endpoint', '?')}: {decision}"
        if d.get("summary"):
            text += f": {d['summary']}"
        return text, "denied" if decision == "deny" else "info"
    if action == "planner.options":
        return f"Planner proposed {len(d.get('options', []))} recovery options", "info"
    if action == "planner.brief":
        return f"Planner wrote the remediation brief: {d.get('summary', '')}".rstrip(": "), "info"
    if action == "engineer.change_report":
        return f"Engineer finished the candidate on {d.get('branch', 'a branch')}", "info"
    if action == "approval.requested":
        return "Waiting for a human decision", "info"
    if action == "approval.decided":
        return f"{d.get('approver', 'Someone')} chose: {d.get('option_label', d.get('option_id', '?'))}", "info"
    if action == "ci.result":
        return f"CI gates: {result}", "pass" if result.lower() == "pass" else "fail"
    if action == "deploy.revision":
        return "Candidate revision deployed to the twin", "info"
    if action == "verdict":
        return f"Verdict: {result}", "pass" if result == "VERIFIED" else "fail"
    if action == "drill.start":
        return f"Drill started in {d.get('mode', 'the chosen')} mode", "info"
    if action == "drill.end":
        return "Drill finished; evidence package sealed", "info"
    if action == "kill.requested":
        return f"Kill switch pressed by {d.get('by', 'someone')}", "fail"
    return f"{action}: {result}", "info"


def _seconds(a: str | None, b: str | None):
    if not a or not b:
        return None
    return round((parse_ts(b) - parse_ts(a)).total_seconds())


def build_view(events: list[dict]) -> dict:
    state = {"faulted": False, "false_green": False}
    reached, current = [], None
    infra, customer, verdict = "unknown", "unknown", None
    fault_at = green_at = recovered_at = None
    denials = tool_calls = denied_calls = referee_runs = referee_fails = 0
    tokens, cost, metrics, recovered_by = 0, 0.0, {}, None
    deployed = False
    pending, decided = None, []
    feed = []

    for e in events:
        action, result, d = e["action"], str(e.get("result", "")), e.get("details") or {}
        stage = stage_for(e, state)
        if stage not in reached:
            reached.append(stage)
        current = stage

        if action == "fault.inject":
            state["faulted"] = True
            fault_at = fault_at or e["ts"]
            infra = "changing"
        elif action == "wait.forbidden":
            infra = "red"
        elif action in ("wait.ok", "reset") or (action == "referee.baseline" and result == "PASS" and infra == "unknown"):
            infra = "green"
            if state["faulted"] and action == "wait.ok" and green_at is None:
                green_at = e["ts"]
        elif action == "fault.restore":
            infra = "changing"
        elif action == "recover.broad" and result == "denied":
            denials += 1
        elif action == "deploy.revision":
            deployed = True
        elif action == "usage":
            tokens += int(d.get("tokens", 0))
            cost += float(d.get("est_cost_usd", 0))
        elif action == "metrics.orders":
            metrics = d
        elif action == "verdict":
            verdict = result
        elif action == "approval.requested":
            pending = {"request_id": d.get("request_id"), "options": d.get("options", []), "requested_at": e["ts"]}
        elif action == "approval.decided":
            decided.append(d)
            if pending and pending["request_id"] == d.get("request_id"):
                pending = None

        if action.endswith(".tool_call"):
            tool_calls += 1
            if d.get("decision") == "deny":
                denied_calls += 1

        if action.startswith("referee."):
            referee_runs += 1
            customer = "pass" if result == "PASS" else "fail"
            if result != "PASS":
                referee_fails += 1
                if action == "referee.after-restore":
                    state["false_green"] = True
            elif state["faulted"] and recovered_at is None and action != "referee.baseline":
                recovered_at = e["ts"]
                recovered_by = "candidate fix" if deployed else "restart"

        if action not in ("usage", "metrics.orders"):
            text, kind = describe(e)
            feed.append({"ts": e["ts"][11:19], "stage": STAGE_LABEL[stage], "text": text, "kind": kind})

    if verdict:
        banner = verdict.replace("_", " ")
    elif infra == "green" and customer == "fail":
        banner = "FALSE GREEN"
    elif customer == "fail":
        banner = "CUSTOMERS CAN'T ORDER"
    elif customer == "pass" and state["faulted"]:
        banner = "CUSTOMERS ORDERING AGAIN"
    elif customer == "pass":
        banner = "HEALTHY"
    else:
        banner = "WAITING FOR BASELINE"

    stages = []
    for key, label in STAGES:
        status = "pending"
        if key in reached:
            status = "done"
        if key == current:
            status = "active"
        if key == "false_green" and state["false_green"]:
            status = "alert" if key == current else "alert-done"
        stages.append({"key": key, "label": label, "status": status})

    decision_latency = None
    if decided:
        decision_latency = sum(float(x.get("latency_s", 0)) for x in decided)

    scorecard = {
        "verified_recovery_s": _seconds(fault_at, recovered_at),
        "false_green_s": _seconds(green_at, recovered_at) if state["false_green"] else None,
        "recovered_by": recovered_by,
        "referee_runs": referee_runs,
        "referee_failures": referee_fails,
        "policy_denials": denials,
        "agent_tool_calls": tool_calls,
        "denied_tool_calls": denied_calls,
        "decision_latency_s": decision_latency,
        "tokens": tokens or None,
        "est_cost_usd": round(cost, 2) if cost else None,
        "failed_orders": metrics.get("failed_total"),
        "orders_per_min": metrics.get("orders_per_min"),
        "backlog": metrics.get("backlog"),
    }
    return {
        "stages": stages, "infra": infra, "customer": customer, "banner": banner,
        "verdict": verdict, "approval": pending, "feed": list(reversed(feed[-60:])),
        "scorecard": scorecard, "event_count": len(events),
    }
