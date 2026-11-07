"""G2: the events WS2's live Ops Service and WS4's runner write, the sync bridge and the metrics fallback."""
import importlib.util
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import load_events
from events import DirStore, make_event, sync_once
from stages import build_view

ROOT = Path(__file__).resolve().parents[3]


def raw_lines(view) -> list[str]:
    """Feed lines that fell through to the 'action: result' default, i.e. events the screen doesn't know."""
    return [f["text"] for f in view["feed"] if re.match(r"^[a-z][a-z0-9_-]*(\.[a-z0-9-]+)*: ", f["text"])]


def stage_of(view, text_start: str) -> str:
    return next(f["stage"] for f in view["feed"] if f["text"].startswith(text_start))


def test_ops_service_smoke_drill_reads_cleanly():
    v = build_view(load_events("drill-sample-ops-smoke"))
    assert raw_lines(v) == []
    assert v["banner"]["text"] == "CUSTOMERS ORDERING AGAIN"  # no verdict from a smoke drill, so never VERIFIED
    assert v["stage"] == "Verify"
    assert stage_of(v, "Fault injected again") == "Verify"  # the durability fault doesn't rewind the timeline
    assert stage_of(v, "Broad recovery denied by Azure Policy (RequestDisallowedByPolicy)") == "Decide"
    assert stage_of(v, "Drift check") is None
    s = v["scorecard"]
    assert s["recovered_by"] == "candidate fix" and s["policy_denials"] == 1


def test_ws4_stub_drill_puts_each_decision_in_its_stage():
    v = build_view(load_events("drill-sample-ws4-stub"))
    assert raw_lines(v) == []
    assert v["banner"]["text"] == "VERIFIED" and v["stage"] == "Evidence"
    assert stage_of(v, "Waiting for a human decision: deploy the candidate?") == "Repair"
    assert stage_of(v, "Waiting for a human decision: open the pull request?") == "Evidence"
    assert stage_of(v, "Fault injected again") == "Verify"
    assert stage_of(v, "Pull request opened") == "Evidence"


def test_decision_card_title_says_what_is_decided():
    events = load_events("drill-sample-ws4-stub")
    asked = [i for i, e in enumerate(events) if e["action"] == "approval.requested"]
    titles = [build_view(events[: i + 1])["decision"]["title"] for i in asked]
    assert titles[0] == "Decision needed: how to recover"
    assert titles[-2] == "Decision needed: deploy the candidate to the twin?"
    assert titles[-1] == "Decision needed: open the pull request for human review?"


def _drill(*steps):
    t = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
    return [make_event("drill-g2-test", a, r, d, ts=(t + timedelta(seconds=10 * i)).isoformat().replace("+00:00", "Z"))
            for i, (a, r, d) in enumerate(steps)]


def test_inconclusive_referee_run_is_not_a_failure():
    events = _drill(("referee.baseline", "PASS", {}),
                    ("referee.baseline", "INCONCLUSIVE", {"reason": "the job wrote no result"}))
    v = build_view(events)
    assert v["banner"]["text"] == "HEALTHY" and v["journey"]["state"] == "pass"
    assert v["feed"][0]["text"].startswith("Customer journey (baseline) could not run")


def test_a_broad_grant_that_stayed_is_shouted_about():
    v = build_view(_drill(("recover.broad", "UNEXPECTED_ALLOW_NOT_REMOVED", {})))
    assert v["feed"][0]["tone"] == "fail" and "NOT removed" in v["feed"][0]["text"]


def test_kill_reset_and_laptop_restart_have_their_own_lines():
    v = build_view(_drill(("reset", "ok", {"by": "kill"}), ("restart", "ok", {"seconds": 52}),
                          ("grant.refused", "refused", {"action": "recover", "reason": "grant already used"})))
    assert raw_lines(v) == []
    assert [f["tone"] for f in v["feed"]] == ["deny", "info", "fail"]


