import json
from pathlib import Path

from stages import build_view

DRILLS = Path(__file__).resolve().parents[1] / "fixtures" / "drills"


def load(name):
    return [json.loads(l) for l in (DRILLS / name / "events.jsonl").read_text().splitlines() if l]


def view_after(events, action):
    upto = next(i for i, e in enumerate(events) if e["action"] == action)
    return build_view(events[: upto + 1])


def test_g1_drill_shows_false_green_then_recovery_by_restart():
    events = load("drill-sample-g1-manual")
    assert build_view(events[:3])["banner"] == "HEALTHY"
    assert view_after(events, "referee.during-fault")["banner"] == "CUSTOMERS CAN'T ORDER"
    fg = view_after(events, "referee.after-restore")
    assert fg["banner"] == "FALSE GREEN" and fg["infra"] == "green" and fg["customer"] == "fail"
    assert next(s for s in fg["stages"] if s["key"] == "false_green")["status"] == "alert"
    end = build_view(events)
    assert end["banner"] == "CUSTOMERS ORDERING AGAIN"
    sc = end["scorecard"]
    assert sc["recovered_by"] == "restart"
    assert sc["verified_recovery_s"] == 74 + 12 + 6 + 96 + 11 + 20 + 1 + 9
    assert sc["false_green_s"] == 11 + 20 + 1 + 9
    assert sc["referee_runs"] == 4 and sc["referee_failures"] == 2


def test_full_loop_counts_denials_decisions_and_verdict():
    end = build_view(load("drill-sample-full-loop"))
    sc = end["scorecard"]
    assert end["banner"] == "VERIFIED" and end["verdict"] == "VERIFIED"
    assert sc["recovered_by"] == "candidate fix"
    assert sc["policy_denials"] == 1
    assert sc["decision_latency_s"] == 43
    assert sc["denied_tool_calls"] == 1 and sc["agent_tool_calls"] == 10
    assert sc["tokens"] == 38200 and sc["failed_orders"] == 31
    assert end["approval"] is None


def test_pending_approval_is_exposed_until_decided():
    events = load("drill-sample-full-loop")
    v = view_after(events, "approval.requested")
    assert v["approval"]["request_id"] == "apr-1"
    assert [o["id"] for o in v["approval"]["options"]] == ["broad", "narrow"]


def test_agent_text_passes_through_as_plain_text():
    e = {"ts": "2026-10-26T09:30:00.000Z", "action": "planner.tool_call", "result": "allow",
         "details": {"tool": "x", "endpoint": "planner", "decision": "allow", "summary": "<img src=x onerror=alert(1)>"}}
    feed = build_view([e])["feed"]
    assert "<img" in feed[0]["text"]          # kept verbatim; the browser renders it with textContent


def test_live_stream_sends_keepalives_when_quiet(tmp_path):
    import asyncio
    from events import DirEventStore, live
    store = DirEventStore(str(tmp_path))
    store.append("drill-quiet", {"ts": "2026-10-26T09:30:00.000Z", "action": "reset", "result": "ok"})

    async def first_two():
        out = []
        async for item in live(store, "drill-quiet", poll_seconds=0.01, keepalive_seconds=0.05):
            out.append(item)
            if len(out) == 2:
                return out
    first, second = asyncio.run(first_two())
    assert len(first) == 1 and second is None
