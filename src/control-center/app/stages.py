"""Events to stages, banner, split view, feed and scorecard.

Everything on the drill screen is computed here by one pure function, build_view(events).
It has no I/O and no clock, so the whole screen is unit-testable and a reconnecting browser
recovers by itself. The rules come from contracts/event-catalog.md, sections 6 and 7.
"""
from __future__ import annotations

import os

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
    if a in ("planner.tool_call", "engineer.tool_call"):
        who = "Planner" if a.startswith("planner") else "Engineer"
        tool = _text(d.get("tool") or "a tool", 80)
        if r == "deny" or d.get("decision") == "deny":
            where = f" on {_text(d.get('endpoint'), 40)}" if d.get("endpoint") else ""
            why = f": {_text(d.get('summary'))}" if d.get("summary") else ""
            return f"{who} was denied {tool}{where}{why}", "deny"
        why = f": {_text(d.get('summary'))}" if d.get("summary") else ""
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


# ---------------------------------------------------------------- the view

def build_view(events: list[dict]) -> dict:
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
    requests: dict[str, dict] = {}
    decided: set[str] = set()
    links: list[dict] = []
    feed: list[dict] = []

    for e in events:
        a, r, d, ts = e["action"], e["result"], e.get("details") or {}, e["ts"]

        stage = _stage_for(a, r, d, s)
        if stage:
            current = stage
            if stage not in visited:
                visited.append(stage)

        # state the stage rules depend on, updated after classifying this event
        if a == "drill.start" and _stubbed(d):
            stubbed = True
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
        elif a in ("planner.tool_call", "engineer.tool_call"):
            tool_calls += 1
            if r == "deny" or d.get("decision") == "deny":
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

    scorecard = {
        "verified_recovery_s": _duration(first_fault_ts, recovery_ts),
        "false_green_s": _duration(first_wait_ok_after_fault, recovery_ts) if after_restore_failed else None,
        "recovered_by": recovered_by,
        "policy_denials": denials if s["fault_seen"] or denials else None,
        "decision_time_s": round(decision_time, 1) if decisions else None,
        "tool_calls": tool_calls if tool_calls else None,
        "tool_denials": tool_denials if tool_calls else None,
        "orders_per_min": _num(metrics.get("orders_per_min")) if metrics else None,
        "failed_orders": _num(metrics.get("failed_total")) if metrics else None,
        "backlog": _num(metrics.get("backlog")) if metrics else None,
        "tokens": tokens if usage_seen else None,
        "est_cost_usd": round(cost, 2) if usage_seen else None,
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
        "feed": list(reversed(feed))[:FEED_LIMIT],
    }
