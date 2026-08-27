\set ON_ERROR_STOP on

\ir schema.sql

-- Waterline owns its explicit application tables through the admin-run schema,
-- while ADK's DatabaseSessionService initializes its own durable session tables
-- at first use. CREATE is limited to this database's public schema.
GRANT USAGE, CREATE ON SCHEMA public TO :"app_user";
-- Keep the grant boundary on Waterline-owned domain state. ADK creates and owns
-- its durable session tables as the application role, so a blanket public-schema
-- grant makes a repeat migration fail when the Cloud SQL admin encounters those
-- separately owned tables.
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLE
  notams,
  stations,
  missions,
  mission_events,
  pilot_attestations,
  ingests,
  dispatch_receipts
TO :"app_user";
GRANT USAGE, SELECT, UPDATE ON SEQUENCE
  mission_events_sequence_seq,
  ingests_id_seq
TO :"app_user";
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLES TO :"app_user";
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO :"app_user";

SELECT postgis_full_version();
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN (
    'notams', 'stations', 'missions', 'mission_events', 'pilot_attestations',
    'ingests', 'dispatch_receipts'
  )
ORDER BY table_name;
