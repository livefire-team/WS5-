"""Sign-in, CSRF, decisions, the kill switch, headers and the live stream."""
import asyncio
import json
import re
import shutil
from pathlib import Path

import pytest

from conftest import APP, PASSCODE, csrf_from, sign_in


def test_pages_need_sign_in(client):
    assert client.get("/health").status_code == 200
    assert client.get("/static/app.css").status_code == 200
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    r = client.get("/drills/drill-sample-full-loop", follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/api/drills/drill-sample-full-loop/view").status_code == 401
    assert client.get("/drills/drill-sample-full-loop/stream").status_code == 401
    assert client.post("/drills/drill-sample-full-loop/kill").status_code == 401
    for path in ("/docs", "/redoc", "/openapi.json"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 404 or r.headers.get("location", "").startswith("/login"), path

    sign_in(client)
    assert client.get("/").status_code == 200
    assert client.get("/docs").status_code == 404 and client.get("/openapi.json").status_code == 404
    view =client.get("/api/drills/drill-sample-full-loop/view").json()
    assert view["banner"]["text"] == "VERIFIED"


def test_easyauth_header_is_trusted_only_in_easyauth_mode(app_factory):
    from starlette.testclient import TestClient
    forged = {"x-ms-client-principal-name": "mallory@example.com"}
    with TestClient(app_factory("passcode").app, base_url="https://cc.test") as c:
        assert c.get("/api/drills/drill-sample-full-loop/view", headers=forged).status_code == 401
    with TestClient(app_factory("easyauth").app, base_url="https://cc.test") as c:
        assert c.get("/api/drills/drill-sample-full-loop/view", headers=forged).status_code == 200
        assert c.get("/api/drills/drill-sample-full-loop/view").status_code == 401


def test_wrong_passcode_and_lockout(client):
    token = csrf_from(client)
    for _ in range(5):
        r = client.post("/login", data={"name": "Eve", "passcode": "guess", "csrf": token})
        assert r.status_code == 401
    r = client.post("/login", data={"name": "Eve", "passcode": PASSCODE, "csrf": token})
    assert r.status_code == 429  # locked out even with the right passcode
    assert client.get("/api/drills/drill-sample-full-loop/view").status_code == 401


def test_csrf_and_origin_are_enforced(client):
    token = sign_in(client)
    url = "/drills/drill-sample-full-loop/kill"
    assert client.post(url).status_code == 403  # no token
    assert client.post(url, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    r = client.post(url, headers={"X-CSRF-Token": token, "Origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post(url, headers={"X-CSRF-Token": token, "Origin": "null"})  # sandboxed frames send null
    assert r.status_code == 403
    r = client.post(url, headers={"X-CSRF-Token": token, "Origin": "https://cc.test"})
    assert r.status_code == 202
    assert client.post("/login", data={"name": "x", "passcode": PASSCODE}).status_code == 403


def test_drill_id_cannot_escape_the_evidence_folder(client, evidence, tmp_path):
    (tmp_path / "secret").mkdir()
    shutil.copy(evidence / "drill-sample-g1-manual" / "events.jsonl", tmp_path / "secret" / "events.jsonl")
    token = sign_in(client)
    for bad in ("..%2Fsecret", "drill-..%2F..%2Fsecret", "secret", "drill-a.b", "drill-" + "a" * 65):
        assert client.get(f"/api/drills/{bad}/view").status_code == 404, bad
        assert client.post(f"/drills/{bad}/kill", headers={"X-CSRF-Token": token}).status_code == 404, bad


def test_approval_records_the_signed_in_person_once(client, evidence):
    token = sign_in(client, name="Ada Customer")
    src = evidence / "drill-sample-full-loop" / "events.jsonl"
    lines = src.read_text().splitlines(keepends=True)
    cut = next(i for i, line in enumerate(lines) if '"approval.decided"' in line)
    target = evidence / "drill-try-decision" / "events.jsonl"
    target.parent.mkdir()
    target.write_text("".join(lines[:cut]).replace("drill-sample-full-loop", "drill-try-decision"))

    view = client.get("/api/drills/drill-try-decision/view").json()
    assert view["decision"]["request_id"] == "req-1"

    url = "/drills/drill-try-decision/approvals"
    h = {"X-CSRF-Token": token}
    assert client.post(url, json={"request_id": "req-1", "option_id": "sideways"}, headers=h).status_code == 400
    assert client.post(url, json={"request_id": "req-9", "option_id": "narrow"}, headers=h).status_code == 409
    r = client.post(url, json={"request_id": "req-1", "option_id": "narrow"}, headers=h)
    assert r.status_code == 202
    assert client.post(url, json={"request_id": "req-1", "option_id": "broad"}, headers=h).status_code == 409

    last = json.loads(target.read_text().splitlines()[-1])
    assert last["action"] == "approval.decided"
    assert last["details"]["approver"] == "Ada Customer"
    assert last["details"]["method"] == "passcode"
    assert last["details"]["option_id"] == "narrow"
    assert isinstance(last["details"]["latency_s"], float)
    assert sum('"approval.decided"' in line for line in target.read_text().splitlines()) == 1
    assert client.get("/api/drills/drill-try-decision/view").json()["decision"] is None


def test_kill_switch_records_who_pressed_it(client, evidence):
    token = sign_in(client, name="Presenter")
    assert client.post("/drills/drill-sample-g1-manual/kill", headers={"X-CSRF-Token": token}).status_code == 202
    last = json.loads((evidence / "drill-sample-g1-manual" / "events.jsonl").read_text().splitlines()[-1])
    assert last["action"] == "kill.requested" and last["details"] == {"by": "Presenter", "method": "passcode"}
    assert client.get("/api/drills/drill-sample-g1-manual/view").json()["killed_by"] == "Presenter"


def test_security_headers_and_csp(client):
    sign_in(client)
    for path in ("/health", "/login", "/", "/drills/drill-sample-full-loop", "/static/app.js"):
        h = client.get(path).headers
        assert h["content-security-policy"].startswith("default-src 'self'; script-src 'self'"), path
        assert "frame-ancestors 'none'" in h["content-security-policy"]
        assert h["x-frame-options"] == "DENY"
        assert h["x-content-type-options"] == "nosniff"
        assert h["referrer-policy"] == "same-origin"  # nothing leaks to other sites; forms keep a real Origin
        assert "max-age" in h["strict-transport-security"]
    cookie = client.get("/login").headers.get("set-cookie", "")
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie.replace("Strict", "strict")


def test_browser_code_never_parses_html():
    js = (APP / "static" / "app.js").read_text()
    code = "\n".join(line for line in js.splitlines() if not line.strip().startswith("//"))
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "DOMParser",
                   "createContextualFragment", "eval(", "new Function"):
        assert banned not in code, banned
    for template in (APP / "templates").glob("*.html"):
        html = template.read_text()
        assert "| safe" not in html and "|safe" not in html, template.name
        assert not re.search(r"<script(?![^>]*\bsrc=)", html), f"inline script in {template.name}"
        assert " style=" not in html and "<style" not in html, f"inline style in {template.name}"
        assert "http://" not in html and "https://" not in html, f"external asset in {template.name}"


def test_live_stream_sends_keepalive_during_quiet_periods(app_factory):
    main = app_factory("local")

    async def first_frames():
        frames = []
        gen = main.stream_events("drill-sample-g1-manual", "live", 10, keepalive=0.2, poll=0.05)
        async for chunk in gen:
            frames.append(chunk)
            if len(frames) == 3:
                break
        await gen.aclose()
        return frames

    frames = asyncio.run(asyncio.wait_for(first_frames(), timeout=5))
    assert frames[0].startswith("data: ")
    assert json.loads(frames[0][6:])["banner"]["text"] == "CUSTOMERS ORDERING AGAIN"
    assert frames[1] == ": keepalive\n\n" and frames[2] == ": keepalive\n\n"


def test_replay_stream_ends_and_marks_itself_as_replay(app_factory):
    main = app_factory("local")

    async def collect():
        return [c async for c in main.stream_events("drill-sample-g1-manual", "replay", 100)]

    frames = asyncio.run(asyncio.wait_for(collect(), timeout=30))
    assert frames[-1].startswith("event: end")
    views = [json.loads(f[6:]) for f in frames[:-1]]
    assert all(v["mode"] == "replay" for v in views)
    assert "FALSE GREEN" in [v["banner"]["text"] for v in views]


def test_local_auth_refuses_to_start_without_the_flag(monkeypatch):
    import importlib
    import security
    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.delenv("ALLOW_LOCAL_AUTH", raising=False)
    with pytest.raises(RuntimeError):
        importlib.reload(security)
    monkeypatch.setenv("AUTH_MODE", "passcode")
    monkeypatch.setenv("SESSION_SECRET", "too-short")
    with pytest.raises(RuntimeError):
        importlib.reload(security)
