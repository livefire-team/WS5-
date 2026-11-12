"""G3: the rest of the business scorecard, and the Improvements panel read from WS4's sealed package."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from conftest import DRILLS, load_events, sign_in
from events import DirStore, make_event, sync_once
from stages import build_view

RUN2 = "drill-g2-stub-1107-run2"  # WS4's stub runner, clicked on the deployed app on Nov 7, 2026


def package_bytes(drill_id: str = RUN2) -> bytes:
    return (DRILLS / drill_id / "package.json").read_bytes()


def test_a_real_sealed_package_fills_the_improvements_panel():
    v = build_view(load_events(RUN2), package_bytes())
    imp = v["improvements"]
    assert imp["state"] == "sealed" and imp["contract_version"] == "1.0"
    assert imp["integrity"] == {"package": True, "same_drill": True, "events": True, "events_count": 41,
                                "sha256": hashlib.sha256(package_bytes()).hexdigest()[:16], "ok": True}
    assert [d["type"] for d in imp["debt"]] == ["application-resilience", "regression-coverage", "runbook"]
    fix = imp["debt"][0]
    assert fix["artifact"] == "candidate/drill-g2-stub-1107-run2-1"
    assert fix["status"] == "pull request opened for review (unmerged)"
    assert fix["url"] is None  # the stub's CI link points at github.com/example, outside the team's org
    assert fix["evidence_refs"] and fix["finding"].startswith("A failed secret read must not be cached")
    assert imp["residual_risks"] == ["Other secrets read at startup only may behave the same way"]
    assert imp["next_drill"] is None  # not in contract 1.0
    assert v["scorecard"]["durable_improvements"] == 3


def test_the_package_waits_for_the_story_to_reach_it():
    events = load_events(RUN2)
    ready = next(i for i, e in enumerate(events) if e["action"] == "package.ready")
    early = build_view(events[:ready], package_bytes())  # a replay before package.ready
    assert early["improvements"]["state"] == "waiting" and early["scorecard"]["durable_improvements"] is None
    assert build_view(events[: ready + 1], None)["improvements"]["state"] == "loading"  # announced, not copied yet
    assert build_view(events[: ready + 1], b"not json")["improvements"]["state"] == "unreadable"


def test_a_changed_package_or_event_is_flagged_as_not_sealed():
    events = load_events(RUN2)
    edited_package = package_bytes().replace(b'"VERIFIED"', b'"VERIFIED "', 1)
    bad = build_view(events, edited_package)["improvements"]["integrity"]
    assert bad["package"] is False and bad["ok"] is False

    events[30] = {**events[30], "details": {**events[30]["details"], "approver": "Mallory"}}
    bad = build_view(events, package_bytes())["improvements"]["integrity"]
    assert bad["package"] is True and bad["events"] is False and bad["ok"] is False


def _sealed_drill(package: dict, steps=()):
    t = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)
    d = package["drill_id"]
    data = json.dumps(package, indent=2).encode()
    rows = [("drill.start", "ok", {"mode": "Autonomous Twin"}), *steps,
            ("package.ready", "ok", {"path": f"{d}/package.json", "sha256": hashlib.sha256(data).hexdigest()})]
    events = [make_event(d, a, r, x, ts=(t + timedelta(seconds=10 * i)).isoformat().replace("+00:00", "Z"))
              for i, (a, r, x) in enumerate(rows)]
    return events, data


def test_ws4_contract_1_1_debt_record_and_next_drill(monkeypatch):
    monkeypatch.delenv("ARTIFACT_LINK_PREFIX", raising=False)
    package = {
        "contract_version": "1.1", "drill_id": "drill-g3-debt",
        "operational_debt": [
            {"type": "application-resilience", "finding": "Failed secret reads were cached",
             "evidence_refs": ["after-restore"], "artifact": "https://github.com/livefire-team/livefire-fusion-poc/pull/40",
             "residual_risk": "Startup-only secrets", "status": "open"},
            {"type": "runbook", "finding": "<img src=x onerror=alert(1)>", "artifact": "https://evil.example/x",
             "status": "open"},
            {"type": "something-new", "finding": "no artifact yet", "status": "open"},
        ],
        "next_drill_recommendation": {"scenario": "T-02 network mystery", "reason": "Same failure shape, other dependency"},
    }
    events, data = _sealed_drill(package)
    v = build_view(events, data)
    imp = v["improvements"]
    assert imp["source"] == "WS4's operational-debt record" and imp["integrity"]["ok"] is True
    first, second, third = imp["debt"]
    assert first["url"] == "https://github.com/livefire-team/livefire-fusion-poc/pull/40"
    assert second["url"] is None and second["finding"] == "<img src=x onerror=alert(1)>"  # text, rendered with textContent
    assert third["type"] == "knowledge" and third["artifact"] is None
    assert imp["next_drill"] == {"scenario": "T-02 network mystery", "reason": "Same failure shape, other dependency"}
    assert v["scorecard"]["durable_improvements"] == 2
    assert imp["integrity"]["events"] is None  # this package didn't record an events checksum


def test_g3_scorecard_tiles(monkeypatch):
    monkeypatch.delenv("TARGET_RECOVERY_S", raising=False)
    t = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)
    rows = [
        (0, "drill.start", "ok", {"mode": "Autonomous Twin", "target_recovery_s": 600}),
        (5, "metrics.orders", "ok", {"orders_per_min": 30, "failed_total": 4, "backlog": 0}),
        (10, "referee.baseline", "PASS", {"duration_s": 30}),
        (60, "fault.inject", "ok", {}),
        (90, "metrics.orders", "ok", {"orders_per_min": 0, "failed_total": 20, "backlog": 25}),
        (100, "referee.during-fault", "FAIL", {"duration_s": 28}),
        (120, "approval.preauthorized", "ok", {"action": "restore_kv_secrets_user"}),
        (130, "approval.preauthorized", "ok", {"action": "deploy_candidate_to_twin"}),
        (300, "deploy.revision", "ok", {}),
        (360, "metrics.orders", "ok", {"orders_per_min": 12, "failed_total": 40, "backlog": 31}),
        (400, "referee.after-fix", "PASS", {"duration_s": 32}),
        (420, "metrics.orders", "ok", {"orders_per_min": 31, "failed_total": 41, "backlog": 9}),
        (450, "verdict", "VERIFIED", {}),
        (480, "drill.end", "ok", {}),
    ]
    events = [make_event("drill-g3-tiles", a, r, d, ts=(t + timedelta(seconds=s)).isoformat().replace("+00:00", "Z"))
              for s, a, r, d in rows]
    s = build_view(events)["scorecard"]
    assert s["mode"] == "Autonomous Twin"
    assert (s["verified_recovery_s"], s["target_recovery_s"], s["target_source"], s["target_met"]) == (340.0, 600.0, "drill", True)
    assert s["failed_orders"] == 36  # 40 at verified recovery minus 4 before the fault; the 41 after it doesn't count
    assert (s["backlog"], s["backlog_peak"], s["backlog_draining"]) == (9, 31, True)
    assert s["browser_minutes"] == 1.5 and s["twin_lifetime_s"] == 480.0
    assert s["preauthorized"] == 2 and s["decision_time_s"] is None
    assert s["durable_improvements"] is None  # no sealed package yet

    monkeypatch.setenv("TARGET_RECOVERY_S", "300")
    events[0] = make_event("drill-g3-tiles", "drill.start", "ok", {"mode": "Supervised"}, ts=events[0]["ts"])
    s = build_view(events)["scorecard"]
    assert (s["target_recovery_s"], s["target_source"], s["target_met"]) == (300.0, "team setting", False)


def test_the_stores_keep_a_package_exactly_once(tmp_path):
    store = DirStore(tmp_path)
    assert store.read_package(RUN2) is None and store.has_package(RUN2) is False
    assert store.write_package(RUN2, b"first") is True
    assert store.write_package(RUN2, b"second") is False  # written once, like WS4's runner
    assert store.read_package(RUN2) == b"first" and store.has_package(RUN2) is True
    with pytest.raises(ValueError):
        store.read_package("../../etc")


def test_sync_copies_the_package_before_the_event_that_announces_it(tmp_path):
    laptop, blob = DirStore(tmp_path / "laptop"), DirStore(tmp_path / "blob")
    for e in load_events(RUN2):
        laptop.append(RUN2, e)
    laptop.write_package(RUN2, package_bytes())
    up, down = sync_once(laptop, blob, RUN2)
    assert (up, down) == (len(load_events(RUN2)) + 1, 0)
    assert blob.read_package(RUN2) == package_bytes()  # byte for byte, so the checksum still matches
    assert build_view(blob.read(RUN2), blob.read_package(RUN2))["improvements"]["integrity"]["ok"] is True
    assert sync_once(laptop, blob, RUN2) == (0, 0)


def test_the_api_serves_the_panel_with_the_package(client):
    sign_in(client)
    view = client.get(f"/api/drills/{RUN2}/view").json()
    assert view["improvements"]["state"] == "sealed" and view["improvements"]["integrity"]["ok"] is True
    assert view["scorecard"]["durable_improvements"] == 3 and view["stubbed"] is True
    assert client.get("/api/drills/drill-sample-full-loop/view").json()["improvements"]["state"] == "waiting"


def _tool_call(action, result, **details):
    return make_event("drill-g3-tools", action, result, {"tool_version": "2.4", "decision": result, **details})


def test_ws3_denials_t03_and_t04_read_plainly_and_count():
    v = build_view([
        _tool_call("planner.tool_call", "allow", tool="query_incident_telemetry", endpoint="planner", caller="key:planner"),
        _tool_call("planner.tool_call", "deny", tool="apply_patch", endpoint="engineer", caller="key:planner",
                   summary="the planner credential can't call /engineer"),
        _tool_call("engineer.tool_call", "deny", tool="apply_patch", endpoint="engineer", caller="key:engineer",
                   summary="referee/journeys/checkout.spec.ts is a protected path"),
        _tool_call("recovery.tool_call", "deny", tool="execute_recovery", endpoint="recovery", caller="key:recovery",
                   summary="grant already used"),
        _tool_call("engineer.tool_call", "error", tool="get_ci_results", endpoint="engineer", caller="key:engineer",
                   summary="GitHub returned 502"),
    ])
    lines = [(f["tone"], f["text"]) for f in reversed(v["feed"])]
    assert lines == [
        ("info", "Planner called query_incident_telemetry"),
        ("deny", "Cross-role call refused: the Planner credential on the Engineer tools (apply_patch): "
                 "the planner credential can't call /engineer"),
        ("deny", "Protected-path write refused: Engineer tried apply_patch: referee/journeys/checkout.spec.ts is a protected path"),
        ("deny", "Recovery was denied execute_recovery on the recovery tools: grant already used"),
        ("fail", "Engineer's call to get_ci_results failed: GitHub returned 502"),
    ]
    assert (v["scorecard"]["tool_calls"], v["scorecard"]["tool_denials"]) == (5, 3)
