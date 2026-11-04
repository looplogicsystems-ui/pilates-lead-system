-- Migration 017: a record of every offsite backup run (security floor §J).
--
-- bin/backup-offsite (nightly cron on the mini) writes one row per run, OK or
-- not. The `Monitor: Backups` workflow reads it and emails the founders when
-- the last good backup is more than 26 hours old or the last run failed,
-- because a backup that quietly stops is the failure that matters.
--
-- ADDITIVE ONLY. Idempotent.
--
-- ROLLBACK:
--   DROP TABLE IF EXISTS backup_runs;
--   DELETE FROM schema_migrations WHERE version = '017_backup_runs';

CREATE TABLE IF NOT EXISTS backup_runs (
    id       BIGSERIAL PRIMARY KEY,
    at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    ok       BOOLEAN NOT NULL,
    snapshot TEXT,              -- restic snapshot id (short)
    bytes    BIGINT,            -- size of the database dump that was backed up
    error    TEXT               -- why it failed (never contains secrets)
);
CREATE INDEX IF NOT EXISTS backup_runs_at_idx ON backup_runs (at);

INSERT INTO schema_migrations (version) VALUES ('017_backup_runs')
ON CONFLICT (version) DO NOTHING;
