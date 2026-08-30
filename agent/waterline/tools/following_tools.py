"""FollowingAgent tools for one attested, duplicate-safe Firestore room."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, MutableMapping

from google.adk.tools import ToolContext

from .. import db
from ..emit import emit_panel, emit_step
from ..handoff import (
    handoff_idempotency_key,
    proposed_expiry,
    signed_handoff_token,
    token_sha256,
)
from ..verification import assess_dispatch_readiness


def _epoch_seconds(value: Any) -> int:
    if isinstance(value, datetime):
        return int(value.timestamp())
    if isinstance(value, str):
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    return int(value)


async def open_follower_room_from_state(
    session_id: str, state: MutableMapping[str, Any],
) -> dict[str, Any]:
    """Claim and reproduce one signed Firestore-room invitation."""
    decision = assess_dispatch_readiness(state)
    state["dispatch_authorized"] = decision.approved
    state["verification_gate"] = decision.as_dict()
    if not decision.approved:
        emit_step("FollowingAgent", "blocked", "; ".join(decision.reasons))
        return {
            "committed": False,
            "room_ready": False,
            "reason": "; ".join(decision.reasons),
            "authority": decision.as_dict(),
        }

    route = state.get("route", {})
    mission_id = str(state.get("mission_id", ""))
    eta = str(state.get("eta", "this afternoon"))
    key = handoff_idempotency_key(
        session_id=session_id, mission_id=mission_id, route=route, eta=eta,
    )
    requested_expiry = proposed_expiry()
    requested_token = signed_handoff_token(
        mission_id=mission_id,
        route=route,
        flight_plan=state.get("flight_plan") if isinstance(state.get("flight_plan"), dict) else None,
        eta=eta,
        expires_at=requested_expiry,
    )
    receipt = await asyncio.to_thread(
        db.claim_handoff_room,
        key,
        session_id,
        mission_id,
        datetime.fromtimestamp(requested_expiry, tz=timezone.utc),
        token_sha256(requested_token),
    )
    if receipt is None:
        raise RuntimeError("follower room claim could not be read after commit")

    expires_at = _epoch_seconds(receipt["handoff_expires_at"])
    token = signed_handoff_token(
        mission_id=mission_id,
        route=route,
        flight_plan=state.get("flight_plan") if isinstance(state.get("flight_plan"), dict) else None,
        eta=eta,
        expires_at=expires_at,
    )
    if token_sha256(token) != receipt["handoff_token_sha256"]:
        raise RuntimeError("persisted follower-room capability does not match mission state")

    duplicate = bool(receipt.get("duplicate_suppressed"))
    handoff = {
        "room_id": mission_id,
        "token": token,
        "expires_at": expires_at,
        "duplicate_suppressed": duplicate,
    }
    result = {
        "committed": True,
        "room_ready": True,
        "receipt_id": key,
        "channel": "firestore",
        "provider_reference": mission_id,
        "provider_status": "room_ready",
        "status": "room_ready",
        "duplicate_suppressed": duplicate,
        "at_most_once": True,
        "handoff": handoff,
    }
    state["dispatch_receipt"] = {
        "idempotency_key": key,
        "channel": "firestore",
        "provider_reference": mission_id,
        "provider_status": "room_ready",
        "status": "room_ready",
    }
    state["handoff"] = handoff
    emit_panel("handoff", result)
    emit_step(
        "FollowingAgent",
        "duplicate-suppressed" if duplicate else "room-ready",
        "The original follower-room invitation was returned."
        if duplicate else "One signed follower-room invitation is ready.",
    )
    return result


async def open_follower_room(tool_context: ToolContext) -> dict[str, Any]:
    """Return one signed, one-hour room only after deterministic pilot authority."""
    return await open_follower_room_from_state(tool_context.session.id, tool_context.state)
