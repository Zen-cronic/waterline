"""Live NAV CANADA fetch. One curl, no key — a judge can reproduce every pull.

We consume the live feed; we do not redistribute it. Ignored frozen captures in
data/captures/ are a local-development fallback only and are excluded from
deployment images. The reproducible source of truth is the URL this module
builds.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

import httpx

from .config import NAVCANADA_ALPHA

_CAPTURES = Path(__file__).resolve().parents[2] / "data" / "captures"


def source_url(site: str, alpha: str = "notam") -> str:
    return f"{NAVCANADA_ALPHA}?site={site}&alpha={alpha}"


def _receipt(payload: dict[str, Any], url: str, product: str, mode: str,
             source_ref: str, live_error: str | None = None) -> dict[str, Any]:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return {
        "provider": "NAV CANADA",
        "product": product,
        "source_url": url,
        "source_mode": mode,
        "source_ref": source_ref,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "payload_sha256": sha256(encoded).hexdigest(),
        "live_error": live_error,
    }


def _fetch(site: str, product: str, capture_name: str,
           timeout: float) -> tuple[dict[str, Any], dict[str, Any]]:
    url = source_url(site, product)
    try:
        r = httpx.get(url, timeout=timeout)
        r.raise_for_status()
        payload = r.json()
        if not isinstance(payload, dict):
            raise ValueError("NAV CANADA payload must be a JSON object")
        return payload, _receipt(payload, url, product, "live", url)
    except (httpx.HTTPError, ValueError) as exc:
        cap = _CAPTURES / capture_name
        if cap.exists():
            payload = json.loads(cap.read_text())
            if not isinstance(payload, dict):
                raise ValueError(f"local capture {cap.name} must be a JSON object")
            return payload, _receipt(
                payload, url, product, "local_frozen_capture", cap.name,
                type(exc).__name__,
            )
        raise


def fetch_notams(site: str, timeout: float = 25.0) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fetch NOTAMs with a structured live/local-fallback provenance receipt."""
    return _fetch(site, "notam", f"navcanada_{site}.json", timeout)


def fetch_metars(site: str, timeout: float = 25.0) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fetch METARs with a structured live/local-fallback provenance receipt."""
    return _fetch(site, "metar", f"metar_{site}.json", timeout)
