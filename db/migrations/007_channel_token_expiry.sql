-- Migration 007: track token expiry per channel account.
--
-- `IG Token Expiry Monitor` had the issue date typed into a Code node as
-- TOKEN_ISSUED = '2026-07-22'. That works for exactly one account, has to be
-- hand-edited after every reconnect, and silently goes stale if someone forgets.
-- Moving it next to the account it describes lets the monitor iterate every
-- active Instagram account and alert per studio.
--
-- Instagram long-lived user tokens last ~60 days; the default reflects that.
-- `token_issued_at` defaults to now() for existing rows so the monitor has a
-- sane starting point rather than immediately alarming.
--
-- ROLLBACK:
--   ALTER TABLE channel_accounts
--     DROP COLUMN token_issued_at, DROP COLUMN token_lifetime_days,
--     DROP COLUMN alert_within_days;
--   DELETE FROM schema_migrations WHERE version = '007_channel_token_expiry';

ALTER TABLE channel_accounts ADD COLUMN IF NOT EXISTS token_issued_at TIMESTAMPTZ;
ALTER TABLE channel_accounts ADD COLUMN IF NOT EXISTS token_lifetime_days INT NOT NULL DEFAULT 60;
ALTER TABLE channel_accounts ADD COLUMN IF NOT EXISTS alert_within_days INT NOT NULL DEFAULT 12;

INSERT INTO schema_migrations (version) VALUES ('007_channel_token_expiry')
ON CONFLICT (version) DO NOTHING;
