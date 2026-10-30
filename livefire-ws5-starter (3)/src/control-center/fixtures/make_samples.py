"""Writes two sample drills in WS2's events.jsonl format. All numbers are synthetic."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path(__file__).parent / "drills"


def write(drill_id, steps):
    t = datetime(2026, 10, 26, 9, 30, 0, tzinfo=timezone.utc)
    lines = []
    for gap, action, result, details in steps:
        t += timedelta(seconds=gap)
        lines.append(json.dumps({"ts": t.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "drill_id": drill_id,
                                 "action": action, "result": result, "details": details}))
    (OUT / drill_id).mkdir(parents=True, exist_ok=True)
    (OUT / drill_id / "events.jsonl").write_text("\n".join(lines) + "\n")


def ref(label, verdict, step, orders, error=""):
    return {"label": label, "verdict": verdict, "last_step": step, "error": error,
            "orders_persisted": orders, "payment_auths_persisted": orders}


ERR = "We couldn't place your order (SecretUnavailable). Please try again."

# What WS2's G1 scripts produce: no agents yet, recovery by restart.
write("drill-sample-g1-manual", [
    (2, "wait.ok", "ok", {"seconds": 3}),
    (1, "reset", "ok", {}),
    (8, "referee.baseline", "PASS", ref("baseline", "PASS", "confirmed", 1)),
    (5, "fault.inject", "ok", {"model": "rbac", "entries_after": 0}),
    (74, "wait.forbidden", "ok", {"seconds": 74}),
    (12, "referee.during-fault", "FAIL", ref("during-fault", "FAIL", "place-order", 0, ERR)),
    (6, "fault.restore", "ok", {"model": "rbac", "entries_after": 1}),
    (96, "wait.ok", "ok", {"seconds": 96}),
    (11, "referee.after-restore", "FAIL", ref("after-restore", "FAIL", "place-order", 0, ERR)),
    (20, "wait.ok", "ok", {"seconds": 18}),
    (1, "reset", "ok", {}),
    (9, "referee.after-restart", "PASS", ref("after-restart", "PASS", "confirmed", 1)),
])


def tool(who, name, endpoint, decision="allow", summary=""):
    return (4, f"{who}.tool_call", decision, {"tool": name, "endpoint": endpoint, "decision": decision, "summary": summary})


def orders(per_min, failed, backlog):
    return (1, "metrics.orders", "ok", {"orders_per_min": per_min, "failed_total": failed, "backlog": backlog})


OPTIONS = [{"id": "broad", "label": "Grant Contributor on the subscription"},
           {"id": "narrow", "label": "Restore Key Vault Secrets User on the twin vault"}]

# The G3 target, including the events WS4 is proposed to emit.
write("drill-sample-full-loop", [
    (1, "drill.start", "ok", {"mode": "Supervised"}),
    orders(12, 0, 0),
    (8, "referee.baseline", "PASS", ref("baseline", "PASS", "confirmed", 1)),
    (5, "fault.inject", "ok", {"model": "rbac", "entries_after": 0}),
    (70, "wait.forbidden", "ok", {"seconds": 70}),
    orders(0, 9, 9),
    (12, "referee.during-fault", "FAIL", ref("during-fault", "FAIL", "place-order", 0, ERR)),
    tool("planner", "query_incident_telemetry", "planner", summary="403 on secret reads since the fault"),
    tool("planner", "get_identity_snapshot", "planner", summary="Key Vault Secrets User missing for id-order-app"),
    tool("planner", "search_incident_history", "planner"),
    (6, "planner.options", "ok", {"options": OPTIONS}),
    (1, "approval.requested", "ok", {"request_id": "apr-1", "options": OPTIONS}),
    (31, "approval.decided", "ok", {"request_id": "apr-1", "option_id": "broad", "option_label": OPTIONS[0]["label"],
                                    "approver": "Customer", "method": "passcode", "latency_s": 31}),
    (3, "recover.broad", "denied", {"code": "RequestDisallowedByPolicy"}),
    (1, "approval.requested", "ok", {"request_id": "apr-2", "options": OPTIONS[1:]}),
    (12, "approval.decided", "ok", {"request_id": "apr-2", "option_id": "narrow", "option_label": OPTIONS[1]["label"],
                                    "approver": "Customer", "method": "passcode", "latency_s": 12}),
    (2, "fault.restore", "ok", {"model": "rbac", "entries_after": 1}),
    (90, "wait.ok", "ok", {"seconds": 90}),
    orders(0, 31, 31),
    (11, "referee.after-restore", "FAIL", ref("after-restore", "FAIL", "place-order", 0, ERR)),
    tool("planner", "read_repository_context", "planner", summary="secret_provider.py"),
    tool("planner", "get_commit_history", "planner", summary="perf commit added a failure cache"),
    (8, "planner.brief", "ok", {"summary": "order path keeps a cached auth failure after access returns"}),
    tool("engineer", "query_incident_telemetry", "planner", decision="deny", summary="not in the Engineer's catalog"),
    tool("engineer", "create_candidate_branch", "engineer"),
    tool("engineer", "apply_patch", "engineer", summary="secret_provider.py: bounded retry with backoff"),
    tool("engineer", "write_regression_test", "engineer"),
    tool("engineer", "update_runbook", "engineer"),
    (2, "engineer.change_report", "ok", {"branch": "candidate/drill-sample-full-loop-1"}),
    (1, "usage", "ok", {"tokens": 38200, "est_cost_usd": 0.41}),
    (120, "ci.result", "pass", {}),
    (40, "deploy.revision", "ok", {"image": "ghcr.io/example/order-service:sha-0000000"}),
    (15, "referee.after-fix", "PASS", ref("after-fix", "PASS", "confirmed", 1)),
    orders(14, 31, 4),
    (60, "verdict", "VERIFIED", {}),
    (2, "drill.end", "ok", {}),
])
print("wrote", sorted(p.name for p in OUT.iterdir()))
