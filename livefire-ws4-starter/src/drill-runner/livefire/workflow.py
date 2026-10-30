"""The drill as a Microsoft Agent Framework workflow: deterministic steps, two agent steps, three human gates.

Every executor receives the one DrillState, does its stage, sets state.route, and sends the state on.
Edges are conditions on route. Any stage can route to "close", which records the terminal state.
"""
import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler, response_handler
from typing_extensions import Never

from . import evidence
from .contracts import (ApprovalDecision, ApprovalOption, ApprovalRequest, ChangeReport, DrillConfig, RecoveryOptions,
                        RemediationBrief, Verdict, VerdictInputs)
from .policy import check_brief, check_changed_paths, check_options, check_secrets, compute_verdict, gate
from .ports import AgentPort, DependencyError, EventLog, OpsPort, RepoPort, Usage


class BudgetExceeded(RuntimeError):
    pass


class Stop(Exception):
    """Ends the drill from inside a stage with a terminal state and a reason."""

    def __init__(self, verdict: Verdict, reason: str):
        super().__init__(reason)
        self.verdict, self.reason = verdict, reason


@dataclass
class DrillState:
    route: str = "prepare"
    terminal: Verdict | None = None
    reason: str = ""
    attempt: int = 0
    options: RecoveryOptions | None = None
    remaining: list[str] = field(default_factory=list)
    chosen: str = ""
    brief: RemediationBrief | None = None
    reports: list[ChangeReport] = field(default_factory=list)
    ci_failure: list[dict] = field(default_factory=list)
    journey_failure: dict | None = None
    commit: str = ""
    image: str = ""
    ci_url: str = ""
    verdict_inputs: VerdictInputs | None = None
    package_sha256: str = ""
    pr_url: str = ""
    cleanup: str = ""


@dataclass
class Run:
    """Everything one drill needs. One Run, one workflow instance, one drill."""
    cfg: DrillConfig
    ops: OpsPort
    agents: AgentPort
    repo: RepoPort
    log: EventLog
    meta: dict                                   # agents, prompt hashes, model deployment, runner version
    price_per_mtok: tuple[float, float] = (0.0, 0.0)
    state: DrillState = field(default_factory=DrillState)
    tokens: int = 0
    cost: float = 0.0
    requests: int = 0
    started: float = field(default_factory=time.monotonic)
    abort: tuple | None = None                   # set by the kill switch or the time budget

    @property
    def budgets(self):
        return self.cfg.policy.budgets

    async def op(self, action: str, timeout: float | None = None, **params):
        try:
            async with asyncio.timeout(timeout or self.budgets.wait_s + 60):
                return await self.ops.call(action, **params)
        except TimeoutError as exc:
            raise DependencyError(f"Ops action {action} did not finish in time") from exc

    def charge(self, usage: Usage) -> None:
        cost = (usage.input_tokens * self.price_per_mtok[0] + usage.output_tokens * self.price_per_mtok[1]) / 1e6
        self.tokens += usage.tokens
        self.cost += cost
        self.log.write("usage", "ok", {"agent": usage.agent, "tokens": usage.tokens, "input_tokens": usage.input_tokens,
                                       "output_tokens": usage.output_tokens, "tool_calls": usage.tool_calls,
                                       "est_cost_usd": round(cost, 4), "response_id": usage.response_id})
        if usage.tool_calls > self.budgets.max_tool_calls_per_agent_call:
            raise BudgetExceeded(f"the {usage.agent} made {usage.tool_calls} tool calls in one turn")
        if self.tokens > self.budgets.max_tokens:
            raise BudgetExceeded(f"token budget used: {self.tokens} of {self.budgets.max_tokens}")

    def request(self, purpose: str, options: list[tuple[str, str]]) -> ApprovalRequest:
        self.requests += 1
        return ApprovalRequest(request_id=f"apr-{self.requests}", purpose=purpose,
                               options=[ApprovalOption(id=i, label=label) for i, label in options])

    def attempts_left(self) -> bool:
        return self.state.attempt < self.budgets.max_candidate_attempts


