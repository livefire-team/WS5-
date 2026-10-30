"""Typed handoffs between the agents, the workflow, the Ops Service and the UI. Version 1.0, frozen at G1.

Change rules: WS4 approves every change, bumps CONTRACT_VERSION, and tells every affected workstream the same day.
"""
from datetime import datetime
from enum import Enum
from fnmatch import fnmatch
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONTRACT_VERSION = "1.0"
DRILL_ID = r"^drill-[A-Za-z0-9-]{1,64}$"
BRANCH = r"^candidate/drill-[A-Za-z0-9-]{1,64}-[12]$"
SHA = r"^[0-9a-f]{7,40}$"

# Must match the Engineer endpoint's write allowlist in contracts/mcp-tools.md (WS3).
ENGINEER_WRITE_PATHS = (
    "src/order-service/app/secret_provider.py",
    "src/order-service/tests/test_*.py",
    "runbooks/*.md",
)
# Must match PROTECTED in WS3's guard.py. Nothing may change these, whatever an allowlist says.
PROTECTED_PATHS = ("journeys/", "infra/", ".github/", "sql/", "policy/", "contracts/", "scripts/",
                   "src/control-center/", "src/mcp-server/", "src/drill-runner/")


def engineer_may_write(path: str) -> bool:
    return not path.startswith(PROTECTED_PATHS) and any(fnmatch(path, p) for p in ENGINEER_WRITE_PATHS)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------- modes and terminal states

class Mode(str, Enum):
    ADVISORY = "Advisory"
    SUPERVISED = "Supervised"
    AUTONOMOUS_TWIN = "Autonomous Twin"


class Verdict(str, Enum):
    """Terminal states (blueprint A.7). Only the workflow sets one; no model can."""
    VERIFIED = "VERIFIED"
    BASELINE_VERIFIED = "BASELINE_VERIFIED"
    ABORTED_BASELINE = "ABORTED_BASELINE"
    ESCALATED_POLICY = "ESCALATED_POLICY"
    ESCALATED_EVIDENCE = "ESCALATED_EVIDENCE"
    ESCALATED_SECURITY = "ESCALATED_SECURITY"
    ESCALATED_BUDGET = "ESCALATED_BUDGET"
    ESCALATED_DEPENDENCY = "ESCALATED_DEPENDENCY"
    KILLED = "KILLED"


# ---------------------------------------------------------------- events (contracts/event-catalog.md)

class Event(Strict):
    """One line of evidence/<drill_id>/events.jsonl. Every writer uses exactly these five keys."""
    ts: str
    drill_id: str = Field(pattern=DRILL_ID)
    action: str = Field(pattern=r"^[a-z]+(\.[a-z_-]+)?$")
    result: str = Field(max_length=40)
    details: dict = Field(default_factory=dict)


class RefereeResult(BaseModel):
    """What WS2's referee writes for one run. Extra fields are kept, never required."""
    model_config = ConfigDict(extra="allow")
    label: str
    verdict: Literal["PASS", "FAIL"]
    last_step: str = ""
    error: str = ""
    orders_persisted: int = 0
    payment_auths_persisted: int = 0


# ---------------------------------------------------------------- Ops Service (contracts/ops-actions.md)

OpsActionName = Literal["inject_fault", "restore_access", "attempt_broad_recovery", "wait_for", "run_referee",
                        "deploy_revision", "reset_twin", "kill"]


class OpsResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    action: OpsActionName
    result: Literal["ok", "denied", "refused", "timeout", "UNEXPECTED_ALLOW", "PASS", "FAIL"]
    details: dict = Field(default_factory=dict)


# ---------------------------------------------------------------- agent handoffs

class EvidenceRef(Strict):
    source: Literal["telemetry", "identity", "incident", "runbook", "repository", "commit", "referee", "ci"]
    ref: str = Field(min_length=1, max_length=200)      # query template, incident ID, path@sha, referee label
    note: str = Field(min_length=1, max_length=300)


class RecoveryOption(Strict):
    id: Literal["broad", "narrow"]
    label: str = Field(min_length=1, max_length=120)
    # The only two actions the workflow knows how to request. Agents cannot invent others.
    action: Literal["grant_contributor_subscription", "restore_kv_secrets_user"]
    risk: str = Field(min_length=1, max_length=300)
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def id_matches_action(self):
        expected = {"broad": "grant_contributor_subscription", "narrow": "restore_kv_secrets_user"}[self.id]
        if self.action != expected:
            raise ValueError(f"option {self.id} must use action {expected}")
        return self


