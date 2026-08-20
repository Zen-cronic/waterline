"""Live NAV CANADA fetch. One curl, no key — a judge can reproduce every pull.

We CONSUME the live feed; we never redistribute it. The frozen captures in
data/captures/ are for offline demo determinism, but the reproducible source of
truth is the URL this module builds.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from .config import NAVCANADA_ALPHA

_CAPTURES = Path(__file__).resolve().parents[2] / "data" / "captures"


def source_url(site: str, alpha: str = "notam") -> str:
    return f"{NAVCANADA_ALPHA}?site={site}&alpha={alpha}"


def fetch_notams(site: str, timeout: float = 25.0) -> tuple[dict[str, Any], str]:
    """Return (payload, source_url). Falls back to a frozen capture if offline."""
    url = source_url(site, "notam")
    try:
        r = httpx.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json(), url
    except Exception:
        cap = _CAPTURES / f"navcanada_{site}.json"
        if cap.exists():
            return json.loads(cap.read_text()), url + "  (served from frozen capture)"
        raise


def fetch_metars(site: str, timeout: float = 25.0) -> tuple[dict[str, Any], str]:
    url = source_url(site, "metar")
    try:
        r = httpx.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json(), url
    except Exception:
        cap = _CAPTURES / f"metar_{site}.json"
        if cap.exists():
            return json.loads(cap.read_text()), url + "  (served from frozen capture)"
        raise