OPTION_LABELS = {"broad": "Grant Contributor on the subscription",
                 "narrow": "Restore Key Vault Secrets User on the twin vault"}


class Stage(Executor):
    """One stage. Subclasses implement run(); errors become terminal states here, in one place."""

    routes: tuple[str, ...] = ()

    def __init__(self, run: Run, id: str):
        super().__init__(id=id)
        self.run_ = run

    async def run(self, s: DrillState) -> ApprovalRequest | None:
        raise NotImplementedError

    async def decided(self, s: DrillState, decision: ApprovalDecision) -> None:
        raise NotImplementedError

    async def timed_out(self, s: DrillState, request: ApprovalRequest) -> None:
        raise Stop(Verdict.ESCALATED_POLICY,
                   f"no decision on {request.purpose} within {self.run_.budgets.approval_timeout_s} s")

    async def _guard(self, s: DrillState, step) -> tuple[bool, Any]:
        try:
            return True, await step()
        except Stop as stop:
            s.terminal, s.reason = stop.verdict, stop.reason
        except BudgetExceeded as exc:
            s.terminal, s.reason = Verdict.ESCALATED_BUDGET, str(exc)
        except DependencyError as exc:
            s.terminal, s.reason = Verdict.ESCALATED_DEPENDENCY, str(exc)
        s.route = "close"
        return False, None

    @handler
    async def handle(self, s: DrillState, ctx: WorkflowContext[DrillState]) -> None:
        ok, ask = await self._guard(s, lambda: self.run(s))
        if ok and isinstance(ask, ApprovalRequest):
            await ctx.request_info(request_data=ask, response_type=ApprovalDecision, request_id=ask.request_id)
            return
        await ctx.send_message(s)

    @response_handler
    async def on_decision(self, original_request: ApprovalRequest, response: ApprovalDecision,
                          ctx: WorkflowContext[DrillState]) -> None:
        s = self.run_.state
        if response.option_id == "timeout":
            await self._guard(s, lambda: self.timed_out(s, original_request))
        else:
            await self._guard(s, lambda: self.decided(s, response))
        await ctx.send_message(s)


# ---------------------------------------------------------------- deterministic stages

class Prepare(Stage):
    routes = ("baseline",)

    async def run(self, s):
        r = self.run_
        r.log.write("drill.start", "ok", {"mode": r.cfg.policy.mode.value, "scenario": r.cfg.scenario,
                                          "policy": r.cfg.policy.model_dump(mode="json"), **r.meta})
        await r.op("reset_twin")
        if (await r.op("wait_for", state="ok", timeout_s=r.budgets.wait_s)).result != "ok":
            raise Stop(Verdict.ABORTED_BASELINE, "the twin did not become healthy after reset")
        s.route = "baseline"


class Baseline(Stage):
    routes = ("inject",)

    async def run(self, s):
        result = await self.run_.op("run_referee", label="baseline", timeout=300)
        if result.result != "PASS":
            raise Stop(Verdict.ABORTED_BASELINE, "the baseline customer journey failed; no fault injected")
        if self.run_.cfg.scenario == "T-00":
            raise Stop(Verdict.BASELINE_VERIFIED, "baseline-only drill")
        s.route = "inject"


class Inject(Stage):
    routes = ("investigate",)

    async def run(self, s):
        r = self.run_
        if (await r.op("inject_fault")).result != "ok":
            raise Stop(Verdict.ESCALATED_DEPENDENCY, "the Ops Service refused the fault")
        if (await r.op("wait_for", state="forbidden", timeout_s=r.budgets.wait_s)).result != "ok":
            raise Stop(Verdict.ESCALATED_DEPENDENCY, "the fault did not take effect in time")
        if (await r.op("run_referee", label="during-fault", timeout=300)).result != "FAIL":
            raise Stop(Verdict.ESCALATED_DEPENDENCY, "the fault had no effect on customers")
        s.route = "investigate"


