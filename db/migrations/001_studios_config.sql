-- Migration 001: give `studios` real per-studio configuration.
--
-- Replaces values that were hardcoded in the workflow JSON: the `Asia/Karachi`
-- timezone (in 4 places), the Slack channel IDs, the Google Calendar ID, the
-- `CAP = 8` class capacity in Format Schedule, the follow-up intervals baked
-- into Find Stale Leads' SQL, and the studio persona literals in the two AI
-- system prompts.
--
-- DESIGN NOTE — typed columns vs one JSONB blob:
-- Split, deliberately. Anything the system *filters, joins or computes on*
-- (timezone, capacity, channel IDs, follow-up intervals) is a typed column:
-- it can be indexed and constrained, and a typo fails loudly at the database
-- instead of silently resolving to NULL halfway through a live conversation.
-- Anything that is only ever interpolated into a prompt string (persona name,
-- voice, location blurb, offer copy) goes in `prompt_vars` JSONB, where adding
-- a field costs no migration and no workflow change.
--
-- ROLLBACK:
--   ALTER TABLE studios
--     DROP COLUMN timezone, DROP COLUMN slack_owner_channel_id,
--     DROP COLUMN slack_lead_channel_id, DROP COLUMN gcal_schedule_id,
--     DROP COLUMN gcal_booking_id, DROP COLUMN class_capacity,
--     DROP COLUMN booking_duration_minutes, DROP COLUMN prompt_vars,
--     DROP COLUMN followup_first_after_minutes,
--     DROP COLUMN followup_final_after_minutes, DROP COLUMN max_nudges,
--     DROP COLUMN active;
--   DELETE FROM schema_migrations WHERE version = '001_studios_config';

ALTER TABLE studios ADD COLUMN IF NOT EXISTS timezone TEXT NOT NULL DEFAULT 'UTC';
ALTER TABLE studios ADD COLUMN IF NOT EXISTS slack_owner_channel_id TEXT;
ALTER TABLE studios ADD COLUMN IF NOT EXISTS slack_lead_channel_id  TEXT;
ALTER TABLE studios ADD COLUMN IF NOT EXISTS gcal_schedule_id TEXT;
ALTER TABLE studios ADD COLUMN IF NOT EXISTS gcal_booking_id  TEXT;
ALTER TABLE studios ADD COLUMN IF NOT EXISTS class_capacity INT NOT NULL DEFAULT 8;
ALTER TABLE studios ADD COLUMN IF NOT EXISTS booking_duration_minutes INT NOT NULL DEFAULT 50;
ALTER TABLE studios ADD COLUMN IF NOT EXISTS prompt_vars JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Follow-up cadence as config rather than magic numbers in Find Stale Leads.
-- Values below reproduce today's production behaviour exactly (1h then 24h,
-- 2 nudges then cold). Changing the cadence is now an UPDATE, not a workflow edit.
ALTER TABLE studios ADD COLUMN IF NOT EXISTS followup_first_after_minutes INT NOT NULL DEFAULT 60;
ALTER TABLE studios ADD COLUMN IF NOT EXISTS followup_final_after_minutes INT NOT NULL DEFAULT 1440;
ALTER TABLE studios ADD COLUMN IF NOT EXISTS max_nudges INT NOT NULL DEFAULT 2;

ALTER TABLE studios ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT true;

-- Guard rails: a zero/negative capacity would mark every class FULL forever,
-- and a bad timezone silently shifts every booking.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'studios_class_capacity_positive') THEN
        ALTER TABLE studios ADD CONSTRAINT studios_class_capacity_positive
            CHECK (class_capacity > 0);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'studios_booking_duration_positive') THEN
        ALTER TABLE studios ADD CONSTRAINT studios_booking_duration_positive
            CHECK (booking_duration_minutes > 0);
    END IF;
END $$;

INSERT INTO schema_migrations (version) VALUES ('001_studios_config')
ON CONFLICT (version) DO NOTHING;
