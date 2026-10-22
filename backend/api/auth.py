"""Employee login and signed session tokens.

A token binds the browser to ONE conversation: it carries the session id, the kind of
login (employee / guest) and an expiry, signed with HMAC-SHA256 under SECRET_KEY.
Format:  v1.<base64url(json payload)>.<base64url(signature)>

Every conversation endpoint requires the token and checks that its session id is the
one requested - so knowing (or guessing) a session id is not enough to read it.

Demo only: employees log in with an employee number and an access code from
data/demo_access.json. In the pilot this is replaced by the organisation's SSO."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path as FilePath
from typing import Literal, Optional

from fastapi import Depends, HTTPException, Path, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..data_loader.paths import DEMO_ACCESS_JSON
from .schemas import SESSION_ID_PATTERN

log = logging.getLogger("strauss.auth")

TOKEN_VERSION = "v1"
TOKEN_TTL_S = 12 * 3600
MIN_SECRET_LEN = 32

Kind = Literal["employee", "guest"]


class AuthError(Exception):
    """Missing, malformed, badly signed or expired token."""


@dataclass(frozen=True)
class Claims:
    sid: str
    kind: Kind
    emp_id: Optional[str]
    exp: int


# --------------------------------------------------------------------------- #
# Secret
# --------------------------------------------------------------------------- #
def load_secret(explicit: Optional[str] = None) -> str:
    """SECRET_KEY from the environment. When missing, a random per-process key is used:
    logins still work, but every token becomes invalid on restart (never in production)."""
    secret = (explicit or os.environ.get("SECRET_KEY", "")).strip()
    if not secret:
        log.warning("SECRET_KEY is not set - using a random key; employees must log in "
                    "again after every restart")
        return secrets.token_urlsafe(48)
    if len(secret) < MIN_SECRET_LEN:
        log.warning("SECRET_KEY is shorter than %d characters - use a long random key",
                    MIN_SECRET_LEN)
    return secret


# --------------------------------------------------------------------------- #
# Tokens
# --------------------------------------------------------------------------- #
def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(secret: str, signed_part: str) -> str:
    return _b64(hmac.new(secret.encode(), signed_part.encode(), hashlib.sha256).digest())


def issue_token(secret: str, sid: str, kind: Kind, emp_id: Optional[str] = None,
                ttl_s: int = TOKEN_TTL_S, now: Optional[float] = None) -> str:
    payload = {"sid": sid, "kind": kind, "emp": emp_id,
               "exp": int((now if now is not None else time.time()) + ttl_s)}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signed_part = f"{TOKEN_VERSION}.{body}"
    return f"{signed_part}.{_sign(secret, signed_part)}"


def verify_token(secret: str, token: str, now: Optional[float] = None) -> Claims:
    try:
        version, body, sig = token.split(".")
    except ValueError:
        raise AuthError("malformed token") from None
    if version != TOKEN_VERSION:
        raise AuthError("unknown token version")
    # constant-time comparison: the signature check leaks nothing about the right value
    if not hmac.compare_digest(sig, _sign(secret, f"{version}.{body}")):
        raise AuthError("bad signature")
    try:
        p = json.loads(_unb64(body))
        claims = Claims(sid=str(p["sid"]), kind=p["kind"], emp_id=p.get("emp"),
                        exp=int(p["exp"]))
    except (ValueError, KeyError, TypeError):
        raise AuthError("malformed payload") from None
    if claims.kind not in ("employee", "guest"):
        raise AuthError("unknown login kind")
    if claims.exp < (now if now is not None else time.time()):
        raise AuthError("expired")
    return claims


# --------------------------------------------------------------------------- #
# Demo access codes (data layer)
# --------------------------------------------------------------------------- #
class DemoAccess:
    """Access codes of the sample employees: a per-employee code, or the default one."""

    def __init__(self, path: FilePath = DEMO_ACCESS_JSON):
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.default_code: Optional[str] = data.get("default_code") or None
        self.codes: dict[str, str] = dict(data.get("codes") or {})

    def code_for(self, emp_id: str) -> Optional[str]:
        return self.codes.get(emp_id, self.default_code)

    def check(self, emp_id: str, code: str) -> bool:
        expected = self.code_for(emp_id)
        return expected is not None and hmac.compare_digest(code.encode(), expected.encode())


class LoginThrottle:
    """Slows down guessing: after MAX_FAILURES wrong codes for one employee number, that
    number is locked for LOCK_S seconds. In-process (one worker), like SessionLocks."""
    MAX_FAILURES = 5
    LOCK_S = 300

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: dict[str, tuple[int, float]] = {}

    def locked(self, key: str, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            count, since = self._failures.get(key, (0, 0.0))
            if count >= self.MAX_FAILURES and now - since < self.LOCK_S:
                return True
            if count >= self.MAX_FAILURES:  # lock expired
                self._failures.pop(key, None)
            return False

    def failed(self, key: str, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            count, _ = self._failures.get(key, (0, 0.0))
            self._failures[key] = (count + 1, now)

    def succeeded(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


# --------------------------------------------------------------------------- #
# FastAPI dependencies
# --------------------------------------------------------------------------- #
_bearer = HTTPBearer(auto_error=False, description="הטוקן שהתקבל מ-POST /api/auth/login")

UNAUTHORIZED = "נדרשת כניסה למערכת (הטוקן חסר, שגוי או שפג תוקפו)"
FORBIDDEN = "אין הרשאה לשיחה הזו"


def require_claims(request: Request,
                   cred: Optional[HTTPAuthorizationCredentials] = Security(_bearer)) -> Claims:
    """A valid signed token - any conversation."""
    if cred is None or cred.scheme.lower() != "bearer" or not cred.credentials:
        raise HTTPException(401, UNAUTHORIZED, headers={"WWW-Authenticate": "Bearer"})
    try:
        return verify_token(request.app.state.secret_key, cred.credentials)
    except AuthError as e:
        log.info("rejected token: %s", e)
        raise HTTPException(401, UNAUTHORIZED, headers={"WWW-Authenticate": "Bearer"}) from None


def ensure_owner(claims: Claims, session_id: str) -> None:
    """The token must belong to exactly this conversation."""
    if not hmac.compare_digest(claims.sid, session_id):
        raise HTTPException(403, FORBIDDEN)


def session_claims(session_id: str = Path(..., pattern=SESSION_ID_PATTERN),
                   claims: Claims = Depends(require_claims)) -> Claims:
    """For routes with {session_id} in the path: token valid AND it owns that session."""
    ensure_owner(claims, session_id)
    return claims
