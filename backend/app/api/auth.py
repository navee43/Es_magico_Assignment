"""Deliberately simple auth: one shared password -> short-lived HMAC-signed bearer token.

Good enough to keep confidential invoices and admin APIs private on a single-user
demo deployment. Not a multi-user identity system (see README "Known limitations").
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

from backend.app.config import Settings


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class TokenSigner:
    def __init__(self, secret: str):
        self._key = secret.encode()

    def issue(self, ttl_seconds: int) -> tuple[str, int]:
        expires = int(time.time()) + ttl_seconds
        payload = f"{expires}.{secrets.token_hex(8)}".encode()
        sig = hmac.new(self._key, payload, hashlib.sha256).digest()
        return f"{_b64(payload)}.{_b64(sig)}", expires

    def verify(self, token: str) -> bool:
        try:
            payload_b64, sig_b64 = token.split(".", 1)
            payload = _unb64(payload_b64)
            expected = hmac.new(self._key, payload, hashlib.sha256).digest()
            if not hmac.compare_digest(expected, _unb64(sig_b64)):
                return False
            expires = int(payload.split(b".", 1)[0])
        except (ValueError, TypeError):
            return False
        return expires > time.time()


class LoginRateLimiter:
    """At most `limit` login attempts per client IP per `window` seconds."""

    def __init__(self, limit: int = 10, window: int = 300):
        self.limit, self.window = limit, window
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True


def check_password(settings: Settings, candidate: str) -> bool:
    if not settings.app_password:
        return False
    return hmac.compare_digest(settings.app_password.encode(), candidate.encode())


def require_auth(request: Request) -> None:
    settings: Settings = request.app.state.settings
    if not settings.auth_enabled:
        return
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token or not request.app.state.signer.verify(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Sign in to access this resource."},
            headers={"WWW-Authenticate": "Bearer"},
        )
