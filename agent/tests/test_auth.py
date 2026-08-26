import asyncio
import json
import time

from google.adk.sessions import InMemorySessionService
import httpx

from waterline import db, service
from waterline.auth import RelayAuthenticationError, sign_relay_request, verify_relay_request


SECRET = "test-waterline-relay-secret-with-more-than-32-bytes"


class _FakeConnection:
    def __init__(self, rows: list[dict | None]) -> None:
        self.rows = rows
        self.executions: list[tuple[str, tuple]] = []
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def cursor(self):
        return self

    def execute(self, sql, params) -> None:
        self.executions.append((sql, params))

    def fetchone(self):
        return self.rows.pop(0)

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


def _approved_state(initial: dict) -> dict:
    return {
        **initial,
        "verification": "APPROVED — sources and inference labels agree.",
        "briefing": (
            "HAZARDS\nNOTAM 0 is on route.\n"
            "WEATHER\nINFERRED from CYXR, 27.8 NM away.\n"
            "NOT FOR OPERATIONAL USE."
        ),
        "weather": {
            "available": True,
            "reach_nm": 27.8,
            "sources": [{"station_id": "CYXR", "metar_raw": "CYXR ..."}],
        },
        "corridor": {"hazards": [{"idx": 0}]},
    }


def _body(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode()


def _headers(path: str, body: bytes, actor: str = "pilot:owner") -> dict[str, str]:
    timestamp = str(int(time.time()))
    return {
        "content-type": "application/json",
        "x-waterline-actor": actor,
        "x-waterline-timestamp": timestamp,
        "x-waterline-signature": sign_relay_request(
            SECRET, "POST", path, body, actor, timestamp,
        ),
    }


def test_relay_signature_binds_actor_path_body_and_freshness() -> None:
    body = b'{"departure":"CYYZ"}'
    timestamp = "1787774400"
    signature = sign_relay_request(
        SECRET, "POST", "/v1/missions", body, "pilot:owner", timestamp,
    )
    identity = verify_relay_request(
        secret=SECRET, method="POST", path="/v1/missions", body=body,
        actor="pilot:owner", timestamp=timestamp, signature=signature,
        now=1787774400,
    )
    assert identity.user_id == identity.owner_ref
    assert identity.actor not in identity.owner_ref

    for changed in (
        {"path": "/v1/other"},
        {"body": body + b" "},
        {"actor": "pilot:attacker"},
        {"now": 1787774461},
    ):
        values = {
            "secret": SECRET, "method": "POST", "path": "/v1/missions", "body": body,
            "actor": "pilot:owner", "timestamp": timestamp, "signature": signature,
            "now": 1787774400,
        }
        values.update(changed)
        try:
            verify_relay_request(**values)
        except RelayAuthenticationError:
            pass
        else:  # pragma: no cover
            raise AssertionError(f"tampered relay request was accepted: {changed}")


def test_attestation_claim_is_atomic_and_never_persists_contact_pii(monkeypatch) -> None:
    connection = _FakeConnection([{"mission_id": "mission-1"}])
    monkeypatch.setattr(db, "connect", lambda: connection)

    assert db.claim_pilot_attestation(
        "attestation-1", "mission-1", "pilot-owner", "OPS@Example.com", "16:00Z", 60,
    ) is True
    assert connection.committed is True
    assert len(connection.executions) == 2
    insert_params = connection.executions[1][1]
    assert "OPS@Example.com" not in insert_params
    assert len(insert_params[3]) == 64

    replay = _FakeConnection([None])
    monkeypatch.setattr(db, "connect", lambda: replay)
    assert db.claim_pilot_attestation(
        "attestation-2", "mission-1", "pilot-owner", "ops@example.com", "16:00Z", 60,
    ) is False
    assert replay.rolled_back is True
    assert len(replay.executions) == 1


def test_api_owns_mission_session_and_user_identity(monkeypatch) -> None:
    monkeypatch.setenv("WATERLINE_RELAY_SECRET", SECRET)
    sessions = InMemorySessionService()
    missions: dict[str, dict] = {}

    def create_mission(mission_id, owner_ref, session_id):
        missions[mission_id] = {
            "mission_id": mission_id, "owner_ref": owner_ref,
            "session_id": session_id, "status": "briefing",
        }
        return True

    async def fake_run(_prompt, *, session_id, user_id, session_service, initial_state):
        assert session_id.startswith("session-")
        assert user_id == initial_state["mission_owner_ref"]
        await session_service.create_session(
            app_name="waterline", user_id=user_id, session_id=session_id,
            state=_approved_state(initial_state),
        )
        yield {"type": "done"}

    def mark_waiting(mission_id, owner_ref):
        assert missions[mission_id]["owner_ref"] == owner_ref
        missions[mission_id]["status"] = "awaiting_attestation"
        return True

    monkeypatch.setattr(service.db, "create_mission", create_mission)
    monkeypatch.setattr(service.db, "mark_mission_awaiting_attestation", mark_waiting)
    monkeypatch.setattr(service, "run_briefing", fake_run)
    async def exercise() -> None:
        transport = httpx.ASGITransport(app=service.create_app(sessions))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {"departure": "CYYZ", "destination": "Lady Evelyn Lake", "cruise_alt_ft": 3500}
            body = _body(payload)
            response = await client.post(
                "/v1/missions", content=body, headers=_headers("/v1/missions", body),
            )
            assert response.status_code == 200
            assert "awaiting_attestation" in response.text
            mission_id = next(iter(missions))
            assert mission_id.startswith("mission-")
            assert missions[mission_id]["session_id"].startswith("session-")

            forged = {
                **payload, "session_id": "browser-choice", "user_id": "pilot",
                "responsible_email": "x@y.test",
            }
            forged_body = _body(forged)
            rejected = await client.post(
                "/v1/missions", content=forged_body,
                headers=_headers("/v1/missions", forged_body),
            )
            assert rejected.status_code == 422
            assert len(missions) == 1

            unauthenticated = await client.post(
                "/v1/missions", content=body, headers={"content-type": "application/json"},
            )
            assert unauthenticated.status_code == 401
            preflight = await client.options(
                "/v1/missions",
                headers={
                    "origin": "https://attacker.example",
                    "access-control-request-method": "POST",
                },
            )
            assert "access-control-allow-origin" not in preflight.headers

    asyncio.run(exercise())


def test_attestation_is_owner_bound_and_replay_safe(monkeypatch) -> None:
    monkeypatch.setenv("WATERLINE_RELAY_SECRET", SECRET)
    sessions = InMemorySessionService()
    owner = verify_relay_request(
        secret=SECRET, method="POST", path="/v1/missions", body=b"{}",
        actor="pilot:owner", timestamp="1787774400",
        signature=sign_relay_request(SECRET, "POST", "/v1/missions", b"{}", "pilot:owner", "1787774400"),
        now=1787774400,
    )
    mission_id = "mission-0123456789abcdefabcd"
    session_id = "session-server-owned"
    mission = {
        "mission_id": mission_id, "owner_ref": owner.owner_ref,
        "session_id": session_id, "status": "awaiting_attestation",
    }
    claimed = False
    dispatches: list[dict] = []

    def owned_mission(requested_id, owner_ref):
        return mission.copy() if requested_id == mission_id and owner_ref == owner.owner_ref else None

    def claim(*_args):
        nonlocal claimed
        if claimed:
            return False
        claimed = True
        mission["status"] = "attestation_claimed"
        return True

    async def dispatch(_session_id, state):
        dispatches.append(state)
        return {"sent": True, "channel": "test"}

    def mark_dispatched(_mission_id, _owner_ref):
        mission["status"] = "dispatched"
        return True

    monkeypatch.setattr(service.db, "owned_mission", owned_mission)
    monkeypatch.setattr(service.db, "claim_pilot_attestation", claim)
    monkeypatch.setattr(service.db, "mark_mission_dispatched", mark_dispatched)
    monkeypatch.setattr(service, "dispatch_from_state", dispatch)
    path = f"/v1/missions/{mission_id}/attest"
    payload = {
        "confirm_dispatch": True, "responsible_email": "ops@example.com",
        "eta": "16:00Z", "grace_min": 60,
    }
    body = _body(payload)

    async def exercise() -> None:
        await sessions.create_session(
            app_name="waterline", user_id=owner.user_id, session_id=session_id,
            state=_approved_state({"mission_id": mission_id, "mission_owner_ref": owner.owner_ref}),
        )
        transport = httpx.ASGITransport(app=service.create_app(sessions))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            attacker = await client.post(
                path, content=body, headers=_headers(path, body, "pilot:attacker"),
            )
            assert attacker.status_code == 404
            assert not claimed and not dispatches

            accepted = await client.post(path, content=body, headers=_headers(path, body))
            assert accepted.status_code == 200
            assert accepted.json()["status"] == "dispatched"
            assert dispatches[0]["pilot_attestation"]["actor_ref"] == owner.owner_ref

            replay = await client.post(path, content=body, headers=_headers(path, body))
            assert replay.status_code == 409
            assert len(dispatches) == 1

    asyncio.run(exercise())
