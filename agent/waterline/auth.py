"""Authenticated web-relay identity for Waterline mission commands.

Cloud Run IAM authenticates the web service to the private agent service. This
application-layer signature additionally binds the server-injected pilot actor
to the exact method, path, body, and a short timestamp window. The browser never
receives the relay secret and cannot select an actor, ADK user, or session id.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import hmac
import os
import re
import time


_ACTOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:@._-]{2,191}$")
_SIGNATURE = re.compile(r"^[0-9a-f]{64}$")
_LOCAL_RELAY_SECRET = "waterline-local-relay-secret-change-before-deploy"


class RelayAuthenticationError(ValueError):
    """A relay request could not be authenticated."""


@dataclass(frozen=True)
class PilotIdentity:
    actor: str
    owner_ref: str
    user_id: str


def configured_relay_secret() -> str:
    secret = os.environ.get("WATERLINE_RELAY_SECRET")
    if not secret and not os.environ.get("K_SERVICE"):
        secret = _LOCAL_RELAY_SECRET
    if not secret or len(secret.encode()) < 32:
        raise RelayAuthenticationError("relay authentication is not configured")
    return secret


def canonical_request(method: str, path: str, body: bytes, actor: str,
                      timestamp: str) -> bytes:
    digest = sha256(body).hexdigest()
    return f"{timestamp}\n{method.upper()}\n{path}\n{digest}\n{actor}".encode()


def sign_relay_request(secret: str, method: str, path: str, body: bytes,
                       actor: str, timestamp: str) -> str:
    return hmac.new(
        secret.encode(), canonical_request(method, path, body, actor, timestamp), sha256,
    ).hexdigest()


def verify_relay_request(*, secret: str, method: str, path: str, body: bytes,
                         actor: str | None, timestamp: str | None,
                         signature: str | None, now: int | None = None,
                         max_age_seconds: int = 60) -> PilotIdentity:
    if not actor or not _ACTOR.fullmatch(actor):
        raise RelayAuthenticationError("authenticated pilot actor is invalid")
    if not timestamp:
        raise RelayAuthenticationError("relay timestamp is missing")
    try:
        sent_at = int(timestamp)
    except ValueError as exc:
        raise RelayAuthenticationError("relay timestamp is invalid") from exc
    current = int(time.time()) if now is None else now
    if abs(current - sent_at) > max_age_seconds:
        raise RelayAuthenticationError("relay signature is stale")
    if not signature or not _SIGNATURE.fullmatch(signature):
        raise RelayAuthenticationError("relay signature is invalid")
    expected = sign_relay_request(secret, method, path, body, actor, timestamp)
    if not hmac.compare_digest(expected, signature):
        raise RelayAuthenticationError("relay signature does not match the request")

    digest = sha256(actor.encode()).hexdigest()
    owner_ref = f"pilot-{digest[:24]}"
    return PilotIdentity(actor=actor, owner_ref=owner_ref, user_id=owner_ref)
