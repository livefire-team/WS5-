"""LiveFire Fusion Control Center: routes, sign-in guard, live stream, approvals, kill switch.

Run locally (from src/control-center/app):
    AUTH_MODE=local ALLOW_LOCAL_AUTH=1 EVENT_SOURCE=dir:../fixtures/drills uvicorn main:app --port 8080
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

from starlette.applications import Starlette
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

import security
from events import make_event, now_ts, parse_ts, replay, store_from_env, validate_drill_id
from stages import build_view

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))  # autoescaping is on for .html
store = store_from_env()
decision_lock = asyncio.Lock()  # one replica, so one lock makes each decision one-off

KEEPALIVE_S = 15.0
POLL_S = 0.5
PACKAGE_RETRY_S = 3.0  # while package.ready is in the log but package.json isn't readable yet
MIN_SPEED, MAX_SPEED = 1.0, 100.0


# ---------------------------------------------------------------- helpers

def drill_or_404(drill_id: str) -> str:
    try:
        return validate_drill_id(drill_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="no such drill") from None


def clamp_speed(raw) -> float:
    try:
        speed = float(raw)
    except (TypeError, ValueError):
        speed = 10.0
    if speed != speed:  # NaN
        speed = 10.0
    return min(max(speed, MIN_SPEED), MAX_SPEED)


def page(request: Request, name: str, context: dict, status_code: int = 200):
    token = security.csrf_token(request)
    context = {"user": security.current_user(request), "auth_mode": security.AUTH_MODE,
               "csrf": token, **context}
    response = templates.TemplateResponse(request, name, context, status_code=status_code)
    security.set_csrf_cookie(response, token)
    return response


def evidence_where(source: str | None = None) -> dict:
    """Where this screen reads drills from, for the Evidence panel. Names only: no keys, no full local paths."""
    source = source or os.environ.get("EVENT_SOURCE", "dir:./evidence")
    kind, _, target = source.partition(":")
    if kind == "blob":
        account_url, _, container = target.rstrip("/").rpartition("/")
        host = urlparse(account_url).hostname or ""
        return {"kind": "blob", "account": host.split(".")[0], "container": container}
    return {"kind": "dir", "folder": Path(target.rstrip("/")).name or target}


WHERE = evidence_where()


def sse(view: dict, mode: str, speed: float | None) -> str:
    view = {**view, "mode": mode, "speed": speed, "where": WHERE}
    return f"data: {json.dumps(view)}\n\n"


def _sealed(events: list[dict]) -> bool:
    return any(e["action"] == "package.ready" and e["result"] == "ok" for e in events)


async def read_package(drill_id: str, events: list[dict]) -> bytes | None:
    """WS4's sealed package.json, read only once a package.ready event says it exists."""
    if not _sealed(events):
        return None
    reader = getattr(store, "read_package", None)
    return await asyncio.to_thread(reader, drill_id) if reader else None


async def stream_events(drill_id: str, mode: str, speed: float, is_disconnected=None,
                        keepalive: float = KEEPALIVE_S, poll: float = POLL_S):
    """The full view after each new event; a comment line keeps quiet streams open through proxies."""
    if mode == "replay":
        recorded = await asyncio.to_thread(store.read, drill_id)
        package = await read_package(drill_id, recorded)  # build_view uses it once the story reaches package.ready
        async for shown in replay(recorded, speed=speed):
            if is_disconnected and await is_disconnected():
                return
            yield sse(build_view(shown, package), "replay", speed)
        yield "event: end\ndata: {}\n\n"
        return

    # Live: poll every half second (same as events.live), send the view whenever it grows,
    # and once more when the sealed package becomes readable (the sync may copy it a moment later).
    seen = -1
    package = None
    next_package_try = 0.0
    last_sent = time.monotonic()
    while True:
        if is_disconnected and await is_disconnected():
            return
        events = await asyncio.to_thread(store.read, drill_id)
        package_arrived = False
        if package is None and _sealed(events) and time.monotonic() >= next_package_try:
            package = await read_package(drill_id, events)
            package_arrived = package is not None
            next_package_try = time.monotonic() + PACKAGE_RETRY_S
        if len(events) != seen or package_arrived:
            seen = len(events)
            yield sse(build_view(events, package), "live", None)
            last_sent = time.monotonic()
        elif time.monotonic() - last_sent >= keepalive:
            yield ": keepalive\n\n"
            last_sent = time.monotonic()
        await asyncio.sleep(poll)


# ---------------------------------------------------------------- routes

async def health(request: Request):
    return JSONResponse({"status": "ok"})


