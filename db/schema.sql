-- Waterline schema. PostGIS is load-bearing: the corridor filter is a spatial
-- query, not application code paging rows into Python. Runs identically on a
-- local postgis/postgis container and on Cloud SQL for PostgreSQL.

CREATE EXTENSION IF NOT EXISTS postgis;

-- One row per NOTAM that carried parseable Q-line geometry.
-- `area` is the affected circle (centre buffered by the Q-line radius); the
-- GiST index on it is what makes the corridor intersection cheap.
CREATE TABLE IF NOT EXISTS notams (
    pk          text PRIMARY KEY,
    location    text,                       -- A) affected aerodrome/FIR
    fir         text,                        -- Q-line FIR (e.g. CZYZ)
    qcode       text,                        -- Q-code (e.g. QMXLC)
    radius_nm   integer NOT NULL,
    fl_lower    integer NOT NULL,            -- flight level, hundreds of feet (000 = surface)
    fl_upper    integer NOT NULL,            -- 999 = unlimited
    start_valid timestamptz,
    end_valid   timestamptz,                 -- NULL = permanent
    fir_wide    boolean NOT NULL DEFAULT false,
    raw         text NOT NULL,               -- untouched NOTAM text; ALWAYS shown beside the decode
    center      geography(Point, 4326) NOT NULL,
    area        geography(Polygon, 4326) NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS notams_area_gix ON notams USING gist (area);
CREATE INDEX IF NOT EXISTS notams_center_gix ON notams USING gist (center);

-- Nearby weather-reporting stations (METAR sites), used to *infer* a briefing
-- for a destination that has no station of its own. We never invent a value —
-- we rank real observations by distance and surface every source.
CREATE TABLE IF NOT EXISTS stations (
    station_id  text PRIMARY KEY,            -- ICAO id, e.g. CYSB
    name        text,
    elevation_m double precision,
    point       geography(Point, 4326) NOT NULL,
    metar_raw   text,                        -- latest raw METAR (always shown)
    observed_at timestamptz
);
CREATE INDEX IF NOT EXISTS stations_point_gix ON stations USING gist (point);

-- Server-owned mission/session identity. Only an authenticated web relay can
-- create a mission; browser-supplied actor, user, and session ids are absent
-- from the public command schema.
CREATE TABLE IF NOT EXISTS missions (
    mission_id   text PRIMARY KEY,
    owner_ref    text NOT NULL,
    session_id   text NOT NULL UNIQUE,
    status       text NOT NULL DEFAULT 'briefing',
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS missions_owner_idx ON missions (owner_ref, created_at DESC);

-- One authenticated human attestation per mission. Contact PII is hashed; the
-- actual address exists only for the bounded send attempt and dispatch ledger.
CREATE TABLE IF NOT EXISTS pilot_attestations (
    attestation_id text PRIMARY KEY,
    mission_id     text NOT NULL UNIQUE REFERENCES missions(mission_id),
    actor_ref      text NOT NULL,
    contact_hash   text NOT NULL,
    eta            text NOT NULL,
    grace_min      integer NOT NULL CHECK (grace_min BETWEEN 15 AND 240),
    attested_at    timestamptz NOT NULL DEFAULT now()
);

-- Source provenance for every NOTAM and METAR pull. A judge can reproduce a
-- live URL and distinguish it from an explicitly labelled local-only capture.
CREATE TABLE IF NOT EXISTS ingests (
    id          bigserial PRIMARY KEY,
    site        text NOT NULL,
    product     text NOT NULL DEFAULT 'notam',
    fetched_at  timestamptz NOT NULL DEFAULT now(),
    retrieved_at timestamptz,
    records     integer NOT NULL,
    parsed      integer NOT NULL,
    source_url  text NOT NULL,
    source_mode text NOT NULL DEFAULT 'legacy',
    source_ref  text,
    payload_sha256 text
);
ALTER TABLE ingests ADD COLUMN IF NOT EXISTS product text NOT NULL DEFAULT 'notam';
ALTER TABLE ingests ADD COLUMN IF NOT EXISTS retrieved_at timestamptz;
ALTER TABLE ingests ADD COLUMN IF NOT EXISTS source_mode text NOT NULL DEFAULT 'legacy';
ALTER TABLE ingests ADD COLUMN IF NOT EXISTS source_ref text;
ALTER TABLE ingests ADD COLUMN IF NOT EXISTS payload_sha256 text;

-- At-most-once external dispatch ledger. The INSERT claim is committed before
-- SMTP is attempted, so retry/resume cannot send a second notice. Recipient PII
-- is represented only as a hash; the actual address remains in session state.
CREATE TABLE IF NOT EXISTS dispatch_receipts (
    idempotency_key text PRIMARY KEY,
    session_id      text NOT NULL,
    recipient_hash  text NOT NULL,
    status          text NOT NULL DEFAULT 'claimed'
                    CHECK (status IN ('claimed', 'sent')),
    channel         text,
    claimed_at      timestamptz NOT NULL DEFAULT now(),
    completed_at    timestamptz
);
