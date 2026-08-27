"""Short-lived signed links for the marked synthetic handoff summary."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from typing import Any
from urllib.parse import quote


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _secret() -> str:
    secret = os.environ.get("WATERLINE_HANDOFF_SECRET")
    if not secret or len(secret.encode("utf-8")) < 32:
        raise RuntimeError("SMS mode requires WATERLINE_HANDOFF_SECRET (at least 32 bytes)")
    return secret


def signed_handoff_url(
    *, mission_id: str, route: dict[str, Any], flight_plan: dict[str, Any] | None,
    eta: str, now: datetime | None = None,
) -> str:
    """Create a self-contained, read-only, one-hour route-summary link."""
    public_url = os.environ.get("WATERLINE_PUBLIC_WEB_URL", "").rstrip("/")
    if not public_url.startswith("https://"):
        raise RuntimeError("SMS mode requires an HTTPS WATERLINE_PUBLIC_WEB_URL")
    corrected = (flight_plan or {}).get("corrected_plan", {})
    issued = now or datetime.now(timezone.utc)
    payload = {
        "v": 1,
        "mission_id": mission_id,
        "departure": route.get("dep_id", "?"),
        "destination": route.get("dst_name", "?"),
        "landing_sector": corrected.get("landing_sector", "pilot review"),
        "eta": eta,
        "expires_at": int((issued + timedelta(hours=1)).timestamp()),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    encoded = _encode(canonical)
    signature = hmac.new(_secret().encode("utf-8"), encoded.encode("ascii"), hashlib.sha256)
    return f"{public_url}/handoff/{quote(encoded)}.{signature.hexdigest()}"