class RecoveryOptions(Strict):
    """The Planner's first output: what broke and how it could be restored."""
    drill_id: str = Field(pattern=DRILL_ID)
    hypothesis: str = Field(min_length=1, max_length=600)
    rejected_hypotheses: list[str] = Field(max_length=5)
    confidence: float = Field(ge=0, le=1)
    options: list[RecoveryOption] = Field(min_length=1, max_length=2)
    recommended: Literal["broad", "narrow"]
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def recommended_is_offered(self):
        ids = [o.id for o in self.options]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate option ids")
        if self.recommended not in ids:
            raise ValueError("recommended option is not in options")
        return self


class RemediationBrief(Strict):
    """The Planner's output after a False Green; the Engineer's complete handoff (blueprint 5.2)."""
    incident_id: str = Field(pattern=DRILL_ID)
    evidence_references: list[EvidenceRef] = Field(min_length=2, max_length=12)
    supported_hypothesis: str = Field(min_length=1, max_length=800)
    rejected_hypotheses: list[str] = Field(max_length=5)
    affected_components: list[str] = Field(min_length=1, max_length=5)
    required_behavior: str = Field(min_length=1, max_length=800)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=8)
    allowed_repository_paths: list[str] = Field(min_length=1, max_length=5)
    prohibited_changes: list[str] = Field(min_length=1, max_length=8)
    residual_risks: list[str] = Field(max_length=6)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def paths_within_engineer_allowlist(self):
        for path in self.allowed_repository_paths:
            if not engineer_may_write(path):
                raise ValueError(f"path outside the Engineer allowlist: {path}")
        return self


class PlannerOutput(Strict):
    """One schema for both Planner passes, so one Foundry agent serves both. Exactly one part is filled."""
    stage: Literal["options", "brief"]
    recovery_options: RecoveryOptions | None
    remediation_brief: RemediationBrief | None

    @model_validator(mode="after")
    def one_part_matches_stage(self):
        filled = {"options": self.recovery_options, "brief": self.remediation_brief}
        if filled[self.stage] is None or filled["brief" if self.stage == "options" else "options"] is not None:
            raise ValueError(f"stage {self.stage} must fill exactly its own part")
        return self


class ChangeMapping(Strict):
    change: str = Field(min_length=1, max_length=300)
    evidence_ref: str = Field(min_length=1, max_length=200)
    acceptance_criterion: str = Field(min_length=1, max_length=300)


class ChangeReport(Strict):
    """The Engineer's output: what it changed on the candidate branch, and why."""
    drill_id: str = Field(pattern=DRILL_ID)
    attempt: int = Field(ge=1, le=2)
    branch: str = Field(pattern=BRANCH)
    commits: list[str] = Field(min_length=1, max_length=10)
    changed_paths: list[str] = Field(min_length=1, max_length=3)
    regression_test_path: str = Field(max_length=200)
    runbook_path: str = Field(max_length=200)
    evidence_to_change: list[ChangeMapping] = Field(min_length=1, max_length=10)
    notes: str = Field(max_length=600)

    @field_validator("commits")
    @classmethod
    def commits_are_shas(cls, value):
        import re
        if not all(re.fullmatch(SHA, c) for c in value):
            raise ValueError("commits must be git SHAs")
        return value

    @model_validator(mode="after")
    def branch_and_paths_belong_to_drill(self):
        if self.branch != f"candidate/{self.drill_id}-{self.attempt}":
            raise ValueError("branch does not belong to this drill and attempt")
        for path in [*self.changed_paths, self.regression_test_path, self.runbook_path]:
            if not engineer_may_write(path):
                raise ValueError(f"path outside the allowlist: {path}")
        return self


# ---------------------------------------------------------------- authorization (Command Gate)

class ApprovalOption(Strict):
    id: str = Field(pattern=r"^[a-z_]{1,24}$")
    label: str = Field(min_length=1, max_length=120)


