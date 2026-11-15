"""G3 after the freeze (lead's OK, Nov 15): WS4's contract v1.2 event in plain words, and no cost on the page."""
from pathlib import Path

from events import make_event
from stages import build_view

DRILL = "drill-g3-v12"


def ev(name, result, **details):
    return make_event(DRILL, name, result, details)


# As WS4's runner writes it (livefire-fusion-poc cedc84d, src/drill-runner/livefire/agents.py; contracts/event-catalog.md
# v1.2): agent, attempt, response_id, errors [{loc, type, msg}] and error_count. Never the output's content.
FIRST = ev("agent.output_invalid", "invalid", agent="planner", attempt=1, response_id="resp_abc123",
           errors=[{"loc": "options.0.label", "type": "string_too_long", "msg": "String should have at most 120 characters"},
                   {"loc": "confidence", "type": "missing", "msg": "Field required"}], error_count=2)
SECOND = ev("agent.output_invalid", "invalid", agent="engineer", attempt=2, response_id="resp_def456",
            errors=[{"loc": "changed_paths", "type": "missing", "msg": "Field required"}], error_count=1)


def test_an_invalid_agent_answer_reads_in_plain_words():
    v = build_view([FIRST, SECOND])
    feed = [(f["tone"], f["text"]) for f in reversed(v["feed"])]
    assert feed == [
        ("info", "The Planner's answer didn't follow the agreed format (2 problems); the workflow sent it back to fix"),
        ("fail", "The Engineer's answer still didn't follow the agreed format (1 problem); the drill stopped safely"),
    ]
    cards = v["board"]["cards"]
    assert [(c["lane"], c["headline"], c["found"], c["mark"]) for c in cards] == [
        ("runner", "Checked the Planner's answer: it didn't follow the agreed format",
         "Sent back to the agent to fix (2 problems)", "Sent back"),
        ("runner", "Checked the Engineer's answer: it didn't follow the agreed format",
         "Wrong again, so the drill stopped safely rather than act on it (1 problem)", "Stopped"),
    ]
    tech = dict(cards[0]["tech"])
    assert tech["attempt"] == "1" and tech["problems"] == "2" and tech["response_id"] == "resp_abc123"
    assert tech["first problems"] == "options.0.label: String should have at most 120 characters; confidence: Field required"


def test_no_raw_event_name_reaches_the_page():
    v = build_view([FIRST, SECOND])
    shown = [f["text"] for f in v["feed"]] + [c["headline"] for c in v["board"]["cards"]]
    assert not [t for t in shown if "agent.output_invalid" in t or t.startswith("agent.")]


def test_a_count_without_error_count_still_reads():
    v = build_view([ev("agent.output_invalid", "invalid", agent="nobody", attempt=1,
                       errors=[{"loc": "x", "type": "missing", "msg": "Field required"}])])
    assert v["feed"][0]["text"] == "The agent's answer didn't follow the agreed format (1 problem); the workflow sent it back to fix"


def test_the_page_no_longer_shows_an_estimated_cost():
    js = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.js").read_text()
    assert "Estimated cost" not in js and "est_cost_usd" not in js
