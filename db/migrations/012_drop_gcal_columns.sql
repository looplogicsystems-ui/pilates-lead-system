-- Migration 012: drop the Google Calendar ids from studios.
--
-- Booking is by link into the studio's own software (vault Decisions
-- 2026-10-14); Google Calendar was removed from the live system on 2026-10-25
-- and its n8n credential deleted. Nothing reads these columns any more.
-- (bookings, n8n_chat_histories and the old Core Loop workflows are left as
-- history.)
--
-- ROLLBACK: ALTER TABLE studios ADD COLUMN gcal_schedule_id TEXT, ADD COLUMN gcal_booking_id TEXT;
--           (values restorable from the llctl snapshot taken before this)
--           DELETE FROM schema_migrations WHERE version = '012_drop_gcal_columns';

ALTER TABLE studios DROP COLUMN IF EXISTS gcal_schedule_id;
ALTER TABLE studios DROP COLUMN IF EXISTS gcal_booking_id;

INSERT INTO schema_migrations (version) VALUES ('012_drop_gcal_columns')
ON CONFLICT (version) DO NOTHING;
