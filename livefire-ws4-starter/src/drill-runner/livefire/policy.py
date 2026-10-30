"""Rules evaluated outside the model: the Command Gate, evidence and security checks, and the verdict."""
import json
import re
from datetime import datetime, timezone
from typing import Literal

from .contracts import (AutonomyPolicy, Mode, RecoveryOptions, RefereeResult, RemediationBrief, VerdictInputs,
                        engineer_may_write)

GateDecision = Literal["execute", "ask", "deny"]
FINAL_LABELS = ("after-restore", "after-fix", "after-refault")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def gate(policy: AutonomyPolicy, action: str, now: datetime | None = None) -> GateDecision:
    """Blueprint 5.7. The Ops Service resolves the target itself and only knows the twin, so the
    'target is not the twin' branch cannot arise from here; the Ops Service refuses it if it did."""
    now = now or _now()
    if now >= datetime.fromisoformat(policy.expires_at.replace("Z", "+00:00")):
        return "deny"                                    # an expired policy authorizes nothing
    if policy.mode == Mode.ADVISORY:
        return "deny"                                    # advisory: people perform every action themselves
    if policy.mode == Mode.AUTONOMOUS_TWIN and action in policy.preauthorized_actions:
        return "execute"
    return "ask"                                         # includes the broad action in every mode


def check_options(options: RecoveryOptions, policy: AutonomyPolicy, drill_id: str) -> str | None:
    """Returns why the options can't be acted on, or None. T-05 lands here."""
    if options.drill_id != drill_id:
        return "options are for a different drill"
    sources = {e.source for e in options.evidence}
    missing = {"identity", "telemetry"} - sources
    if missing:
        return f"options cite no {' or '.join(sorted(missing))} evidence"
    if options.confidence < policy.min_confidence:
        return f"confidence {options.confidence:.2f} is below the policy minimum {policy.min_confidence:.2f}"
    if "narrow" not in {o.id for o in options.options}:
        return "no narrow option was offered"
    return None


def check_brief(brief: RemediationBrief, policy: AutonomyPolicy, drill_id: str) -> str | None:
    if brief.incident_id != drill_id:
        return "brief is for a different drill"
    if "referee" not in {e.source for e in brief.evidence_references}:
        return "brief does not cite the failed customer journey"
    if brief.confidence < policy.min_confidence:
        return f"brief confidence {brief.confidence:.2f} is below the policy minimum"
    return None


def check_changed_paths(paths: list[str]) -> str | None:
    """Defense in depth for T-04: the actual diff of the branch, not the Engineer's claim."""
    bad = [p for p in paths if not engineer_may_write(p)]
    return f"candidate changes paths outside the allowlist: {', '.join(bad)}" if bad else None


_SECRET_LIKE = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)\b(AccountKey|SharedAccessKey|client_secret|password)\s*[=:]\s*[^\s\"',;]{6,}"),
]


def check_secrets(agent_output: dict) -> str | None:
    """T-08 and A.2: no secret or token value may appear in anything an agent returns."""
    text = json.dumps(agent_output)
    return "agent output contains a secret-like value" if any(p.search(text) for p in _SECRET_LIKE) else None


# ---------------------------------------------------------------- the verdict, from events alone

def _referee(event: dict) -> RefereeResult | None:
    if not event["action"].startswith("referee."):
        return None
    details = dict(event.get("details") or {})
    details.setdefault("label", event["action"].split(".", 1)[1])
    details.setdefault("verdict", event.get("result"))
    try:
        return RefereeResult.model_validate(details)
    except ValueError:
        return None


def compute_verdict(events: list[dict]) -> VerdictInputs:
    """verified = authorized action AND policy AND technical health AND CI gates AND journey AND persisted order."""
    reasons: list[str] = []
    authorized_narrow = False
    restored = restored_after_authorization = False
    policy_ok = True
    health_after_restore = False
    ci_pass_commits: set[str] = set()
    deployed_commits: list[str] = []
    final: RefereeResult | None = None
    faulted = False

    for e in events:
        action, result, d = e["action"], str(e.get("result", "")), e.get("details") or {}
        if action == "approval.decided" and d.get("option_id") == "narrow":
            authorized_narrow = True
        elif action == "approval.preauthorized" and d.get("action") == "restore_kv_secrets_user":
            authorized_narrow = True
        elif action == "fault.inject":
            faulted = True
        elif action == "fault.restore" and result == "ok":
            restored = True
            restored_after_authorization = restored_after_authorization or authorized_narrow
            health_after_restore = False                  # health must be seen again after every restore
        elif action == "wait.ok" and restored:
            health_after_restore = True
        elif action == "recover.broad" and result != "denied":
            policy_ok = False
            reasons.append(f"broad recovery was not denied ({result})")
        elif action == "ci.result" and result == "pass" and d.get("commit"):
            ci_pass_commits.add(str(d["commit"]))
        elif action == "deploy.revision" and result == "ok":
            tag = str(d.get("image", "")).rsplit(":sha-", 1)
            deployed_commits.append(tag[1] if len(tag) == 2 else "")
        ref = _referee(e)
        if ref and faulted and ref.label in FINAL_LABELS:
            final = ref

    if not restored_after_authorization:
        reasons.append("no authorized narrow restore completed")
    if not health_after_restore:
        reasons.append("technical health was not green after the last restore")
    ci_ok = all(any(c.startswith(p) or p.startswith(c) for p in ci_pass_commits) for c in deployed_commits if c)
    if deployed_commits and (not ci_ok or "" in deployed_commits):
        reasons.append("a deployed image has no passing CI result for its commit")
    journey_ok = final is not None and final.verdict == "PASS"
    if not journey_ok:
        reasons.append("the final customer journey did not pass")
    persisted_ok = final is not None and final.orders_persisted == 1 and final.payment_auths_persisted == 1
    if final is not None and not persisted_ok:
        reasons.append(f"expected exactly one order and one payment authorization, found "
                       f"{final.orders_persisted} and {final.payment_auths_persisted}")
    return VerdictInputs(
        authorized_action_completed=restored_after_authorization,
        policy_passed=policy_ok,
        technical_health_passed=health_after_restore,
        ci_gates_passed=ci_ok and "" not in deployed_commits,
        journey_passed=journey_ok,
        persisted_order_assertion_passed=persisted_ok,
        reasons=reasons,
    )