# ---------------------------------------------------------------- agent stages

class Investigate(Stage):
    routes = ("authorize",)

    async def run(self, s):
        r = self.run_
        task = {"task": "investigate", "drill_id": r.cfg.drill_id, "output_stage": "options",
                "goal": "Customers cannot place orders. Find the cause from evidence and propose recovery options.",
                "observed": {"baseline": "PASS", "during_fault": "FAIL at place-order"}}
        async with asyncio.timeout(r.budgets.agent_timeout_s):
            out, usage = await r.agents.planner(task)
        r.charge(usage)
        if out.stage != "options" or out.recovery_options is None:
            raise DependencyError("the Planner returned a brief when options were asked for")
        if reason := check_secrets(out.model_dump()):
            raise Stop(Verdict.ESCALATED_SECURITY, reason)
        if reason := check_options(out.recovery_options, r.cfg.policy, r.cfg.drill_id):
            raise Stop(Verdict.ESCALATED_EVIDENCE, reason)
        s.options = out.recovery_options
        s.remaining = [o.id for o in s.options.options]
        r.log.write("planner.options", "ok", {
            "options": [{"id": o.id, "label": o.label} for o in s.options.options],
            "recommended": s.options.recommended, "confidence": s.options.confidence,
            "hypothesis": s.options.hypothesis, "response_id": usage.response_id})
        s.route = "authorize"


class Reflect(Stage):
    routes = ("engineer",)

    async def run(self, s):
        r = self.run_
        task = {"task": "remediation_brief", "drill_id": r.cfg.drill_id, "output_stage": "brief",
                "goal": "Infrastructure is green but the customer journey still fails. Explain why from evidence "
                        "and write the brief the Engineer will implement.",
                "failed_journey": s.journey_failure,
                "earlier_hypothesis": s.options.hypothesis if s.options else "",
                "previous_brief": s.brief.model_dump() if s.brief else None,
                "previous_candidate": s.reports[-1].model_dump() if s.reports else None}
        async with asyncio.timeout(r.budgets.agent_timeout_s):
            out, usage = await r.agents.planner(task)
        r.charge(usage)
        if out.stage != "brief" or out.remediation_brief is None:
            raise DependencyError("the Planner returned options when a brief was asked for")
        if reason := check_secrets(out.model_dump()):
            raise Stop(Verdict.ESCALATED_SECURITY, reason)
        if reason := check_brief(out.remediation_brief, r.cfg.policy, r.cfg.drill_id):
            raise Stop(Verdict.ESCALATED_EVIDENCE, reason)
        s.brief, s.ci_failure = out.remediation_brief, []
        r.log.write("planner.brief", "ok", {"summary": s.brief.required_behavior[:160],
                                            "confidence": s.brief.confidence,
                                            "acceptance_criteria": len(s.brief.acceptance_criteria),
                                            "response_id": usage.response_id})
        s.route = "engineer"


class Engineer(Stage):
    routes = ("ci",)

    async def run(self, s):
        r = self.run_
        if not r.attempts_left():
            raise Stop(Verdict.ESCALATED_BUDGET, f"all {r.budgets.max_candidate_attempts} candidate attempts used")
        s.attempt += 1
        task = {"task": "candidate", "drill_id": r.cfg.drill_id, "attempt": s.attempt, "base_ref": r.cfg.base_ref,
                "brief": s.brief.model_dump(), "ci_failure": s.ci_failure or None,
                "failed_journey_after_previous_candidate": s.journey_failure if s.attempt > 1 else None}
        async with asyncio.timeout(r.budgets.agent_timeout_s):
            report, usage = await r.agents.engineer(task)
        r.charge(usage)
        if report.drill_id != r.cfg.drill_id or report.attempt != s.attempt:
            raise DependencyError("the Engineer reported on a different drill or attempt")
        if reason := check_secrets(report.model_dump()):
            raise Stop(Verdict.ESCALATED_SECURITY, reason)
        actual = await r.repo.changed_paths(r.cfg.base_ref, report.branch)
        if reason := check_changed_paths(actual):
            raise Stop(Verdict.ESCALATED_SECURITY, reason)
        s.reports.append(report)
        r.log.write("engineer.change_report", "ok", {"branch": report.branch, "attempt": s.attempt,
                                                     "commits": report.commits, "changed_paths": actual,
                                                     "response_id": usage.response_id})
        s.route = "ci"


