from datetime import datetime, timedelta, timezone

from livefire.contracts import AutonomyPolicy, Mode
from livefire.policy import check_secrets, compute_verdict, gate

LATER = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()


def test_gate_matrix():
    sup = AutonomyPolicy(mode=Mode.SUPERVISED, expires_at=LATER)
    auto = AutonomyPolicy(mode=Mode.AUTONOMOUS_TWIN, expires_at=LATER)
    adv = AutonomyPolicy(mode=Mode.ADVISORY, expires_at=LATER)
    assert gate(sup, "restore_kv_secrets_user") == "ask"
    assert gate(auto, "restore_kv_secrets_user") == "execute"
    assert gate(auto, "deploy_candidate_to_twin") == "execute"
    assert gate(auto, "grant_contributor_subscription") == "ask"        # never pre-authorized
    assert gate(adv, "restore_kv_secrets_user") == "deny"
    expired = AutonomyPolicy(mode=Mode.AUTONOMOUS_TWIN, expires_at="2026-01-01T00:00:00Z")
    assert gate(expired, "restore_kv_secrets_user") == "deny"


def test_secret_like_values_are_caught():
    assert check_secrets({"note": "token ghp_" + "a" * 30})
    assert check_secrets({"note": "AccountKey=abcdefgh1234"})
    assert check_secrets({"note": "the app reads a secret named order-db-password"}) is None


def ev(action, result="ok", **details):
    return {"ts": "2026-10-26T09:00:00.000Z", "drill_id": "drill-v", "action": action, "result": result,
            "details": details}


def ref(label, verdict, orders=1):
    return ev(f"referee.{label}", verdict, label=label, verdict=verdict, orders_persisted=orders,
              payment_auths_persisted=orders)


GOOD = [ref("baseline", "PASS"), ev("fault.inject"), ref("during-fault", "FAIL", 0),
        ev("approval.decided", option_id="narrow"), ev("fault.restore"), ev("wait.ok"),
        ref("after-restore", "FAIL", 0), ev("ci.result", "pass", commit="ab" * 20),
        ev("deploy.revision", image="ghcr.io/x/order-service:sha-" + "ab" * 20), ref("after-fix", "PASS")]


def test_verdict_passes_only_when_all_six_inputs_pass():
    assert compute_verdict(GOOD).verified


def test_verdict_rejects_each_missing_input():
    cases = {
        "no authorization": [e for e in GOOD if e["action"] != "approval.decided"],
        "broad allowed": GOOD + [ev("recover.broad", "UNEXPECTED_ALLOW")],
        "no health after restore": [e for e in GOOD if e["action"] != "wait.ok"],
        "deployed without CI": [e for e in GOOD if e["action"] != "ci.result"],
        "journey failed": GOOD[:-1] + [ref("after-fix", "FAIL", 0)],
        "duplicate order": GOOD[:-1] + [ref("after-fix", "PASS", 2)],
    }
    for name, events in cases.items():
        inputs = compute_verdict(events)
        assert not inputs.verified, name
        assert inputs.reasons, name


def test_a_later_restore_needs_fresh_health():
    events = GOOD + [ev("fault.inject"), ev("fault.restore"), ref("after-refault", "PASS")]
    assert not compute_verdict(events).technical_health_passed
    assert compute_verdict(events[:-1] + [ev("wait.ok"), events[-1]]).verified
