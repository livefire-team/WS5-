import json
import re
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parent / "fixtures" / "drills"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    shutil.copytree(FIXTURES, tmp_path / "drills")
    monkeypatch.setenv("EVENT_SOURCE", f"dir:{tmp_path / 'drills'}")
    import importlib
    import events, security, main  # noqa: E401
    importlib.reload(security); importlib.reload(main)
    c = TestClient(main.app, base_url="https://testserver")
    c.tmp = tmp_path
    return c


def login(c):
    c.get("/login")
    token = c.cookies.get("cc_csrf")
    r = c.post("/login", data={"name": "Customer", "passcode": "open-sesame", "csrf": token}, follow_redirects=False)
    assert r.status_code == 303
    return token


def test_pages_need_sign_in(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client.post("/drills/drill-sample-full-loop/kill").status_code == 401


def test_wrong_passcode_and_lockout(client):
    client.get("/login")
    token = client.cookies.get("cc_csrf")
    for _ in range(5):
        assert client.post("/login", data={"name": "x", "passcode": "nope", "csrf": token}).status_code == 401
    assert client.post("/login", data={"name": "x", "passcode": "open-sesame", "csrf": token}).status_code == 429


def test_security_headers(client):
    r = client.get("/health")
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"


def test_csrf_and_origin_are_enforced(client):
    token = login(client)
    url = "/drills/drill-sample-full-loop/kill"
    assert client.post(url).status_code == 403
    assert client.post(url, headers={"x-csrf-token": "wrong"}).status_code == 403
    assert client.post(url, headers={"x-csrf-token": token, "origin": "https://evil.example"}).status_code == 403
    assert client.post(url, headers={"x-csrf-token": token, "origin": "https://testserver"}).status_code == 202


def test_drill_id_cannot_escape_the_evidence_folder(client):
    login(client)
    assert client.get("/api/drills/..%2f..%2fetc/view").status_code == 404
    assert client.get("/api/drills/not-a-drill/view").status_code == 404


def test_approval_records_the_signed_in_person_once(client):
    token = login(client)
    drill = client.tmp / "drills" / "drill-sample-full-loop" / "events.jsonl"
    lines = drill.read_text().splitlines()
    cut = next(i for i, l in enumerate(lines) if '"approval.requested"' in l)
    drill.write_text("\n".join(lines[: cut + 1]) + "\n")
    url = "/drills/drill-sample-full-loop/approvals"
    body = {"request_id": "apr-1", "option_id": "narrow"}
    assert client.post(url, json=body, headers={"x-csrf-token": token}).status_code == 202
    last = json.loads(drill.read_text().splitlines()[-1])
    assert last["action"] == "approval.decided" and last["details"]["approver"] == "Customer"
    assert client.post(url, json=body, headers={"x-csrf-token": token}).status_code == 409


def test_browser_code_never_parses_html():
    js = (HERE.parent / "app" / "static" / "app.js").read_text()
    assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write", js)