class Ci(Stage):
    routes = ("deploy", "engineer")
    poll_s = 10.0

    async def run(self, s):
        r = self.run_
        branch = s.reports[-1].branch
        commit = await r.repo.head_sha(branch)
        deadline = time.monotonic() + r.budgets.ci_timeout_s
        while (status := await r.repo.ci_status(branch, commit)).state == "pending":
            if time.monotonic() > deadline:
                raise Stop(Verdict.ESCALATED_DEPENDENCY, "CI did not finish in time")
            await asyncio.sleep(self.poll_s)
        r.log.write("ci.result", status.state, {"attempt": s.attempt, "branch": branch, "commit": commit,
                                                "run_url": status.url, "failed": status.failed})
        if status.state == "pass":
            s.commit, s.ci_url, s.image = commit, status.url, f"{r.cfg.image_repo}:sha-{commit}"
            s.route = "deploy"
            return
        if not r.attempts_left():
            raise Stop(Verdict.ESCALATED_BUDGET, f"CI failed on all {r.budgets.max_candidate_attempts} candidates")
        s.ci_failure = status.failed
        s.route = "engineer"                         # implementation failure: the Engineer revises, not the Planner


# ---------------------------------------------------------------- human gates (Command Gate)

class Authorize(Stage):
    routes = ("recover",)

    async def run(self, s):
        r = self.run_
        action = {"broad": "grant_contributor_subscription", "narrow": "restore_kv_secrets_user"}
        proposed = s.options.recommended if s.options.recommended in s.remaining else s.remaining[0]
        decision = gate(r.cfg.policy, action[proposed])
        if decision == "deny":
            raise Stop(Verdict.ESCALATED_POLICY, f"{r.cfg.policy.mode.value} policy does not allow the workflow to "
                                                 "execute recovery; a person must perform it")
        if decision == "execute":
            r.log.write("approval.preauthorized", "ok", {"action": action[proposed], "mode": r.cfg.policy.mode.value,
                                                         "policy_version": r.cfg.policy.version})
            s.chosen, s.route = proposed, "recover"
            return None
        labels = {o.id: o.label for o in s.options.options}
        return r.request("recovery", [(i, labels.get(i, OPTION_LABELS[i])) for i in s.remaining])

    async def decided(self, s, decision):
        if decision.option_id not in s.remaining:
            raise Stop(Verdict.ESCALATED_POLICY, f"decision {decision.option_id} was not on offer")
        s.chosen, s.route = decision.option_id, "recover"


class Recover(Stage):
    routes = ("authorize", "check")

    async def run(self, s):
        r = self.run_
        if s.chosen == "broad":
            result = await r.op("attempt_broad_recovery")
            if result.result != "denied":
                raise Stop(Verdict.ESCALATED_POLICY, f"broad recovery was not denied ({result.result}); "
                                                     "the deny policy is not in effect")
            s.remaining = [o for o in s.remaining if o != "broad"]
            if not s.remaining:
                raise Stop(Verdict.ESCALATED_POLICY, "the only option offered was denied by policy")
            s.route = "authorize"
            return
        if (await r.op("restore_access")).result != "ok":
            raise Stop(Verdict.ESCALATED_DEPENDENCY, "the narrow restore did not complete")
        if (await r.op("wait_for", state="ok", timeout_s=r.budgets.wait_s)).result != "ok":
            raise Stop(Verdict.ESCALATED_DEPENDENCY, "technical health did not turn green after the restore")
        s.route = "check"


