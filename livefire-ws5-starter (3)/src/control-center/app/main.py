import json
import os
import secrets as pysecrets
from datetime import datetime, timezone

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import security
from events import live, parse_ts, replay, store_from_env, valid_drill_id
from stages import build_view

BASE = os.path.dirname(__file__)
HTTPS_ONLY = security.AUTH_MODE != "local"

app = FastAPI(title="LiveFire Fusion Control Center", docs_url=None, redoc_url=None, openapi_url=None)
templates = Jinja2Templates(directory=os.path.join(BASE, "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")
store = store_from_env()


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    if not path.startswith(security.PUBLIC_PATHS) and security.current_user(request) is None:
        if path.startswith(("/api/", "/drills/")) and request.method != "GET":
            response = JSONResponse({"error": "sign in first"}, status_code=401)
        elif request.headers.get("accept", "").startswith("text/event-stream"):
            response = JSONResponse({"error": "sign in first"}, status_code=401)
        else:
            response = RedirectResponse("/login", status_code=303)
    else:
        response = await call_next(request)
    for name, value in security.HEADERS.items():
        response.headers.setdefault(name, value)
    if security.CSRF_COOKIE not in request.cookies:
        response.set_cookie(security.CSRF_COOKIE, security.csrf_token(request), httponly=True,
                            samesite="strict", secure=HTTPS_ONLY)
    return response


# Added after guard so it runs first and request.session exists inside guard.
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET") or pysecrets.token_urlsafe(48),
    session_cookie="cc_session", same_site="strict", https_only=HTTPS_ONLY, max_age=8 * 3600,
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _drill(drill_id: str) -> str:
    if not valid_drill_id(drill_id):
        raise HTTPException(status_code=404, detail="no such drill")
    return drill_id


def _page(request: Request, name: str, context: dict, status_code: int = 200):
    context = {"csrf": security.csrf_token(request), "user": security.current_user(request), **context}
    response = templates.TemplateResponse(request, name, context, status_code=status_code)
    if security.CSRF_COOKIE not in request.cookies:
        response.set_cookie(security.CSRF_COOKIE, context["csrf"], httponly=True,
                            samesite="strict", secure=HTTPS_ONLY)
    return response


# ---- sign-in (passcode mode) ----

@app.get("/login")
def login_page(request: Request):
    if security.AUTH_MODE != "passcode":
        return RedirectResponse("/", status_code=303)
    return _page(request, "login.html", {"error": None})


@app.post("/login")
def login(request: Request, name: str = Form(...), passcode: str = Form(...), csrf: str = Form(...)):
    security.require_csrf(request, csrf)
    ip = request.client.host if request.client else "unknown"
    if security.too_many_failures(ip):
        return _page(request, "login.html", {"error": "Too many attempts. Wait five minutes."}, 429)
    if not security.check_passcode(passcode):
        security.record_failure(ip)
        return _page(request, "login.html", {"error": "That passcode isn't right."}, 401)
    display = " ".join(name.split())[:40] or "Presenter"
    request.session["user"] = {"name": display, "method": "passcode"}
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request, csrf: str = Form(...)):
    security.require_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---- pages ----

@app.get("/")
def index(request: Request):
    return _page(request, "index.html", {"drills": store.list_drills()})


@app.get("/drills/{drill_id}")
def drill_page(request: Request, drill_id: str, mode: str = "live", speed: float = 10.0):
    _drill(drill_id)
    mode = "replay" if mode == "replay" else "live"
    speed = min(max(speed, 0.5), 100.0)
    return _page(request, "drill.html", {"drill_id": drill_id, "mode": mode, "speed": speed})


# ---- data ----

@app.get("/api/drills/{drill_id}/view")
def drill_view(drill_id: str):
    return build_view(store.read(_drill(drill_id)))


@app.get("/drills/{drill_id}/stream")
async def drill_stream(request: Request, drill_id: str, mode: str = "live", speed: float = 10.0):
    _drill(drill_id)
    source = replay(store.read(drill_id), speed=min(max(speed, 0.5), 100.0)) if mode == "replay" else live(store, drill_id)

    async def stream():
        yield ": connected\n\n"
        async for events in source:
            if await request.is_disconnected():
                break
            if events is None:
                yield ": keepalive\n\n"        # keeps proxies from closing a quiet stream
                continue
            yield f"event: view\ndata: {json.dumps(build_view(events))}\n\n"
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---- human decisions ----

@app.post("/drills/{drill_id}/approvals", status_code=202)
async def approve(request: Request, drill_id: str):
    security.require_csrf(request, request.headers.get("x-csrf-token"))
    body = await request.json()
    events = store.read(_drill(drill_id))
    pending = build_view(events)["approval"]
    if not pending or pending["request_id"] != body.get("request_id"):
        raise HTTPException(status_code=409, detail="no open decision with that id")
    option = next((o for o in pending["options"] if o.get("id") == body.get("option_id")), None)
    if option is None:
        raise HTTPException(status_code=400, detail="unknown option")
    user = security.current_user(request)
    latency = (datetime.now(timezone.utc) - parse_ts(pending["requested_at"])).total_seconds()
    store.append(drill_id, {
        "ts": _now(), "drill_id": drill_id, "action": "approval.decided", "result": "ok",
        "details": {"request_id": pending["request_id"], "option_id": option["id"],
                    "option_label": option.get("label", option["id"]), "approver": user["name"],
                    "method": user["method"], "latency_s": round(latency, 1)},
    })
    return {"recorded": True}


@app.post("/drills/{drill_id}/kill", status_code=202)
def kill(request: Request, drill_id: str):
    security.require_csrf(request, request.headers.get("x-csrf-token"))
    user = security.current_user(request)
    store.append(_drill(drill_id), {"ts": _now(), "drill_id": drill_id, "action": "kill.requested",
                                    "result": "ok", "details": {"by": user["name"], "method": user["method"]}})
    return {"recorded": True}


@app.get("/health")
def health():
    return {"status": "up"}
