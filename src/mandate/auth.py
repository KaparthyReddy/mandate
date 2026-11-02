import hmac
import math
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Literal, NamedTuple

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from mandate import keys
from mandate.config import get_settings
from mandate.db import get_session


class Principal(NamedTuple):
    role: Literal["admin", "agent"]
    agent_id: str | None
    key_id: str


@dataclass(frozen=True)
class AuthConfig:
    enabled: bool
    admin_key: str


def get_auth_config() -> AuthConfig:
    settings = get_settings()
    return AuthConfig(settings.auth_enabled, settings.admin_key)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def _bearer(header: str | None) -> str | None:
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def authenticate(
    session: Annotated[Session, Depends(get_session)],
    config: Annotated[AuthConfig, Depends(get_auth_config)],
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    if not config.enabled:
        return Principal("admin", None, "auth-disabled")
    if not config.admin_key:
        raise HTTPException(
            status_code=503,
            detail="MANDATE_ADMIN_KEY is not set. Set it, or set AUTH_ENABLED=false for local use.",
        )
    token = _bearer(authorization)
    if token is None:
        raise _unauthorized("missing API key")
    if hmac.compare_digest(token.encode(), config.admin_key.encode()):
        return Principal("admin", None, "admin")
    record = keys.find_active(session, token)
    if record is None:
        raise _unauthorized("invalid or revoked API key")
    return Principal("agent", record.agent_id, record.key_id)


def require_admin(principal: Annotated[Principal, Depends(authenticate)]) -> Principal:
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="this action needs an admin key")
    return principal


class RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self._limit = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, now: float | None = None) -> int:
        if self._limit <= 0:
            return 0
        moment = time.monotonic() if now is None else now
        with self._lock:
            window = self._hits[key]
            while window and moment - window[0] >= 60:
                window.popleft()
            if len(window) >= self._limit:
                return max(1, math.ceil(60 - (moment - window[0])))
            window.append(moment)
        return 0


@lru_cache
def get_rate_limiter() -> RateLimiter:
    return RateLimiter(get_settings().rate_limit_per_minute)


def rate_limited(
    principal: Annotated[Principal, Depends(authenticate)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> Principal:
    retry_after = limiter.check(principal.key_id)
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail="rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
    return principal
