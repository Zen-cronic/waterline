"""Private Waterline mission API with durable deterministic authority.

The public browser reaches this service only through the signed Next.js relay.
Agents can populate a briefing, but state changes and dispatch are controlled by
an owner-bound, append-only mission lifecycle in Cloud SQL.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Annotated, Any, AsyncGenerator, Literal
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
from .verification import assess_briefing_readiness, assess_dispatch_readiness


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


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_resume: Literal[True]


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


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


def _authenticated_identity(method: str, path: str, body: bytes,
                            relay: RelayEnvelope) -> PilotIdentity:
    try:
        return verify_relay_request(
            secret=configured_relay_secret(), method=method, path=path, body=body,
            actor=relay.actor, timestamp=relay.timestamp, signature=relay.signature,
        )
    except RelayAuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _mission_event(mission: dict[str, Any], status: str,
                   event: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "type": "mission",
        "mission_id": mission["mission_id"],
        "owner_ref": mission["owner_ref"],
        "trace_id": mission["trace_id"],
        "status": status,
    }
    if event:
        payload["event"] = event
    return payload


def _prompt(request: dict[str, Any], recovery: bool = False) -> str:
    prefix = "Recover and re-run the interrupted briefing. " if recovery else ""
    return (
        f"{prefix}Brief my flight from {request['departure']} to "
        f"{request['destination']} at {request['cruise_alt_ft']} feet this afternoon."
    )


async def _briefing_stream(*, mission: dict[str, Any], identity: PilotIdentity,
                           sessions: BaseSessionService,
                           initial_event: dict[str, Any] | None,
                           resume: bool) -> AsyncGenerator[str, None]:
    """Run or recover one proposal, then deterministically hold it for a pilot."""
    yield ":ok\n\n"
    yield _sse(_mission_event(mission, "proposed", initial_event))
    had_error = False
    error_details: list[str] = []
    try:
        async for event in run_briefing(
            _prompt(mission["request"], recovery=resume),
            session_id=mission["session_id"],
            user_id=identity.user_id,
            session_service=sessions,
            initial_state={
                "mission_id": mission["mission_id"],
                "mission_owner_ref": identity.owner_ref,
                "pilot_attestation": None,
            },
            resume=resume,
        ):
            if event.get("type") == "error":
                had_error = True
                error_details.append(str(event.get("detail", "briefing execution failed")))
            if event.get("type") != "done":
                yield _sse(event)
    except Exception as exc:
        had_error = True
        error_details.append(f"{type(exc).__name__}: {exc}")
        yield _sse({"type": "error", "detail": error_details[-1]})

    session = await sessions.get_session(
        app_name=APP_NAME, user_id=identity.user_id, session_id=mission["session_id"],
    )
    readiness = assess_briefing_readiness(session.state if session else {})
    reasons = list(readiness.reasons)
    if had_error:
        reasons.extend(error_details)

    if had_error or not readiness.approved:
        rejected = db.transition_mission(
            mission["mission_id"], identity.owner_ref, "proposed", "rejected",
            "proposal_rejected", "briefing_execution_failed",
            {"reasons": reasons or ["briefing execution failed"], "recoverable": True},
        )
        if rejected:
            yield _sse(_mission_event(mission, "rejected", rejected))
        yield _sse({
            "type": "recovery", "available": rejected is not None,
            "reasons": reasons or ["briefing execution failed"],
        })
        yield _sse({"type": "done"})
        return

    rejected = db.transition_mission(
        mission["mission_id"], identity.owner_ref, "proposed", "rejected",
        "proposal_rejected", "pilot_attestation_missing",
        {
            "briefing_ready": True,
            "reasons": ["agents have zero dispatch authority"],
            "dispatch_authority": False,
        },
    )
    if not rejected:
        yield _sse({"type": "error", "detail": "mission state changed during briefing"})
        yield _sse({"type": "done"})
        return
    yield _sse(_mission_event(mission, "rejected", rejected))

    waiting = db.transition_mission(
        mission["mission_id"], identity.owner_ref, "rejected", "awaiting_attestation",
        "pilot_review_requested", "owner_attestation_required",
        {"briefing_ready": True, "dispatch_authority": False},
    )
    if waiting:
        yield _sse(_mission_event(mission, "awaiting_attestation", waiting))
    else:
        yield _sse({"type": "error", "detail": "mission could not enter pilot review"})
    yield _sse({"type": "done"})


def create_app(session_service: BaseSessionService | None = None) -> FastAPI:
    app = FastAPI(title="Waterline Mission API", version="0.3.0")
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
        request_data = req.model_dump()
        trace_id = f"trace-{uuid4().hex[:20]}"
        mission: dict[str, Any] | None = None
        proposed: dict[str, Any] | None = None
        for _ in range(4):
            mission_id = f"mission-{uuid4().hex[:20]}"
            session_id = f"session-{uuid4().hex}"
            proposed = db.create_mission(
                mission_id, identity.owner_ref, session_id, trace_id, request_data,
            )
            if proposed:
                mission = {
                    "mission_id": mission_id, "owner_ref": identity.owner_ref,
                    "session_id": session_id, "trace_id": trace_id,
                    "request": request_data, "status": "proposed",
                }
                break
        if mission is None:  # pragma: no cover - UUID collision/DB anomaly
            raise HTTPException(status_code=503, detail="could not allocate mission identity")

        return StreamingResponse(
            _briefing_stream(
                mission=mission, identity=identity, sessions=sessions,
                initial_event=proposed, resume=False,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/v1/missions/{mission_id}")
    async def get_mission(mission_id: str, relay: Relay) -> dict[str, Any]:
        if not _MISSION_ID.fullmatch(mission_id):
            raise HTTPException(status_code=404, detail="mission not found")
        identity = _authenticated_identity(
            "GET", f"/v1/missions/{mission_id}", b"", relay,
        )
        timeline = db.mission_timeline(mission_id, identity.owner_ref)
        if timeline is None:
            raise HTTPException(status_code=404, detail="mission not found")
        return timeline

    @app.post("/v1/missions/{mission_id}/resume")
    async def resume_mission(mission_id: str, req: ResumeRequest,
                             relay: Relay) -> StreamingResponse:
        if not _MISSION_ID.fullmatch(mission_id):
            raise HTTPException(status_code=404, detail="mission not found")
        path = f"/v1/missions/{mission_id}/resume"
        identity = _authenticated_identity("POST", path, req.model_dump_json().encode(), relay)
        mission = db.owned_mission(mission_id, identity.owner_ref)
        if mission is None:
            raise HTTPException(status_code=404, detail="mission not found")
        if mission["status"] != "rejected":
            raise HTTPException(status_code=409, detail="mission is not recoverable")
        recovery = db.transition_mission(
            mission_id, identity.owner_ref, "rejected", "proposed",
            "recovery_started", "owner_requested_resume",
            {"same_mission": True, "same_session": True},
        )
        if recovery is None:
            raise HTTPException(status_code=409, detail="mission recovery already claimed")
        mission = {**mission, "status": "proposed"}
        return StreamingResponse(
            _briefing_stream(
                mission=mission, identity=identity, sessions=sessions,
                initial_event=recovery, resume=True,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/v1/missions/{mission_id}/attest")
    async def attest_and_resume(mission_id: str, req: AttestationRequest,
                                relay: Relay) -> dict[str, Any]:
        if not _MISSION_ID.fullmatch(mission_id):
            raise HTTPException(status_code=404, detail="mission not found")
        path = f"/v1/missions/{mission_id}/attest"
        identity = _authenticated_identity("POST", path, req.model_dump_json().encode(), relay)
        mission = db.owned_mission(mission_id, identity.owner_ref)
        if mission is None:
            raise HTTPException(status_code=404, detail="mission not found")
        if mission["status"] not in {"awaiting_attestation", "accepted"}:
            raise HTTPException(status_code=409, detail="mission is not awaiting attestation")

        session = await sessions.get_session(
            app_name=APP_NAME, user_id=identity.user_id, session_id=mission["session_id"],
        )
        if session is None:
            raise HTTPException(status_code=409, detail="mission session is unavailable")
        readiness = assess_briefing_readiness(session.state)
        if not readiness.approved:
            raise HTTPException(
                status_code=409,
                detail={"message": "briefing is not ready to attest", "reasons": readiness.reasons},
            )

        corrected: dict[str, Any] | None = None
        accepted: dict[str, Any] | None = None
        if mission["status"] == "awaiting_attestation":
            attestation_id = f"attestation-{uuid4().hex[:20]}"
            corrected = db.claim_pilot_attestation(
                attestation_id, mission_id, identity.owner_ref,
                req.responsible_email, req.eta, req.grace_min,
            )
            if corrected is None:
                raise HTTPException(status_code=409, detail="attestation already claimed")
        else:
            prior = db.matching_pilot_attestation(
                mission_id, identity.owner_ref, req.responsible_email, req.eta, req.grace_min,
            )
            if prior is None:
                raise HTTPException(status_code=409, detail="recovery attestation does not match")
            attestation_id = prior["attestation_id"]
            db.record_mission_event(
                mission_id, identity.owner_ref,
                "dispatch_recovery_started", "owner_reconfirmed_attestation",
                {"attestation_id": attestation_id, "dispatch_authority": False},
            )

        state = dict(session.state)
        state.update({
            "responsible_email": req.responsible_email, "eta": req.eta,
            "grace_min": req.grace_min,
            "pilot_attestation": {
                "attestation_id": attestation_id, "mission_id": mission_id,
                "actor_ref": identity.owner_ref, "confirmed": True,
                "attested_at": datetime.now(timezone.utc).isoformat(),
            },
        })
        authority = assess_dispatch_readiness(state)
        if not authority.approved:
            db.record_mission_event(
                mission_id, identity.owner_ref, "acceptance_failed", "authority_gate_failed",
                {"reasons": list(authority.reasons), "dispatch_authority": False},
            )
            raise HTTPException(status_code=409, detail="corrected mission failed authority gate")

        if mission["status"] == "awaiting_attestation":
            accepted = db.transition_mission(
                mission_id, identity.owner_ref, "corrected", "accepted",
                "proposal_accepted", "deterministic_gate_passed",
                {"attestation_id": attestation_id, "dispatch_authority": True},
            )
            if accepted is None:
                raise HTTPException(status_code=409, detail="mission acceptance already claimed")

        result = await dispatch_from_state(mission["session_id"], state)
        if result.get("sent") is not True:
            db.record_mission_event(
                mission_id, identity.owner_ref, "dispatch_failed", "notice_not_completed",
                {
                    "duplicate_suppressed": bool(result.get("duplicate_suppressed")),
                    "dispatch_authority": False,
                },
            )
            raise HTTPException(status_code=409, detail="dispatch was not completed")

        receipt_id = state.get("dispatch_receipt", {}).get("idempotency_key")
        dispatched = db.transition_mission(
            mission_id, identity.owner_ref, "accepted", "dispatched",
            "dispatch_completed", "verified_notice_receipt",
            {
                "attestation_id": attestation_id, "receipt_id": receipt_id,
                "channel": result.get("channel"), "dispatch_authority": False,
            },
        )
        if dispatched is None:
            raise HTTPException(status_code=409, detail="dispatch state could not be committed")

        return {
            "mission_id": mission_id, "owner_ref": identity.owner_ref,
            "trace_id": mission["trace_id"], "status": "dispatched",
            "attestation_id": attestation_id, "receipt_id": receipt_id,
            "events": [event for event in (corrected, accepted, dispatched) if event],
            "dispatch": {"sent": True, "channel": result.get("channel")},
        }

    return app


app = create_app()
