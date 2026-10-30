"""Sign-in, CSRF protection and security headers for the Control Center."""
import hashlib
import hmac
import os
import secrets
import time
from urllib.parse import urlparse

from fastapi import HTTPException, Request

AUTH_MODE = os.environ.get("AUTH_MODE", "passcode")          # easyauth | passcode | local
if AUTH_MODE not in ("easyauth", "passcode", "local"):
    raise RuntimeError("AUTH_MODE must be easyauth, passcode or local")
if AUTH_MODE == "local" and os.environ.get("ALLOW_LOCAL_AUTH") != "1":
    raise RuntimeError("AUTH_MODE=local needs ALLOW_LOCAL_AUTH=1 and must never be deployed")

PUBLIC_PATHS = ("/health", "/login", "/static/")
CSRF_COOKIE = "cc_csrf"
_failures: dict[str, list[float]] = {}

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Strict-Transport-Security": "max-age=31536000",
}


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
        return False                     # no passcode configured means nobody gets in
    actual = hashlib.sha256(passcode.encode()).hexdigest()
    return hmac.compare_digest(actual, expected)


def too_many_failures(ip: str, limit: int = 5, window: float = 300.0) -> bool:
    now = time.monotonic()
    recent = [t for t in _failures.get(ip, []) if now - t < window]
    _failures[ip] = recent
    return len(recent) >= limit


def record_failure(ip: str) -> None:
    _failures.setdefault(ip, []).append(time.monotonic())


def csrf_token(request: Request) -> str:
    return request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(32)


def require_csrf(request: Request, submitted: str | None) -> None:
    """Double-submit token plus an Origin check on every state-changing request."""
    cookie = request.cookies.get(CSRF_COOKIE)
    if not cookie or not submitted or not hmac.compare_digest(cookie, submitted):
        raise HTTPException(status_code=403, detail="CSRF check failed")
    origin = request.headers.get("origin")
    if origin and urlparse(origin).netloc != request.headers.get("host"):
        raise HTTPException(status_code=403, detail="cross-origin request refused")