async def index(request: Request):
    drills = await asyncio.to_thread(store.list_drills)
    for d in drills:
        d["updated_text"] = datetime.fromtimestamp(d["updated"], timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return page(request, "index.html", {"drills": drills})


async def drill_page(request: Request):
    drill_id = drill_or_404(request.path_params["drill_id"])
    mode = "replay" if request.query_params.get("mode") == "replay" else "live"
    speed = clamp_speed(request.query_params.get("speed", 10))
    return page(request, "drill.html", {"drill_id": drill_id, "mode": mode,
                                        "speed": f"{speed:g}"})


async def drill_stream(request: Request):
    drill_id = drill_or_404(request.path_params["drill_id"])
    mode = "replay" if request.query_params.get("mode") == "replay" else "live"
    speed = clamp_speed(request.query_params.get("speed", 10))
    return StreamingResponse(
        stream_events(drill_id, mode, speed, request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


async def drill_view(request: Request):
    drill_id = drill_or_404(request.path_params["drill_id"])
    events = await asyncio.to_thread(store.read, drill_id)
    package = await read_package(drill_id, events)
    return JSONResponse({"drill_id": drill_id, **build_view(events, package), "where": WHERE})


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="body must be JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")
    return body


async def record_approval(request: Request):
    drill_id = drill_or_404(request.path_params["drill_id"])
    security.require_csrf(request, request.headers.get(security.CSRF_HEADER))
    user = security.current_user(request)
    body = await _json_body(request)
    request_id, option_id = body.get("request_id"), body.get("option_id")
    if not isinstance(request_id, str) or not isinstance(option_id, str):
        raise HTTPException(status_code=400, detail="request_id and option_id are required")

    async with decision_lock:
        events = await asyncio.to_thread(store.read, drill_id)
        asked = None
        for e in events:
            d = e.get("details") or {}
            if e["action"] == "approval.requested" and d.get("request_id") == request_id:
                asked = e
            elif e["action"] == "approval.decided" and d.get("request_id") == request_id:
                asked = None
        if asked is None:
            return JSONResponse({"error": "no open decision with that request_id"}, status_code=409)
        options = [o for o in asked["details"].get("options", []) if isinstance(o, dict)]
        chosen = next((o for o in options if o.get("id") == option_id), None)
        if chosen is None:
            return JSONResponse({"error": "that option was not offered"}, status_code=400)

        decided = make_event(drill_id, "approval.decided", "ok", {
            "request_id": request_id,
            "option_id": option_id,
            "option_label": str(chosen.get("label", option_id))[:200],
            "approver": user["name"],
            "method": user["method"],
            "latency_s": round(max((parse_ts(now_ts()) - parse_ts(asked["ts"])).total_seconds(), 0.0), 1),
        })
        await asyncio.to_thread(store.append, drill_id, decided)
    return JSONResponse({"recorded": decided}, status_code=202)


async def record_kill(request: Request):
    drill_id = drill_or_404(request.path_params["drill_id"])
    security.require_csrf(request, request.headers.get(security.CSRF_HEADER))
    user = security.current_user(request)
    event = make_event(drill_id, "kill.requested", "ok", {"by": user["name"], "method": user["method"]})
    await asyncio.to_thread(store.append, drill_id, event)
    return JSONResponse({"recorded": event}, status_code=202)


def _safe_next(target: str | None) -> str:
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return "/"


async def login_page(request: Request):
    if security.AUTH_MODE != "passcode" or security.current_user(request):
        return RedirectResponse(_safe_next(request.query_params.get("next")), status_code=303)
    return page(request, "login.html", {"next": _safe_next(request.query_params.get("next")), "error": None})


async def login_submit(request: Request):
    if security.AUTH_MODE != "passcode":
        return RedirectResponse("/", status_code=303)
    form = await request.form()
    security.require_csrf(request, form.get("csrf"))
    target = _safe_next(form.get("next"))
    ip = request.client.host if request.client else "unknown"
    if security.too_many_failures(ip):
        return page(request, "login.html", {"next": target, "error": "Too many attempts. Wait five minutes."},
                    status_code=429)
    name = security.clean_name(str(form.get("name", "")))
    if not name or not security.check_passcode(str(form.get("passcode", ""))):
        security.record_failure(ip)
        return page(request, "login.html", {"next": target, "error": "Wrong passcode, or no name given."},
                    status_code=401)
    request.session.clear()
    request.session["user"] = {"name": name, "method": "passcode"}
    return RedirectResponse(target, status_code=303)


async def logout(request: Request):
    form = await request.form()
    security.require_csrf(request, form.get("csrf"))
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---------------------------------------------------------------- middleware

class SignInGuard:
    """Every page and API needs sign-in; only /health, /login and /static/ are open."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or security.is_public(scope["path"]):
            return await self.app(scope, receive, send)
        request = Request(scope)
        if security.current_user(request) is None:
            path = scope["path"]
            wants_page = request.method == "GET" and not path.startswith("/api/") and not path.endswith("/stream")
            if wants_page and security.AUTH_MODE == "passcode":
                response = RedirectResponse(f"/login?next={quote(path)}", status_code=303)
            else:
                response = JSONResponse({"error": "sign-in required"}, status_code=401)
            return await response(scope, receive, send)
        return await self.app(scope, receive, send)


class SecurityHeaders:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for key, value in security.HEADERS.items():
                    headers[key] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)


async def http_error(request: Request, exc: HTTPException):
    return PlainTextResponse(str(exc.detail), status_code=exc.status_code)


routes = [
    Route("/", index),
    Route("/health", health),
    Route("/login", login_page, methods=["GET"]),
    Route("/login", login_submit, methods=["POST"]),
    Route("/logout", logout, methods=["POST"]),
    Route("/drills/{drill_id}", drill_page),
    Route("/drills/{drill_id}/stream", drill_stream),
    Route("/drills/{drill_id}/approvals", record_approval, methods=["POST"]),
    Route("/drills/{drill_id}/kill", record_kill, methods=["POST"]),
    Route("/api/drills/{drill_id}/view", drill_view),
    Mount("/static", StaticFiles(directory=str(HERE / "static")), name="static"),
]

app = Starlette(
    routes=routes,
    middleware=[
        Middleware(SecurityHeaders),
        Middleware(SessionMiddleware, secret_key=security.SESSION_SECRET, session_cookie="cc_session",
                   max_age=security.SESSION_MAX_AGE, same_site="strict", https_only=True),
        Middleware(SignInGuard),
    ],
    exception_handlers={HTTPException: http_error},
)
