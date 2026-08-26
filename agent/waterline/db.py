"""PostGIS access for Waterline.

The corridor filter and the station ranking are SQL, not Python loops — the
spatial index does the work. This is the same code against a local
postgis/postgis container and against Cloud SQL for PostgreSQL; only
DATABASE_URL changes.
"""
from __future__ import annotations

import os
import hashlib
from typing import Any, Optional

import psycopg
from psycopg.rows import dict_row

from .notam import Notam, parse_dump

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


def claim_dispatch(idempotency_key: str, session_id: str, recipient: str) -> bool:
    """Atomically claim one external dispatch before SMTP is attempted.

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


def complete_dispatch(idempotency_key: str, channel: str) -> None:
    """Mark a claimed dispatch complete without exposing its recipient."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE dispatch_receipts
            SET status = 'sent', channel = %s, completed_at = now()
            WHERE idempotency_key = %s
            """,
            (channel, idempotency_key),
        )
        conn.commit()


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
