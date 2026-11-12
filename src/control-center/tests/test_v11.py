"""G3 against what the other workstreams actually write: WS3's refusal events and WS4's contract v1.1."""
import hashlib
import json

from events import make_event
from stages import build_view

DRILL = "drill-g3-v11"


def ev(name, result, **details):
    return make_event(DRILL, name, result, details)


# Exactly as WS3's server records them (src/mcp-server/app/main.py and guard.py, livefire-fusion-poc 3e0cdf8)
T03 = ev("planner.tool_call", "deny", tool="apply_patch", tool_version="2.4", endpoint="planner", caller="key:planner",
         decision="deny", summary="planner credential refused on /engineer", source="", args_sha256="", latency_ms=0)
T04 = ev("engineer.tool_call", "deny", tool="apply_patch", tool_version="2.4", endpoint="engineer",
         caller="key:engineer", decision="deny",
         summary="journeys/checkout.spec.ts is a protected path and can't be changed", source="livefire:apply_patch")
OUTSIDE = ev("engineer.tool_call", "deny", tool="apply_patch", endpoint="engineer", caller="key:engineer",
             decision="deny", summary="infra/main.bicep is outside the paths this tool may change")


def test_ws3s_recorded_cross_role_refusal_is_t03_not_t04():
    v = build_view([T03, T04, OUTSIDE])
    feed = [(f["tone"], f["text"]) for f in reversed(v["feed"])]
    assert feed == [
        ("deny", "Cross-role call refused: the Planner credential on the Engineer tools (apply_patch)"),
        ("deny", "Protected-path write refused: Engineer tried apply_patch: "
                 "journeys/checkout.spec.ts is a protected path and can't be changed"),
        ("deny", "Engineer was denied apply_patch on the engineer tools: "
                 "infra/main.bicep is outside the paths this tool may change"),
    ]
    board = v["board"]
    assert [(c["headline"], c["found"]) for c in board["cards"]] == [
        ("Tool server refused a cross-role call", "The Planner credential tried the Engineer tool apply_patch"),
        ("Tool server refused a protected write",
         "The Engineer agent tried apply_patch: journeys/checkout.spec.ts is a protected path and can't be changed"),
        ("Tool server refused a call",
         "The Engineer agent tried apply_patch: infra/main.bicep is outside the paths this tool may change"),
    ]
    strip = {g["id"]: (g["state"], g["count"]) for g in board["guardrails"]}
    assert strip == {"T-02": ("not-seen", 0), "T-03": ("refused", 1), "T-04": ("refused", 1)}
    assert ["evidence source", "livefire:apply_patch"] in board["cards"][1]["tech"]


def test_prompt_shields_block_is_a_guardrail_card_and_a_plain_line():
    v = build_view([
        ev("drill.start", "ok", mode="Autonomous Twin", scenario="T-08", agent_kind="prompt", target_recovery_s=1800),
        ev("guardrail.blocked", "blocked", agent="planner", filter="indirect_attack", response_id="resp_123"),
        ev("verdict", "ESCALATED_SECURITY", reason="Prompt Shields blocked a model call"),
    ])
    blocked = next(c for c in v["board"]["cards"] if c["lane"] == "guard")
    assert (blocked["headline"], blocked["status"], blocked["mark"]) == (
        "Prompt Shields blocked the Planner agent's model call", "deny", "Blocked")
    assert blocked["found"] == "Filter: indirect attack. The drill stops for a security review"
    assert ["response_id", "resp_123"] in blocked["tech"]
    lines = [f["text"] for f in v["feed"]]
    assert ("Prompt Shields blocked the Planner agent's model call (indirect attack); "
            "the drill stops for a security review") in lines
    assert not any(t.startswith("guardrail.blocked") for t in lines)  # never a raw event name
    assert v["board"]["refusals"] == 1 and not v["stubbed"]


def test_autonomous_policy_test_sits_in_decide_and_is_not_a_human_decision():
    v = build_view([
        ev("drill.start", "ok", mode="Autonomous Twin", scenario="T-01"),
        ev("fault.inject", "ok"),
        ev("approval.policy_test", "ok", action="Grant Contributor on the subscription", mode="Autonomous Twin",
           policy_version="1.1"),
        ev("recover.broad", "denied", code="RequestDisallowedByPolicy", refused_by="Azure Policy"),
    ])
    card = next(c for c in v["board"]["cards"] if c["headline"] == "Tested the safety rule on purpose")
    assert card["lane"] == "runner" and card["stage"] == "Decide"
    assert card["found"] == ("Asked for the broad option (Grant Contributor on the subscription); "
                             "Azure Policy must refuse it")
    assert v["decision"] is None  # nothing waits for a click
    assert not [c for c in v["board"]["cards"] if c["lane"] == "person"]
    assert {g["id"]: g["state"] for g in v["board"]["guardrails"]}["T-02"] == "refused"
    assert any(f["text"].startswith("The workflow tested the safety rule on purpose") for f in v["feed"])


def _sealed(pkg: dict):
    raw = json.dumps(pkg).encode()
    return [ev("package.ready", "ok", path=f"evidence/{DRILL}/package.json",
               sha256=hashlib.sha256(raw).hexdigest())], raw


def test_v11_package_debt_kinds_and_next_drill_text():
    events, raw = _sealed({
        "contract_version": "1.1", "drill_id": DRILL, "residual_risks": [],
        "operational_debt": [
            {"kind": "application-resilience", "finding": "A failed secret read is no longer cached",
             "evidence_refs": ["ev-1"], "artifact": "candidate/drill-g3-v11-1", "residual_risk": "",
             "status": "proposed"},
            {"kind": "runbook", "finding": "Runbook covers the 403", "evidence_refs": ["ev-2"],
             "artifact": "docs/runbooks/kv.md@candidate/drill-g3-v11-1", "residual_risk": "", "status": "proposed"},
        ],
        "next_drill_recommendation": "T-04: check the Engineer can't write the customer journeys",
    })
    imp = build_view(events, raw)["improvements"]
    assert imp["state"] == "sealed" and imp["contract_version"] == "1.1"
    assert [(d["type"], d["type_label"]) for d in imp["debt"]] == [
        ("application-resilience", "Application resilience"), ("runbook", "Runbook")]
    assert imp["next_drill"] == {"scenario": "T-04", "reason": "check the Engineer can't write the customer journeys"}

    events, raw = _sealed({"contract_version": "1.1", "drill_id": DRILL, "operational_debt": [],
                           "next_drill_recommendation": "Repeat T-01 in Supervised mode"})
    assert build_view(events, raw)["improvements"]["next_drill"] == {
        "scenario": None, "reason": "Repeat T-01 in Supervised mode"}


def test_v11_stub_flag_and_ci_checks():
    v = build_view([
        ev("drill.start", "ok", mode="Supervised", agent_kind="stub"),
        ev("ci.result", "pass", branch="candidate/drill-g3-v11-1", commit="a" * 40,
           checks={"order-service.yml": "success", "security.yml": "success"}),
    ])
    assert v["stubbed"]
    ci = next(c for c in v["board"]["cards"] if c["lane"] == "check")
    assert ["checks", "order-service.yml: success; security.yml: success"] in ci["tech"]
