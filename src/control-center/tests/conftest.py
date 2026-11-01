import hashlib
import importlib
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
DRILLS = ROOT / "fixtures" / "drills"
sys.path.insert(0, str(APP))
sys.path.insert(0, str(ROOT.parent / "shared"))

PASSCODE = "correct horse battery staple"
SESSION_SECRET = "s" * 48


def load_events(drill_id: str) -> list[dict]:
    from events import DirStore
    return DirStore(DRILLS).read(drill_id)


@pytest.fixture
def evidence(tmp_path):
    """A private copy of the sample drills, so tests can append to them."""
    target = tmp_path / "evidence"
    shutil.copytree(DRILLS, target)
    return target


def _make_app(monkeypatch, evidence, mode="passcode"):
    monkeypatch.setenv("AUTH_MODE", mode)
    monkeypatch.setenv("EVENT_SOURCE", f"dir:{evidence}")
    monkeypatch.setenv("SESSION_SECRET", SESSION_SECRET)
    monkeypatch.setenv("PASSCODE_SHA256", hashlib.sha256(PASSCODE.encode()).hexdigest())
    if mode == "local":
        monkeypatch.setenv("ALLOW_LOCAL_AUTH", "1")
    import security
    importlib.reload(security)
    security._failures.clear()
    import main
    return importlib.reload(main)


@pytest.fixture
def app_factory(monkeypatch, evidence):
    def factory(mode="passcode"):
        return _make_app(monkeypatch, evidence, mode)
    return factory


@pytest.fixture
def client(app_factory):
    from starlette.testclient import TestClient
    main = app_factory("passcode")
    # https, because the session and CSRF cookies are Secure
    with TestClient(main.app, base_url="https://cc.test") as c:
        yield c


def csrf_from(client) -> str:
    """Load a page so the CSRF cookie is set, then return the token."""
    client.get("/login")
    return client.cookies.get("cc_csrf")


def sign_in(client, name="Ada Customer"):
    token = csrf_from(client)
    r = client.post("/login", data={"name": name, "passcode": PASSCODE, "csrf": token, "next": "/"},
                    follow_redirects=False)
    assert r.status_code == 303, r.text
    return client.cookies.get("cc_csrf")