class Check(Stage):
    routes = ("reflect", "verdict")

    async def run(self, s):
        result = await self.run_.op("run_referee", label="after-restore", timeout=300)
        if result.result == "PASS":
            s.route = "verdict"                      # recovered without a code change: no False Green this time
            return
        s.journey_failure = {"label": "after-restore", **result.details}
        s.route = "reflect"                          # the False Green


class Deploy(Stage):
    routes = ("prove",)

    async def run(self, s):
        r = self.run_
        decision = gate(r.cfg.policy, "deploy_candidate_to_twin")
        if decision == "deny":
            raise Stop(Verdict.ESCALATED_POLICY, "policy does not allow deploying the candidate")
        if decision == "execute":
            r.log.write("approval.preauthorized", "ok", {"action": "deploy_candidate_to_twin", "image": s.image,
                                                         "mode": r.cfg.policy.mode.value})
            await self._deploy(s)
            return None
        return r.request("deploy", [("deploy", f"Deploy candidate {s.attempt} to the twin"),
                                    ("reject", "Don't deploy; end the drill")])

    async def decided(self, s, decision):
        if decision.option_id != "deploy":
            raise Stop(Verdict.ESCALATED_POLICY, "deploying the candidate was not approved")
        await self._deploy(s)

    async def _deploy(self, s):
        result = await self.run_.op("deploy_revision", image=s.image, timeout=600)
        if result.result != "ok":
            raise Stop(Verdict.ESCALATED_POLICY, f"the Ops Service did not deploy the candidate ({result.result})")
        s.route = "prove"


class Prove(Stage):
    """The same journey after the fix. Then, if the policy asks, a second fault without any restart:
    a new revision is itself a restart, so only the second cycle proves the fix recovers on its own."""
    routes = ("verdict", "reflect")

    async def run(self, s):
        r = self.run_
        result = await r.op("run_referee", label="after-fix", timeout=300)
        if result.result == "PASS" and r.cfg.policy.prove_durable:
            for action, params in (("inject_fault", {}), ("wait_for", {"state": "forbidden"}),
                                   ("restore_access", {}), ("wait_for", {"state": "ok"})):
                if "state" in params:
                    params["timeout_s"] = r.budgets.wait_s
                if (await r.op(action, **params)).result != "ok":
                    raise Stop(Verdict.ESCALATED_DEPENDENCY, f"the durability check could not {action}")
            result = await r.op("run_referee", label="after-refault", timeout=300)
        if result.result == "PASS":
            s.route = "verdict"
            return
        if not r.attempts_left():
            raise Stop(Verdict.ESCALATED_BUDGET, "the customer journey still fails and no candidate attempts remain")
        s.journey_failure = {"label": result.details.get("label", "after-fix"), **result.details}
        s.route = "reflect"                          # new evidence goes back to the Planner


class Accept(Stage):
    """Final acceptance is always a person's, in every mode. The workflow opens the pull request; it never merges."""
    routes = ("cleanup",)

    async def run(self, s):
        return self.run_.request("acceptance", [("open_pr", "Open a pull request for human review"),
                                                ("reject", "Reject the candidate")])

    async def decided(self, s, decision):
        r, report = self.run_, s.reports[-1]
        s.route = "cleanup"                          # the verdict is already sealed; nothing here can change it
        if decision.option_id != "open_pr":
            r.log.write("package.pr", "rejected", {"branch": report.branch, "approver": decision.approver})
            return
        try:
            title = f"[{r.cfg.drill_id}] Recover from cached Key Vault failures (candidate {s.attempt})"
            s.pr_url = await r.repo.open_pull_request(report.branch, title, evidence.pull_request_body(r, s))
            r.log.write("package.pr", "ok", {"url": s.pr_url, "branch": report.branch, "approver": decision.approver})
        except DependencyError as exc:
            r.log.write("package.pr", "error", {"branch": report.branch, "error": str(exc)[:300]})

    async def timed_out(self, s, request):
        self.run_.log.write("package.pr", "no_decision", {"branch": s.reports[-1].branch})
        s.route = "cleanup"


