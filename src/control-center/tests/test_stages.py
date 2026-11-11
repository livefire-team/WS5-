"""The screen is one pure function, so the drill story is tested without a browser."""
import asyncio
import time

import pytest

from conftest import load_events
from events import make_event, parse_lines, replay
from stages import build_view


def upto(events, action, result=None):
    """Events up to and including the first match."""
    for i, e in enumerate(events):
        if e["action"] == action and (result is None or e["result"] == result):
            return events[: i + 1]
    raise AssertionError(f"{action} not in drill")


def test_g1_false_green_sequence():
    events = load_events("drill-sample-g1-manual")

    v = build_view(upto(events, "referee.baseline"))
    assert v["banner"]["text"] == "HEALTHY" and v["stage"] == "Baseline"

    v = build_view(upto(events, "referee.during-fault"))
    assert v["banner"]["text"] == "CUSTOMERS CAN'T ORDER" and v["stage"] == "Fault"

    v = build_view(upto(events, "referee.after-restore"))
    assert v["banner"]["text"] == "FALSE GREEN"
    assert v["stage"] == "False Green"
    assert v["infra"]["state"] == "healthy" and v["journey"]["state"] == "fail"
    assert next(s for s in v["timeline"] if s["name"] == "False Green")["false_green"] is True

    v = build_view(events)  # recovered by restart: honest progress, not a verified fix
    assert v["banner"]["text"] == "CUSTOMERS ORDERING AGAIN"
    assert v["banner"]["kind"] != "verified"
    assert v["scorecard"]["recovered_by"] == "restart"
    assert v["scorecard"]["false_green_s"] is not None


def test_full_loop_scorecard_fills_every_tile():
    v = build_view(load_events("drill-sample-full-loop"))
    assert v["banner"] == {"kind": "verified", "text": "VERIFIED", "from_verdict": True}
    assert v["stage"] == "Evidence" and v["ended"] is True
    s = v["scorecard"]
    g2_tiles = ("verified_recovery_s", "false_green_s", "recovered_by", "policy_denials", "decision_time_s",
                "tool_calls", "tool_denials", "orders_per_min", "failed_orders", "backlog", "tokens", "est_cost_usd")
    assert all(s[k] is not None for k in g2_tiles), s  # the G3 tiles need fields this G1 sample predates
    assert s["recovered_by"] == "candidate fix"
    assert s["policy_denials"] == 1
    assert s["decision_time_s"] == 60.0
    assert (s["tool_calls"], s["tool_denials"]) == (15, 2)
    assert s["tokens"] == 48210 and s["est_cost_usd"] == 0.62
    assert {t["name"] for t in v["timeline"] if t["status"] != "pending"} == {
        "Baseline", "Fault", "Investigate", "Decide", "Restore", "False Green", "Repair", "Verify", "Evidence"}
    feed_actions = " ".join(f["text"] for f in v["feed"])
    assert "orders/min" not in feed_actions  # metrics live in the tiles, not the feed


def test_open_decision_state():
    events = load_events("drill-sample-full-loop")
    v = build_view(upto(events, "approval.requested"))
    assert v["stage"] == "Decide"
    d = v["decision"]
    assert d["request_id"] == "req-1"
    assert [(o["id"], o["broad"]) for o in d["options"]] == [("broad", True), ("narrow", False)]

    v = build_view(upto(events, "approval.decided"))
    assert v["decision"] is None


def test_only_a_verdict_says_verified_and_it_always_wins():
    events = load_events("drill-sample-g1-manual")
    escalated = events + [make_event("drill-sample-g1-manual", "verdict", "ESCALATED_BUDGET")]
    v = build_view(escalated)
    assert v["banner"]["kind"] == "escalated" and "ESCALATED" in v["banner"]["text"]
    assert all(build_view(events[:i])["banner"]["kind"] != "verified" for i in range(len(events) + 1))


def test_half_written_lines_are_skipped_until_complete():
    good = '{"ts": "2026-10-26T09:30:00.000Z", "drill_id": "drill-a", "action": "reset", "result": "ok", "details": {}}'
    assert len(parse_lines(good + "\n" + good[:40])) == 1
    assert len(parse_lines(good + "\n" + good + "\n")) == 2
    assert len(parse_lines("not json\n" + good + "\n")) == 1


def test_replay_keeps_spacing_but_caps_long_waits():
    a = make_event("drill-a", "fault.inject", ts="2026-10-26T09:30:00.000Z")
    b = make_event("drill-a", "wait.forbidden", ts="2026-10-26T09:31:30.000Z")  # a 90 s propagation wait

    async def run():
        frames = []
        start = time.monotonic()
        async for shown in replay([a, b], speed=10, max_gap=0.2):
            frames.append(len(shown))
        return frames, time.monotonic() - start

    frames, took = asyncio.run(run())
    assert frames == [1, 2]
    assert took < 1.0  # 9 s at x10, capped at 0.2 s


@pytest.mark.parametrize("bad", ["../etc", "drill-../../x", "drill-a/b", "drill-", "x" * 10, "drill-" + "a" * 65])
def test_events_validate_drill_ids(bad):
    with pytest.raises(ValueError):
        make_event(bad, "reset")
