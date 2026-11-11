"""Events to stages, banner, split view, feed and scorecard.

Everything on the drill screen is computed here by one pure function, build_view(events, package).
It has no I/O and no clock, so the whole screen is unit-testable and a reconnecting browser
recovers by itself. The rules come from contracts/event-catalog.md, sections 6 and 7.

G3 adds the rest of the business scorecard (blueprint 1.6) and the Improvements panel (2.2, 8),
read from WS4's sealed evidence package. The package's two checksums are re-computed here, so the
screen says whether the package and the events it summarises are still exactly what was sealed.
"""
from __future__ import annotations

import hashlib
import json
import os

from board import build_board
from events import parse_ts

STAGES = ["Baseline", "Fault", "Investigate", "Decide", "Restore",
          "False Green", "Repair", "Verify", "Evidence"]

BANNERS = {
    "healthy": "HEALTHY",
    "cant-order": "CUSTOMERS CAN'T ORDER",
    "false-green": "FALSE GREEN",
    "ordering-again": "CUSTOMERS ORDERING AGAIN",
}

FEED_LIMIT = 200
QUIET_IN_FEED = {"metrics.orders", "usage"}  # shown in the scorecard tiles, not as feed lines


def link_prefixes() -> tuple[str, ...]:
    raw = os.environ.get("LINK_PREFIXES", "https://ai.azure.com/,https://portal.azure.com/,https://github.com/")
    return tuple(p.strip() for p in raw.split(",") if p.strip().startswith("https://"))


def safe_link(url) -> str | None:
    """Only links with an allowed https prefix reach the page, and only as an href attribute."""
    if isinstance(url, str) and len(url) < 2048 and url.startswith(link_prefixes()):
        return url
    return None


TOOL_CALLS = {"planner.tool_call": "Planner", "engineer.tool_call": "Engineer", "recovery.tool_call": "Recovery"}


def artifact_prefix() -> str:
    """Improvement artifacts link only into the team's own GitHub org: a stub's example.com or another
    org's repository is shown as text, never as a link."""
    return os.environ.get("ARTIFACT_LINK_PREFIX", "https://github.com/livefire-team/")


def artifact_link(url) -> str | None:
    if (isinstance(url, str) and len(url) < 2048 and url.startswith(artifact_prefix())
            and url.startswith("https://") and not any(c.isspace() for c in url)):
        return url
    return None


def default_target_s() -> float | None:
    """The team's recovery target, used only when the drill doesn't carry one (WS4 contracts v1.1)."""
    try:
        value = float(os.environ.get("TARGET_RECOVERY_S", ""))
    except ValueError:
        return None
    return value if 0 < value < 86400 else None


def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _text(value, limit: int = 300) -> str:
    if value is None:
        return ""
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _is_broad(option: dict) -> bool:
    return option.get("broad") is True or "broad" in str(option.get("id", "")).lower()


def _options(details: dict) -> list[dict]:
    raw = details.get("options")
    if not isinstance(raw, list):
        return []
    return [{"id": _text(o.get("id"), 64), "label": _text(o.get("label"), 200), "broad": _is_broad(o)}
            for o in raw if isinstance(o, dict) and o.get("id") is not None]


def _stubbed(details: dict) -> bool:
    """WS4's stub runner says so on drill.start; the screen must never pass it off as a live run."""
    agents = details.get("agents")
    return details.get("model_deployment") == "stub" or (
        isinstance(agents, dict) and "stub" in [str(v) for v in agents.values()])


