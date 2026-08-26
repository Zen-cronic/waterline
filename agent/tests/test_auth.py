import asyncio
from datetime import datetime, timezone
import json
import time
from typing import Any

from google.adk.sessions import InMemorySessionService
from google.adk.events import Event, EventActions
import httpx
import pytest

from waterline import condition_card, db, service
from waterline.auth import RelayAuthenticationError, sign_relay_request, verify_relay_request
from waterline.state_machine import require_transition


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


class _MissionStore:
    def __init__(self) -> None:
        self.missions: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.attestations: dict[str, dict[str, Any]] = {}
        self._counter = 0

    def _event(self, mission: dict[str, Any], current: str | None, target: str,
               event_type: str, reason_code: str,
               evidence: dict[str, Any] | None = None) -> dict[str, Any]:
        self._counter += 1
        event = {
            "event_id": f"event-test-{self._counter:04d}",
            "mission_id": mission["mission_id"],
            "from_status": current,
            "to_status": target,
            "event_type": event_type,
            "reason_code": reason_code,
            "evidence": evidence or {},
            "trace_id": mission["trace_id"],
        }
        self.events.append(event)
        return event

    def create_mission(self, mission_id, owner_ref, session_id, trace_id, request):
        if mission_id in self.missions:
            return None
        mission = {
            "mission_id": mission_id, "owner_ref": owner_ref, "session_id": session_id,
            "trace_id": trace_id, "request": request, "status": "proposed",
        }
        self.missions[mission_id] = mission
        return self._event(
            mission, None, "proposed", "mission_proposed", "authenticated_intake",
            {"request": request, "dispatch_authority": False},
        )

    def owned_mission(self, mission_id, owner_ref):
        mission = self.missions.get(mission_id)
        return mission.copy() if mission and mission["owner_ref"] == owner_ref else None

    def transition_mission(self, mission_id, owner_ref, current, target,
                           event_type, reason_code, evidence=None):
        require_transition(current, target)
        mission = self.missions.get(mission_id)
        if not mission or mission["owner_ref"] != owner_ref or mission["status"] != current:
            return None
        mission["status"] = target
        return self._event(mission, current, target, event_type, reason_code, evidence)

    def record_mission_event(self, mission_id, owner_ref, event_type, reason_code, evidence=None):
        mission = self.missions.get(mission_id)
        if not mission or mission["owner_ref"] != owner_ref:
            return None
        return self._event(
            mission, mission["status"], mission["status"], event_type, reason_code, evidence,
        )

    def claim_pilot_attestation(self, attestation_id, mission_id, owner_ref,
                                email, eta, grace_min):
        event = self.transition_mission(
            mission_id, owner_ref, "awaiting_attestation", "corrected",
            "pilot_attestation_recorded", "owner_attested",
            {"attestation_id": attestation_id, "eta": eta, "grace_min": grace_min},
        )
        if event:
            self.attestations[mission_id] = {
                "attestation_id": attestation_id, "actor_ref": owner_ref,
                "email": email.lower(), "eta": eta, "grace_min": grace_min,
            }
        return event

    def matching_pilot_attestation(self, mission_id, owner_ref, email, eta, grace_min):
        attestation = self.attestations.get(mission_id)
        mission = self.missions.get(mission_id)
        if not attestation or not mission or mission["status"] != "accepted":
            return None
        if (
            attestation["actor_ref"] != owner_ref or
            attestation["email"] != email.lower() or
            attestation["eta"] != eta or
            attestation["grace_min"] != grace_min
        ):
            return None
        return attestation.copy()

    def mission_timeline(self, mission_id, owner_ref):
        mission = self.owned_mission(mission_id, owner_ref)
        if not mission:
            return None
        return {
            "mission": mission,
            "events": [event for event in self.events if event["mission_id"] == mission_id],
        }


def _install_store(monkeypatch: pytest.MonkeyPatch, store: _MissionStore) -> None:
    for name in (
        "create_mission", "owned_mission", "transition_mission",
        "record_mission_event", "claim_pilot_attestation",
        "matching_pilot_attestation", "mission_timeline",
    ):
        monkeypatch.setattr(service.db, name, getattr(store, name))


