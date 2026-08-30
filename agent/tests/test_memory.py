import asyncio
from hashlib import sha256
from types import SimpleNamespace

from waterline import db, embed
from waterline.gemma_ranker import DEFAULT_GEMMA_MODEL, RankedNotams
from waterline.tools import geo_tools


class FakeConnection:
    def __init__(self, rows):
        self.rows = list(rows)
        self.executions = []
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def cursor(self):
        return self

    def execute(self, sql, params):
        self.executions.append((sql, params))

    def fetchone(self):
        return self.rows.pop(0)

    def fetchall(self):
        return self.rows.pop(0)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def test_write_notam_acknowledgements_requires_owner_terminal_attestation(monkeypatch):
    connection = FakeConnection([None])
    monkeypatch.setattr(db, "connect", lambda: connection)
    result = db.write_notam_acknowledgements(
        "owner-a", "mission-a", "Lady Evelyn Lake", [0.0] * 768,
        [{"pk": "n-1", "raw": "source", "end_valid": None}],
    )
    assert result is None
    assert connection.rolled_back is True
    assert len(connection.executions) == 1
    sql, params = connection.executions[0]
    assert "m.owner_ref = %s" in sql
    assert "m.status = 'dispatched'" in sql
    assert "pilot_attestations" in sql
    assert params == ("mission-a", "owner-a")


def test_write_and_recall_are_owner_scoped_and_digest_bound(monkeypatch):
    write_connection = FakeConnection([{"mission_id": "mission-a"}, {"memory_id": "m-1"}])
    monkeypatch.setattr(db, "connect", lambda: write_connection)
    result = db.write_notam_acknowledgements(
        "owner-a", "mission-a", "Lady Evelyn Lake", [0.25] * 768,
        [{"pk": "n-1", "raw": "source", "end_valid": "2026-09-01T00:00:00+00:00"}],
    )
    assert result and result["written"] == 1
    insert_sql, insert_params = write_connection.executions[1]
    assert "owner_ref" in insert_sql
    assert insert_params[1:4] == ("owner-a", "mission-a", "Lady Evelyn Lake")
    assert insert_params[6] == sha256(b"source").hexdigest()

    recalled = [{"notam_pk": "n-1", "raw_sha256": insert_params[6]}]
    recall_connection = FakeConnection([recalled])
    monkeypatch.setattr(db, "connect", lambda: recall_connection)
    assert db.recall_notam_acknowledgements("owner-b", [0.25] * 768) == recalled
    recall_sql, recall_params = recall_connection.executions[0]
    assert "WHERE owner_ref = %s" in recall_sql
    assert recall_params[1] == "owner-b"


def test_embedding_pins_vertex_model_and_768_dimensions(monkeypatch):
    captured = {}

    class Models:
        def embed_content(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                embeddings=[SimpleNamespace(values=[0.1] * 768)],
            )

    class Client:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.models = Models()

    monkeypatch.delenv("WATERLINE_EMBEDDING_MODE", raising=False)
    monkeypatch.setattr(embed, "vertex_model_client_kwargs", lambda: {
        "vertexai": True, "project": "project-a", "location": "global",
    })
    monkeypatch.setattr(embed.genai, "Client", Client)
    values = embed.embed_destination("Lady Evelyn Lake", task_type="RETRIEVAL_QUERY")
    assert len(values) == 768
    assert captured["model"] == "gemini-embedding-001"
    assert captured["config"].output_dimensionality == 768
    assert captured["config"].task_type == "RETRIEVAL_QUERY"


def _route_rows(count=79):
    return [
        {
            "idx": idx, "pk": f"notam-{idx}", "raw": f"RAW {idx}",
            "end_valid": "2026-09-01T00:00:00+00:00", "location": "CZYZ",
            "qcode": "QXXXX", "dist_nm": float(idx), "radius_nm": 5,
            "fir_wide": False, "lon": -79.0, "lat": 44.0,
        }
        for idx in range(count)
    ]


def _acks(rows):
    return [
        {
            "notam_pk": row["pk"],
            "raw_sha256": sha256(row["raw"].encode()).hexdigest(),
            "end_valid": row["end_valid"],
        }
        for row in rows
    ]


def test_recall_reduces_79_to_23_and_changed_source_resurfaces(monkeypatch):
    rows = _route_rows()
    acknowledgements = _acks(rows)
    acknowledgements[0]["raw_sha256"] = sha256(b"BEFORE MUTATION").hexdigest()
    state = {
        "mission_owner_ref": "owner-a",
        "route": {"dst_name": "Lady Evelyn Lake"},
        "_route_notams": rows,
        "corridor": {"total": 471, "kept": 79, "hazards": []},
    }
    monkeypatch.setattr(geo_tools, "embed_destination", lambda *_args, **_kwargs: [0.1] * 768)
    monkeypatch.setattr(
        geo_tools.db, "recall_notam_acknowledgements",
        lambda owner_ref, _embedding: acknowledgements if owner_ref == "owner-a" else [],
    )
    monkeypatch.setattr(
        geo_tools, "rank_notams",
        lambda hazards: RankedNotams(hazards, DEFAULT_GEMMA_MODEL, "fixture", "fixture"),
    )
    monkeypatch.setattr(geo_tools, "emit_step", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(geo_tools, "emit_layer", lambda *_args, **_kwargs: None)

    result = asyncio.run(
        geo_tools.recall_destination_memory(SimpleNamespace(state=state))
    )
    assert result["on_route"] == 79
    assert result["surfaced"] == 23
    assert result["suppressed"] == 56
    assert result["changed"] == [{
        "idx": 0, "pk": "notam-0", "reason": "digest_or_end_valid_mismatch",
    }]
    assert state["corridor"]["gemini_reads"] == 14
    assert len(state["corridor"]["surfaced_hazards"]) == 23
    assert state["corridor"]["hazards"][0]["idx"] == 0


def test_embedding_failure_surfaces_every_notam(monkeypatch):
    rows = _route_rows(7)
    state = {
        "mission_owner_ref": "owner-a",
        "route": {"dst_name": "Lady Evelyn Lake"},
        "_route_notams": rows,
        "corridor": {"total": 471, "kept": 7, "hazards": []},
    }
    monkeypatch.setattr(
        geo_tools, "embed_destination",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    monkeypatch.setattr(
        geo_tools, "rank_notams",
        lambda hazards: RankedNotams(hazards, DEFAULT_GEMMA_MODEL, "disabled", "fallback"),
    )
    monkeypatch.setattr(geo_tools, "emit_step", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(geo_tools, "emit_layer", lambda *_args, **_kwargs: None)
    result = asyncio.run(
        geo_tools.recall_destination_memory(SimpleNamespace(state=state))
    )
    assert result == {
        "on_route": 7, "suppressed": 0, "surfaced": 7,
        "changed": [], "notes": [],
    }