def _duration(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    return max((parse_ts(end) - parse_ts(start)).total_seconds(), 0.0)


# ---------------------------------------------------------------- stage mapping

APPROVAL_STAGE = {"recovery": "Decide", "deploy": "Repair", "acceptance": "Evidence"}
BOARD_TITLES = {"recovery": "Asked to choose: how to recover",
                "deploy": "Asked: deploy the fix to the twin?",
                "acceptance": "Asked: open a pull request for review?"}
DECISION_TITLE = {"recovery": "Decision needed: how to recover",
                  "deploy": "Decision needed: deploy the candidate to the twin?",
                  "acceptance": "Decision needed: open the pull request for human review?"}
NO_STAGE = {"metrics.orders", "usage", "kill.requested", "drift.check", "grant.refused", "wait.drifted"}


def _purpose(details: dict, s: dict) -> str:
    """recovery, deploy or acceptance: from the event, or from the request it answers (G3, WS4's gate)."""
    purpose = details.get("purpose") or s["purposes"].get(details.get("request_id"))
    return purpose if purpose in APPROVAL_STAGE else "recovery"


def _stage_for(action: str, result: str, d: dict, s: dict) -> str | None:
    """Which stage an event belongs to, given what happened before it (state s)."""
    fault, restored, false_green = s["fault_seen"], s["restore_seen"], s["false_green_seen"]

    if action in NO_STAGE:
        return None  # scorecard numbers, checks and refusals: shown in the feed, never move the stage
    if s["verdict_seen"]:
        return "Evidence"  # after the verdict: package, pull-request decision, final reset
    if action == "drill.start":
        return "Baseline"
    if action in ("verdict", "drill.end") or action.startswith("package."):
        return "Evidence"
    if action.startswith("approval."):
        return APPROVAL_STAGE[_purpose(d, s)]
    if false_green and action in ("fault.inject", "wait.forbidden", "fault.restore", "recover.narrow"):
        return "Verify"  # the second fault, injected to prove the fix survives it
    if action in ("fault.inject", "wait.forbidden"):
        return "Fault"
    if action.startswith("referee."):
        label = action.split(".", 1)[1]
        if label == "after-restore" and result == "FAIL":
            return "False Green"
        if label == "baseline" or not fault:
            return "Baseline"
        if label == "during-fault":
            return "Fault"
        if false_green:
            return "Verify"
        return "Restore" if restored else "Fault"
    if action in ("reset", "restart"):
        if false_green:
            return "Verify"
        return "Baseline" if not fault else "Restore"
    if action == "wait.ok":
        if not fault:
            return "Baseline"
        return "Verify" if false_green else "Restore"
    if action in ("fault.restore", "recover.narrow"):
        return "Restore"
    if action == "recover.broad":
        return "Decide"
    if action.startswith("planner."):
        return "Repair" if false_green else "Investigate"
    if action.startswith("engineer.") or action == "ci.result":
        return "Repair"
    if action == "deploy.revision":
        return "Verify"
    return None  # metrics.orders, usage, kill.requested and anything new: no stage change


# ---------------------------------------------------------------- feed lines

def _feed_line(event: dict) -> tuple[str, str]:
    """A plain-language sentence for one event, and its tone: pass, fail, deny or info."""
    a, r, d = event["action"], event["result"], event.get("details") or {}

    if a == "drill.start":
        stub = " with stubbed agents and Ops: no Azure, no model" if _stubbed(d) else ""
        return f"Drill started in {_text(d.get('mode') or 'supervised')} mode{stub}", "info"
    if a == "drill.end":
        if r == "cleanup_failed":
            return "Drill ended, but cleanup failed: check the twin before the next drill", "fail"
        return "Drill ended", "info"
    if a == "fault.inject":
        extra = f" ({_text(d.get('model'))})" if d.get("model") else ""
        return f"Fault injected: the app's Key Vault access was removed{extra}", "fail"
    if a == "fault.restore":
        who = {"agent": " by the Recovery agent", "workflow": " by the workflow"}.get(d.get("executed_by"), "")
        approved = f", approved by {_text(d.get('approved_by'), 80)}" if d.get("approved_by") else ""
        return f"Key Vault access restored for the app identity{who}{approved}", "info"
    if a == "wait.drifted":
        return f"The twin drifted from its baseline{_after(_num(d.get('seconds')))}", "fail"
    if a == "drift.check":
        if r == "ok":
            return "Drift check: the app's Key Vault access matches the baseline", "pass"
        return "Drift check: the app's Key Vault access is still missing", "fail"
    if a == "grant.refused":
        why = f": {_text(d.get('reason'), 120)}" if d.get("reason") else ""
        return f"Ops Service refused a recovery grant for {_text(d.get('action') or 'an action', 80)}{why}", "deny"
    if a == "restart":
        if r == "ok":
            return f"App restarted{_after(_num(d.get('seconds')))}", "info"
        return "App restart did not finish in time", "fail"
    if a == "wait.forbidden":
        secs = _num(d.get("seconds"))
        if r == "ok":
            return f"Fault took effect{_after(secs)}: Key Vault now refuses the app (403)", "fail"
        return f"Still waiting for the fault to take effect{_after(secs)}", "info"
    if a == "wait.ok":
        secs = _num(d.get("seconds"))
        if r == "ok":
            return f"Dependency check is green{_after(secs)}", "pass"
        return f"Dependency check still not green{_after(secs)}", "fail"
    if a.startswith("referee."):
        label = a.split(".", 1)[1]
        if r not in ("PASS", "FAIL"):
            why = f": {_text(d.get('reason'), 120)}" if d.get("reason") else ""
            return f"Customer journey ({label}) could not run, so there is no verdict{why}", "fail"
        if r == "PASS":
            saved = _num(d.get("orders_persisted"))
            tail = f": {saved} order saved" if saved is not None else ""
            return f"Customer journey ({label}) passed{tail}", "pass"
        step = f" at {_text(d.get('last_step'))}" if d.get("last_step") else ""
        err = f": {_text(d.get('error'))}" if d.get("error") else ""
        # WS2's Referee keeps a Playwright trace of a failed run as a path inside the evidence store, not a URL
        trace = " (trace saved in the evidence store)" if str(d.get("trace_url") or "").startswith("evidence/") else ""
        return f"Customer journey ({label}) failed{step}{err}{trace}", "fail"
    if a == "recover.broad":
        if r == "denied":
            code = f" ({_text(d.get('code'))})" if d.get("code") else ""
            by = _text(d.get("refused_by"), 40) or "the platform"
            if d.get("simulated"):  # a laptop run: no Azure call was made, so never claim Azure refused it
                return f"Broad recovery denied (simulated on a laptop, as {by} would){code}", "deny"
            return f"Broad recovery denied by {by}{code}", "deny"
        if r == "UNEXPECTED_ALLOW_NOT_REMOVED":
            return "Broad recovery was ALLOWED and NOT removed: remove the role by hand now", "fail"
        if r.startswith("UNEXPECTED_ALLOW"):
            return "Broad recovery was NOT denied (the grant was removed). Check the policy assignment", "fail"
        return "Broad recovery attempt failed without a policy decision", "fail"
    if a == "recover.narrow":
        return "Narrow recovery approved: only Key Vault Secrets User restored", "info"
    if a == "reset":
        if d.get("by") == "kill":
            return "Kill switch: the twin was reset to its baseline", "fail"
        return "Twin reset: access restored, revision rolled back, drill orders cleared", "info"
    if a in TOOL_CALLS:
        # WS3's MCP server writes one of these per tool call (contracts/mcp-contract.md, section 7)
        who = TOOL_CALLS[a]
        tool = _text(d.get("tool") or "a tool", 80)
        why = f": {_text(d.get('summary'))}" if d.get("summary") else ""
        decision = d.get("decision") or r
        if decision == "deny":
            caller = str(d.get("caller") or "")
            endpoint = str(d.get("endpoint") or "")
            caller_role = caller.split(":", 1)[1] if caller.startswith("key:") else ""
            if caller_role and endpoint and caller_role != endpoint:  # T-03: a credential used on another endpoint
                return (f"Cross-role call refused: the {_text(caller_role.title(), 20)} credential on the "
                        f"{_text(endpoint.title(), 20)} tools ({tool}){why}"), "deny"
            where = f" on the {_text(endpoint, 40)} tools" if endpoint else ""
            return f"{who} was denied {tool}{where}{why}", "deny"
        if decision == "error":
            return f"{who}'s call to {tool} failed{why}", "fail"
        return f"{who} called {tool}{why}", "info"
    if a == "planner.options":
        labels = [o["label"] for o in _options(d)]
        return f"Planner proposed {len(labels)} recovery options: " + "; ".join(labels), "info"
    if a == "planner.brief":
        return f"Planner wrote the repair brief: {_text(d.get('summary'))}", "info"
    if a == "approval.requested":
        what = {"deploy": ": deploy the candidate?", "acceptance": ": open the pull request?"}.get(d.get("purpose"), "")
        return f"Waiting for a human decision{what}", "info"
    if a == "approval.preauthorized":
        mode = _text(d.get("mode") or "drill", 40)
        return f"Pre-authorized by the {mode} policy: {_text(d.get('action') or 'action', 80)}", "info"
    if a == "approval.decided" and (r == "timeout" or d.get("option_id") == "timeout"):
        return "Nobody decided in time; the workflow stops rather than guess", "fail"
    if a == "approval.decided":
        lat = _num(d.get("latency_s"))
        after = f" after {round(lat)} s" if lat is not None else ""
        return f"{_text(d.get('approver') or 'Someone', 80)} chose “{_text(d.get('option_label') or d.get('option_id'))}”{after}", "info"
    if a == "engineer.change_report":
        return f"Engineer pushed a candidate fix to {_text(d.get('branch'), 120)}", "info"
    if a == "ci.result":
        return ("CI passed on the candidate", "pass") if r == "pass" else ("CI failed on the candidate", "fail")
    if a == "deploy.revision":
        if r != "ok":
            return f"Candidate deploy refused: {_text(d.get('reason') or r, 120)}", "deny"
        secs = _num(d.get("seconds"))
        return f"Candidate revision deployed{_after(secs)}: {_text(d.get('image'), 160)}", "info"
    if a == "verdict":
        return f"Verdict: {_text(r, 64)}", "pass" if r == "VERIFIED" else "fail"
    if a == "kill.requested":
        return f"Kill switch pressed by {_text(d.get('by') or 'someone', 80)}", "fail"
    if a == "package.ready":
        if r != "ok":
            return "Evidence package could not be sealed", "fail"
        return "Evidence package ready", "pass"
    if a == "package.pr":
        if r == "ok":
            return "Pull request opened for human review (left unmerged)", "pass"
        return {"rejected": ("Pull request not opened: the reviewer declined", "info"),
                "no_decision": ("Pull request not opened: nobody decided in time", "info")}.get(
                    r, ("Pull request could not be opened", "fail"))
    return f"{a}: {_text(r, 64)}", "info"


def _after(secs) -> str:
    return f" after {round(secs)} s" if secs is not None else ""


def _feed_link(event: dict) -> tuple[str | None, str | None]:
    d = event.get("details") or {}
    for key, label in (("trace_url", "trace"), ("run_url", "CI run"), ("pr_url", "pull request"),
                       ("evidence_url", "evidence")):
        url = safe_link(d.get(key))
        if url:
            return url, label
    return None, None



# ---------------------------------------------------------------- the sealed package and the Improvements panel

PACKAGE_MAX_BYTES = 1_000_000
DEBT_TYPES = {"application-resilience": "Application resilience", "regression-coverage": "Regression coverage",
              "runbook": "Runbook", "knowledge": "Knowledge"}


def _events_sha256(events: list[dict]) -> str:
    """WS4's events_sha256: compact JSON of each event, one per line (livefire/evidence.py, build())."""
    raw = "".join(json.dumps(e, separators=(",", ":")) + "\n" for e in events).encode()
    return hashlib.sha256(raw).hexdigest()


def _strings(value, limit: int = 300, most: int = 10) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_text(v, limit) for v in value[:most] if isinstance(v, (str, int, float)) and str(v).strip()]