def _approved_state(initial: dict) -> dict:
    condition_line = (
        "\nCONDITION CARD\nPlan v1 EAST cove rejected for the log boom. "
        "Plan v2 proposes WEST cove pending pilot REVIEW."
        if initial.get("condition_evidence") else ""
    )
    return {
        **initial,
        "verification": "APPROVED — sources and inference labels agree.",
        "briefing": (
            "HAZARDS\nNOTAM 0 is on route.\nWEATHER\n"
            f"INFERRED from CYXR, 27.8 NM away.{condition_line}\n"
            "NOT FOR OPERATIONAL USE."
        ),
        "weather": {
            "available": True, "reach_nm": 27.8,
            "sources": [{"station_id": "CYXR", "metar_raw": "CYXR ..."}],
        },
        "corridor": {"hazards": [{"idx": 0}]},
    }


def _body(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode()


def _headers(path: str, body: bytes, actor: str = "pilot:owner",
             method: str = "POST") -> dict[str, str]:
    timestamp = str(int(time.time()))
    return {
        "content-type": "application/json",
        "x-waterline-actor": actor,
        "x-waterline-timestamp": timestamp,
        "x-waterline-signature": sign_relay_request(
            SECRET, method, path, body, actor, timestamp,
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
        actor="pilot:owner", timestamp=timestamp, signature=signature, now=1787774400,
    )
    assert identity.user_id == identity.owner_ref
    assert identity.actor not in identity.owner_ref

    for changed in (
        {"path": "/v1/other"}, {"body": body + b" "},
        {"actor": "pilot:attacker"}, {"now": 1787774461},
    ):
        values = {
            "secret": SECRET, "method": "POST", "path": "/v1/missions", "body": body,
            "actor": "pilot:owner", "timestamp": timestamp, "signature": signature,
            "now": 1787774400,
        }
        values.update(changed)
        with pytest.raises(RelayAuthenticationError):
            verify_relay_request(**values)


def test_state_graph_rejects_skips_and_reverse_mutation() -> None:
    assert require_transition("proposed", "rejected") == ("proposed", "rejected")
    assert require_transition("rejected", "awaiting_attestation") == (
        "rejected", "awaiting_attestation",
    )
    for current, target in (
        ("proposed", "dispatched"), ("rejected", "accepted"),
        ("dispatched", "accepted"), ("awaiting_attestation", "dispatched"),
    ):
        with pytest.raises(ValueError, match="not allowed"):
            require_transition(current, target)


def test_transition_claim_is_atomic_with_its_event(monkeypatch) -> None:
    connection = _FakeConnection([{
        "mission_id": "mission-1", "trace_id": "trace-1",
        "status": "rejected", "updated_at": "now",
    }])
    monkeypatch.setattr(db, "connect", lambda: connection)
    event = db.transition_mission(
        "mission-1", "owner-1", "proposed", "rejected",
        "proposal_rejected", "pilot_attestation_missing",
    )
    assert event and event["to_status"] == "rejected"
    assert connection.committed and len(connection.executions) == 2

    losing = _FakeConnection([None])
    monkeypatch.setattr(db, "connect", lambda: losing)
    assert db.transition_mission(
        "mission-1", "owner-1", "proposed", "rejected",
        "proposal_rejected", "pilot_attestation_missing",
    ) is None
    assert losing.rolled_back and len(losing.executions) == 1


def test_attestation_claim_is_atomic_and_never_persists_contact_pii(monkeypatch) -> None:
    connection = _FakeConnection([{"mission_id": "mission-1", "trace_id": "trace-1"}])
    monkeypatch.setattr(db, "connect", lambda: connection)
    event = db.claim_pilot_attestation(
        "attestation-1", "mission-1", "pilot-owner", "OPS@Example.com", "16:00Z", 60,
    )
    assert event and event["to_status"] == "corrected"
    assert connection.committed and len(connection.executions) == 3
    insert_params = connection.executions[1][1]
    assert "OPS@Example.com" not in insert_params
    assert len(insert_params[3]) == 64
    assert "OPS@Example.com" not in str(connection.executions)

    replay = _FakeConnection([None])
    monkeypatch.setattr(db, "connect", lambda: replay)
    assert db.claim_pilot_attestation(
        "attestation-2", "mission-1", "pilot-owner", "ops@example.com", "16:00Z", 60,
    ) is None
    assert replay.rolled_back and len(replay.executions) == 1

    lookup = _FakeConnection([{
        "attestation_id": "attestation-1", "mission_id": "mission-1",
        "actor_ref": "pilot-owner", "attested_at": "now",
    }])
    monkeypatch.setattr(db, "connect", lambda: lookup)
    match = db.matching_pilot_attestation(
        "mission-1", "pilot-owner", "OPS@Example.com", "16:00Z", 60,
    )
    assert match and match["attestation_id"] == "attestation-1"
    assert "OPS@Example.com" not in str(lookup.executions)
    assert len(lookup.executions[0][1][3]) == 64


def test_api_owns_identity_and_emits_proposed_rejected_waiting(monkeypatch) -> None:
    monkeypatch.setenv("WATERLINE_RELAY_SECRET", SECRET)
    sessions = InMemorySessionService()
    store = _MissionStore()
    _install_store(monkeypatch, store)

    async def fake_run(_prompt, *, session_id, user_id, session_service,
                       initial_state, resume):
        assert not resume and session_id.startswith("session-")
        assert user_id == initial_state["mission_owner_ref"]
        assert initial_state["condition_evidence"]["blocked_sector"] == "east"
        assert initial_state["flight_plan"]["corrected_plan"]["landing_sector"] == "west"
        assert initial_state["quarantine_receipt"]["dispatch_authority"] is False
        assert "IGNORE SAFETY" not in json.dumps(initial_state)
        assert "IGNORE SAFETY" not in _prompt
        await session_service.create_session(
            app_name="waterline", user_id=user_id, session_id=session_id,
            state=_approved_state(initial_state),
        )
        yield {"type": "done"}

    monkeypatch.setattr(service, "run_briefing", fake_run)

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=service.create_app(sessions))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {
                "departure": "CYYZ", "destination": "Lady Evelyn Lake",
                "cruise_alt_ft": 3500,
            }
            body = _body(payload)
            response = await client.post(
                "/v1/missions", content=body, headers=_headers("/v1/missions", body),
            )
            assert response.status_code == 200
            states = ["proposed", "rejected", "awaiting_attestation"]
            positions = [response.text.index(f'"status": "{state}"') for state in states]
            assert positions == sorted(positions)
            mission_id, mission = next(iter(store.missions.items()))
            assert mission["session_id"].startswith("session-")
            assert mission["status"] == "awaiting_attestation"
            assert mission["request"]["condition_card_ref"].startswith("prepared://")
            state_changes = [
                event["to_status"] for event in store.events
                if event["from_status"] != event["to_status"]
            ]
            assert state_changes == states
            assert [event["event_type"] for event in store.events[1:3]] == [
                "condition_card_evaluated", "condition_card_quarantined",
            ]
            assert store.events[-2]["reason_code"] == "east_cove_obstructed"
            assert "IGNORE SAFETY" not in json.dumps(store.events)

            restore_path = f"/v1/missions/{mission_id}"
            restored = await client.get(
                restore_path, headers=_headers(restore_path, b"", method="GET"),
            )
            assert restored.status_code == 200
            assert len(restored.json()["events"]) == 5
            attacker = await client.get(
                restore_path,
                headers=_headers(restore_path, b"", actor="pilot:attacker", method="GET"),
            )
            assert attacker.status_code == 404

            forged = {**payload, "session_id": "browser-choice", "responsible_email": "x@y.test"}
            forged_body = _body(forged)
            rejected = await client.post(
                "/v1/missions", content=forged_body,
                headers=_headers("/v1/missions", forged_body),
            )
            assert rejected.status_code == 422
            assert len(store.missions) == 1

            preflight = await client.options(
                "/v1/missions", headers={
                    "origin": "https://attacker.example",
                    "access-control-request-method": "POST",
                },
            )
            assert "access-control-allow-origin" not in preflight.headers

    asyncio.run(exercise())


def test_visual_evidence_review_fails_closed_without_agent_or_dispatch_mutation(
    monkeypatch,
) -> None:
    monkeypatch.setenv("WATERLINE_RELAY_SECRET", SECRET)
    sessions = InMemorySessionService()
    store = _MissionStore()
    _install_store(monkeypatch, store)
    artifact = condition_card.load_prepared_artifact()
    image_bytes = artifact.image_path.read_bytes()
    extraction = condition_card.ConditionCardExtraction.model_validate(
        artifact.expected_extraction,
    ).model_copy(update={"observed_confidence": 0.42})
    decision = condition_card.validate_condition_card(
        artifact=artifact,
        image_bytes=image_bytes,
        extraction=extraction,
        destination="Lady Evelyn Lake",
        trace_id="trace-replaced-by-service",
        extractor="fixture",
        now=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )
    assert decision.validation_result == "review_required"

    async def review_required(*, destination, trace_id, now=None):
        receipt = dict(decision.model_receipt)
        receipt["trace_id"] = trace_id
        return condition_card.EvidenceDecision(
            validation_result="review_required",
            reason_codes=decision.reason_codes,
            model_receipt=receipt,
            trusted_evidence=None,
            quarantine_receipt=decision.quarantine_receipt,
            plan_revision=None,
        )

    async def must_not_run(*_args, **_kwargs):
        raise AssertionError("ADK pipeline must not run on unvalidated visual evidence")
        yield  # pragma: no cover

    dispatches: list[dict] = []

    async def must_not_dispatch(*args, **kwargs):
        dispatches.append({"args": args, "kwargs": kwargs})
        raise AssertionError("review-required evidence must not dispatch")

    monkeypatch.setattr(service, "evaluate_prepared_card", review_required)
    monkeypatch.setattr(service, "run_briefing", must_not_run)
    monkeypatch.setattr(service, "dispatch_from_state", must_not_dispatch)

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=service.create_app(sessions))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {
                "departure": "CYYZ", "destination": "Lady Evelyn Lake",
                "cruise_alt_ft": 3500,
            }
            body = _body(payload)
            response = await client.post(
                "/v1/missions", content=body, headers=_headers("/v1/missions", body),
            )
            assert response.status_code == 200
            assert '"validation_result": "review_required"' in response.text
            assert '"status": "awaiting_attestation"' in response.text
            mission_id, mission = next(iter(store.missions.items()))
            session = await sessions.get_session(
                app_name="waterline", user_id=mission["owner_ref"],
                session_id=mission["session_id"],
            )
            assert session is not None
            assert "condition_evidence" not in session.state
            assert "flight_plan" not in session.state
            assert session.state["condition_receipt"]["dispatch_authority"] is False
            assert store.events[-2]["reason_code"] == "condition_card_review_required"

            path = f"/v1/missions/{mission_id}/attest"
            attest_payload = {
                "confirm_dispatch": True, "responsible_email": "ops@example.com",
                "eta": "16:00Z", "grace_min": 60,
            }
            attest_body = _body(attest_payload)
            refused = await client.post(
                path, content=attest_body, headers=_headers(path, attest_body),
            )
            assert refused.status_code == 409
            assert not dispatches

    asyncio.run(exercise())


