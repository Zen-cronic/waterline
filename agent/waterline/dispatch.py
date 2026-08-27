"""The real-world loop: file a flight itinerary and send it to a responsible person.

This is the irreversible external action. A float pilot flying to a station-less
lake is exactly the person who goes missing; leaving an itinerary with a
responsible person (route, ETA, and what to do if overdue) is a legal Canadian
pre-flight requirement for this flight. Waterline sends that itinerary for real.

Sends via SMTP when creds are configured; otherwise writes the message to a local
outbox (still a real, inspectable artifact) so the flow is provable end to end
without secrets. Structured as a real send either way.
"""
from __future__ import annotations

import os
import smtplib
import hashlib
import json
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

_OUTBOX = Path(__file__).resolve().parents[1] / "data" / "outbox"


def dispatch_idempotency_key(session_id: str, to: str, route: dict[str, Any],
                             eta: str, grace_min: int) -> str:
    """Stable identity for one itinerary send across retry and crash-resume."""
    canonical = json.dumps(
        {
            "session_id": session_id,
            "to": to.strip().lower(),
            "dep_id": route.get("dep_id"),
            "dst_name": route.get("dst_name"),
            "eta": eta,
            "grace_min": grace_min,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _smtp_configured() -> bool:
    return bool(os.environ.get("WATERLINE_SMTP_HOST") and os.environ.get("WATERLINE_SMTP_FROM"))


def outbound_mode() -> str:
    """Return the explicit outbound mode, failing closed on unsafe configuration."""
    configured = os.environ.get("WATERLINE_OUTBOUND_MODE")
    if configured is None:
        return "smtp" if _smtp_configured() else "outbox"
    mode = configured.strip().lower()
    if mode not in {"outbox", "smtp"}:
        raise RuntimeError("WATERLINE_OUTBOUND_MODE must be 'outbox' or 'smtp'")
    if mode == "smtp" and not _smtp_configured():
        raise RuntimeError("SMTP outbound mode requires host and sender configuration")
    return mode


def send_email(to: str, subject: str, body: str) -> dict[str, Any]:
    """Send an email for real via SMTP, or write it to the local outbox as a fallback."""
    frm = os.environ.get("WATERLINE_SMTP_FROM", "waterline@localhost")
    msg = EmailMessage()
    msg["From"] = frm
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    if outbound_mode() == "smtp":
        host = os.environ["WATERLINE_SMTP_HOST"]
        port = int(os.environ.get("WATERLINE_SMTP_PORT", "587"))
        user = os.environ.get("WATERLINE_SMTP_USER")
        pw = os.environ.get("WATERLINE_SMTP_PASS")
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls()
            if user and pw:
                s.login(user, pw)
            s.send_message(msg)
        return {"sent": True, "channel": "smtp", "to": to}

    _OUTBOX.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = _OUTBOX / f"{stamp}_{to.replace('@', '_at_')}.eml"
    path.write_text(str(msg))
    return {"sent": True, "channel": "outbox", "to": to, "path": str(path)}


def compose_following_notice(route: dict[str, Any], corridor: dict[str, Any],
                             weather: dict[str, Any], briefing: str,
                             eta: str, grace_min: int) -> tuple[str, str]:
    """Compose the flight-following notice a responsible person receives."""
    dep, dst = route.get("dep_id", "?"), route.get("dst_name", "?")
    subject = f"[Waterline] Flight itinerary: {dep} → {dst} (ETA {eta})"
    reach = weather.get("reach_nm")
    body = f"""FLIGHT-FOLLOWING NOTICE — please keep until you hear the pilot is safe.

Pilot filing a float flight:
  Departure:    {dep} ({route.get('dep_name','')})
  Destination:  {dst}  (a lake with NO identifier and NO weather station)
  Cruise:       FL{route.get('cruise_fl_upper','?'):03d} band
  ETA:          {eta}
  Overdue if no check-in by: ETA + {grace_min} min

If the pilot has NOT checked in by the overdue time above:
  1. Try to reach them.
  2. If unreachable, call the Joint Rescue Coordination Centre (JRCC) 1-800-267-7270.
  3. Give them this route and the search corridor below.

Search corridor: a ~{'10'} NM band along the straight line {dep} → {dst}.
On-route hazards briefed: {corridor.get('kept','?')} NOTAMs (of {corridor.get('total','?')} in the region).
Destination weather is INFERRED from the nearest station ~{reach} NM away — not measured at the lake.

--- BRIEFING (aid only; NOT FOR OPERATIONAL USE) ---
{briefing}
"""
    return subject, body
