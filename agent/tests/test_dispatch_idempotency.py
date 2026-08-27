import asyncio
from types import SimpleNamespace

import pytest

from waterline import dispatch
from waterline.tools import dispatch_tools


def _run_threads_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_inline(function, *args):
        return function(*args)

    monkeypatch.setattr(dispatch_tools.asyncio, "to_thread", run_inline)


def _context() -> SimpleNamespace:
    return SimpleNamespace(
        session=SimpleNamespace(id="briefing-resume-42"),
        state={
            "dispatch_authorized": True,
            "route": {
                "dep_id": "CYYZ",
                "dst_name": "Lady Evelyn Lake",
                "cruise_fl_upper": 35,
            },
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


def test_retry_resume_sends_no_duplicate(monkeypatch: pytest.MonkeyPatch) -> None:
    _run_threads_inline(monkeypatch)
    claimed: set[str] = set()
    sends: list[str] = []
    completed: list[str] = []
    receipts: dict[str, dict] = {}

    def claim(key: str, _session_id: str, _to: str) -> bool:
        if key in claimed:
            return False
        claimed.add(key)
        return True

    monkeypatch.setattr(dispatch_tools.db, "claim_dispatch", claim)
    def complete(key, channel, provider_reference, provider_status, recipient_redacted):
        completed.append(key)
        receipts[key] = {
            "status": "sent", "channel": channel,
            "provider_reference": provider_reference, "provider_status": provider_status,
            "recipient_redacted": recipient_redacted,
        }

    monkeypatch.setattr(dispatch_tools.db, "complete_dispatch", complete)
    monkeypatch.setattr(dispatch_tools.db, "dispatch_receipt", lambda key: receipts.get(key))
    monkeypatch.setattr(
        dispatch_tools,
        "send_notice",
        lambda target, _subject, _email, _sms: sends.append(target.address) or {
            "sent": True, "channel": "test", "status": "completed",
        },
    )
    ctx = _context()

    first = asyncio.run(dispatch_tools.file_and_notify(ctx))
    retry = asyncio.run(dispatch_tools.file_and_notify(ctx))

    assert first["sent"] is True
    assert retry["duplicate_suppressed"] is True
    assert retry["sent"] is True
    assert retry["status"] == "replay_suppressed"
    assert sends == ["operator-owned@example.test"]
    assert len(completed) == 1


def test_failed_smtp_claim_is_at_most_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed SMTP attempt remains claimed; retry needs operator reconciliation."""
    _run_threads_inline(monkeypatch)
    claimed: set[str] = set()
    attempts = 0

    def claim(key: str, _session_id: str, _to: str) -> bool:
        if key in claimed:
            return False
        claimed.add(key)
        return True

    def fail_send(*_args) -> dict:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("simulated SMTP ambiguity")

    monkeypatch.setattr(dispatch_tools.db, "claim_dispatch", claim)
    monkeypatch.setattr(dispatch_tools.db, "complete_dispatch", lambda *_args: None)
    monkeypatch.setattr(dispatch_tools.db, "dispatch_receipt", lambda _key: {"status": "claimed"})
    monkeypatch.setattr(dispatch_tools, "send_notice", fail_send)
    ctx = _context()

    with pytest.raises(RuntimeError, match="SMTP ambiguity"):
        asyncio.run(dispatch_tools.file_and_notify(ctx))
    retry = asyncio.run(dispatch_tools.file_and_notify(ctx))

    assert retry["duplicate_suppressed"] is True
    assert attempts == 1


def test_client_style_authorized_flag_cannot_bypass_attestation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dispatch recomputes authority instead of trusting a mutable state flag."""
    _run_threads_inline(monkeypatch)
    claims: list[str] = []
    sends: list[str] = []
    ctx = _context()
    ctx.state["pilot_attestation"] = None

    monkeypatch.setattr(
        dispatch_tools.db,
        "claim_dispatch",
        lambda key, *_args: claims.append(key) or True,
    )
    monkeypatch.setattr(
        dispatch_tools,
        "send_notice",
        lambda target, *_args: sends.append(target.address) or {"sent": True},
    )

    result = asyncio.run(dispatch_tools.file_and_notify(ctx))

    assert result["sent"] is False
    assert "pilot attestation" in result["reason"]
    assert claims == []
    assert sends == []


def test_explicit_outbox_mode_cannot_accidentally_send_smtp(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    monkeypatch.setenv("WATERLINE_OUTBOUND_MODE", "outbox")
    monkeypatch.setenv("WATERLINE_SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("WATERLINE_SMTP_FROM", "waterline@example.test")
    monkeypatch.setattr(dispatch, "_OUTBOX", tmp_path)

    result = dispatch.send_email(
        "pilot-owned@example.test", "Waterline test", "No external delivery.",
    )

    assert result["sent"] is True
    assert result["channel"] == "outbox"
    assert len(list(tmp_path.glob("*.eml"))) == 1


def test_smtp_mode_fails_closed_without_complete_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WATERLINE_OUTBOUND_MODE", "smtp")
    monkeypatch.delenv("WATERLINE_SMTP_HOST", raising=False)
    monkeypatch.delenv("WATERLINE_SMTP_FROM", raising=False)

    with pytest.raises(RuntimeError, match="requires host and sender"):
        dispatch.outbound_mode()


def test_sms_mode_requires_complete_server_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WATERLINE_OUTBOUND_MODE", "sms")
    for name in (
        "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER",
        "WATERLINE_DEMO_SMS_TO", "WATERLINE_PUBLIC_WEB_URL", "WATERLINE_HANDOFF_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="complete Twilio"):
        dispatch.outbound_mode()


def test_twilio_receipt_distinguishes_acceptance_from_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"sid": "SM" + "a" * 32, "status": "queued"}

    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC" + "b" * 32)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15550001111")
    captured: dict = {}

    def post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr(dispatch.httpx, "post", post)
    result = dispatch.send_sms(
        "+15550002222", "WATERLINE DEMO — NO ACTIVE FLIGHT", "https://example.test/status",
    )

    assert result["status"] == "provider_accepted"
    assert result["provider_status"] == "queued"
    assert result["provider_reference"] == "SM" + "a" * 32
    assert captured["data"]["StatusCallback"] == "https://example.test/status"


def test_sms_copy_uses_the_validated_candidate_sector() -> None:
    body = dispatch.compose_sms_notice(
        {"dep_id": "CYYZ", "dst_name": "Lady Evelyn Lake"},
        {"corrected_plan": {"landing_sector": "west"}},
        "16:00Z",
        "https://waterline.example/handoff/signed",
    )

    assert body.splitlines() == [
        "WATERLINE DEMO — NO ACTIVE FLIGHT",
        "CYYZ → Lady Evelyn Lake · WEST COVE · ETA 16:00Z",
        "Briefing and route: https://waterline.example/handoff/signed",
    ]