def test_sync_copies_up_and_clicks_down_once(tmp_path):
    local, remote = DirStore(tmp_path / "laptop"), DirStore(tmp_path / "blob")
    d = "drill-g2-sync"
    local.append(d, make_event(d, "fault.inject"))
    local.append(d, make_event(d, "approval.requested", "ok", {"request_id": "req-1", "options": []}))
    assert sync_once(local, remote, d) == (2, 0)
    remote.append(d, make_event(d, "approval.decided", "ok", {"request_id": "req-1", "option_id": "narrow"}))
    remote.append(d, make_event(d, "metrics.orders", "ok", {"orders_per_min": 30}))  # stays up there
    remote.append(d, make_event(d, "kill.requested", "ok", {"by": "Ada", "method": "passcode"}))
    assert sync_once(local, remote, d) == (0, 2)
    assert sync_once(local, remote, d) == (0, 0)  # safe to run again, or restart
    assert [e["action"] for e in local.read(d)] == ["fault.inject", "approval.requested", "approval.decided",
                                                   "kill.requested"]
    assert len(remote.read(d)) == 5


@pytest.fixture(scope="module")
def bridge():
    spec = importlib.util.spec_from_file_location("metrics_bridge", ROOT / "scripts" / "metrics_bridge.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(ts: datetime, name: str, total: float, result: str = "", peak: float | None = None) -> dict:
    """One AppMetrics row the way az returns it: every value a string."""
    return {"TimeGenerated": ts.isoformat().replace("+00:00", "Z"), "Name": name, "Sum": str(total),
            "Max": str(total if peak is None else peak), "Result": result, "TableName": "PrimaryResult"}


def test_metrics_bridge_reads_the_latest_export(bridge):
    since = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
    t1, t2 = since + timedelta(seconds=30), since + timedelta(seconds=90)
    rows = [
        _row(t1, "loadgen_orders_sent", 30, "ok"), _row(t1, "loadgen_backlog_size", 0),
        _row(t2 - timedelta(seconds=2), "loadgen_orders_sent", 12, "ok"),  # same export, a moment earlier
        _row(t2, "loadgen_orders_sent", 18, "failed"), _row(t2, "loadgen_backlog_size", 18),
        _row(since - timedelta(minutes=5), "orders_failed", 7),  # before the bridge started: not counted
        _row(t1, "orders_failed", 4), _row(t2, "orders_failed", 18),
    ]
    out = bridge.summarize(rows, since, now=t2 + timedelta(seconds=100))
    assert out == {"orders_per_min": 12.0, "failed_total": 22, "backlog": 18, "source": "app-insights",
                   "lag_s": 100}


def test_metrics_bridge_writes_nothing_when_the_load_generator_is_silent(bridge):
    since = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
    assert bridge.summarize([_row(since, "orders_failed", 3)], since) is None
    assert "AppMetrics" in bridge.build_query(since) and "loadgen_backlog_size" in bridge.build_query(since)


def test_who_refused_the_broad_option_is_named_honestly():
    lines = [f["text"] for f in reversed(build_view(_drill(
        ("recover.broad", "denied", {"code": "RequestDisallowedByPolicy", "refused_by": "Azure Policy"}),
        ("recover.broad", "denied", {"code": "AuthorizationFailed", "refused_by": "Azure RBAC"}),
        ("recover.broad", "denied", {"code": "RequestDisallowedByPolicy", "refused_by": "Azure Policy",
                                     "simulated": True}),
        ("recover.broad", "denied", {"code": "AuthorizationFailed"})))["feed"])]
    assert lines == ["Broad recovery denied by Azure Policy (RequestDisallowedByPolicy)",
                     "Broad recovery denied by Azure RBAC (AuthorizationFailed)",
                     "Broad recovery denied (simulated on a laptop, as Azure Policy would) (RequestDisallowedByPolicy)",
                     "Broad recovery denied by the platform (AuthorizationFailed)"]


def test_a_failed_run_says_its_trace_was_kept():
    v = build_view(load_events("drill-sample-ops-smoke"))
    failed = [f["text"] for f in v["feed"] if "failed at" in f["text"]]
    assert failed and all(t.endswith("(trace saved in the evidence store)") for t in failed)
    assert all(f["url"] is None for f in v["feed"])  # a path is not a link the browser could open


def test_a_stub_run_is_never_passed_off_as_live():
    v = build_view(load_events("drill-sample-ws4-stub"))
    assert v["stubbed"] is True
    assert v["feed"][-1]["text"] == "Drill started in Supervised mode with stubbed agents and Ops: no Azure, no model"
    assert build_view(load_events("drill-sample-ops-smoke"))["stubbed"] is False
