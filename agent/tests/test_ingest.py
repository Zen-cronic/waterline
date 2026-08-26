import asyncio
from hashlib import sha256
import json
from types import SimpleNamespace

import httpx

from waterline import db, navcanada
from waterline.tools import geo_tools


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _Cursor:
    def __init__(self) -> None:
        self.sql = ""
        self.params = ()

    def execute(self, sql, params) -> None:
        self.sql = sql
        self.params = params


def test_live_fetch_has_structured_digest_provenance(monkeypatch) -> None:
    payload = {"data": [{"text": "METAR CYYZ 261900Z 25008KT 15SM BKN030 20/12 A2992"}]}
    monkeypatch.setattr(navcanada.httpx, "get", lambda *_args, **_kwargs: _Response(payload))

    result, receipt = navcanada.fetch_metars("CZYZ")

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    assert result == payload
    assert receipt["provider"] == "NAV CANADA"
    assert receipt["product"] == "metar"
    assert receipt["source_mode"] == "live"
    assert receipt["source_ref"] == receipt["source_url"]
    assert receipt["payload_sha256"] == sha256(encoded).hexdigest()
    assert receipt["live_error"] is None


def test_offline_fetch_labels_local_capture(monkeypatch, tmp_path) -> None:
    payload = {"data": [{"text": "frozen fixture"}]}
    capture = tmp_path / "navcanada_CZYZ.json"
    capture.write_text(json.dumps(payload))
    monkeypatch.setattr(navcanada, "_CAPTURES", tmp_path)

    def offline(*_args, **_kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(navcanada.httpx, "get", offline)
    result, receipt = navcanada.fetch_notams("CZYZ")

    assert result == payload
    assert receipt["product"] == "notam"
    assert receipt["source_mode"] == "local_frozen_capture"
    assert receipt["source_ref"] == capture.name
    assert receipt["live_error"] == "ConnectError"
    assert "/" not in receipt["source_ref"]


def test_ingest_receipt_persists_source_mode_reference_and_digest() -> None:
    cursor = _Cursor()
    receipt = {
        "source_url": "https://example.test/notams",
        "source_mode": "local_frozen_capture",
        "source_ref": "navcanada_CZYZ.json",
        "retrieved_at": "2026-08-26T19:00:00+00:00",
        "payload_sha256": "c" * 64,
    }

    db._record_ingest(cursor, "CZYZ", "notam", 17, 12, receipt)

    assert "source_mode" in cursor.sql
    assert "source_ref" in cursor.sql
    assert "payload_sha256" in cursor.sql
    assert cursor.params == (
        "CZYZ", "notam", 17, 12, receipt["source_url"],
        "local_frozen_capture", "navcanada_CZYZ.json",
        receipt["retrieved_at"], receipt["payload_sha256"],
    )


def test_ingest_tool_loads_notam_and_metar_before_weather(monkeypatch) -> None:
    notam_payload = {"data": [{"text": "NOTAM fixture"}]}
    metar_payload = {
        "data": [
            {
                "text": "METAR CYYZ 261900Z 25008KT 15SM BKN030 20/12 A2992",
                "startValidity": "2026-08-26T19:00:00Z",
            }
        ]
    }
    notam_receipt = {
        "provider": "NAV CANADA", "product": "notam", "source_url": "notam-url",
        "source_mode": "live", "source_ref": "notam-url",
        "retrieved_at": "2026-08-26T19:00:00+00:00", "payload_sha256": "a" * 64,
        "live_error": None,
    }
    metar_receipt = {
        "provider": "NAV CANADA", "product": "metar", "source_url": "metar-url",
        "source_mode": "local_frozen_capture", "source_ref": "metar_CZYZ.json",
        "retrieved_at": "2026-08-26T19:00:01+00:00", "payload_sha256": "b" * 64,
        "live_error": "ConnectError",
    }
    calls: list[str] = []

    async def run_inline(function, *args):
        # The managed test sandbox's default executor does not complete even a
        # minimal asyncio.to_thread call. Keep this unit test deterministic and
        # exercise the orchestration without depending on that executor.
        return function(*args)

    monkeypatch.setattr(geo_tools.asyncio, "to_thread", run_inline)
    monkeypatch.setattr(geo_tools, "fetch_notams", lambda _site: (notam_payload, notam_receipt))
    monkeypatch.setattr(geo_tools, "fetch_metars", lambda _site: (metar_payload, metar_receipt))

    def load_notams(site, payload, provenance):
        calls.append("notam")
        assert site == "CZYZ" and payload is notam_payload and provenance is notam_receipt
        return {"records": 1, "parsed": 1, "source_mode": provenance["source_mode"]}

    def load_stations(site, stations, records, provenance):
        calls.append("metar")
        assert site == "CZYZ" and records == 1 and provenance is metar_receipt
        assert [station.station_id for station in stations] == ["CYYZ"]
        return {"records": 1, "parsed": 1, "source_mode": provenance["source_mode"]}

    monkeypatch.setattr(geo_tools.db, "load_notams", load_notams)
    monkeypatch.setattr(geo_tools.db, "load_stations", load_stations)
    monkeypatch.setattr(geo_tools, "emit_panel", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(geo_tools, "emit_step", lambda *_args, **_kwargs: None)

    context = SimpleNamespace(state={"route": {"fir_site": "CZYZ"}})
    result = asyncio.run(geo_tools.fetch_and_load_sources(context))

    assert calls == ["notam", "metar"]
    assert result["notam"]["source_mode"] == "live"
    assert result["metar"]["source_mode"] == "local_frozen_capture"
    assert context.state["ingest"] == result
