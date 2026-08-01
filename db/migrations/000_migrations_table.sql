-- Migration 000: migration bookkeeping.
--
-- Every later migration ends by recording its version here, so `db/migrate.sh`
-- can be re-run safely and you can see what a given database has had applied.
--
-- ROLLBACK: DROP TABLE IF EXISTS schema_migrations;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO schema_migrations (version) VALUES ('000_migrations_table')
ON CONFLICT (version) DO NOTHING;
