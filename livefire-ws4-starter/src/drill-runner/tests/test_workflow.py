"""Workflow integration tests (blueprint A.8): the real graph on stubs, one test per scenario and escalation."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from livefire.contracts import AutonomyPolicy, DrillConfig, Mode, Verdict
from livefire.events import DirEventStore
from livefire.ports import EventLog
from livefire.runner import run_drill
from livefire.stubs import StubAgents, StubOps, StubRepo, auto_approve
from livefire.workflow import Ci, Run

Ci.poll_s = 0.01
LATER = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()


def make(tmp_path, scenario="T-01", mode=Mode.SUPERVISED, prove=True, ops=None, agents=None, repo=None, **budgets):
    store = DirEventStore(str(tmp_path))
    drill = f"drill-test-{scenario.lower()}"
    policy = AutonomyPolicy(mode=mode, prove_durable=prove, expires_at=LATER)
    for k, v in budgets.items():
        setattr(policy.budgets, k, v)
    cfg = DrillConfig(drill_id=drill, scenario=scenario, policy=policy, image_repo="ghcr.io/example/order-service")
    log = EventLog(store, drill)
    run = Run(cfg=cfg, ops=ops or StubOps(log, delay=0), agents=agents or StubAgents(log, scenario, delay=0),
              repo=repo or StubRepo(scenario), log=log, meta={"agents": {"planner": "stub", "engineer": "stub"}},
              price_per_mtok=(1.25, 10.0))
    return run, store


def drill(run, store, script=(), nobody=False):
    async def go():
        approver = None if nobody else asyncio.create_task(auto_approve(store, run.cfg.drill_id, list(script), poll=0.01))
        try:
            return await run_drill(run, poll_s=0.01)
        finally:
            if approver:
                approver.cancel()
    return asyncio.run(go())


def actions(store, run):
    return [(e["action"], e["result"]) for e in store.read(run.cfg.drill_id)]


def test_t01_supervised_full_loop(tmp_path):
    run, store = make(tmp_path)
    s = drill(run, store, ["broad", "narrow", "deploy", "open_pr"])
    assert s.terminal == Verdict.VERIFIED, s.reason
    seen = actions(store, run)
    for expected in [("recover.broad", "denied"), ("referee.after-restore", "FAIL"), ("planner.brief", "ok"),
                     ("ci.result", "pass"), ("deploy.revision", "ok"), ("referee.after-fix", "PASS"),
                     ("referee.after-refault", "PASS"), ("verdict", "VERIFIED"), ("package.ready", "ok"),
                     ("package.pr", "ok"), ("drill.end", "ok")]:
        assert expected in seen, expected
    assert seen.index(("verdict", "VERIFIED")) < seen.index(("package.pr", "ok"))
    assert s.attempt == 1 and s.pr_url and run.state is s
    package = json.loads((tmp_path / run.cfg.drill_id / "package.json").read_text())
    assert package["terminal_state"] == "VERIFIED" and all(
        v for k, v in package["verdict_inputs"].items() if k != "reasons")
    assert package["counts"]["policy_denials"] == 1 and package["usage"]["est_cost_usd"] > 0


def test_t01_autonomous_twin_asks_only_for_acceptance(tmp_path):
    run, store = make(tmp_path, mode=Mode.AUTONOMOUS_TWIN)
    s = drill(run, store, ["open_pr"])
    assert s.terminal == Verdict.VERIFIED, s.reason
    events = store.read(run.cfg.drill_id)
    assert [e["details"]["purpose"] for e in events if e["action"] == "approval.requested"] == ["acceptance"]
    assert sum(e["action"] == "approval.preauthorized" for e in events) == 2


def test_t00_baseline_only(tmp_path):
    run, store = make(tmp_path, scenario="T-00")
    assert drill(run, store).terminal == Verdict.BASELINE_VERIFIED
    assert ("fault.inject", "ok") not in actions(store, run)


def test_baseline_failure_aborts_before_any_fault(tmp_path):
    run, store = make(tmp_path)
    run.ops.twin.customer_ok = lambda: False
    assert drill(run, store).terminal == Verdict.ABORTED_BASELINE
    assert ("fault.inject", "ok") not in actions(store, run)


def test_advisory_mode_executes_nothing(tmp_path):
    run, store = make(tmp_path, mode=Mode.ADVISORY)
    assert drill(run, store).terminal == Verdict.ESCALATED_POLICY
    assert ("fault.restore", "ok") not in actions(store, run)


def test_t02_broad_allowed_means_policy_is_missing(tmp_path):
    run, store = make(tmp_path, scenario="T-02")
    run.ops.broad_allowed = True
    assert drill(run, store, ["broad"]).terminal == Verdict.ESCALATED_POLICY


def test_t04_denied_write_is_evidence_and_the_drill_continues(tmp_path):
    run, store = make(tmp_path, scenario="T-04")
    assert drill(run, store, ["narrow", "deploy", "open_pr"]).terminal == Verdict.VERIFIED
    assert ("engineer.tool_call", "deny") in actions(store, run)


def test_t04_protected_path_on_the_branch_stops_the_drill(tmp_path):
    run, store = make(tmp_path, scenario="T-04", repo=StubRepo("T-04", extra_paths=["journeys/test_order_journey.py"]))
    s = drill(run, store, ["narrow"])
    assert s.terminal == Verdict.ESCALATED_SECURITY and "journeys/" in s.reason
    assert ("deploy.revision", "ok") not in actions(store, run)


def test_t05_missing_identity_evidence_escalates(tmp_path):
    run, store = make(tmp_path, scenario="T-05")
    s = drill(run, store)
    assert s.terminal == Verdict.ESCALATED_EVIDENCE
    assert not any(a.startswith("engineer.") or a == "fault.restore" for a, _ in actions(store, run))


def test_t06_engineer_revises_after_a_ci_failure(tmp_path):
    run, store = make(tmp_path, scenario="T-06")
    s = drill(run, store, ["narrow", "deploy", "open_pr"])
    assert s.terminal == Verdict.VERIFIED and s.attempt == 2
    seen = actions(store, run)
    assert seen.count(("ci.result", "fail")) == 1 and seen.count(("planner.brief", "ok")) == 1


def test_t07_budget_stops_after_two_failed_candidates(tmp_path):
    run, store = make(tmp_path, scenario="T-07")
    s = drill(run, store, ["narrow"])
    assert s.terminal == Verdict.ESCALATED_BUDGET
    seen = actions(store, run)
    assert seen.count(("ci.result", "fail")) == 2 and ("deploy.revision", "ok") not in seen
    assert seen[-1] == ("drill.end", "ok")


def test_t08_secret_in_agent_output_escalates(tmp_path):
    class LeakyAgents(StubAgents):
        async def planner(self, task):
            out, usage = await super().planner(task)
            out.recovery_options.hypothesis += " AccountKey=abcdef123456"
            return out, usage
    run, store = make(tmp_path, scenario="T-08")
    run.agents = LeakyAgents(run.log, "T-08", delay=0)
    assert drill(run, store).terminal == Verdict.ESCALATED_SECURITY


def test_t08_injected_record_changes_nothing(tmp_path):
    run, store = make(tmp_path, scenario="T-08")
    assert drill(run, store, ["narrow", "deploy", "open_pr"]).terminal == Verdict.VERIFIED


def test_t09_kill_switch(tmp_path):
    run, store = make(tmp_path)
    s = drill(run, store, ["kill"])
    assert s.terminal == Verdict.KILLED
    seen = actions(store, run)
    assert ("reset", "ok") in seen and ("verdict", "KILLED") in seen and seen[-1] == ("drill.end", "ok")


def test_t10_dependency_outage_escalates(tmp_path):
    run, store = make(tmp_path, scenario="T-10")
    assert drill(run, store).terminal == Verdict.ESCALATED_DEPENDENCY


def test_unanswered_approval_escalates(tmp_path):
    run, store = make(tmp_path)
    run.cfg.policy.budgets.approval_timeout_s = 0.2
    s = drill(run, store, nobody=True)
    assert s.terminal == Verdict.ESCALATED_POLICY and "no decision" in s.reason


def test_restart_alone_does_not_pass_the_durability_check(tmp_path):
    """A new revision is a restart, so a weak candidate passes after-fix. The second fault catches it."""
    from livefire.stubs import _sha
    run, store = make(tmp_path)
    run.ops.good_images = {f"ghcr.io/example/order-service:sha-{_sha(run.cfg.drill_id, 2, 2)}"}   # only candidate 2
    s = drill(run, store, ["narrow", "deploy", "deploy", "open_pr"])
    assert s.terminal == Verdict.VERIFIED and s.attempt == 2
    seen = actions(store, run)
    assert ("referee.after-refault", "FAIL") in seen and seen.count(("planner.brief", "ok")) == 2


def test_without_the_durability_check_a_weak_candidate_passes(tmp_path):
    run, store = make(tmp_path, prove=False)
    run.ops.good_images = set()
    assert drill(run, store, ["narrow", "deploy", "open_pr"]).terminal == Verdict.VERIFIED


def test_drill_time_budget(tmp_path):
    run, store = make(tmp_path)
    run.ops.delay = 0.05
    run.cfg.policy.budgets.max_drill_s = 0.3
    s = drill(run, store, ["narrow", "deploy", "open_pr"])
    assert s.terminal == Verdict.ESCALATED_BUDGET and "time budget" in s.reason
