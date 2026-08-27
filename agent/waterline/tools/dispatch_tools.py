"""DispatchAgent tools — the real-world loop, authenticated-human-gated.

file_and_notify files the itinerary and sends the flight-following notice to the
responsible person (the irreversible external action). It independently checks
that the briefing proof and mission-owner attestation agree before any claim.
"""
from __future__ import annotations

import asyncio
from typing import Any, MutableMapping

from google.adk.tools import ToolContext

from .. import db
from ..dispatch import compose_following_notice, dispatch_idempotency_key, send_email
from ..emit import emit_step, emit_panel
from ..verification import assess_dispatch_readiness


async def dispatch_from_state(session_id: str,
                              state: MutableMapping[str, Any]) -> dict[str, Any]:
    """Execute one dispatch only after independently rechecking authority."""
    decision = assess_dispatch_readiness(state)
    state["dispatch_authorized"] = decision.approved
    state["verification_gate"] = decision.as_dict()
    if not decision.approved:
        emit_step("DispatchAgent", "blocked", "; ".join(decision.reasons))
        return {
            "filed": False,
            "sent": False,
            "reason": "; ".join(decision.reasons),
            "authority": decision.as_dict(),
        }

    to = state.get("responsible_email")
    if not isinstance(to, str) or not to:
        return {"filed": False, "sent": False, "reason": "responsible email is missing"}

    route = state.get("route", {})
    corridor = state.get("corridor", {})
    weather = state.get("weather", {})
    briefing = state.get("briefing", "")
    eta = state.get("eta", "this afternoon")
    grace = int(state.get("grace_min", 60))
    dispatch_key = dispatch_idempotency_key(session_id, to, route, eta, grace)
    subject, body = compose_following_notice(route, corridor, weather, briefing, eta, grace)

    claimed = await asyncio.to_thread(db.claim_dispatch, dispatch_key, session_id, to)
    if not claimed:
        result = {
            "filed": True,
            "sent": False,
            "duplicate_suppressed": True,
            "receipt_id": dispatch_key,
            "to": to,
        }
        emit_panel("dispatch", result)
        emit_step("DispatchAgent", "duplicate-suppressed",
                  "This itinerary was already filed; retry/resume sent no duplicate notice.")
        return result

    emit_step("DispatchAgent", "filing", f"Filing itinerary and notifying {to}…")
    result = await asyncio.to_thread(send_email, to, subject, body)
    await asyncio.to_thread(db.complete_dispatch, dispatch_key, result["channel"])
    state["dispatch_receipt"] = {
        "idempotency_key": dispatch_key,
        "channel": result["channel"],
        "sent": True,
    }
    emit_panel("dispatch", {"to": to, "eta": eta, "grace_min": grace, **result})
    emit_step("DispatchAgent", "filed",
              f"Itinerary filed; flight-following notice sent to {to} via {result['channel']}.")
    return {"filed": True, "receipt_id": dispatch_key, **result}


async def file_and_notify(tool_context: ToolContext) -> dict[str, Any]:
    """File the flight itinerary and send the flight-following notice to the responsible person.

    Reads the attested responsible person's email, ETA, and grace window from
    state. Missing or mismatched authority fails closed before any send claim.
    Returns {filed: bool, sent: bool, channel, to} or {filed: false, reason}.
    """
    return await dispatch_from_state(tool_context.session.id, tool_context.state)