def test_malformed_visual_model_output_becomes_bounded_review_receipt(monkeypatch) -> None:
    monkeypatch.setenv("WATERLINE_RELAY_SECRET", SECRET)
    sessions = InMemorySessionService()
    store = _MissionStore()
    _install_store(monkeypatch, store)

    async def malformed_output(**_kwargs):
        raise ValueError("raw model output must not cross the boundary")

    async def must_not_run(*_args, **_kwargs):
        raise AssertionError("malformed extraction must stop before ADK")
        yield  # pragma: no cover

    monkeypatch.setattr(service, "evaluate_prepared_card", malformed_output)
    monkeypatch.setattr(service, "run_briefing", must_not_run)

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=service.create_app(sessions))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {
                "departure": "CYYZ", "destination": "Lady Evelyn Lake",
                "cruise_alt_ft": 3500,
            }
            body = _body(payload)
            response = await client.post(
                "/v1/missions", content=body, headers=_headers("/v1/missions", body),
            )
            assert response.status_code == 200
            assert "condition_extraction_failed" in response.text
            assert "raw model output" not in response.text
            mission_id, mission = next(iter(store.missions.items()))
            assert mission["status"] == "awaiting_attestation"
            session = await sessions.get_session(
                app_name="waterline", user_id=mission["owner_ref"],
                session_id=mission["session_id"],
            )
            assert session is not None
            assert set(session.state) == {
                "mission_id", "mission_owner_ref", "mission_trace_id",
                "pilot_attestation", "condition_receipt",
            }
            assert session.state["condition_receipt"]["confidence"] == 0.0
            assert "raw model output" not in json.dumps(store.events)
            assert any(
                event["event_type"] == "condition_card_rejected"
                for event in store.events if event["mission_id"] == mission_id
            )

    asyncio.run(exercise())


