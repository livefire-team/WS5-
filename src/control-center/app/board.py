"""The agent-action board: one card per thing that happened, for judges and engineers alike.

Every card says who acted (an AI agent, a person, a guardrail, an independent check or the
Drill Runner), what they did in plain words, what they found, and how it ended. The tech
list on each card holds the exact event, tool, endpoint, IDs and codes for an engineer.

Cards are placed in five phases (groups of the nine stages) and seven lanes (who acted), so
the browser can lay the same cards out by phase, by person or as a grid of both.
"""
from __future__ import annotations

import re

PHASES = [
    {"id": "break", "name": "Break it", "stages": ["Baseline", "Fault"],
     "plain": "Check the shop works, then break it on purpose"},
    {"id": "diagnose", "name": "Diagnose", "stages": ["Investigate"],
     "plain": "AI reads the evidence and finds the cause"},
    {"id": "decide", "name": "Decide and restore", "stages": ["Decide", "Restore", "False Green"],
     "plain": "A person chooses; access comes back"},
    {"id": "fix", "name": "Fix the code", "stages": ["Repair"],
     "plain": "AI writes a lasting fix, a test and a runbook"},
    {"id": "prove", "name": "Prove it", "stages": ["Verify", "Evidence"],
     "plain": "Deploy the fix, break it again, seal the record"},
]
PHASE_OF = {stage: p["id"] for p in PHASES for stage in p["stages"]}

LANES = [
    {"id": "planner", "name": "Planner agent", "kind": "AI agent",
     "plain": "reads the evidence, proposes options, writes the brief"},
    {"id": "engineer", "name": "Engineer agent", "kind": "AI agent",
     "plain": "writes the fix, the test and the runbook"},
    {"id": "recovery", "name": "Recovery agent", "kind": "AI agent",
     "plain": "carries out a restore the policy allows"},
    {"id": "person", "name": "Incident commander", "kind": "Person",
     "plain": "decides, and can stop everything"},
    {"id": "guard", "name": "Guardrails", "kind": "Platform",
     "plain": "Azure Policy and the tool server refuse unsafe actions"},
    {"id": "check", "name": "Independent checks", "kind": "Check",
     "plain": "the customer journey and the CI tests"},
    {"id": "runner", "name": "Drill Runner", "kind": "Automation",
     "plain": "runs each step and seals the evidence"},
]
AI_LANES = {"planner", "engineer", "recovery"}
AGENT_OF = {"planner.tool_call": "planner", "engineer.tool_call": "engineer", "recovery.tool_call": "recovery"}
AGENT_NAME = {"planner": "Planner", "engineer": "Engineer", "recovery": "Recovery"}

TOOL_PLAIN = {
    "query_incident_telemetry": "Read the error logs",
    "get_identity_snapshot": "Checked who has access to what",
    "search_incident_history": "Searched past incidents",
    "read_repository_context": "Read the app's code",
    "get_commit_history": "Checked recent code changes",
    "get_ci_results": "Checked the test results",
    "get_runbook": "Read the runbook",
    "create_candidate_branch": "Opened a separate branch for the fix",
    "apply_patch": "Wrote the code fix",
    "write_regression_test": "Added a test so this can't come back",
    "update_runbook": "Updated the runbook for people",
}
WRITE_TOOLS = {"create_candidate_branch", "apply_patch", "write_regression_test", "update_runbook"}

GUARDRAILS = [
    {"id": "T-02", "name": "Broad access", "plain": "asking for far more access than the fix needs"},
    {"id": "T-03", "name": "Cross-role tool call", "plain": "one agent's credential used on another agent's tools"},
    {"id": "T-04", "name": "Protected-path write", "plain": "an agent writing where it isn't allowed"},
]


def _t(value, limit: int = 300) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _short(sha) -> str:
    return _t(sha, 64)[:12] if sha else ""


def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _caller_role(d: dict) -> str:
    caller = str(d.get("caller") or "")
    return caller.split(":", 1)[1] if caller.startswith("key:") else ""


# WS3's server records a refused credential under the holder's role, with the endpoint it tried only in the summary
# (src/mcp-server/app/main.py): "planner credential refused on /engineer".
REFUSED_ON = re.compile(r"\b(planner|engineer|recovery) credential refused on /(planner|engineer|recovery)\b")


def cross_role(d: dict) -> tuple[str, str] | None:
    """(the credential's role, the endpoint it tried) when one role's credential was used on another's tools."""
    m = REFUSED_ON.search(str(d.get("summary") or ""))
    if m and m.group(1) != m.group(2):
        return m.group(1), m.group(2)
    role, endpoint = _caller_role(d), str(d.get("endpoint") or "")
    if role and endpoint and role != endpoint:
        return role, endpoint
    return None


