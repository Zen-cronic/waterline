"""Private Waterline mission API with an authenticated web-service relay.

The browser never calls this service directly. A narrow Next.js relay injects
and signs the authenticated pilot actor, while Cloud Run IAM authenticates the
web service itself. Mission, ADK user, and session identifiers are generated
here and remain bound to the derived owner reference.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from google.adk.sessions import BaseSessionService
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import db
from .auth import (
    PilotIdentity,
    RelayAuthenticationError,
    configured_relay_secret,
    verify_relay_request,
)
from .config import APP_NAME
from .run import make_session_service, run_briefing
from .tools.dispatch_tools import dispatch_from_state
from .verification import assess_briefing_readiness


_MISSION_ID = re.compile(r"^mission-[0-9a-f]{20}$")


class MissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    departure: str = Field(min_length=3, max_length=8, pattern=r"^[A-Za-z0-9]+$")
    destination: str = Field(min_length=3, max_length=120)
    cruise_alt_ft: int = Field(ge=500, le=12_500)

    @field_validator("departure")
    @classmethod
    def normalize_departure(cls, value: str) -> str:
        return value.upper()


class AttestationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_dispatch: Literal[True]
    responsible_email: str = Field(min_length=5, max_length=254)
    eta: str = Field(min_length=2, max_length=80)
    grace_min: int = Field(ge=15, le=240)

    @field_validator("responsible_email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized.count("@") != 1 or normalized.startswith("@") or normalized.endswith("@"):
            raise ValueError("responsible_email must be a valid address")
        return normalized


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event)}\n\n"


class RelayEnvelope(BaseModel):
    actor: str | None
    timestamp: str | None
    signature: str | None


async def _relay_envelope(
    actor: Annotated[str | None, Header(alias="x-waterline-actor")] = None,
    timestamp: Annotated[str | None, Header(alias="x-waterline-timestamp")] = None,
    signature: Annotated[str | None, Header(alias="x-waterline-signature")] = None,
) -> RelayEnvelope:
    return RelayEnvelope(actor=actor, timestamp=timestamp, signature=signature)


Relay = Annotated[RelayEnvelope, Depends(_relay_envelope)]


def _authenticated_identity(method: str, path: str, body: bytes,
                            relay: RelayEnvelope) -> PilotIdentity:
    try:
        return verify_relay_request(
            secret=configured_relay_secret(),
            method=method,
            path=path,
            body=body,
            actor=relay.actor,
            timestamp=relay.timestamp,
            signature=relay.signature,
        )
    except RelayAuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def create_app(session_service: BaseSessionService | None = None) -> FastAPI:
    app = FastAPI(title="Waterline Mission API", version="0.2.0")
    sessions = session_service or make_session_service()
    app.state.sessions = sessions

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "waterline", "boundary": "private-relay"}

    @app.post("/v1/missions")
    async def create_mission(req: MissionRequest, relay: Relay) -> StreamingResponse:
        identity = _authenticated_identity(
            "POST", "/v1/missions", req.model_dump_json().encode(), relay,
        )
        mission_id = ""
        session_id = ""
        for _ in range(4):
            mission_id = f"mission-{uuid4().hex[:20]}"
            session_id = f"session-{uuid4().hex}"
            if db.create_mission(mission_id, identity.owner_ref, session_id):
                break
        else:  # pragma: no cover - UUID collision/DB anomaly
            raise HTTPException(status_code=503, detail="could not allocate mission identity")

        prompt = (
            f"Brief my flight from {req.departure} to {req.destination} at "
            f"{req.cruise_alt_ft} feet this afternoon."
        )
        initial_state = {
            "mission_id": mission_id,
            "mission_owner_ref": identity.owner_ref,
            "pilot_attestation": None,
        }

        async def stream():
            yield ":ok\n\n"
            yield _sse({
                "type": "mission",
                "mission_id": mission_id,
                "owner_ref": identity.owner_ref,
                "status": "briefing",
            })
            had_error = False
            async for event in run_briefing(
                prompt,
                session_id=session_id,
                user_id=identity.user_id,
                session_service=sessions,
                initial_state=initial_state,
            ):
                if event.get("type") == "error":
                    had_error = True
                if event.get("type") != "done":
                    yield _sse(event)

            session = await sessions.get_session(
                app_name=APP_NAME, user_id=identity.user_id, session_id=session_id,
            )
            readiness = assess_briefing_readiness(session.state if session else {})
            if not had_error and readiness.approved and db.mark_mission_awaiting_attestation(
                mission_id, identity.owner_ref,
            ):
                yield _sse({
                    "type": "mission",
                    "mission_id": mission_id,
                    "owner_ref": identity.owner_ref,
                    "status": "awaiting_attestation",
                })
            else:
                yield _sse({
                    "type": "authority",
                    "approved": False,
                    "reasons": list(readiness.reasons) or ["briefing execution failed"],
                })
            yield _sse({"type": "done"})

        return StreamingResponse(
            stream(), media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-store",
                "X-Accel-Buffering": "no",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/v1/missions/{mission_id}/attest")
    async def attest_and_resume(mission_id: str, req: AttestationRequest,
                                relay: Relay) -> dict[str, Any]:
        if not _MISSION_ID.fullmatch(mission_id):
            raise HTTPException(status_code=404, detail="mission not found")
        identity = _authenticated_identity(
            "POST", f"/v1/missions/{mission_id}/attest", req.model_dump_json().encode(), relay,
        )
        mission = db.owned_mission(mission_id, identity.owner_ref)
        if mission is None:
            raise HTTPException(status_code=404, detail="mission not found")
        if mission["status"] != "awaiting_attestation":
            raise HTTPException(status_code=409, detail="mission is not awaiting attestation")

        session = await sessions.get_session(
            app_name=APP_NAME,
            user_id=identity.user_id,
            session_id=mission["session_id"],
        )
        if session is None:
            raise HTTPException(status_code=409, detail="mission session is unavailable")
        readiness = assess_briefing_readiness(session.state)
        if not readiness.approved:
            raise HTTPException(
                status_code=409,
                detail={"message": "briefing is not ready to attest", "reasons": readiness.reasons},
            )

        attestation_id = f"attestation-{uuid4().hex[:20]}"
        if not db.claim_pilot_attestation(
            attestation_id,
            mission_id,
            identity.owner_ref,
            req.responsible_email,
            req.eta,
            req.grace_min,
        ):
            raise HTTPException(status_code=409, detail="attestation already claimed")

        state = dict(session.state)
        state.update({
            "responsible_email": req.responsible_email,
            "eta": req.eta,
            "grace_min": req.grace_min,
            "pilot_attestation": {
                "attestation_id": attestation_id,
                "mission_id": mission_id,
                "actor_ref": identity.owner_ref,
                "confirmed": True,
                "attested_at": datetime.now(timezone.utc).isoformat(),
            },
        })
        result = await dispatch_from_state(mission["session_id"], state)
        if result.get("sent") is not True:
            raise HTTPException(status_code=409, detail="dispatch was not completed")
        if not db.mark_mission_dispatched(mission_id, identity.owner_ref):
            raise HTTPException(status_code=409, detail="dispatch state could not be committed")

        return {
            "mission_id": mission_id,
            "owner_ref": identity.owner_ref,
            "status": "dispatched",
            "attestation_id": attestation_id,
            "dispatch": {"sent": True, "channel": result.get("channel")},
        }

    return app


app = create_app()
