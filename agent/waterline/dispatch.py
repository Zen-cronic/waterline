"""The real-world loop: hand a marked synthetic itinerary to a responsible person.

This is the bounded external action. Waterline sends a marked synthetic route,
ETA, and follow-up context to an operator-designated responsible person. It does
not file a flight plan, notify SAR, or perform operational dispatch.

SMS is the camera-visible provider path. SMTP is the operator-selected fallback;
the local outbox remains the no-network default. The recipient is always selected
from server configuration and never accepted from a browser request.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import smtplib
import hashlib
import json
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import httpx

_OUTBOX = Path(__file__).resolve().parents[1] / "data" / "outbox"
_TWILIO_SID = "https://api.twilio.com/2010-04-01/Accounts/{account}/Messages.json"
_PROVIDER_ACCEPTED = {"accepted", "queued", "sending", "sent", "delivered"}


@dataclass(frozen=True)
class DeliveryTarget:
    channel: str
    address: str
    redacted: str


def _redact_phone(value: str) -> str:
    digits = "".join(character for character in value if character.isdigit())
    return f"+{digits[:1]}••••••{digits[-4:]}" if len(digits) >= 5 else "configured phone"


def configured_delivery_target() -> DeliveryTarget:
    """Resolve the sole server-owned recipient for the selected transport."""
    mode = outbound_mode()
    if mode == "sms":
        recipient = os.environ.get("WATERLINE_DEMO_SMS_TO", "").strip()
        if not recipient.startswith("+") or not recipient[1:].isdigit():
            raise RuntimeError("SMS mode requires WATERLINE_DEMO_SMS_TO in E.164 format")
        return DeliveryTarget("sms", recipient, _redact_phone(recipient))
    recipient = os.environ.get("WATERLINE_DEMO_EMAIL_TO", "").strip().lower()
    if mode == "smtp" and (not recipient or recipient.count("@") != 1):
        raise RuntimeError("SMTP mode requires WATERLINE_DEMO_EMAIL_TO")
    if not recipient:
        recipient = "operator-owned@example.test"
    return DeliveryTarget(mode, recipient, "operator-owned email")


def dispatch_idempotency_key(session_id: str, channel: str, to: str,
                             route: dict[str, Any], eta: str, grace_min: int) -> str:
    """Stable identity for one itinerary send across retry and crash-resume."""
    canonical = json.dumps(
        {
            "session_id": session_id,
            "channel": channel,
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


def _sms_configured() -> bool:
    return all(os.environ.get(name) for name in (
        "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER",
        "WATERLINE_DEMO_SMS_TO", "WATERLINE_PUBLIC_WEB_URL", "WATERLINE_HANDOFF_SECRET",
    ))


def outbound_mode() -> str:
    """Return the explicit outbound mode, failing closed on unsafe configuration."""
    configured = os.environ.get("WATERLINE_OUTBOUND_MODE")
    if configured is None:
        return "smtp" if _smtp_configured() else "outbox"
    mode = configured.strip().lower()
    if mode not in {"outbox", "smtp", "sms"}:
        raise RuntimeError("WATERLINE_OUTBOUND_MODE must be 'outbox', 'smtp', or 'sms'")
    if mode == "smtp" and not _smtp_configured():
        raise RuntimeError("SMTP outbound mode requires host and sender configuration")
    if mode == "sms" and not _sms_configured():
        raise RuntimeError("SMS outbound mode requires complete Twilio, recipient, URL, and handoff configuration")
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
        return {"sent": True, "channel": "smtp", "status": "completed"}

    _OUTBOX.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    recipient_ref = hashlib.sha256(to.strip().lower().encode("utf-8")).hexdigest()[:12]
    path = _OUTBOX / f"{stamp}_{recipient_ref}.eml"
    path.write_text(str(msg))
    return {
        "sent": True,
        "channel": "outbox",
        "status": "completed",
        "artifact_ref": path.name,
    }


def send_sms(to: str, body: str, status_callback: str) -> dict[str, Any]:
    """Create one Twilio Message and preserve its initial provider state truthfully."""
    account = os.environ["TWILIO_ACCOUNT_SID"]
    response = httpx.post(
        _TWILIO_SID.format(account=account),
        auth=(account, os.environ["TWILIO_AUTH_TOKEN"]),
        data={
            "To": to,
            "From": os.environ["TWILIO_FROM_NUMBER"],
            "Body": body,
            "StatusCallback": status_callback,
        },
        timeout=20.0,
    )
    response.raise_for_status()
    payload = response.json()
    provider_reference = payload.get("sid")
    provider_status = str(payload.get("status", "")).lower()
    if not isinstance(provider_reference, str) or not provider_reference.startswith("SM"):
        raise RuntimeError("Twilio response did not include a Message SID")
    if provider_status not in _PROVIDER_ACCEPTED:
        raise RuntimeError(f"Twilio did not accept the message: {provider_status or 'unknown'}")
    return {
        "sent": True,
        "channel": "sms",
        "provider": "twilio",
        "provider_reference": provider_reference,
        "provider_status": provider_status,
        "status": "delivered" if provider_status == "delivered" else "provider_accepted",
    }


def send_notice(target: DeliveryTarget, subject: str, email_body: str,
                sms_body: str) -> dict[str, Any]:
    if target.channel == "sms":
        callback = f"{os.environ['WATERLINE_PUBLIC_WEB_URL'].rstrip('/')}/api/waterline/providers/twilio/status"
        return send_sms(target.address, sms_body, callback)
    return send_email(target.address, subject, email_body)


def compose_sms_notice(route: dict[str, Any], flight_plan: dict[str, Any] | None,
                       eta: str, handoff_url: str) -> str:
    dep, dst = route.get("dep_id", "?"), route.get("dst_name", "?")
    corrected = (flight_plan or {}).get("corrected_plan", {})
    sector = str(corrected.get("landing_sector", "pilot review")).upper()
    return (
        "WATERLINE DEMO — NO ACTIVE FLIGHT\n"
        f"{dep} → {dst} · {sector} COVE · ETA {eta}\n"
        f"Briefing and route: {handoff_url}"
    )


def compose_following_notice(route: dict[str, Any], corridor: dict[str, Any],
                             weather: dict[str, Any], briefing: str,
                             eta: str, grace_min: int) -> tuple[str, str]:
    """Compose the flight-following notice a responsible person receives."""
    dep, dst = route.get("dep_id", "?"), route.get("dst_name", "?")
    subject = f"[Waterline] Flight itinerary: {dep} → {dst} (ETA {eta})"
    reach = weather.get("reach_nm")
    body = f"""WATERLINE DEMO — NO ACTIVE FLIGHT

Marked synthetic flight-following handoff:
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

--- PILOT-REVIEWED BRIEFING PACKAGE ---
{briefing}
"""
    return subject, body