def test_interrupted_run_recovers_same_mission_and_session_once(monkeypatch) -> None:
    monkeypatch.setenv("WATERLINE_RELAY_SECRET", SECRET)
    sessions = InMemorySessionService()
    store = _MissionStore()
    _install_store(monkeypatch, store)
    calls: list[tuple[str, bool]] = []

    async def flaky_run(_prompt, *, session_id, user_id, session_service,
                        initial_state, resume):
        calls.append((session_id, resume))
        if not resume:
            await session_service.create_session(
                app_name="waterline", user_id=user_id, session_id=session_id,
                state=initial_state,
            )
            yield {"type": "error", "detail": "simulated worker interruption"}
        else:
            existing = await session_service.get_session(
                app_name="waterline", user_id=user_id, session_id=session_id,
            )
            assert existing is not None
            await session_service.append_event(
                existing,
                Event(
                    author="test-recovery",
                    invocation_id="recovery-test",
                    actions=EventActions(state_delta=_approved_state(existing.state)),
                ),
            )
            yield {"type": "done"}

    monkeypatch.setattr(service, "run_briefing", flaky_run)

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=service.create_app(sessions))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {
                "departure": "CYYZ", "destination": "Lady Evelyn Lake",
                "cruise_alt_ft": 3500,
            }
            body = _body(payload)
            first = await client.post(
                "/v1/missions", content=body, headers=_headers("/v1/missions", body),
            )
            assert '"status": "rejected"' in first.text
            assert '"available": true' in first.text
            mission_id, mission = next(iter(store.missions.items()))
            original_session = mission["session_id"]

            path = f"/v1/missions/{mission_id}/resume"
            resume_body = _body({"confirm_resume": True})
            recovered = await client.post(path, content=resume_body, headers=_headers(path, resume_body))
            assert recovered.status_code == 200
            assert '"status": "awaiting_attestation"' in recovered.text
            assert calls == [(original_session, False), (original_session, True)]
            assert store.missions[mission_id]["status"] == "awaiting_attestation"
            assert any(event["event_type"] == "recovery_started" for event in store.events)

            replay = await client.post(path, content=resume_body, headers=_headers(path, resume_body))
            assert replay.status_code == 409
            assert len(calls) == 2

    asyncio.run(exercise())


