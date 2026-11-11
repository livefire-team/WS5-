"""The agent-action board: every agent action gets a card, in plain words, with its technical detail."""
from conftest import load_events
from stages import build_view
from test_g3 import RUN2, _tool_call

RUN4 = "drill-g2-stub-1107-run4"  # WS4's stub runner, stopped with the kill switch on Nov 7, 2026
MERGED = {"usage", "metrics.orders", "approval.decided"}  # folded into another card or shown in the tiles


def test_every_event_becomes_exactly_one_card():
    for drill in (RUN2, RUN4):
        events = load_events(drill)
        board = build_view(events)["board"]
        assert len(board["cards"]) == sum(1 for e in events if e["action"] not in MERGED)
        assert [c["id"] for c in board["cards"]] == list(range(1, len(board["cards"]) + 1))


def test_every_agent_action_is_on_the_board_in_its_agents_lane():
    events = load_events(RUN2)
    board = build_view(events)["board"]
    agent_events = [e for e in events if e["action"].split(".")[0] in ("planner", "engineer")]
    ai = [c for c in board["cards"] if c["ai"]]
    assert len(ai) == len(agent_events) == board["ai_actions"] == 12
    assert [c["lane"] for c in ai] == [e["action"].split(".")[0] for e in agent_events]
    assert ai[0]["headline"] == "Read the error logs"
    assert ai[0]["found"] == "Found: 403 on secret reads since the fault"
    assert ["tool", "query_incident_telemetry"] in ai[0]["tech"]


def test_token_use_is_folded_into_the_agents_answer():
    board = build_view(load_events(RUN2))["board"]
    options = next(c for c in board["cards"] if c["headline"] == "Proposed 2 ways to recover")
    assert options["mark"] == "Answer" and options["phase"] == "diagnose"
    assert ["tokens", "16200 (14000 in, 2200 out)"] in options["tech"]
    assert options["extra"] == "Recommends: Restore Key Vault Secrets User on the twin vault (86% confident)"


def test_each_decision_is_one_card_that_says_who_chose_what():
    board = build_view(load_events(RUN2))["board"]
    decisions = [c for c in board["cards"] if c["lane"] == "person"]
    assert [c["mark"] for c in decisions] == ["Decided"] * 4
    assert decisions[0]["found"].startswith("Kris Rolle chose “Grant Contributor on the subscription”, 229 s")


def test_the_policy_denial_is_t02_on_the_board_and_in_the_strip():
    board = build_view(load_events(RUN2))["board"]
    denial = next(c for c in board["cards"] if c["status"] == "deny")
    assert (denial["lane"], denial["headline"]) == ("guard", "Azure Policy refused the broad grant")
    assert denial["found"] == "Asked for: Grant Contributor on the subscription"
    strip = {g["id"]: (g["state"], g["count"]) for g in board["guardrails"]}
    assert strip == {"T-02": ("refused", 1), "T-03": ("not-seen", 0), "T-04": ("not-seen", 0)}


def test_ws3_refusals_are_t03_and_t04():
    board = build_view([
        _tool_call("planner.tool_call", "deny", tool="apply_patch", endpoint="engineer", caller="key:planner"),
        _tool_call("engineer.tool_call", "deny", tool="apply_patch", endpoint="engineer", caller="key:engineer",
                   summary="referee/journeys/checkout.spec.ts is a protected path"),
        _tool_call("recovery.tool_call", "deny", tool="execute_recovery", endpoint="recovery", caller="key:recovery"),
    ])["board"]
    heads = [(c["lane"], c["headline"], c["found"]) for c in board["cards"]]
    assert heads == [
        ("guard", "Tool server refused a cross-role call", "The Planner credential tried the Engineer tool apply_patch"),
        ("guard", "Tool server refused a protected write",
         "The Engineer agent tried apply_patch: referee/journeys/checkout.spec.ts is a protected path"),
        ("guard", "Tool server refused a call", "The Recovery agent tried execute_recovery"),
    ]
    strip = {g["id"]: (g["state"], g["count"]) for g in board["guardrails"]}
    assert strip == {"T-02": ("not-seen", 0), "T-03": ("refused", 1), "T-04": ("refused", 1)}


def test_a_killed_drill_closes_its_open_decision():
    board = build_view(load_events(RUN4))["board"]
    ask = next(c for c in board["cards"] if c["lane"] == "person" and c["headline"].startswith("Asked"))
    assert (ask["status"], ask["mark"]) == ("info", "Not decided")
    assert ask["found"] == "The kill switch stopped the drill before anyone chose"
    kill = next(c for c in board["cards"] if c["headline"] == "Pressed the kill switch")
    assert kill["found"] == "Kris Rolle stopped the drill"
    assert not [c for c in board["cards"] if c["phase"] == "fix"]


def test_the_evidence_panel_names_the_store_without_local_paths():
    import main
    assert main.evidence_where("blob:https://stlfevwwssez2am6724.blob.core.windows.net/evidence") == \
        {"kind": "blob", "account": "stlfevwwssez2am6724", "container": "evidence"}
    assert main.evidence_where("dir:/home/someone/secret/place/preview-drills/") == \
        {"kind": "dir", "folder": "preview-drills"}
