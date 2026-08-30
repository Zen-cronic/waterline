-- Waterline schema. PostGIS is load-bearing: the corridor filter is a spatial
-- query, not application code paging rows into Python. Runs identically on a
-- local postgis/postgis container and on Cloud SQL for PostgreSQL.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;

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
    trace_id     text NOT NULL,
    request      jsonb NOT NULL,
    status       text NOT NULL DEFAULT 'proposed',
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE missions ADD COLUMN IF NOT EXISTS trace_id text;
ALTER TABLE missions ADD COLUMN IF NOT EXISTS request jsonb;
UPDATE missions SET trace_id = 'legacy-' || mission_id WHERE trace_id IS NULL;
UPDATE missions SET request = '{}'::jsonb WHERE request IS NULL;
-- Normalize the two pre-state-machine labels before enforcing the public graph.
UPDATE missions SET status = 'proposed' WHERE status = 'briefing';
UPDATE missions SET status = 'accepted' WHERE status = 'attestation_claimed';
ALTER TABLE missions ALTER COLUMN trace_id SET NOT NULL;
ALTER TABLE missions ALTER COLUMN request SET NOT NULL;
ALTER TABLE missions ALTER COLUMN status SET DEFAULT 'proposed';
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'missions_status_check'
    ) THEN
        ALTER TABLE missions ADD CONSTRAINT missions_status_check CHECK (
            status IN (
                'proposed', 'rejected', 'awaiting_attestation',
                'corrected', 'accepted', 'dispatched'
            )
        );
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS missions_owner_idx ON missions (owner_ref, created_at DESC);

-- Append-only proof of every deterministic state change and recovery/failure.
-- Evidence is structured and must never contain raw contact PII or model reasoning.
CREATE TABLE IF NOT EXISTS mission_events (
    sequence      bigserial PRIMARY KEY,
    event_id      text NOT NULL UNIQUE,
    mission_id    text NOT NULL REFERENCES missions(mission_id),
    from_status   text,
    to_status     text NOT NULL,
    event_type    text NOT NULL,
    reason_code   text NOT NULL,
    evidence      jsonb NOT NULL DEFAULT '{}'::jsonb,
    trace_id      text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS mission_events_timeline_idx
    ON mission_events (mission_id, sequence);
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'mission_events_status_check'
    ) THEN
        ALTER TABLE mission_events ADD CONSTRAINT mission_events_status_check CHECK (
            to_status IN (
                'proposed', 'rejected', 'awaiting_attestation',
                'corrected', 'accepted', 'dispatched'
            )
            AND (
                from_status IS NULL OR from_status IN (
                    'proposed', 'rejected', 'awaiting_attestation',
                    'corrected', 'accepted', 'dispatched'
                )
            )
        );
    END IF;
END $$;

-- One authenticated human attestation per mission. The retained hash binds the
-- attestation to its mission-scoped follower-room capability, never to contact PII.
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

-- Duplicate-safe follower-room ledger. The existing table name and terminal
-- mission status remain for migration compatibility. No raw capability is stored.
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
ALTER TABLE dispatch_receipts ADD COLUMN IF NOT EXISTS provider_reference text;
ALTER TABLE dispatch_receipts ADD COLUMN IF NOT EXISTS provider_status text;
ALTER TABLE dispatch_receipts ADD COLUMN IF NOT EXISTS recipient_redacted text;
ALTER TABLE dispatch_receipts ADD COLUMN IF NOT EXISTS handoff_expires_at timestamptz;
ALTER TABLE dispatch_receipts ADD COLUMN IF NOT EXISTS handoff_token_sha256 char(64);
CREATE UNIQUE INDEX IF NOT EXISTS dispatch_receipts_provider_reference_idx
    ON dispatch_receipts (provider_reference) WHERE provider_reference IS NOT NULL;

-- Owner-scoped, post-attestation acknowledgement memory. The destination embedding
-- is deliberately load-bearing: recall starts by resolving the current
-- destination in this vector space, then exact source digests decide whether a
-- NOTAM is unchanged. Model output never writes or mutates source text.
CREATE TABLE IF NOT EXISTS notam_acknowledgements (
    memory_id             text PRIMARY KEY,
    owner_ref             text NOT NULL,
    mission_id            text NOT NULL REFERENCES missions(mission_id),
    destination           text NOT NULL,
    destination_embedding vector(768) NOT NULL,
    notam_pk              text NOT NULL,
    raw_sha256            char(64) NOT NULL,
    end_valid             timestamptz,
    created_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (mission_id, notam_pk)
);
CREATE INDEX IF NOT EXISTS notam_ack_owner_idx
    ON notam_acknowledgements (owner_ref, created_at DESC);
CREATE INDEX IF NOT EXISTS notam_ack_destination_hnsw_idx
    ON notam_acknowledgements USING hnsw (destination_embedding vector_cosine_ops);
