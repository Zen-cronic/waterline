"""DispatchAgent tools — the real-world loop, authenticated-human-gated.

file_and_notify creates one marked synthetic flight-following handoff to the
responsible person (the bounded external action). It independently checks
that the briefing proof and mission-owner attestation agree before any claim.
"""
from __future__ import annotations

import asyncio
from typing import Any, MutableMapping

from google.adk.tools import ToolContext

from .. import db
from ..dispatch import (
    compose_following_notice,
    compose_sms_notice,
    configured_delivery_target,
    dispatch_idempotency_key,
    send_notice,
)
from ..emit import emit_step, emit_panel
from ..handoff import signed_handoff_url
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
            "committed": False,
            "sent": False,
            "reason": "; ".join(decision.reasons),
            "authority": decision.as_dict(),
        }

    target = configured_delivery_target()
    route = state.get("route", {})
    corridor = state.get("corridor", {})
    weather = state.get("weather", {})
    briefing = state.get("briefing", "")
    eta = state.get("eta", "this afternoon")
    grace = int(state.get("grace_min", 60))
    dispatch_key = dispatch_idempotency_key(
        session_id, target.channel, target.address, route, eta, grace,
    )
    subject, body = compose_following_notice(route, corridor, weather, briefing, eta, grace)

    handoff_url = ""
    if target.channel == "sms":
        handoff_url = signed_handoff_url(
            mission_id=str(state.get("mission_id", "")),
            route=route,
            flight_plan=state.get("flight_plan") if isinstance(state.get("flight_plan"), dict) else None,
            eta=eta,
        )
    sms_body = compose_sms_notice(
        route,
        state.get("flight_plan") if isinstance(state.get("flight_plan"), dict) else None,
        eta,
        handoff_url,
    )

    claimed = await asyncio.to_thread(db.claim_dispatch, dispatch_key, session_id, target.address)
    if not claimed:
        existing = await asyncio.to_thread(db.dispatch_receipt, dispatch_key)
        result = {
            "committed": True,
            "sent": bool(existing and existing.get("status") == "sent"),
            "duplicate_suppressed": True,
            "receipt_id": dispatch_key,
            "channel": existing.get("channel") if existing else target.channel,
            "provider_reference": existing.get("provider_reference") if existing else None,
            "provider_status": existing.get("provider_status") if existing else None,
            "recipient_redacted": (
                existing.get("recipient_redacted") if existing else target.redacted
            ),
            "status": (
                "delivered" if existing and existing.get("provider_status") == "delivered"
                else "replay_suppressed" if existing and existing.get("status") == "sent"
                else "reconciliation_required"
            ),
            "at_most_once": True,
        }
        emit_panel("dispatch", result)
        emit_step("DispatchAgent", "duplicate-suppressed",
                  "The original receipt was returned; retry/resume sent no duplicate notice.")
        return result

    emit_step(
        "DispatchAgent", "claim-created",
        f"Creating one marked synthetic handoff to {target.redacted}…",
    )
    result = await asyncio.to_thread(send_notice, target, subject, body, sms_body)
    provider_status = result.get("provider_status")
    receipt_status = result.get("status") or "completed"
    await asyncio.to_thread(
        db.complete_dispatch,
        dispatch_key,
        result["channel"],
        result.get("provider_reference"),
        provider_status,
        target.redacted,
    )
    state["dispatch_receipt"] = {
        "idempotency_key": dispatch_key,
        "channel": result["channel"],
        "sent": True,
        "provider_reference": result.get("provider_reference"),
        "provider_status": provider_status,
        "recipient_redacted": target.redacted,
        "status": receipt_status,
    }
    emit_panel("dispatch", {
        "recipient_redacted": target.redacted, "eta": eta, "grace_min": grace,
        "receipt_id": dispatch_key, **result,
    })
    wording = "provider accepted" if result["channel"] == "sms" else "completed"
    emit_step(
        "DispatchAgent", "handoff-completed",
        f"Marked synthetic handoff {wording} via {result['channel']} for {target.redacted}.",
    )
    return {
        "committed": True, "receipt_id": dispatch_key,
        "recipient_redacted": target.redacted, "at_most_once": True,
        **result,
    }


async def file_and_notify(tool_context: ToolContext) -> dict[str, Any]:
    """Send one marked synthetic flight-following handoff to the responsible person.

    Resolves the allowlisted responsible person from server configuration and
    reads only ETA/grace from state. Missing or mismatched authority fails closed
    before any send claim. The return value never exposes raw contact PII.
    """
    return await dispatch_from_state(tool_context.session.id, tool_context.state)
