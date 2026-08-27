\set ON_ERROR_STOP on

\ir schema.sql

-- Waterline owns its explicit application tables through the admin-run schema,
-- while ADK's DatabaseSessionService initializes its own durable session tables
-- at first use. CREATE is limited to this database's public schema.
GRANT USAGE, CREATE ON SCHEMA public TO :"app_user";
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public TO :"app_user";
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO :"app_user";
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
