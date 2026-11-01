"""Writes the two sample drills in WS2's events.jsonl format. The numbers are synthetic.

    drill-sample-g1-manual   what WS2's scripts produce at G1 (no agents; recovered by restart)
    drill-sample-full-loop   the G3 target: agents, decisions, False Green, candidate fix, VERIFIED

Run again whenever contracts/event-catalog.md changes:  python src/control-center/fixtures/make_samples.py
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent / "drills"
START = datetime(2026, 10, 26, 9, 30, 0, tzinfo=timezone.utc)

BROAD = {"id": "broad", "label": "Grant the app identity Owner on the twin vault", "broad": True}
NARROW = {"id": "narrow", "label": "Restore Key Vault Secrets User on the twin vault only"}


class Drill:
    def __init__(self, drill_id: str):
        self.drill_id = drill_id
        self.t = START
        self.events = []

    def at(self, seconds: float):
        self.t = START + timedelta(seconds=seconds)
        return self

    def add(self, action, result="ok", **details):
        ts = self.t.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        self.events.append({"ts": ts, "drill_id": self.drill_id, "action": action,
                            "result": result, "details": details})
        self.t += timedelta(seconds=1)
        return self

    def write(self):
        folder = OUT / self.drill_id
        folder.mkdir(parents=True, exist_ok=True)
        self.events.sort(key=lambda e: e["ts"])
        with open(folder / "events.jsonl", "w", encoding="utf-8") as f:
            for e in self.events:
                f.write(json.dumps(e) + "\n")
        return folder / "events.jsonl"


def referee_pass(d, label):
    d.add(f"referee.{label}", "PASS", last_step="confirmation", error=None, orders_persisted=1)


def referee_fail(d, label, error):
    d.add(f"referee.{label}", "FAIL", last_step="checkout", error=error, orders_persisted=0)


def g1_manual():
    d = Drill("drill-sample-g1-manual")
    d.at(0).add("reset")
    d.at(20).add("wait.ok", seconds=3)
    d.at(60); referee_pass(d, "baseline")
    d.at(120).add("fault.inject", model="rbac", entries_after=0)
    d.at(215).add("wait.forbidden", seconds=94)
    d.at(260); referee_fail(d, "during-fault", "OrderFailed: 503 from POST /orders")
    d.at(330).add("recover.broad", "denied", code="RequestDisallowedByPolicy")
    d.at(360).add("fault.restore", model="rbac", entries_after=1)
    d.at(442).add("wait.ok", seconds=81)
    d.at(500); referee_fail(d, "after-restore", "SecretUnavailable: cached 403 from Key Vault")
    d.at(600).add("reset")
    d.at(660); referee_pass(d, "after-reset")
    return d.write()


def full_loop():
    d = Drill("drill-sample-full-loop")
    # orders per minute, failures and backlog from WS1's load generator, every 30 s
    for i in range(0, 2400, 30):
        if i < 140:
            opm, failed, backlog = 12.0, 0, 0
        elif i < 1840:  # fault, then the False Green, until the candidate is live
            opm, failed, backlog = 0.0, (i - 140) // 5, (i - 140) // 5
        else:  # backlog drains after the verified fix
            drained = (i - 1840) // 30 * 60
            opm, failed, backlog = 18.0, 340, max(340 - drained, 0)
        d.at(i + 0.5).add("metrics.orders", orders_per_min=opm, failed_total=failed, backlog=backlog)

    d.at(0).add("drill.start", mode="supervised")
    d.at(20).add("wait.ok", seconds=2)
    d.at(60); referee_pass(d, "baseline")
    d.at(140).add("fault.inject", model="rbac", entries_after=0)
    d.at(232).add("wait.forbidden", seconds=91)
    d.at(280); referee_fail(d, "during-fault", "OrderFailed: 503 from POST /orders")

    calls = [
        ("query_incident_telemetry", "Exceptions spike: 403 Forbidden from Key Vault since fault start"),
        ("get_identity_snapshot", "App identity has no Key Vault Secrets User assignment on the twin vault"),
        ("search_incident_history", "INC-2041: orders failed after secret rotation, fixed by restart"),
        ("read_runbook", "Runbook v1: restore access, confirm /health is green"),
        ("get_commit_history", "secret_provider.py: 'cache secret failures to stop hammering Key Vault'"),
    ]
    t = 300
    for tool, summary in calls:
        d.at(t).add("planner.tool_call", "allow", tool=tool, endpoint="/planner", decision="allow", summary=summary)
        t += 25
    d.at(t).add("planner.tool_call", "deny", tool="apply_patch", endpoint="/engineer", decision="deny",
                summary="cross-role call with Planner credentials refused (403)")
    d.at(440).add("planner.options", options=[BROAD, NARROW])
    d.at(445).add("approval.requested", request_id="req-1", options=[BROAD, NARROW])
    d.at(487).add("approval.decided", request_id="req-1", option_id="broad", option_label=BROAD["label"],
                  approver="Customer guest", method="passcode", latency_s=42.0)
    d.at(495).add("recover.broad", "denied", code="RequestDisallowedByPolicy")
    d.at(500).add("approval.requested", request_id="req-2", options=[NARROW])
    d.at(518).add("approval.decided", request_id="req-2", option_id="narrow", option_label=NARROW["label"],
                  approver="Customer guest", method="passcode", latency_s=18.0)
    d.at(525).add("recover.narrow")
    d.at(526).add("fault.restore", model="rbac", entries_after=1)
    d.at(604).add("wait.ok", seconds=78)
    d.at(660); referee_fail(d, "after-restore", "SecretUnavailable: cached 403 from Key Vault")

    d.at(700).add("planner.tool_call", "allow", tool="query_incident_telemetry", endpoint="/planner",
                  decision="allow", summary="Dependency probe 200, but order path still raises SecretUnavailable")
    d.at(730).add("planner.tool_call", "allow", tool="read_repository_context", endpoint="/planner",
                  decision="allow", summary="SecretProvider marks the secret unavailable on 403 and never retries")
    d.at(790).add("planner.brief", summary="Stale failure cache in secret_provider.py; retry with backoff after 403")

    eng = [
        ("read_allowed_files", "Read secret_provider.py and tests/"),
        ("create_candidate_branch", "Created candidate/drill-sample-full-loop-1"),
        ("apply_patch", "Bounded exponential backoff with a half-open check (1 file, 64 lines)"),
        ("write_regression_test", "tests/test_secret_recovery.py: 403 then success, order succeeds"),
        ("update_runbook", "Runbook: run the customer journey after access returns"),
    ]
    t = 830
    for tool, summary in eng:
        d.at(t).add("engineer.tool_call", "allow", tool=tool, endpoint="/engineer", decision="allow", summary=summary)
        t += 40
    d.at(t).add("engineer.tool_call", "deny", tool="apply_patch", endpoint="/engineer", decision="deny",
                summary="journeys/ is on the Engineer deny list")
    d.at(1060).add("engineer.change_report", branch="candidate/drill-sample-full-loop-1")
    d.at(1500).add("ci.result", "pass", run_url="https://github.com/livefire-team/livefire/actions/runs/1")
    d.at(1510).add("engineer.tool_call", "allow", tool="get_ci_results", endpoint="/engineer", decision="allow",
                   summary="Tests, CodeQL, gitleaks, pip-audit and Trivy passed")
    d.at(1700).add("deploy.revision", image="ghcr.io/livefire-team/order-service:sha-3f9c2e1")
    d.at(1840); referee_pass(d, "after-deploy")
    d.at(1900).add("verdict", "VERIFIED")
    d.at(1905).add("usage", tokens=48210, est_cost_usd=0.62)
    d.at(2395).add("drill.end")  # after the backlog has drained
    return d.write()


if __name__ == "__main__":
    for path in (g1_manual(), full_loop()):
        print("wrote", path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path)
