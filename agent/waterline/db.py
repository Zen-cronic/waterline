"""PostGIS access for Waterline.

The corridor filter and the station ranking are SQL, not Python loops — the
spatial index does the work. This is the same code against a local
postgis/postgis container and against Cloud SQL for PostgreSQL; only
DATABASE_URL changes.
"""
from __future__ import annotations

import os
import hashlib
import json
from typing import Any, Optional
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from .notam import Notam, parse_dump
from .state_machine import require_transition

DEFAULT_URL = "postgresql://waterline:waterline@localhost:5455/waterline"
NM_TO_M = 1852.0


def dsn() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


def connect() -> psycopg.Connection:
    return psycopg.connect(dsn(), row_factory=dict_row)


def _record_ingest(cur: Any, site: str, product: str, records: int, parsed: int,
                   provenance: dict[str, Any]) -> None:
    cur.execute(
        """
        INSERT INTO ingests (
            site, product, records, parsed, source_url, source_mode, source_ref,
            retrieved_at, payload_sha256
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            site, product, records, parsed, provenance["source_url"],
            provenance["source_mode"], provenance["source_ref"],
            provenance["retrieved_at"], provenance["payload_sha256"],
        ),
    )


def load_notams(site: str, payload: dict[str, Any],
                provenance: dict[str, Any]) -> dict[str, Any]:
    """Parse a NAV CANADA dump and upsert every geometry-bearing NOTAM.

    The circle `area` is built in SQL with ST_Buffer over geography so the
    radius is honest metres, not planar degrees.
    """
    notams = parse_dump(payload)
    records = len(payload.get("data", []) if isinstance(payload, dict) else payload)
    with connect() as conn, conn.cursor() as cur:
        for n in notams:
            cur.execute(
                """
                INSERT INTO notams (pk, location, fir, qcode, radius_nm, fl_lower,
                    fl_upper, start_valid, end_valid, fir_wide, raw, center, area)
                VALUES (%(pk)s, %(location)s, %(fir)s, %(qcode)s, %(radius_nm)s,
                    %(fl_lower)s, %(fl_upper)s, %(start)s, %(end)s, %(fir_wide)s, %(raw)s,
                    ST_MakePoint(%(lon)s, %(lat)s)::geography,
                    ST_Buffer(ST_MakePoint(%(lon)s, %(lat)s)::geography, %(radius_m)s)::geography)
                ON CONFLICT (pk) DO UPDATE SET
                    start_valid = EXCLUDED.start_valid, end_valid = EXCLUDED.end_valid,
                    raw = EXCLUDED.raw, area = EXCLUDED.area, ingested_at = now()
                """,
                {**n.as_dict(), "radius_m": n.radius_nm * NM_TO_M},
            )
        _record_ingest(cur, site, "notam", records, len(notams), provenance)
        conn.commit()
    return {
        "records": records,
        "parsed": len(notams),
        "source_mode": provenance["source_mode"],
    }


def load_stations(site: str, stations: list, records: int,
                  provenance: dict[str, Any]) -> dict[str, Any]:
    """Upsert located METAR stations (from metar.stations_from_metar_dump)."""
    with connect() as conn, conn.cursor() as cur:
        for s in stations:
            cur.execute(
                """
                INSERT INTO stations (station_id, name, elevation_m, metar_raw, observed_at, point)
                VALUES (%s,%s,%s,%s,%s, ST_MakePoint(%s,%s)::geography)
                ON CONFLICT (station_id) DO UPDATE SET
                    metar_raw = EXCLUDED.metar_raw, observed_at = EXCLUDED.observed_at,
                    point = EXCLUDED.point
                """,
                (s.station_id, s.name, s.elevation_m, s.metar_raw, s.observed_at, s.lon, s.lat),
            )
        _record_ingest(cur, site, "metar", records, len(stations), provenance)
        conn.commit()
    return {
        "records": records,
        "parsed": len(stations),
        "source_mode": provenance["source_mode"],
    }


def create_mission(mission_id: str, owner_ref: str, session_id: str,
                   trace_id: str, request: dict[str, Any]) -> dict[str, Any] | None:
    """Persist a server-generated mission identity before any agent work starts."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO missions (mission_id, owner_ref, session_id, trace_id, request)
            VALUES (%s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (mission_id) DO NOTHING
            RETURNING mission_id
            """,
            (mission_id, owner_ref, session_id, trace_id, json.dumps(request)),
        )
        created = cur.fetchone() is not None
        event_id = f"event-{uuid4().hex[:20]}"
        if created:
            cur.execute(
                """
                INSERT INTO mission_events (
                    event_id, mission_id, from_status, to_status, event_type,
                    reason_code, evidence, trace_id
                ) VALUES (%s, %s, NULL, 'proposed', 'mission_proposed',
                          'authenticated_intake', %s::jsonb, %s)
                """,
                (
                    event_id, mission_id,
                    json.dumps({"request": request, "dispatch_authority": False}), trace_id,
                ),
            )
        conn.commit()
    if not created:
        return None
    return {
        "event_id": event_id,
        "mission_id": mission_id,
        "from_status": None,
        "to_status": "proposed",
        "event_type": "mission_proposed",
        "reason_code": "authenticated_intake",
        "evidence": {"request": request, "dispatch_authority": False},
        "trace_id": trace_id,
    }


def owned_mission(mission_id: str, owner_ref: str) -> dict[str, Any] | None:
    """Return a mission only to its authenticated owner."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT mission_id, owner_ref, session_id, trace_id, request,
                   status, created_at, updated_at
            FROM missions
            WHERE mission_id = %s AND owner_ref = %s
            """,
            (mission_id, owner_ref),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def transition_mission(mission_id: str, owner_ref: str, current_status: str,
                       target_status: str, event_type: str, reason_code: str,
                       evidence: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Atomically apply one allowed edge and append its structured proof."""
    require_transition(current_status, target_status)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE missions
            SET status = %s, updated_at = now()
            WHERE mission_id = %s AND owner_ref = %s AND status = %s
            RETURNING mission_id, trace_id, status, updated_at
            """,
            (target_status, mission_id, owner_ref, current_status),
        )
        row = cur.fetchone()
        if row is None:
            conn.rollback()
            return None
        event_id = f"event-{uuid4().hex[:20]}"
        cur.execute(
            """
            INSERT INTO mission_events (
                event_id, mission_id, from_status, to_status, event_type,
                reason_code, evidence, trace_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                event_id, mission_id, current_status, target_status, event_type,
                reason_code, json.dumps(evidence or {}), row["trace_id"],
            ),
        )
        conn.commit()
    return {
        "event_id": event_id,
        "mission_id": mission_id,
        "from_status": current_status,
        "to_status": target_status,
        "event_type": event_type,
        "reason_code": reason_code,
        "evidence": evidence or {},
        "trace_id": row["trace_id"],
    }


def record_mission_event(mission_id: str, owner_ref: str, event_type: str,
                         reason_code: str,
                         evidence: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Append durable failure/recovery evidence without pretending state changed."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT status, trace_id FROM missions
            WHERE mission_id = %s AND owner_ref = %s
            FOR UPDATE
            """,
            (mission_id, owner_ref),
        )
        row = cur.fetchone()
        if row is None:
            conn.rollback()
            return None
        event_id = f"event-{uuid4().hex[:20]}"
        cur.execute(
            """
            INSERT INTO mission_events (
                event_id, mission_id, from_status, to_status, event_type,
                reason_code, evidence, trace_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                event_id, mission_id, row["status"], row["status"], event_type,
                reason_code, json.dumps(evidence or {}), row["trace_id"],
            ),
        )
        conn.commit()
    return {
        "event_id": event_id,
        "mission_id": mission_id,
        "from_status": row["status"],
        "to_status": row["status"],
        "event_type": event_type,
        "reason_code": reason_code,
        "evidence": evidence or {},
        "trace_id": row["trace_id"],
    }


def mission_timeline(mission_id: str, owner_ref: str) -> dict[str, Any] | None:
    """Return an owner-bound mission and its append-only state evidence."""
    mission = owned_mission(mission_id, owner_ref)
    if mission is None:
        return None
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT event_id, from_status, to_status, event_type, reason_code,
                   evidence, trace_id, created_at
            FROM mission_events
            WHERE mission_id = %s
            ORDER BY sequence
            """,
            (mission_id,),
        )
        events = [dict(row) for row in cur.fetchall()]
    return {"mission": mission, "events": events}


def claim_pilot_attestation(attestation_id: str, mission_id: str, actor_ref: str,
                            responsible_contact: str, eta: str,
                            grace_min: int) -> dict[str, Any] | None:
    """Atomically claim the one human attestation allowed for a mission."""
    contact_hash = hashlib.sha256(
        responsible_contact.strip().lower().encode("utf-8"),
    ).hexdigest()
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE missions
            SET status = 'corrected', updated_at = now()
            WHERE mission_id = %s AND owner_ref = %s AND status = 'awaiting_attestation'
            RETURNING mission_id, trace_id
            """,
            (mission_id, actor_ref),
        )
        mission = cur.fetchone()
        if mission is None:
            conn.rollback()
            return None
        event_id = f"event-{uuid4().hex[:20]}"
        event_evidence = {
            "attestation_id": attestation_id,
            "contact_hash": contact_hash,
            "eta": eta,
            "grace_min": grace_min,
            "dispatch_authority": False,
        }
        cur.execute(
            """
            INSERT INTO pilot_attestations (
                attestation_id, mission_id, actor_ref, contact_hash, eta, grace_min
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (attestation_id, mission_id, actor_ref, contact_hash, eta, grace_min),
        )
        cur.execute(
            """
            INSERT INTO mission_events (
                event_id, mission_id, from_status, to_status, event_type,
                reason_code, evidence, trace_id
            ) VALUES (%s, %s, 'awaiting_attestation', 'corrected',
                      'pilot_attestation_recorded', 'owner_attested', %s::jsonb, %s)
            """,
            (
                event_id, mission_id, json.dumps(event_evidence),
                mission["trace_id"],
            ),
        )
        conn.commit()
    return {
        "event_id": event_id,
        "mission_id": mission_id,
        "from_status": "awaiting_attestation",
        "to_status": "corrected",
        "event_type": "pilot_attestation_recorded",
        "reason_code": "owner_attested",
        "evidence": event_evidence,
        "trace_id": mission["trace_id"],
    }


def matching_pilot_attestation(mission_id: str, actor_ref: str,
                               responsible_contact: str, eta: str,
                               grace_min: int) -> dict[str, Any] | None:
    """Validate a recovery attestation without storing or returning contact PII."""
    contact_hash = hashlib.sha256(
        responsible_contact.strip().lower().encode("utf-8"),
    ).hexdigest()
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT pa.attestation_id, pa.mission_id, pa.actor_ref, pa.attested_at
            FROM pilot_attestations pa
            JOIN missions m ON m.mission_id = pa.mission_id
            WHERE pa.mission_id = %s AND pa.actor_ref = %s
              AND m.owner_ref = %s AND m.status IN ('accepted', 'dispatched')
              AND pa.contact_hash = %s AND pa.eta = %s AND pa.grace_min = %s
            """,
            (mission_id, actor_ref, actor_ref, contact_hash, eta, grace_min),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def mark_mission_dispatched(mission_id: str, owner_ref: str) -> bool:
    """Compatibility wrapper for the final accepted -> dispatched edge."""
    return transition_mission(
        mission_id, owner_ref, "accepted", "dispatched",
        "dispatch_completed", "verified_notice_receipt",
    ) is not None


def claim_dispatch(idempotency_key: str, session_id: str, recipient: str) -> bool:
    """Atomically claim one external dispatch before any provider is called.

    The primary-key insert is the cross-process recovery guard: retries and
    concurrent Cloud Run requests using the same itinerary key cannot both win.
    We intentionally prefer at-most-once delivery over a possible duplicate.
    """
    recipient_hash = hashlib.sha256(recipient.strip().lower().encode("utf-8")).hexdigest()
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO dispatch_receipts (idempotency_key, session_id, recipient_hash)
            VALUES (%s, %s, %s)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING idempotency_key
            """,
            (idempotency_key, session_id, recipient_hash),
        )
        claimed = cur.fetchone() is not None
        conn.commit()
    return claimed


def complete_dispatch(idempotency_key: str, channel: str,
                      provider_reference: str | None = None,
                      provider_status: str | None = None,
                      recipient_redacted: str | None = None) -> None:
    """Mark a claim complete with bounded provider proof and no raw recipient."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE dispatch_receipts
            SET status = 'sent', channel = %s, provider_reference = %s,
                provider_status = %s, recipient_redacted = %s, completed_at = now()
            WHERE idempotency_key = %s
            """,
            (
                channel, provider_reference, provider_status,
                recipient_redacted, idempotency_key,
            ),
        )
        conn.commit()


def dispatch_receipt(idempotency_key: str) -> dict[str, Any] | None:
    """Return bounded receipt proof for an idempotent replay."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT idempotency_key, status, channel, provider_reference,
                   provider_status, recipient_redacted, claimed_at, completed_at
            FROM dispatch_receipts WHERE idempotency_key = %s
            """,
            (idempotency_key,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


_PROVIDER_STATUS_RANK = {
    "accepted": 10, "scheduled": 10, "queued": 20, "sending": 30,
    "sent": 40, "delivered": 50, "read": 60,
    "failed": 50, "undelivered": 50, "canceled": 50,
}
_PROVIDER_TERMINAL = {"delivered", "read", "failed", "undelivered", "canceled"}


def update_dispatch_provider_status(provider_reference: str, provider_status: str,
                                    error_code: str | None = None) -> dict[str, Any] | None:
    """Apply a signed provider callback without allowing out-of-order regression."""
    if provider_status not in _PROVIDER_STATUS_RANK:
        raise ValueError("unsupported provider status")
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT dr.idempotency_key, dr.provider_status, dr.recipient_redacted,
                   m.mission_id, m.owner_ref, m.trace_id, m.status AS mission_status
            FROM dispatch_receipts dr
            JOIN missions m ON m.session_id = dr.session_id
            WHERE dr.provider_reference = %s
            FOR UPDATE OF dr
            """,
            (provider_reference,),
        )
        row = cur.fetchone()
        if row is None:
            conn.rollback()
            return None
        current = row.get("provider_status")
        should_update = (
            current not in _PROVIDER_TERMINAL
            and _PROVIDER_STATUS_RANK[provider_status] >= _PROVIDER_STATUS_RANK.get(current, -1)
        )
        if should_update:
            cur.execute(
                """
                UPDATE dispatch_receipts SET provider_status = %s
                WHERE provider_reference = %s
                """,
                (provider_status, provider_reference),
            )
        conn.commit()
    return {
        **dict(row),
        "provider_reference": provider_reference,
        "provider_status": provider_status if should_update else current,
        "error_code": error_code,
        "updated": should_update,
    }