def guardrail_kind(action: str, d: dict, result: str = "") -> str | None:
    """T-03 for a cross-role call, T-04 for a write to a protected path (journeys/); None for any other refusal."""
    if action not in AGENT_OF or (d.get("decision") or result) != "deny":
        return None
    if cross_role(d):
        return "T-03"
    if "protected path" in str(d.get("summary") or "") or str(d.get("path") or "").lstrip("/").startswith(
            ("journeys/", "referee/journeys/")):
        return "T-04"
    return None


def _tech(*pairs) -> list[list[str]]:
    return [[k, _t(v, 200)] for k, v in pairs if v not in (None, "", [], {})]


def _card(lane, headline, status="info", found=None, mark=None, tech=(), url=None, link_label=None,
          minor=False, extra=None) -> dict:
    return {"lane": lane, "ai": lane in AI_LANES, "headline": _t(headline, 160), "found": _t(found) or None,
            "extra": _t(extra, 200) or None, "status": status, "mark": mark, "tech": list(tech),
            "url": url, "link_label": link_label, "minor": minor}


def _card_for(e: dict, stage: str | None, ctx: dict, safe_link) -> dict | None:
    a, r, d = e["action"], e["result"], e.get("details") or {}
    event = ["event", f"{a} → {r}"]

    if a in AGENT_OF:
        lane, tool = AGENT_OF[a], _t(d.get("tool") or "a tool", 80)
        decision = d.get("decision") or r
        base = _tech(("tool", tool), ("endpoint", d.get("endpoint")), ("caller", d.get("caller")),
                     ("decision", decision), ("path", d.get("path")), ("evidence source", d.get("source")),
                     ("tool version", d.get("tool_version")), ("stubbed", "yes" if d.get("stub") else None))
        if decision == "deny":
            kind = guardrail_kind(a, d, r)
            who = AGENT_NAME[lane]
            if kind == "T-03":
                holder, target = cross_role(d)
                head = "Tool server refused a cross-role call"
                found = f"The {holder.title()} credential tried the {target.title()} tool {tool}"
            else:
                head = "Tool server refused a protected write" if kind == "T-04" else "Tool server refused a call"
                found = f"The {who} agent tried {tool}" + (f": {_t(d.get('summary'))}" if d.get("summary") else "")
            return _card("guard", head, "deny", found, "Refused", [event] + base, extra=kind)
        head = TOOL_PLAIN.get(tool, f"Used the {tool} tool")
        found = f"Found: {_t(d.get('summary'))}" if d.get("summary") else None
        if decision == "error":
            return _card(lane, head, "fail", found or "The tool call failed", "Failed", [event] + base)
        return _card(lane, head, "ok", found, "Allowed", [event] + base)

    if a == "usage":
        return None  # folded into the agent's next answer (see build_board)
    if a == "metrics.orders":
        return None  # the Customers tiles show these

    if a == "planner.options":
        opts = [o for o in (d.get("options") or []) if isinstance(o, dict)]
        rec = next((o for o in opts if o.get("id") == d.get("recommended")), None)
        conf = _num(d.get("confidence"))
        extra = None
        if rec:
            extra = f"Recommends: {_t(rec.get('label'), 120)}" + (f" ({round(conf * 100)}% confident)" if conf is not None else "")
        tech = _tech(("options", "; ".join(f"{o.get('id')}: {_t(o.get('label'), 80)}" for o in opts)),
                     ("recommended", d.get("recommended")), ("confidence", conf), ("response_id", d.get("response_id")))
        return _card("planner", f"Proposed {len(opts)} ways to recover", "ok",
                     f"Cause: {_t(d.get('hypothesis'))}" if d.get("hypothesis") else None, "Answer",
                     [event] + tech, extra=extra)
    if a == "planner.brief":
        n = _num(d.get("acceptance_criteria"))
        return _card("planner", "Wrote the repair brief for the Engineer", "ok", _t(d.get("summary")) or None, "Answer",
                     [event] + _tech(("confidence", d.get("confidence")), ("acceptance criteria", n),
                                     ("response_id", d.get("response_id"))),
                     extra=f"{n} acceptance criteria" if n else None)
    if a == "engineer.change_report":
        paths = [p for p in (d.get("changed_paths") or []) if isinstance(p, str)]
        return _card("engineer", "Handed in the fix for review", "ok",
                     f"{len(paths)} files on the branch {_t(d.get('branch'), 120)}" if paths else _t(d.get("branch")),
                     "Answer", [event] + _tech(("branch", d.get("branch")), ("attempt", d.get("attempt")),
                                               ("commits", ", ".join(_short(c) for c in d.get("commits") or [])),
                                               ("changed", ", ".join(paths)), ("response_id", d.get("response_id"))))

    if a == "approval.requested":
        return None  # one card per decision: see build_board
    if a == "approval.preauthorized":
        return _card("person", f"Approved in advance by the {_t(d.get('mode') or 'drill', 40)} policy", "ok",
                     _t(d.get("action")) or None, "No click needed",
                     [event] + _tech(("action", d.get("action")), ("policy version", d.get("policy_version"))))
    if a == "approval.policy_test":  # v1.1, Autonomous Twin: the workflow, not a person, tries the broad option
        return _card("runner", "Tested the safety rule on purpose", "info",
                     f"Asked for the broad option ({_t(d.get('action'), 120) or 'broad'}); Azure Policy must refuse it",
                     None, [event] + _tech(("action", d.get("action")), ("mode", d.get("mode")),
                                           ("policy version", d.get("policy_version"))))
    if a == "guardrail.blocked":  # v1.1: Prompt Shields on the model deployment refused a call
        agent = _t(d.get("agent") or "an", 20)
        flt = _t(d.get("filter") or "a filter", 60).replace("_", " ")
        return _card("guard", f"Prompt Shields blocked the {agent.title() if agent != 'an' else 'an'} agent's model call",
                     "deny", f"Filter: {flt}. The drill stops for a security review", "Blocked",
                     [event] + _tech(("agent", d.get("agent")), ("filter", d.get("filter")),
                                     ("response_id", d.get("response_id"))),
                     extra="Guardrail on the model itself, before any tool runs")
    if a == "kill.requested":
        return _card("person", "Pressed the kill switch", "fail", f"{_t(d.get('by') or 'Someone', 80)} stopped the drill",
                     "Stopped", [event] + _tech(("by", d.get("by")), ("method", d.get("method"))))

    if a == "recover.broad":
        code = d.get("code")
        tech = [event] + _tech(("code", code), ("refused by", d.get("refused_by")),
                               ("simulated", "yes" if d.get("simulated") else None))
        asked = f"Asked for: {ctx['broad_label']}" if ctx.get("broad_label") else None
        if r == "denied":
            by = _t(d.get("refused_by"), 40) or "Azure Policy"
            head = f"Refusal simulated, as {by} would" if d.get("simulated") else f"{by} refused the broad grant"
            return _card("guard", head, "deny", asked, "Refused", tech, extra="T-02")
        return _card("guard", "The broad grant was NOT refused", "fail", asked, "Check the policy", tech, extra="T-02")
    if a == "grant.refused":
        return _card("guard", "Ops Service refused a recovery grant", "deny", _t(d.get("reason")) or None, "Refused",
                     [event] + _tech(("action", d.get("action"))))

    if a.startswith("referee."):
        label = a.split(".", 1)[1]
        tech = [event] + _tech(("run", label), ("last step", d.get("last_step")), ("error", d.get("error")),
                               ("orders saved", d.get("orders_persisted")), ("seconds", d.get("duration_s")))
        url = safe_link(d.get("trace_url"))
        if r == "PASS":
            return _card("check", f"Customer test ({label}): an order went through", "pass", None, "Passed", tech)
        if r == "FAIL":
            fg = label == "after-restore"
            head = (f"Customer test ({label}): still can't order. This is the False Green" if fg
                    else f"Customer test ({label}): couldn't place an order")
            found = f"Stopped at {_t(d.get('last_step'), 60)}" if d.get("last_step") else None
            return _card("check", head, "fail", found, "Failed", tech, url, "trace" if url else None,
                         extra="Systems look healthy, customers still can't order" if fg else None)
        return _card("check", f"Customer test ({label}) couldn't run", "fail", _t(d.get("reason")) or None,
                     "No verdict", tech)
    if a == "ci.result":
        failed = ", ".join(_t(x, 40) for x in d.get("failed") or [])
        url = safe_link(d.get("run_url"))
        checks = d.get("checks") if isinstance(d.get("checks"), dict) else {}
        tech = [event] + _tech(("branch", d.get("branch")), ("commit", _short(d.get("commit"))), ("failed", failed),
                               ("checks", "; ".join(f"{_t(k, 40)}: {_t(v, 20)}" for k, v in list(checks.items())[:6])))
        if r == "pass":
            return _card("check", "Automated tests (CI) passed on the fix", "pass", None, "Passed", tech, url, "CI run")
        return _card("check", "Automated tests (CI) failed on the fix", "fail", failed or None, "Failed", tech, url, "CI run")

    # the Drill Runner and the Ops Service
    if a == "drill.start":
        agents = d.get("agents") if isinstance(d.get("agents"), dict) else {}
        stubbed = (d.get("agent_kind") == "stub" or d.get("model_deployment") == "stub"
                   or "stub" in [str(v) for v in agents.values()])
        stub = " Stubbed: no Azure, no model." if stubbed else ""
        return _card("runner", "Drill started", "info",
                     f"{_t(d.get('mode') or 'Supervised', 40)} mode, scenario {_t(d.get('scenario') or '?', 40)}.{stub}",
                     None, [event] + _tech(("contract", d.get("contract_version")), ("runner", d.get("runner_version")),
                                           ("model", d.get("model_deployment")), ("agents", d.get("agent_kind")),
                                           ("target", f"{d.get('target_recovery_s')} s" if d.get("target_recovery_s") else None)))
    if a == "fault.inject":
        if stage == "Verify":
            return _card("runner", "Broke it again, to prove the fix holds", "fail", None, None, [event])
        return _card("runner", "Broke the app on purpose", "fail", "Removed its access to Key Vault, where its secrets live",
                     None, [event] + _tech(("model", d.get("model"))))
    if a == "wait.forbidden":
        return _card("runner", "The fault took effect", "fail" if r == "ok" else "info",
                     "Key Vault now refuses the app (403)" if r == "ok" else "Still waiting", None,
                     [event] + _tech(("seconds", d.get("seconds"))), minor=r != "ok")
    if a in ("fault.restore", "recover.narrow"):
        lane = "recovery" if d.get("executed_by") == "agent" else "runner"
        return _card(lane, "Gave the app its Key Vault access back", "ok",
                     f"Approved by {_t(d.get('approved_by'), 80)}" if d.get("approved_by") else "Only the one role it lost",
                     None, [event] + _tech(("executed by", d.get("executed_by")), ("model", d.get("model"))))
    if a == "wait.ok":
        return _card("runner", "Health checks are green" if r == "ok" else "Health checks still not green",
                     "pass" if r == "ok" else "fail", None, None, [event] + _tech(("seconds", d.get("seconds"))), minor=True)
    if a == "reset":
        if d.get("by") == "kill":
            return _card("runner", "Reset the twin after the kill switch", "info", "Access back, revision rolled back",
                         None, [event])
        return _card("runner", "Reset the twin to its known-good state", "info", None, None, [event], minor=True)
    if a == "restart":
        return _card("runner", "Restarted the app", "info" if r == "ok" else "fail", None, None, [event], minor=True)
    if a == "deploy.revision":
        tech = [event] + _tech(("image", d.get("image")), ("digest", d.get("image_digest")), ("revision", d.get("revision")))
        if r != "ok":
            return _card("guard", "Deployment of the fix was refused", "deny", _t(d.get("reason") or r), "Refused", tech)
        return _card("runner", "Deployed the fix to the twin", "ok", None, None, tech)
    if a == "verdict":
        inputs = d.get("inputs") or {}
        failed = "; ".join(_t(x, 120) for x in inputs.get("reasons") or [])
        return _card("runner", f"Verdict: {_t(r, 40).replace('_', ' ')}", "pass" if r == "VERIFIED" else "fail",
                     _t(d.get("reason")) or None, None,
                     [event] + _tech(("failed checks", failed), ("attempts", d.get("attempts")), ("tokens", d.get("tokens"))))
    if a == "package.ready":
        if r != "ok":
            return _card("runner", "Could not seal the evidence package", "fail", None, None, [event])
        return _card("runner", "Sealed the evidence package", "pass", "Checksums recorded, so later changes show up",
                     None, [event] + _tech(("sha256", _short(d.get("sha256"))), ("events_sha256", _short(d.get("events_sha256")))))
    if a == "package.pr":
        url = safe_link(d.get("url"))
        if r == "ok":
            return _card("runner", "Opened a pull request for human review", "pass", "Left unmerged: a person decides",
                         None, [event] + _tech(("branch", d.get("branch"))), url, "pull request")
        return _card("runner", "No pull request opened", "info", _t(r), None, [event])
    if a == "drill.end":
        return _card("runner", "Drill ended", "info" if r == "ok" else "fail",
                     "Cleanup failed: check the twin" if r == "cleanup_failed" else None, None, [event], minor=r == "ok")
    if a in ("drift.check", "wait.drifted"):
        return _card("runner", "Drift check" if a == "drift.check" else "The twin drifted from its baseline",
                     "pass" if r == "ok" else "fail", None, None, [event], minor=True)
    return _card("runner", a, "info", _t(r, 64), None, [event], minor=True)