# ---------------------------------------------------------------- verdict, close and cleanup

class VerdictStage(Stage):
    routes = ("close",)

    async def run(self, s):
        inputs = compute_verdict(self.run_.log.read())
        s.verdict_inputs = inputs
        if inputs.verified:
            s.terminal, s.reason = Verdict.VERIFIED, "all six verdict inputs passed"
        else:
            s.terminal, s.reason = Verdict.ESCALATED_EVIDENCE, "; ".join(inputs.reasons)
        s.route = "close"


async def close(run: Run, s: DrillState) -> None:
    """Records the terminal state and seals the evidence package. Used by the graph and by kill."""
    if s.verdict_inputs is None:
        s.verdict_inputs = compute_verdict(run.log.read())
    run.log.write("verdict", s.terminal.value, {"reason": s.reason, "inputs": s.verdict_inputs.model_dump(),
                                                "attempts": s.attempt, "tokens": run.tokens,
                                                "est_cost_usd": round(run.cost, 4)})
    s.package_sha256 = evidence.seal(run, s)


async def cleanup(run: Run, s: DrillState, already_reset: bool = False) -> None:
    try:
        if not already_reset:
            await run.op("reset_twin", timeout=600)
        s.cleanup = "ok"
    except Exception as exc:                          # never hidden: recorded on drill.end
        s.cleanup = f"failed: {exc}"
    run.log.write("drill.end", "ok" if s.cleanup == "ok" else "cleanup_failed",
                  {"terminal": s.terminal.value, "cleanup": s.cleanup, "pr_url": s.pr_url})


class Close(Stage):
    routes = ("accept", "cleanup")

    async def run(self, s):
        try:
            await close(self.run_, s)
        except Exception as exc:                      # recorded, then cleanup still runs
            self.run_.log.write("package.ready", "error", {"error": str(exc)[:300]})
        s.route = "accept" if s.terminal == Verdict.VERIFIED and s.reports else "cleanup"


class Cleanup(Executor):
    def __init__(self, run: Run, id: str):
        super().__init__(id=id)
        self.run_ = run

    @handler
    async def handle(self, s: DrillState, ctx: WorkflowContext[Never, DrillState]) -> None:
        await cleanup(self.run_, s)
        await ctx.yield_output(s)


def build_workflow(run: Run):
    stages = {
        "prepare": Prepare(run, "prepare"), "baseline": Baseline(run, "baseline"), "inject": Inject(run, "inject"),
        "investigate": Investigate(run, "investigate"), "authorize": Authorize(run, "authorize"),
        "recover": Recover(run, "recover"), "check": Check(run, "check"), "reflect": Reflect(run, "reflect"),
        "engineer": Engineer(run, "engineer"), "ci": Ci(run, "ci"), "deploy": Deploy(run, "deploy"),
        "prove": Prove(run, "prove"), "verdict": VerdictStage(run, "verdict"), "close": Close(run, "close"),
        "accept": Accept(run, "accept"), "cleanup": Cleanup(run, "cleanup"),
    }
    builder = WorkflowBuilder(start_executor=stages["prepare"], name="livefire-drill", max_iterations=400)
    for name, stage in stages.items():
        if not isinstance(stage, Stage):
            continue
        targets = set(stage.routes) | ({"close"} if name not in ("close", "accept") else set())
        for target in sorted(targets):
            builder.add_edge(stage, stages[target], condition=lambda s, t=target: s.route == t)
    return builder.build()