def corridor_filter(
    dep: tuple[float, float],       # (lon, lat)
    dst: tuple[float, float],
    corridor_nm: float = 10.0,
    cruise_fl_lower: int = 0,
    cruise_fl_upper: int = 95,      # a float plane rarely climbs above ~9,500 ft
    at: Optional[str] = None,       # ISO time of flight; None = now
) -> dict[str, Any]:
    """Reduce the whole-FIR NOTAM set to the ones that actually touch the route.

    Three deterministic conditions, all in PostGIS:
      1. the NOTAM circle intersects the buffered route corridor,
      2. its flight-level band overlaps the aircraft's altitude band,
      3. it is valid during the flight.
    Returns {total, kept, dropped, on_route:[...]} — `on_route` carries the raw
    text so the UI can show the decode beside the untouched source.
    """
    corridor_m = corridor_nm * NM_TO_M
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS c FROM notams")
        total = cur.fetchone()["c"]
        cur.execute(
            """
            WITH route AS (
                SELECT ST_MakeLine(
                    ST_MakePoint(%(dep_lon)s, %(dep_lat)s),
                    ST_MakePoint(%(dst_lon)s, %(dst_lat)s)
                )::geography AS line
            ),
            corridor AS (
                SELECT ST_Buffer(line, %(corridor_m)s) AS geom FROM route
            )
            SELECT n.pk, n.location, n.qcode, n.radius_nm, n.fl_lower, n.fl_upper,
                   n.fir_wide, n.raw, n.start_valid, n.end_valid,
                   ST_Y(n.center::geometry) AS lat, ST_X(n.center::geometry) AS lon,
                   ST_Distance(n.center, (SELECT line FROM route)) / %(nm)s AS dist_nm
            FROM notams n, corridor c
            WHERE ST_Intersects(n.area, c.geom)
              AND n.fl_lower <= %(fl_upper)s AND n.fl_upper >= %(fl_lower)s
              AND (n.end_valid IS NULL OR n.end_valid >= COALESCE(%(at)s::timestamptz, now()))
              AND (n.start_valid IS NULL OR n.start_valid <= COALESCE(%(at)s::timestamptz, now()))
            ORDER BY dist_nm ASC
            """,
            {
                "dep_lon": dep[0], "dep_lat": dep[1], "dst_lon": dst[0], "dst_lat": dst[1],
                "corridor_m": corridor_m, "fl_lower": cruise_fl_lower,
                "fl_upper": cruise_fl_upper, "at": at, "nm": NM_TO_M,
            },
        )
        rows = cur.fetchall()
    return {
        "total": total,
        "kept": len(rows),
        "dropped": total - len(rows),
        "on_route": [dict(r) for r in rows],
    }