class ApprovalRequest(Strict):
    """What the workflow asks a person. The Control Center shows it from the approval.requested event."""
    request_id: str = Field(pattern=r"^apr-[0-9]{1,3}$")
    purpose: Literal["recovery", "deploy", "acceptance"]
    options: list[ApprovalOption] = Field(min_length=1, max_length=3)


class ApprovalDecision(Strict):
    """Read from the approval.decided event the Control Center writes. option_id 'timeout' means nobody chose."""
    request_id: str
    option_id: str
    option_label: str = ""
    approver: str = ""
    method: str = ""
    latency_s: float = 0


# ---------------------------------------------------------------- policy (blueprint 5.7)

PreauthorizableAction = Literal["restore_kv_secrets_user", "deploy_candidate_to_twin"]


class Budgets(Strict):
    max_candidate_attempts: int = Field(default=2, ge=1, le=2)
    max_tool_calls_per_agent_call: int = Field(default=25, ge=1, le=60)
    max_tokens: int = Field(default=400_000, ge=10_000)
    max_drill_s: int = Field(default=5400, ge=300)            # whole drill, including propagation waits
    wait_s: int = Field(default=1800, ge=60)                  # one wait for access to change
    approval_timeout_s: int = Field(default=900, ge=30)
    ci_timeout_s: int = Field(default=900, ge=60)
    agent_timeout_s: int = Field(default=600, ge=30)


class AutonomyPolicy(Strict):
    """Chosen before the drill and evaluated outside the model. Production and merge never appear here."""
    version: str = "policy-1"
    mode: Mode = Mode.SUPERVISED
    # The broad action can never be listed: it is not in PreauthorizableAction.
    preauthorized_actions: list[PreauthorizableAction] = Field(
        default_factory=lambda: ["restore_kv_secrets_user", "deploy_candidate_to_twin"])
    min_confidence: float = Field(default=0.6, ge=0, le=1)
    prove_durable: bool = True        # after the fix passes, fault and restore again; the fix must recover alone
    budgets: Budgets = Field(default_factory=Budgets)
    expires_at: str

    @field_validator("expires_at")
    @classmethod
    def is_iso_time(cls, value):
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


Scenario = Literal["T-00", "T-01", "T-02", "T-04", "T-05", "T-06", "T-07", "T-08", "T-09", "T-10"]


class DrillConfig(Strict):
    drill_id: str = Field(pattern=DRILL_ID)
    scenario: Scenario = "T-01"
    policy: AutonomyPolicy
    base_ref: str = "main"                     # the pinned commit the twin and the candidate start from
    image_repo: str = Field(pattern=r"^ghcr\.io/[a-z0-9._-]+/order-service$")


# ---------------------------------------------------------------- the verdict and the evidence package

class VerdictInputs(Strict):
    """Blueprint 5.5. Every input is computed from events.jsonl alone, so anyone can recompute it."""
    authorized_action_completed: bool
    policy_passed: bool
    technical_health_passed: bool
    ci_gates_passed: bool
    journey_passed: bool
    persisted_order_assertion_passed: bool
    reasons: list[str] = Field(default_factory=list)

    @property
    def verified(self) -> bool:
        return all([self.authorized_action_completed, self.policy_passed, self.technical_health_passed,
                    self.ci_gates_passed, self.journey_passed, self.persisted_order_assertion_passed])


class EvidencePackage(Strict):
    """evidence/<drill_id>/package.json: the sealed summary of one drill (blueprint 8.1, A.9)."""
    contract_version: str = CONTRACT_VERSION
    drill_id: str = Field(pattern=DRILL_ID)
    scenario: str
    terminal_state: Verdict
    reason: str
    verdict_inputs: VerdictInputs | None
    policy: AutonomyPolicy
    agents: dict[str, str]                      # role -> "name:version"
    prompt_sha256: dict[str, str]               # role -> sha256 of the instructions file
    model_deployment: str
    base_ref: str
    candidate: dict                             # branch, commits, ci run, image, change report
    options: dict | None                        # the Planner's first output, in full
    brief: dict | None
    counts: dict[str, int]                      # events, tool calls, denials, referee runs, attempts
    usage: dict[str, float]                     # tokens, est_cost_usd
    timeline: dict[str, str]                    # started, fault, false_green, verified, ended
    events_sha256: str
    events_count: int
    residual_risks: list[str]