def _debt_item(kind, finding, artifact, status, evidence_refs=(), residual_risk=None, url=None) -> dict:
    artifact = _text(artifact, 200) or None
    return {"type": kind if kind in DEBT_TYPES else "knowledge",
            "type_label": DEBT_TYPES.get(kind, _text(kind, 40) or "Knowledge"),
            "finding": _text(finding, 400) or None, "artifact": artifact,
            "url": artifact_link(url) or artifact_link(artifact), "status": _text(status, 120) or None,
            "evidence_refs": [_text(r, 80) for r in evidence_refs][:6],
            "residual_risk": _text(residual_risk, 300) or None}


def _debt_from_contract_1_0(pkg: dict, verdict: str | None, pr_opened: bool, pr_url) -> list[dict]:
    """WS4's contract 1.0 has no operational_debt list yet, so the panel lists what the package does hold:
    the candidate fix, and the regression test and runbook its change report says it changed."""
    cand = pkg.get("candidate") if isinstance(pkg.get("candidate"), dict) else {}
    report = cand.get("change_report") if isinstance(cand.get("change_report"), dict) else {}
    brief = pkg.get("brief") if isinstance(pkg.get("brief"), dict) else {}
    if not cand.get("branch"):
        return []
    if pr_opened:
        status = "pull request opened for review (unmerged)"
    elif verdict == "VERIFIED":
        status = "verified on the twin; no pull request yet"
    else:
        status = "not verified"
    refs = []
    for item in report.get("evidence_to_change") or []:
        ref = item.get("evidence_ref") if isinstance(item, dict) else None
        if isinstance(ref, str) and ref not in refs:
            refs.append(ref)
    items = [_debt_item("application-resilience", brief.get("required_behavior") or brief.get("supported_hypothesis"),
                        cand.get("branch"), status, refs, url=pr_url or cand.get("ci_run"))]
    if report.get("regression_test_path"):
        items.append(_debt_item("regression-coverage", "Regression test added with the fix",
                                report["regression_test_path"], status))
    if report.get("runbook_path"):
        items.append(_debt_item("runbook", "Runbook updated with the fix", report["runbook_path"], status))
    return items