def route_geometry(dep: tuple[float, float], dst: tuple[float, float],
                   corridor_nm: float = 10.0) -> dict[str, Any]:
    """Route line + buffered corridor polygon as GeoJSON, straight from PostGIS.

    Returned to the map layer, never to the model — the corridor polygon is the
    visual proof that the filter is spatial, not a keyword match on text.
    """
    corridor_m = corridor_nm * NM_TO_M
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            WITH route AS (
                SELECT ST_MakeLine(ST_MakePoint(%(dep_lon)s,%(dep_lat)s),
                                   ST_MakePoint(%(dst_lon)s,%(dst_lat)s))::geography AS line
            )
            SELECT ST_AsGeoJSON(line::geometry) AS route_gj,
                   ST_AsGeoJSON(ST_Buffer(line, %(m)s)::geometry) AS corridor_gj
            FROM route
            """,
            {"dep_lon": dep[0], "dep_lat": dep[1], "dst_lon": dst[0], "dst_lat": dst[1], "m": corridor_m},
        )
        r = cur.fetchone()
    import json as _json
    return {"route": _json.loads(r["route_gj"]), "corridor": _json.loads(r["corridor_gj"])}


def nearest_stations(lon: float, lat: float, k: int = 5) -> list[dict[str, Any]]:
    """The k closest METAR stations to a station-less destination, nearest first.

    This is the raw material for the inference — every one is surfaced to the
    pilot; nothing is suppressed.
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT station_id, name, elevation_m, metar_raw, observed_at,
                   ST_Distance(point, ST_MakePoint(%s,%s)::geography) / %s AS dist_nm
            FROM stations
            ORDER BY point <-> ST_MakePoint(%s,%s)::geography
            LIMIT %s
            """,
            (lon, lat, NM_TO_M, lon, lat, k),
        )
        return [dict(r) for r in cur.fetchall()]
