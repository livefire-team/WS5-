"""Sign-in, CSRF protection and security headers for the Control Center.

The Control Center can record a decision but never act on one. These controls keep that record
trustworthy and keep the page safe against the untrusted text that agents produce.
"""
import hashlib
import hmac
import os
import secrets
import time
from urllib.parse import urlparse

from starlette.exceptions import HTTPException
from starlette.requests import Request

AUTH_MODE = os.environ.get("AUTH_MODE", "passcode")  # easyauth | passcode | local
if AUTH_MODE not in ("easyauth", "passcode", "local"):
    raise RuntimeError("AUTH_MODE must be easyauth, passcode or local")
if AUTH_MODE == "local" and os.environ.get("ALLOW_LOCAL_AUTH") != "1":
    raise RuntimeError("AUTH_MODE=local needs ALLOW_LOCAL_AUTH=1 and must never be deployed")

SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
if AUTH_MODE == "passcode" and len(SESSION_SECRET) < 32:
    raise RuntimeError("AUTH_MODE=passcode needs SESSION_SECRET (at least 32 characters, e.g. openssl rand -base64 48)")
if not SESSION_SECRET:
    SESSION_SECRET = secrets.token_urlsafe(48)  # sessions aren't used for identity in the other modes

PUBLIC_PATHS = ("/health", "/login", "/static/")
CSRF_COOKIE = "cc_csrf"
CSRF_HEADER = "x-csrf-token"
SESSION_MAX_AGE = 8 * 3600
_failures: dict[str, list[float]] = {}

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    # Not "no-referrer": with that, browsers send "Origin: null" on form posts and the Origin check
    # below would refuse the sign-in form. "same-origin" still sends nothing to other sites.
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Strict-Transport-Security": "max-age=31536000",
}


def is_public(path: str) -> bool:
    return path in ("/health", "/login") or path.startswith("/static/")


def current_user(request: Request) -> dict | None:
    """Who is signed in. Easy Auth headers are trusted only when Easy Auth is the configured mode."""
    if AUTH_MODE == "easyauth":
        name = request.headers.get("x-ms-client-principal-name")
        return {"name": name, "method": "entra"} if name else None
    if AUTH_MODE == "passcode":
        return request.session.get("user")
    return {"name": "local-dev", "method": "local"}


def check_passcode(passcode: str) -> bool:
    expected = os.environ.get("PASSCODE_SHA256", "")
    if len(expected) != 64:
        return False  # no passcode configured means nobody gets in
    actual = hashlib.sha256(passcode.encode()).hexdigest()
    return hmac.compare_digest(actual, expected.lower())


def too_many_failures(ip: str, limit: int = 5, window: float = 300.0) -> bool:
    now = time.monotonic()
    recent = [t for t in _failures.get(ip, []) if now - t < window]
    _failures[ip] = recent
    return len(recent) >= limit


def record_failure(ip: str) -> None:
    _failures.setdefault(ip, []).append(time.monotonic())


def clean_name(name: str) -> str:
    """The approver's name goes into the evidence, so keep it short and printable."""
    name = "".join(ch for ch in (name or "") if ch.isprintable()).strip()
    return name[:64]


def csrf_token(request: Request) -> str:
    return request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(32)


def set_csrf_cookie(response, token: str) -> None:
    response.set_cookie(CSRF_COOKIE, token, max_age=SESSION_MAX_AGE, httponly=True,
                        secure=True, samesite="strict", path="/")


def require_csrf(request: Request, submitted: str | None) -> None:
    """Double-submit token plus an Origin check on every state-changing request."""
    cookie = request.cookies.get(CSRF_COOKIE)
    if not cookie or not submitted or not hmac.compare_digest(cookie, submitted):
        raise HTTPException(status_code=403, detail="CSRF check failed")
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.headers.get("host"):
        raise HTTPException(status_code=403, detail="cross-origin request refused")