def _improvements(drill_id: str | None, events: list[dict], package: bytes | None, verdict: str | None) -> dict:
    ready = next((e for e in events if e["action"] == "package.ready" and e["result"] == "ok"), None)
    panel = {"state": "waiting", "contract_version": None, "source": None, "integrity": None,
             "debt": [], "next_drill": None, "residual_risks": []}
    if ready is None:
        return panel  # nothing sealed yet; in replay, the package appears when the story reaches it
    if package is None:
        return {**panel, "state": "loading"}
    if len(package) > PACKAGE_MAX_BYTES:
        return {**panel, "state": "unreadable"}
    try:
        pkg = json.loads(package.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {**panel, "state": "unreadable"}
    if not isinstance(pkg, dict):
        return {**panel, "state": "unreadable"}

    d = ready.get("details") or {}
    sealed_sha = d.get("sha256")
    count = pkg.get("events_count")
    integrity = {
        "package": (hashlib.sha256(package).hexdigest() == sealed_sha) if isinstance(sealed_sha, str) else None,
        "same_drill": pkg.get("drill_id") == drill_id if drill_id else None,
        "events": None,
        "events_count": count if isinstance(count, int) else None,
        "sha256": sealed_sha[:16] if isinstance(sealed_sha, str) else None,
    }
    if isinstance(count, int) and 0 < count <= len(events) and isinstance(pkg.get("events_sha256"), str):
        integrity["events"] = _events_sha256(events[:count]) == pkg["events_sha256"]
    integrity["ok"] = (integrity["package"] is True and integrity["same_drill"] is not False
                       and integrity["events"] is not False)

    pr = next((e for e in events if e["action"] == "package.pr"), None)
    pr_opened = bool(pr and pr["result"] == "ok")
    pr_url = (pr.get("details") or {}).get("pr_url") if pr else None

    raw_debt = pkg.get("operational_debt")
    if isinstance(raw_debt, list):  # WS4 contracts v1.1
        debt = [_debt_item(i.get("type"), i.get("finding"), i.get("artifact"), i.get("status"),
                           _strings(i.get("evidence_refs"), 80), i.get("residual_risk"))
                for i in raw_debt[:12] if isinstance(i, dict)]
        source = "WS4's operational-debt record"
    else:
        debt = _debt_from_contract_1_0(pkg, verdict, pr_opened, pr_url)
        source = "the evidence package's change report (WS4 contract 1.0 has no debt record yet)"

    nxt = pkg.get("next_drill_recommendation")
    next_drill = None
    if isinstance(nxt, dict) and (nxt.get("scenario") or nxt.get("reason")):
        next_drill = {"scenario": _text(nxt.get("scenario"), 80) or None, "reason": _text(nxt.get("reason"), 300) or None}

    return {"state": "sealed", "contract_version": _text(pkg.get("contract_version"), 16) or None,
            "source": source, "integrity": integrity, "debt": debt, "next_drill": next_drill,
            "residual_risks": _strings(pkg.get("residual_risks"))}

# ---------------------------------------------------------------- the view

def build_view(events: list[dict], package: bytes | None = None) -> dict:
    """The whole drill screen. package is WS4's sealed package.json as stored (bytes), once it exists;
    it is used only after the package.ready event, so a replay shows it when the story reaches it."""
    s = {"fault_seen": False, "restore_seen": False, "false_green_seen": False, "verdict_seen": False,
         "purposes": {}}
    visited: list[str] = []
    current = None

    infra = {"state": "healthy", "text": "Dependency check green", "since": None}
    journey = {"state": "unknown", "text": "No customer journey run yet", "label": None,
               "last_step": None, "error": None, "orders_persisted": None, "ts": None}
    pass_after_fault = False
    verdict = None
    killed_by = None
    ended = False
    stubbed = False

    first_fault_ts = first_wait_ok_after_fault = recovery_ts = None
    after_restore_failed = False
    deploy_before_recovery = False
    denials = decisions = 0
    decision_time = 0.0
    tool_calls = tool_denials = 0
    tokens = 0
    cost = 0.0
    usage_seen = False
    metrics = None
    # G3 scorecard (blueprint 1.6): target, drill economics, backlog peak, failed orders during the incident
    mode = None
    drill_target_s = None
    started_ts = ended_ts = None
    browser_s, browser_seen = 0.0, False
    backlog_peak = None
    failed_base = failed_end = None
    preauthorized = 0
    requests: dict[str, dict] = {}
    decided: set[str] = set()
    links: list[dict] = []
    feed: list[dict] = []
    card_stages: list[str | None] = []
    metrics_seen = False

    for e in events:
        a, r, d, ts = e["action"], e["result"], e.get("details") or {}, e["ts"]

        stage = _stage_for(a, r, d, s)
        if stage:
            current = stage
            if stage not in visited:
                visited.append(stage)
        card_stages.append(stage or current)

        # state the stage rules depend on, updated after classifying this event
        if a == "drill.start" and _stubbed(d):
            stubbed = True

        # G3 scorecard inputs; recovery_ts and verdict still hold their values from before this event
        if a == "drill.start":
            started_ts = started_ts or ts
            mode = mode or (_text(d.get("mode"), 40) or None)
            target = _num(d.get("target_recovery_s"))
            if target and target > 0:
                drill_target_s = float(target)
        elif a == "drill.end":
            ended_ts = ts
        elif a.startswith("referee."):
            secs = _num(d.get("duration_s"))
            if secs is not None and secs >= 0:
                browser_s += secs
                browser_seen = True
        elif a == "metrics.orders":
            metrics_seen = True
            peak = _num(d.get("backlog"))
            if peak is not None:
                backlog_peak = peak if backlog_peak is None else max(backlog_peak, peak)
            failed = _num(d.get("failed_total"))
            if failed is not None:
                if not s["fault_seen"] or failed_base is None:
                    failed_base = failed  # the last count before the fault, or the first one after it
                if s["fault_seen"] and recovery_ts is None and verdict is None:
                    failed_end = failed   # counted until verified recovery, or the verdict if it never came
        elif a == "approval.preauthorized":
            preauthorized += 1
        if a == "fault.inject":
            s["fault_seen"] = True
            first_fault_ts = first_fault_ts or ts
            infra = {"state": "waiting", "text": "Waiting for the access change to take effect", "since": ts}
            pass_after_fault = False
        elif a in ("fault.restore", "recover.narrow"):
            if s["fault_seen"]:
                s["restore_seen"] = True
            infra = {"state": "waiting", "text": "Access restored, waiting for it to take effect", "since": ts}
        elif a == "wait.forbidden":
            if r == "ok":
                infra = {"state": "failing", "text": "Dependency check failing: Key Vault returns 403", "since": ts}
        elif a == "wait.ok":
            if r == "ok":
                infra = {"state": "healthy", "text": "Dependency check green: Key Vault and SQL reachable", "since": ts}
                if s["fault_seen"] and first_wait_ok_after_fault is None:
                    first_wait_ok_after_fault = ts
            else:
                infra = {"state": "failing", "text": "Dependency check still failing", "since": ts}
        elif a == "reset":
            infra = {"state": "healthy", "text": "Twin reset to the baseline revision", "since": ts}
        elif a.startswith("referee.") and r not in ("PASS", "FAIL"):
            pass  # INCONCLUSIVE: the Referee gave no verdict, so the journey state stays as it was
        elif a.startswith("referee."):
            label = a.split(".", 1)[1]
            passed = r == "PASS"
            journey = {"state": "pass" if passed else "fail",
                       "text": "Order placed and saved" if passed else "Customers can't complete an order",
                       "label": _text(label, 64), "last_step": _text(d.get("last_step"), 80) or None,
                       "error": _text(d.get("error")) or None,
                       "orders_persisted": _num(d.get("orders_persisted")), "ts": ts}
            if label == "after-restore" and not passed:
                s["false_green_seen"] = True
                after_restore_failed = True
            if passed and s["fault_seen"]:
                pass_after_fault = True
                if recovery_ts is None:
                    recovery_ts = ts
        elif a == "deploy.revision":
            if recovery_ts is None:
                deploy_before_recovery = True
        elif a == "recover.broad":
            if r == "denied":
                denials += 1
        elif a == "approval.requested":
            rid = d.get("request_id")
            if isinstance(rid, str) and rid:
                purpose = _purpose(d, s)
                s["purposes"][rid] = purpose
                requests[rid] = {"request_id": rid, "options": _options(d), "requested_at": ts,
                                 "purpose": purpose, "title": DECISION_TITLE[purpose]}
        elif a == "approval.decided":
            rid = d.get("request_id")
            if isinstance(rid, str):
                decided.add(rid)
            lat = _num(d.get("latency_s"))
            decisions += 1
            decision_time += lat or 0.0
        elif a in TOOL_CALLS:
            tool_calls += 1
            if (d.get("decision") or r) == "deny":
                tool_denials += 1
        elif a == "usage":
            usage_seen = True
            tokens += int(_num(d.get("tokens")) or 0)
            cost += float(_num(d.get("est_cost_usd")) or 0.0)
        elif a == "metrics.orders":
            metrics = d
        elif a == "verdict":
            verdict = r
            s["verdict_seen"] = True
        elif a == "kill.requested":
            killed_by = _text(d.get("by") or "someone", 80)
        elif a == "drill.end":
            ended = True
        elif a == "package.ready":
            for key, label in (("evidence_url", "Evidence package"), ("pr_url", "Pull request (unmerged)")):
                url = safe_link(d.get(key))
                if url:
                    links.append({"label": label, "url": url})

        if a not in QUIET_IN_FEED:
            text, tone = _feed_line(e)
            if a == "fault.inject" and stage == "Verify":
                text = "Fault injected again, to prove the fix survives it"
            url, link_label = _feed_link(e)
            feed.append({"ts": ts, "time": ts[11:19], "text": text, "tone": tone,
                         "stage": stage, "url": url, "link_label": link_label})

    # banner: the verdict always wins
    if verdict:
        kind = "verified" if verdict == "VERIFIED" else ("false-green" if verdict == "FALSE_GREEN" else "escalated")
        text = verdict.replace("_", " ", 1) if verdict.startswith("ESCALATED_") else verdict.replace("_", " ")
        banner = {"kind": kind, "text": _text(text, 64), "from_verdict": True}
    elif journey["state"] == "fail" and infra["state"] == "healthy":
        banner = {"kind": "false-green", "text": BANNERS["false-green"], "from_verdict": False}
    elif journey["state"] == "fail":
        banner = {"kind": "cant-order", "text": BANNERS["cant-order"], "from_verdict": False}
    elif journey["state"] == "pass" and pass_after_fault:
        banner = {"kind": "ordering-again", "text": BANNERS["ordering-again"], "from_verdict": False}
    else:
        banner = {"kind": "healthy", "text": BANNERS["healthy"], "from_verdict": False}

    timeline = []
    for name in STAGES:
        status = "current" if name == current else ("done" if name in visited else "pending")
        timeline.append({"name": name, "status": status, "false_green": name == "False Green" and name in visited})

    open_requests = [req for rid, req in requests.items() if rid not in decided]
    decision = None
    if open_requests and not killed_by:
        decision = {k: v for k, v in open_requests[-1].items()}

    recovered_by = None
    if recovery_ts:
        recovered_by = "candidate fix" if deploy_before_recovery else "restart"

    verified_s = _duration(first_fault_ts, recovery_ts)
    target_s, target_source = (drill_target_s, "drill") if drill_target_s else (default_target_s(), "team setting")
    if target_s is None:
        target_source = None
    if s["fault_seen"] and failed_base is not None and failed_end is not None:
        failed_orders = max(failed_end - failed_base, 0)
    elif metrics and _num(metrics.get("failed_total")) is not None:
        failed_orders = 0 if not s["fault_seen"] else None
    else:
        failed_orders = None
    backlog_now = _num(metrics.get("backlog")) if metrics else None
    drill_id = events[0].get("drill_id") if events else None
    improvements = _improvements(drill_id, events, package, verdict)

    scorecard = {
        "verified_recovery_s": verified_s,
        "false_green_s": _duration(first_wait_ok_after_fault, recovery_ts) if after_restore_failed else None,
        "recovered_by": recovered_by,
        "policy_denials": denials if s["fault_seen"] or denials else None,
        "decision_time_s": round(decision_time, 1) if decisions else None,
        "tool_calls": tool_calls if tool_calls else None,
        "tool_denials": tool_denials if tool_calls else None,
        "orders_per_min": _num(metrics.get("orders_per_min")) if metrics else None,
        "failed_orders": failed_orders,
        "backlog": backlog_now,
        "backlog_peak": backlog_peak,
        "backlog_draining": (backlog_now < backlog_peak) if backlog_now is not None and backlog_peak else None,
        "tokens": tokens if usage_seen else None,
        "est_cost_usd": round(cost, 2) if usage_seen else None,
        "mode": mode,
        "target_recovery_s": target_s,
        "target_source": target_source,
        "target_met": (verified_s <= target_s) if verified_s is not None and target_s else None,
        "preauthorized": preauthorized or None,
        "browser_minutes": round(browser_s / 60, 1) if browser_seen else None,
        "twin_lifetime_s": _duration(started_ts, ended_ts),
        "orders_seen": metrics_seen,
        "durable_improvements": (sum(1 for i in improvements["debt"] if i["artifact"])
                                 if improvements["state"] == "sealed" else None),
    }

    return {
        "event_count": len(events),
        "last_ts": events[-1]["ts"] if events else None,
        "stage": current,
        "timeline": timeline,
        "banner": banner,
        "infra": infra,
        "journey": journey,
        "scorecard": scorecard,
        "decision": decision,
        "killed_by": killed_by,
        "stubbed": stubbed,
        "ended": ended,
        "links": links,
        "improvements": improvements,
        "feed": list(reversed(feed))[:FEED_LIMIT],
        "board": build_board(events, card_stages, BOARD_TITLES, safe_link),
    }
