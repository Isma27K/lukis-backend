"""Attempt counting for abuse-prone routes (logins, sign-ups, emails).

Counts live in this process's memory, like the live-collaboration hub, so they
assume a single backend process and reset on restart.
"""

import time
from collections import deque
from dataclasses import dataclass

from fastapi import Request

from function.errors import TooManyRequestsError


@dataclass(frozen=True)
class Limit:
    name: str
    attempts: int
    seconds: int


# Per client IP unless noted. Generous for people, tight for scripts.
LOGIN_PER_IP = Limit("login-ip", 30, 5 * 60)
# Failed logins per account, against password guessing from many addresses.
LOGIN_FAILURES_PER_EMAIL = Limit("login-email", 10, 15 * 60)
REGISTER_PER_IP = Limit("register-ip", 10, 60 * 60)
# These send email, so they're also limited per address to stop mail bombing.
RESET_PER_IP = Limit("reset-ip", 10, 60 * 60)
RESET_PER_EMAIL = Limit("reset-email", 3, 60 * 60)
VERIFY_RESEND_PER_USER = Limit("verify-user", 3, 15 * 60)
INVITES_PER_USER = Limit("invite-user", 30, 60 * 60)
# Wrong passwords when changing it or deleting the account, per user, so a
# stolen session can't be used to guess the password.
PASSWORD_FAILURES_PER_USER = Limit("password-user", 10, 15 * 60)
# Using a link from an email (verify, reset).
TOKEN_USE_PER_IP = Limit("token-ip", 30, 60 * 60)
# Logging in with Google and the like: going to the provider and coming back
# each count.
OAUTH_PER_IP = Limit("oauth-ip", 60, 5 * 60)
# Passwords on public links: every attempt per address, and wrong ones per
# link, against guessing from many addresses.
SHARE_UNLOCK_PER_IP = Limit("share-unlock-ip", 20, 15 * 60)
SHARE_UNLOCK_FAILURES_PER_LINK = Limit("share-unlock-link", 100, 15 * 60)

# Forget keys that have been quiet this long; longer than any window above.
_FORGET_AFTER = 2 * 60 * 60
_SWEEP_EVERY = 1000


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._calls = 0

    def check(self, limit: Limit, key: str) -> None:
        """Counts an attempt, or raises TooManyRequestsError if over the limit."""
        retry_after = self.retry_after(limit, key)
        if retry_after:
            raise TooManyRequestsError(retry_after)
        self.count(limit, key)

    def retry_after(self, limit: Limit, key: str) -> int:
        """Seconds until another attempt is allowed; 0 if one is allowed now."""
        hits = self._recent(limit, key)
        if len(hits) < limit.attempts:
            return 0
        return max(1, int(hits[0] + limit.seconds - time.monotonic()) + 1)

    def count(self, limit: Limit, key: str) -> None:
        self._recent(limit, key).append(time.monotonic())
        self._calls += 1
        if self._calls % _SWEEP_EVERY == 0:
            self._sweep()

    def _recent(self, limit: Limit, key: str) -> deque[float]:
        hits = self._hits.setdefault((limit.name, key), deque())
        cutoff = time.monotonic() - limit.seconds
        while hits and hits[0] <= cutoff:
            hits.popleft()
        return hits

    def _sweep(self) -> None:
        cutoff = time.monotonic() - _FORGET_AFTER
        stale = [k for k, hits in self._hits.items() if not hits or hits[-1] < cutoff]
        for key in stale:
            del self._hits[key]


def client_ip(request: Request) -> str:
    # Behind Caddy and Next.js this is the visitor's address: uvicorn runs with
    # --proxy-headers and takes it from X-Forwarded-For, which Caddy sets.
    return request.client.host if request.client else "unknown"


def get_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter
