"""Stand-ins for the Ops Service, the two agents and GitHub, so the whole workflow runs before they exist.

The stubs write the same events, in the same shapes, as WS2's scripts and WS3's MCP server (see
fixtures/drills in the Control Center). Each scenario switch reproduces one test from blueprint A.5.
"""
import asyncio
import hashlib
import json
from pathlib import Path

from .contracts import ChangeReport, OpsResult, PlannerOutput
from .ports import CiStatus, DependencyError, EventLog, Usage

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
ERROR = "We couldn't place your order (SecretUnavailable). Please try again."


def _referee(label: str, passed: bool) -> dict:
    return {"label": label, "verdict": "PASS" if passed else "FAIL",
            "last_step": "confirmed" if passed else "place-order", "error": "" if passed else ERROR,
            "orders_persisted": 1 if passed else 0, "payment_auths_persisted": 1 if passed else 0}


class StubTwin:
    """The order service's behavior, reduced to four facts. The base image keeps a cached failure after
    access returns (the seeded weakness); a good candidate recovers on its own; any restart clears the cache."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.access, self.cached_failure, self.image, self.fixed = True, False, "base", False

    def customer_ok(self) -> bool:
        return self.access and not (self.cached_failure and not self.fixed)


class StubOps:
    def __init__(self, log: EventLog, twin: StubTwin | None = None, delay: float = 0.2, broad_allowed=False,
                 good_images: set[str] | None = None):
        self.log, self.twin, self.delay, self.broad_allowed = log, twin or StubTwin(), delay, broad_allowed
        self.good_images = good_images          # None: every candidate fixes the weakness

    async def call(self, action: str, **p) -> OpsResult:
        await asyncio.sleep(self.delay)
        t = self.twin
        if action in ("reset_twin", "kill"):
            t.reset()
            self.log.write("reset", "ok", {"by": "kill"} if action == "kill" else {})
            return OpsResult(action=action, result="ok")
        if action == "inject_fault":
            t.access, t.cached_failure = False, True
            self.log.write("fault.inject", "ok", {"model": "rbac", "entries_after": 0})
            return OpsResult(action=action, result="ok")
        if action == "restore_access":
            t.access = True
            self.log.write("fault.restore", "ok", {"model": "rbac", "entries_after": 1})
            return OpsResult(action=action, result="ok")
        if action == "attempt_broad_recovery":
            if self.broad_allowed:
                self.log.write("recover.broad", "UNEXPECTED_ALLOW", {"code": "Created"})
                return OpsResult(action=action, result="UNEXPECTED_ALLOW")
            self.log.write("recover.broad", "denied", {"code": "RequestDisallowedByPolicy"})
            return OpsResult(action=action, result="denied", details={"code": "RequestDisallowedByPolicy"})
        if action == "wait_for":
            self.log.write(f"wait.{p['state']}", "ok", {"seconds": 3})
            return OpsResult(action=action, result="ok", details={"seconds": 3})
        if action == "run_referee":
            result = _referee(p["label"], t.customer_ok())
            self.log.write(f"referee.{p['label']}", result["verdict"], result)
            return OpsResult(action=action, result=result["verdict"], details=result)
        if action == "deploy_revision":
            t.image, t.cached_failure = p["image"], False                 # a new revision is also a restart
            t.fixed = self.good_images is None or p["image"] in self.good_images
            self.log.write("deploy.revision", "ok", {"image": p["image"], "revision": "stub-rev"})
            return OpsResult(action=action, result="ok", details={"image": p["image"]})
        raise DependencyError(f"stub Ops has no action {action}")


def _sha(*parts) -> str:
    return hashlib.sha1("/".join(map(str, parts)).encode()).hexdigest()


class StubAgents:
    """Returns the fixture outputs, and writes the tool-call events the MCP server would write."""

    def __init__(self, log: EventLog, scenario: str = "T-01", delay: float = 0.2):
        self.log, self.scenario, self.delay = log, scenario, delay

    def _tool(self, who, tool, endpoint, decision="allow", summary=""):
        self.log.write(f"{who}.tool_call", decision, {"tool": tool, "endpoint": endpoint, "decision": decision,
                                                       "summary": summary, "stub": True})

    def _fixture(self, name: str, drill_id: str) -> dict:
        return json.loads((FIXTURES / name).read_text().replace("drill-fixture", drill_id))

    async def planner(self, task: dict) -> tuple[PlannerOutput, Usage]:
        await asyncio.sleep(self.delay)
        if self.scenario == "T-10":
            raise DependencyError("Planner MCP endpoint unavailable (stub)")
        drill = task["drill_id"]
        if task["task"] == "investigate":
            self._tool("planner", "query_incident_telemetry", "planner", summary="403 on secret reads since the fault")
            if self.scenario != "T-05":
                self._tool("planner", "get_identity_snapshot", "planner",
                           summary="Key Vault Secrets User missing for id-order-app")
            self._tool("planner", "search_incident_history", "planner",
                       summary="3 records, one with an embedded instruction" if self.scenario == "T-08" else "3 records")
            data = self._fixture("planner-options.json", drill)
            if self.scenario == "T-05":
                data["recovery_options"]["confidence"] = 0.4
                data["recovery_options"]["evidence"] = [e for e in data["recovery_options"]["evidence"]
                                                        if e["source"] != "identity"]
        else:
            self._tool("planner", "read_repository_context", "planner", summary="secret_provider.py")
            self._tool("planner", "get_commit_history", "planner", summary="perf commit added a failure cache")
            data = self._fixture("planner-brief.json", drill)
        return PlannerOutput.model_validate(data), Usage("planner", 14000, 2200, 4, "stub-response")

    async def engineer(self, task: dict) -> tuple[ChangeReport, Usage]:
        await asyncio.sleep(self.delay)
        drill, attempt = task["drill_id"], task["attempt"]
        self._tool("engineer", "create_candidate_branch", "engineer")
        if self.scenario == "T-04" and attempt == 1:
            self._tool("engineer", "apply_patch", "engineer", decision="deny", summary="journeys/ is protected")
        self._tool("engineer", "apply_patch", "engineer", summary="secret_provider.py: bounded retry with backoff")
        self._tool("engineer", "write_regression_test", "engineer")
        self._tool("engineer", "update_runbook", "engineer")
        data = self._fixture("change-report.json", drill)
        data["attempt"], data["branch"] = attempt, f"candidate/{drill}-{attempt}"
        data["commits"] = [_sha(drill, attempt, n)[:12] for n in range(3)]
        return ChangeReport.model_validate(data), Usage("engineer", 18000, 6000, 5, "stub-response")


class StubRepo:
    """GitHub as the runner sees it. CI fails attempt 1 in T-06 and every attempt in T-07."""

    def __init__(self, scenario: str = "T-01", extra_paths: list[str] | None = None):
        self.scenario, self.extra_paths, self.polls, self.prs = scenario, extra_paths or [], {}, []

    async def head_sha(self, branch: str) -> str:
        drill, attempt = branch.removeprefix("candidate/").rsplit("-", 1)
        return _sha(drill, attempt, 2)

    async def changed_paths(self, base: str, branch: str) -> list[str]:
        return sorted(["src/order-service/app/secret_provider.py", "src/order-service/tests/test_secret_recovery.py",
                       "runbooks/order-service-keyvault-recovery.md", *self.extra_paths])

    async def ci_status(self, branch: str, commit: str) -> CiStatus:
        self.polls[branch] = self.polls.get(branch, 0) + 1
        if self.polls[branch] < 2:
            return CiStatus("pending", commit)
        attempt = int(branch.rsplit("-", 1)[1])
        failing = self.scenario == "T-07" or (self.scenario == "T-06" and attempt == 1)
        url = f"https://github.com/example/livefire/actions/runs/{attempt}"
        if failing:
            return CiStatus("fail", commit, url, [{"job": "test-build-push", "steps": ["Unit tests"]}])
        return CiStatus("pass", commit, url)

    async def open_pull_request(self, branch: str, title: str, body: str) -> str:
        self.prs.append({"branch": branch, "title": title, "body": body})
        return f"https://github.com/example/livefire/pull/{len(self.prs)}"


async def auto_approve(store, drill_id: str, script: list[str], poll: float = 0.05):
    """Plays the person at the Control Center: answers each approval.requested with the next scripted choice.
    'kill' presses the kill switch instead. Writes exactly what the Control Center's API writes."""
    from .ports import now_iso
    answered, choices = set(), list(script)
    while True:
        for e in store.read(drill_id):
            d = e.get("details") or {}
            if e["action"] != "approval.requested" or d.get("request_id") in answered:
                continue
            answered.add(d["request_id"])
            ids = [o["id"] for o in d.get("options", [])]
            choice = next((c for c in choices if c in ids or c == "kill"), ids[0])
            if choice in choices:
                choices.remove(choice)
            if choice == "kill":
                store.append(drill_id, {"ts": now_iso(), "drill_id": drill_id, "action": "kill.requested",
                                        "result": "ok", "details": {"by": "Tester", "method": "test"}})
                continue
            label = next(o["label"] for o in d["options"] if o["id"] == choice)
            store.append(drill_id, {"ts": now_iso(), "drill_id": drill_id, "action": "approval.decided", "result": "ok",
                                    "details": {"request_id": d["request_id"], "option_id": choice,
                                                "option_label": label, "approver": "Tester", "method": "test",
                                                "latency_s": 0.1}})
        await asyncio.sleep(poll)
