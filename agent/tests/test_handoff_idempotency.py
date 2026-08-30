import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from waterline.handoff import (
    handoff_idempotency_key,
    proposed_expiry,
    signed_handoff_token,
    token_sha256,
)
from waterline.auth import PilotIdentity
from waterline import service
from waterline.tools import following_tools


SECRET = "test-handoff-secret-with-at-least-thirty-two-bytes"


def _run_threads_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_inline(function, *args):
        return function(*args)

    monkeypatch.setattr(following_tools.asyncio, "to_thread", run_inline)


def _context() -> SimpleNamespace:
    return SimpleNamespace(
        session=SimpleNamespace(id="briefing-resume-42"),
        state={
            "dispatch_authorized": True,
            "route": {"dep_id": "CYYZ", "dst_name": "Lady Evelyn Lake"},
            "flight_plan": {"corrected_plan": {"landing_sector": "west"}},
            "eta": "16:00Z",
            "grace_min": 60,
            "mission_id": "mission-0123456789abcdefabcd",
            "mission_owner_ref": "pilot-owner-ref",
            "pilot_attestation": {
                "confirmed": True,
                "mission_id": "mission-0123456789abcdefabcd",
                "actor_ref": "pilot-owner-ref",
            },
            "verification": "APPROVED — sources and inference labels agree.",
            "weather": {
                "available": True,
                "reach_nm": 27.8,
                "sources": [{"station_id": "CYXR", "metar_raw": "CYXR ..."}],
            },
            "corridor": {"kept": 2, "total": 20, "hazards": []},
            "briefing": "INFERRED from CYXR, 27.8 NM away. PILOT REVIEW REQUIRED.",
        },
    )


def test_identical_room_replay_returns_same_token_and_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _run_threads_inline(monkeypatch)
    monkeypatch.setenv("WATERLINE_HANDOFF_SECRET", SECRET)
    receipt: dict | None = None

    def claim(key, session_id, mission_id, expires_at, capability_sha256):
        nonlocal receipt
        if receipt is None:
            receipt = {
                "idempotency_key": key,
                "session_id": session_id,
                "provider_reference": mission_id,
                "handoff_expires_at": expires_at,
                "handoff_token_sha256": capability_sha256,
                "duplicate_suppressed": False,
            }
            return receipt
        return {**receipt, "duplicate_suppressed": True}

    monkeypatch.setattr(following_tools.db, "claim_handoff_room", claim)
    ctx = _context()
    first = asyncio.run(following_tools.open_follower_room(ctx))
    replay = asyncio.run(following_tools.open_follower_room(ctx))

    assert first["room_ready"] is True
    assert replay["duplicate_suppressed"] is True
    assert replay["handoff"]["token"] == first["handoff"]["token"]
    assert replay["handoff"]["expires_at"] == first["handoff"]["expires_at"]
    assert replay["provider_reference"] == ctx.state["mission_id"]


def test_client_authorized_flag_cannot_bypass_attestation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _run_threads_inline(monkeypatch)
    monkeypatch.setenv("WATERLINE_HANDOFF_SECRET", SECRET)
    claims: list[str] = []
    ctx = _context()
    ctx.state["pilot_attestation"] = None
    monkeypatch.setattr(
        following_tools.db,
        "claim_handoff_room",
        lambda key, *_args: claims.append(key),
    )

    result = asyncio.run(following_tools.open_follower_room(ctx))

    assert result["room_ready"] is False
    assert "pilot attestation" in result["reason"]
    assert claims == []


def test_token_is_deterministic_and_hash_only_is_persistable() -> None:
    route = {"dep_id": "CYYZ", "dst_name": "Lady Evelyn Lake"}
    expiry = proposed_expiry(datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc))
    kwargs = {
        "mission_id": "mission-0123456789abcdefabcd",
        "route": route,
        "flight_plan": {"corrected_plan": {"landing_sector": "west"}},
        "eta": "16:00Z",
        "expires_at": expiry,
        "secret": SECRET,
    }
    first = signed_handoff_token(**kwargs)
    second = signed_handoff_token(**kwargs)

    assert first == second
    assert len(token_sha256(first)) == 64
    assert first not in token_sha256(first)
    assert handoff_idempotency_key(
        session_id="session-1", mission_id=kwargs["mission_id"], route=route, eta="16:00Z",
    ) == handoff_idempotency_key(
        session_id="session-1", mission_id=kwargs["mission_id"], route=route, eta="16:00Z",
    )


def test_persisted_token_mismatch_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _run_threads_inline(monkeypatch)
    monkeypatch.setenv("WATERLINE_HANDOFF_SECRET", SECRET)
    monkeypatch.setattr(
        following_tools.db,
        "claim_handoff_room",
        lambda *_args: {
            "handoff_expires_at": datetime.now(timezone.utc),
            "handoff_token_sha256": "0" * 64,
            "duplicate_suppressed": True,
        },
    )

    with pytest.raises(RuntimeError, match="does not match mission state"):
        asyncio.run(following_tools.open_follower_room(_context()))


def test_owner_restore_reconstructs_identical_invitation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WATERLINE_HANDOFF_SECRET", SECRET)
    mission_id = "mission-0123456789abcdefabcd"
    expiry = datetime(2026, 8, 30, 13, 0, tzinfo=timezone.utc)
    route = {"dep_id": "CYYZ", "dst_name": "Lady Evelyn Lake"}
    flight_plan = {"corrected_plan": {"landing_sector": "west"}}
    expected = signed_handoff_token(
        mission_id=mission_id,
        route=route,
        flight_plan=flight_plan,
        eta="16:00Z",
        expires_at=int(expiry.timestamp()),
        secret=SECRET,
    )
    monkeypatch.setattr(
        service.db,
        "handoff_receipt_for_mission",
        lambda _mission_id, _owner_ref: {
            "handoff_expires_at": expiry,
            "handoff_token_sha256": token_sha256(expected),
            "eta": "16:00Z",
        },
    )

    class Sessions:
        async def get_session(self, **_kwargs):
            return SimpleNamespace(state={"route": route, "flight_plan": flight_plan})

    identity = PilotIdentity(actor="pilot:test", owner_ref="pilot-owner-ref", user_id="pilot-owner-ref")
    mission = {
        "mission_id": mission_id,
        "session_id": "session-1",
        "request": {"departure": "CYYZ", "destination": "Lady Evelyn Lake"},
    }
    first = asyncio.run(service._restored_handoff(mission, identity, Sessions()))
    second = asyncio.run(service._restored_handoff(mission, identity, Sessions()))

    assert first == second
    assert first == {
        "room_id": mission_id,
        "token": expected,
        "expires_at": int(expiry.timestamp()),
        "duplicate_suppressed": False,
    }