def build_board(events: list[dict], stages: list[str | None], decision_titles: dict, safe_link) -> dict:
    """stages[i] is the stage events[i] belongs to, or the stage the drill was in when it happened."""
    cards: list[dict] = []
    by_request: dict[str, dict] = {}
    usage: dict[str, dict] = {}
    ctx: dict = {}
    guard = {g["id"]: {**g, "count": 0, "first": None, "state": "not-seen"} for g in GUARDRAILS}

    def add(card, e, stage):
        card["id"] = len(cards) + 1
        card["time"] = e["ts"][11:19]
        card["ts"] = e["ts"]
        card["stage"] = stage
        card["phase"] = PHASE_OF.get(stage or "", "break")
        cards.append(card)
        return card

    for e, stage in zip(events, stages):
        a, r, d = e["action"], e["result"], e.get("details") or {}
        if a == "usage":
            agent = d.get("agent")
            if agent in AI_LANES:
                usage[agent] = d
            continue
        if a == "approval.requested":
            rid = d.get("request_id")
            purpose = d.get("purpose") or "recovery"
            opts = [o for o in d.get("options") or [] if isinstance(o, dict)]
            for o in opts:
                if o.get("id") == "broad" or "subscription" in str(o.get("label", "")).lower():
                    ctx["broad_label"] = _t(o.get("label"), 120)
            card = _card("person", decision_titles.get(purpose, "Decision needed"), "wait", "Waiting for a person",
                         "Waiting", [["event", f"{a} → {r}"]] + _tech(
                             ("request", rid), ("options", "; ".join(_t(o.get("label"), 80) for o in opts))))
            card = add(card, e, stage)
            if isinstance(rid, str):
                by_request[rid] = card
            continue
        if a == "approval.decided" and d.get("request_id") in by_request:
            card = by_request[d["request_id"]]
            lat = _num(d.get("latency_s"))
            after = f", {round(lat)} s after being asked" if lat is not None else ""
            if r == "timeout" or d.get("option_id") == "timeout":
                card.update(status="fail", mark="Timed out", found="Nobody decided in time, so the drill stops")
            else:
                card.update(status="ok", mark="Decided",
                            found=f"{_t(d.get('approver') or 'Someone', 80)} chose “{_t(d.get('option_label') or d.get('option_id'), 120)}”{after}")
            card["tech"] = card["tech"] + _tech(("chosen", d.get("option_id")), ("approver", d.get("approver")),
                                                ("method", d.get("method")), ("seconds", lat))
            continue

        card = _card_for(e, stage, ctx, safe_link)
        if card is None:
            continue
        if card["lane"] in AI_LANES and card["mark"] == "Answer" and card["lane"] in usage:
            u = usage.pop(card["lane"])
            card["tech"] += _tech(("tokens", f"{u.get('tokens')} ({u.get('input_tokens')} in, {u.get('output_tokens')} out)"),
                                  ("tool calls this turn", u.get("tool_calls")), ("cost", u.get("est_cost_usd")))
        add(card, e, stage)

        gid = card.get("extra") if card["lane"] == "guard" else None
        if gid in guard:
            g = guard[gid]
            if card["status"] == "deny":
                g["count"] += 1
                g["first"] = g["first"] or card["time"]
                if g["state"] != "breached":
                    g["state"] = "refused"
            else:
                g["state"] = "breached"
            card["extra"] = f"Guardrail test {gid}: {g['plain']}"

    stopped = any(e["action"] == "kill.requested" for e in events)
    over = any(e["action"] in ("verdict", "drill.end") for e in events)
    for card in cards:
        if card["status"] == "wait" and (stopped or over):
            card.update(status="info", mark="Not decided",
                        found=("The kill switch stopped the drill before anyone chose" if stopped
                               else "The drill ended before anyone chose"))

    lanes_used = {c["lane"] for c in cards}
    lanes = [ln for ln in LANES if ln["id"] != "recovery" or "recovery" in lanes_used]
    ai = [c for c in cards if c["ai"]]
    current = cards[-1]["phase"] if cards else None
    return {
        "phases": PHASES,
        "lanes": lanes,
        "cards": cards,
        "current_phase": current,
        "ai_actions": len(ai),
        "refusals": sum(1 for c in cards if c["status"] == "deny"),
        "guardrails": list(guard.values()),
    }