def test_attestation_is_owner_bound_and_full_state_path_is_replay_safe(monkeypatch) -> None:
    monkeypatch.setenv("WATERLINE_RELAY_SECRET", SECRET)
    sessions = InMemorySessionService()
    store = _MissionStore()
    _install_store(monkeypatch, store)
    owner = verify_relay_request(
        secret=SECRET, method="POST", path="/v1/missions", body=b"{}",
        actor="pilot:owner", timestamp="1787774400",
        signature=sign_relay_request(
            SECRET, "POST", "/v1/missions", b"{}", "pilot:owner", "1787774400",
        ),
        now=1787774400,
    )
    mission_id = "mission-0123456789abcdefabcd"
    session_id = "session-server-owned"
    trace_id = "trace-0123456789abcdefabcd"
    store.create_mission(
        mission_id, owner.owner_ref, session_id, trace_id,
        {"departure": "CYYZ", "destination": "Lady Evelyn Lake", "cruise_alt_ft": 3500},
    )
    store.transition_mission(
        mission_id, owner.owner_ref, "proposed", "rejected",
        "proposal_rejected", "pilot_attestation_missing",
    )
    store.transition_mission(
        mission_id, owner.owner_ref, "rejected", "awaiting_attestation",
        "pilot_review_requested", "owner_attestation_required",
    )
    dispatches: list[dict] = []

    async def dispatch(_session_id, state):
        state["dispatch_receipt"] = {"idempotency_key": "receipt-test-1"}
        dispatches.append(state)
        return {"sent": True, "channel": "test"}

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
            assert not dispatches

            accepted = await client.post(path, content=body, headers=_headers(path, body))
            assert accepted.status_code == 200
            result = accepted.json()
            assert result["status"] == "dispatched"
            assert result["receipt_id"] == "receipt-test-1"
            assert [event["to_status"] for event in result["events"]] == [
                "corrected", "accepted", "dispatched",
            ]
            assert dispatches[0]["pilot_attestation"]["actor_ref"] == owner.owner_ref
            assert store.missions[mission_id]["status"] == "dispatched"

            replay = await client.post(path, content=body, headers=_headers(path, body))
            assert replay.status_code == 409
            assert len(dispatches) == 1
            assert [event["to_status"] for event in store.events] == [
                "proposed", "rejected", "awaiting_attestation",
                "corrected", "accepted", "dispatched",
            ]

    asyncio.run(exercise())


