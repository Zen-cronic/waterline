"""Deterministic, short-lived capabilities for one Firestore follower room."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from typing import Any


HANDOFF_LIFETIME = timedelta(hours=1)


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def configured_handoff_secret() -> str:
    secret = os.environ.get("WATERLINE_HANDOFF_SECRET")
    if not secret or len(secret.encode("utf-8")) < 32:
        raise RuntimeError("WATERLINE_HANDOFF_SECRET must contain at least 32 bytes")
    return secret


def handoff_idempotency_key(
    *, session_id: str, mission_id: str, route: dict[str, Any], eta: str,
) -> str:
    """Stable identity for one mission room across retries and recovery."""
    canonical = json.dumps(
        {
            "session_id": session_id,
            "mission_id": mission_id,
            "departure": route.get("dep_id"),
            "destination": route.get("dst_name"),
            "eta": eta,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def handoff_payload(
    *, mission_id: str, route: dict[str, Any],
    flight_plan: dict[str, Any] | None, eta: str, expires_at: int,
) -> dict[str, Any]:
    corrected = (flight_plan or {}).get("corrected_plan", {})
    return {
        "v": 1,
        "mission_id": mission_id,
        "departure": route.get("dep_id", "?"),
        "destination": route.get("dst_name", "?"),
        "landing_sector": corrected.get("landing_sector", "pilot review"),
        "eta": eta,
        "expires_at": expires_at,
    }


def signed_handoff_token(
    *, mission_id: str, route: dict[str, Any],
    flight_plan: dict[str, Any] | None, eta: str, expires_at: int,
    secret: str | None = None,
) -> str:
    """Sign an immutable invitation without storing the raw token in SQL."""
    payload = handoff_payload(
        mission_id=mission_id,
        route=route,
        flight_plan=flight_plan,
        eta=eta,
        expires_at=expires_at,
    )
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    encoded = _encode(canonical)
    signing_secret = secret or configured_handoff_secret()
    signature = hmac.new(
        signing_secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256,
    )
    return f"{encoded}.{signature.hexdigest()}"


def proposed_expiry(now: datetime | None = None) -> int:
    issued = now or datetime.now(timezone.utc)
    return int((issued + HANDOFF_LIFETIME).timestamp())


def token_sha256(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()
