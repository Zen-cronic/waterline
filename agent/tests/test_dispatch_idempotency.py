import asyncio
from types import SimpleNamespace

import pytest

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
            "responsible_email": "ops@example.com",
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
            "briefing": "INFERRED from CYXR, 27.8 NM away. NOT FOR OPERATIONAL USE.",
        },
    )


def test_retry_resume_sends_no_duplicate(monkeypatch: pytest.MonkeyPatch) -> None:
    _run_threads_inline(monkeypatch)
    claimed: set[str] = set()
    sends: list[str] = []
    completed: list[str] = []

    def claim(key: str, _session_id: str, _to: str) -> bool:
        if key in claimed:
            return False
        claimed.add(key)
        return True

    monkeypatch.setattr(dispatch_tools.db, "claim_dispatch", claim)
    monkeypatch.setattr(
        dispatch_tools.db, "complete_dispatch", lambda key, _channel: completed.append(key),
    )
    monkeypatch.setattr(
        dispatch_tools,
        "send_email",
        lambda to, _subject, _body: sends.append(to) or {"sent": True, "channel": "test", "to": to},
    )
    ctx = _context()

    first = asyncio.run(dispatch_tools.file_and_notify(ctx))
    retry = asyncio.run(dispatch_tools.file_and_notify(ctx))

    assert first["sent"] is True
    assert retry["duplicate_suppressed"] is True
    assert sends == ["ops@example.com"]
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

    def fail_send(_to: str, _subject: str, _body: str) -> dict:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("simulated SMTP ambiguity")

    monkeypatch.setattr(dispatch_tools.db, "claim_dispatch", claim)
    monkeypatch.setattr(dispatch_tools.db, "complete_dispatch", lambda *_args: None)
    monkeypatch.setattr(dispatch_tools, "send_email", fail_send)
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
        "send_email",
        lambda to, *_args: sends.append(to) or {"sent": True},
    )

    result = asyncio.run(dispatch_tools.file_and_notify(ctx))

    assert result["sent"] is False
    assert "pilot attestation" in result["reason"]
    assert claims == []
    assert sends == []