def test_accepted_crash_recovery_reconfirms_attestation_without_duplicate_send(
    monkeypatch,
) -> None:
    """A crash after acceptance can reconcile, but an existing claim is never resent."""
    monkeypatch.setenv("WATERLINE_RELAY_SECRET", SECRET)
    sessions = InMemorySessionService()
    store = _MissionStore()
    _install_store(monkeypatch, store)
    owner = verify_relay_request(
        secret=SECRET, method="POST", path="/v1/missions", body=b"{}",
        actor="pilot:owner", timestamp="1787774400",
        signature=sign_relay_request(
            SECRET, "POST", "/v1/missions", b"{}", "pilot:owner", "1787774400",
        ),
        now=1787774400,
    )
    mission_id = "mission-abcdef0123456789abcd"
    session_id = "session-accepted-recovery"
    store.create_mission(
        mission_id, owner.owner_ref, session_id, "trace-accepted-recovery",
        {"departure": "CYYZ", "destination": "Lady Evelyn Lake", "cruise_alt_ft": 3500},
    )
    store.transition_mission(
        mission_id, owner.owner_ref, "proposed", "rejected",
        "proposal_rejected", "pilot_attestation_missing",
    )
    store.transition_mission(
        mission_id, owner.owner_ref, "rejected", "awaiting_attestation",
        "pilot_review_requested", "owner_attestation_required",
    )
    store.claim_pilot_attestation(
        "attestation-recovery", mission_id, owner.owner_ref,
        "ops@example.com", "16:00Z", 60,
    )
    store.transition_mission(
        mission_id, owner.owner_ref, "corrected", "accepted",
        "proposal_accepted", "deterministic_gate_passed",
    )
    dispatch_attempts = 0

    async def duplicate_claim(_session_id, _state):
        nonlocal dispatch_attempts
        dispatch_attempts += 1
        return {"sent": False, "duplicate_suppressed": True}

    monkeypatch.setattr(service, "dispatch_from_state", duplicate_claim)
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
            response = await client.post(path, content=body, headers=_headers(path, body))
            assert response.status_code == 409
            assert dispatch_attempts == 1
            assert store.missions[mission_id]["status"] == "accepted"
            assert any(event["event_type"] == "dispatch_recovery_started" for event in store.events)
            assert any(event["event_type"] == "dispatch_failed" for event in store.events)

            mismatch = {**payload, "responsible_email": "attacker@example.com"}
            mismatch_body = _body(mismatch)
            denied = await client.post(
                path, content=mismatch_body, headers=_headers(path, mismatch_body),
            )
            assert denied.status_code == 409
            assert dispatch_attempts == 1

    asyncio.run(exercise())
