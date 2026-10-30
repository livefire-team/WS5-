"""Runs one drill: the workflow, its human gates over the event log, the kill switch and the time budget.

  Local, all stubs:  python -m livefire.runner --drill-id drill-g1-demo --stub
  In Azure (job):    configuration comes from environment variables set by drill-runner.bicep
"""
import argparse
import asyncio
import hashlib
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .contracts import CONTRACT_VERSION, ApprovalDecision, ApprovalRequest, AutonomyPolicy, DrillConfig, Mode, Verdict
from .events import store_from_env
from .ports import EventLog
from .workflow import DrillState, Run, build_workflow, cleanup, close

RUNNER_VERSION = os.environ.get("RUNNER_VERSION", "dev")
AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"


async def ask(run: Run, request: ApprovalRequest, poll_s: float) -> ApprovalDecision:
    """Publishes the question for the Control Center and waits for the matching approval.decided event."""
    run.log.write("approval.requested", "ok", request.model_dump())
    deadline = time.monotonic() + run.budgets.approval_timeout_s
    while time.monotonic() < deadline:
        for e in run.log.read():
            d = e.get("details") or {}
            if e["action"] == "approval.decided" and d.get("request_id") == request.request_id:
                return ApprovalDecision.model_validate({k: d.get(k) for k in ApprovalDecision.model_fields if k in d})
        await asyncio.sleep(poll_s)
    run.log.write("approval.decided", "timeout", {"request_id": request.request_id, "option_id": "timeout",
                                                  "approver": "nobody", "method": "timeout"})
    return ApprovalDecision(request_id=request.request_id, option_id="timeout")


async def drive(run: Run, poll_s: float) -> DrillState:
    workflow = build_workflow(run)
    stream = workflow.run(run.state, stream=True)
    while True:
        pending = []
        async for event in stream:
            if event.type == "request_info":
                pending.append((event.request_id, event.data))
            elif event.type == "output":
                return event.data
            elif event.type == "failed":
                raise RuntimeError(f"workflow failed: {event.data}")
        if not pending:
            return run.state
        responses = {rid: await ask(run, request, poll_s) for rid, request in pending}
        stream = workflow.run(stream=True, responses=responses)


async def watch(run: Run, main: asyncio.Task, poll_s: float) -> None:
    """The kill switch (T-09) and the whole-drill time budget, checked outside the workflow."""
    while not main.done():
        await asyncio.sleep(poll_s)
        kill = next((e for e in run.log.read() if e["action"] == "kill.requested"), None)
        if kill:
            run.abort = (Verdict.KILLED, f"kill switch pressed by {(kill.get('details') or {}).get('by', 'someone')}")
        elif time.monotonic() - run.started > run.budgets.max_drill_s:
            run.abort = (Verdict.ESCALATED_BUDGET, f"drill time budget of {run.budgets.max_drill_s} s used")
        else:
            continue
        main.cancel()
        return


async def run_drill(run: Run, poll_s: float = 2.0) -> DrillState:
    run.abort = None
    main = asyncio.create_task(drive(run, poll_s))
    watcher = asyncio.create_task(watch(run, main, poll_s))
    try:
        return await main
    except asyncio.CancelledError:
        if run.abort is None:
            raise
        s = run.state
        s.terminal, s.reason = run.abort
        killed = s.terminal == Verdict.KILLED
        try:
            await run.op("kill" if killed else "reset_twin", timeout=600)   # reverses the fault, resets the twin
        except Exception as exc:
            s.reason += f"; reversal failed: {exc}"
        await close(run, s)
        await cleanup(run, s, already_reset=True)
        return s
    finally:
        watcher.cancel()


# ---------------------------------------------------------------- wiring

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def build_run(args) -> Run:
    store = store_from_env()
    expires = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    policy = AutonomyPolicy(mode=Mode(args.mode), prove_durable=not args.no_prove, expires_at=expires)
    cfg = DrillConfig(drill_id=args.drill_id, scenario=args.scenario, policy=policy,
                      base_ref=os.environ.get("BASE_REF", "main"),
                      image_repo=os.environ.get("IMAGE_REPO", "ghcr.io/example/order-service"))
    log = EventLog(store, cfg.drill_id)
    if log.read():
        sys.exit(f"refused: {cfg.drill_id} already has events; every drill needs a new ID")
    if args.stub:
        from .stubs import StubAgents, StubOps, StubRepo
        ops, agents, repo = StubOps(log, delay=args.stub_delay), StubAgents(log, args.scenario, args.stub_delay), \
            StubRepo(args.scenario)
        meta = {"agents": {"planner": "stub", "engineer": "stub"}, "model_deployment": "stub",
                "prompt_sha256": {r: _sha256((AGENTS_DIR / f"{r}.md").read_text()) for r in ("planner", "engineer")}}
    else:
        from .agents import FoundryAgents
        from .ports import GitHubRepo, HttpOps
        from .github_auth import token_provider_from_env
        ops = HttpOps(os.environ["OPS_URL"], os.environ["OPS_KEY"], cfg.drill_id)
        agents = FoundryAgents(os.environ["FOUNDRY_PROJECT_ENDPOINT"], os.environ["PLANNER_AGENT"],
                               os.environ["ENGINEER_AGENT"])
        repo = GitHubRepo(os.environ["GITHUB_REPO"], token_provider_from_env())
        meta = {"agents": {"planner": os.environ["PLANNER_AGENT"], "engineer": os.environ["ENGINEER_AGENT"]},
                "model_deployment": os.environ.get("MODEL_DEPLOYMENT", ""),
                "prompt_sha256": agents.instruction_hashes()}
    meta.update({"contract_version": CONTRACT_VERSION, "runner_version": RUNNER_VERSION})
    price = (float(os.environ.get("PRICE_INPUT_PER_MTOK", "0")), float(os.environ.get("PRICE_OUTPUT_PER_MTOK", "0")))
    return Run(cfg=cfg, ops=ops, agents=agents, repo=repo, log=log, meta=meta, price_per_mtok=price)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Run one LiveFire drill")
    p.add_argument("--drill-id", default=os.environ.get("DRILL_ID"), required="DRILL_ID" not in os.environ)
    p.add_argument("--mode", default=os.environ.get("MODE", "Supervised"), choices=[m.value for m in Mode])
    p.add_argument("--scenario", default=os.environ.get("SCENARIO", "T-01"))
    p.add_argument("--no-prove", action="store_true", default=os.environ.get("PROVE_DURABLE", "1") == "0",
                   help="skip the second fault after the fix (shorter, weaker proof)")
    p.add_argument("--stub", action="store_true", help="stub Ops, agents and GitHub (G1)")
    p.add_argument("--stub-delay", type=float, default=1.0, help="seconds per stubbed step, so the UI can follow")
    p.add_argument("--auto-approve", default="", help="comma-separated choices for tests, e.g. broad,narrow,deploy,open_pr")
    args = p.parse_args(argv)
    run = build_run(args)

    async def go():
        approver = None
        if args.auto_approve:
            from .stubs import auto_approve
            approver = asyncio.create_task(auto_approve(run.log.store, run.cfg.drill_id, args.auto_approve.split(",")))
        try:
            return await run_drill(run, poll_s=0.5 if args.stub else 2.0)
        finally:
            if approver:
                approver.cancel()

    state = asyncio.run(go())
    print(f"{run.cfg.drill_id}: {state.terminal.value if state.terminal else 'no terminal state'} ({state.reason})")
    return 0 if state.terminal in (Verdict.VERIFIED, Verdict.BASELINE_VERIFIED) else 3


if __name__ == "__main__":
    sys.exit(main())
