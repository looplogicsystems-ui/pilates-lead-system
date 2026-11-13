-- Migration 019: drop the last Slack columns.
--
-- Slack was removed entirely on 2026-11-08 (`248e97a`, vault decision
-- "2026-11-08 Slack Removed"): the adapter, the alerts, the credentials and the
-- Slack channel_accounts row are gone. These two columns (added in 001) were
-- left behind and are read by nothing: no workflow, function, view or script.
-- They only held old Slack channel ids for studios 1 and 2 (not secrets).
--
-- Idempotent. Not additive: the two columns and their values are dropped.
--
-- ROLLBACK (the old values are in any backup taken before this migration):
--   ALTER TABLE studios ADD COLUMN IF NOT EXISTS slack_owner_channel_id TEXT,
--                       ADD COLUMN IF NOT EXISTS slack_lead_channel_id  TEXT;
--   DELETE FROM schema_migrations WHERE version = '019_drop_slack_columns';

ALTER TABLE studios DROP COLUMN IF EXISTS slack_owner_channel_id,
                    DROP COLUMN IF EXISTS slack_lead_channel_id;

INSERT INTO schema_migrations (version) VALUES ('019_drop_slack_columns')
ON CONFLICT (version) DO NOTHING;
