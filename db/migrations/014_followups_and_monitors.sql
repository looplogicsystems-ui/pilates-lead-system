-- Migration 014: follow-up nudges, the unanswered-DM alarm, and the daily
-- booking-link check (pilot build step 7; vault Final System Changes §G, §I).
--
-- ADDITIVE ONLY. Idempotent: safe to re-run.
--
-- ROLLBACK:
--   DROP INDEX IF EXISTS messages_unanswered_idx;
--   ALTER TABLE messages DROP COLUMN IF EXISTS unanswered_alerted_at, DROP COLUMN IF EXISTS nudge;
--   ALTER TABLE leads DROP COLUMN IF EXISTS nudge_claimed_at;
--   ALTER TABLE studios DROP COLUMN IF EXISTS link_problem, DROP COLUMN IF EXISTS links_checked_at;
--   (the demo studio's followup_final_after_minutes was 1440 before)
--   DELETE FROM schema_migrations WHERE version = '014_followups_and_monitors';

-- ---------------------------------------------------------------------------
-- 1. Unanswered-DM alarm: an inbound message with no reply after 5 minutes
--    emails the founders ONCE; this records that it did.
ALTER TABLE messages ADD COLUMN IF NOT EXISTS unanswered_alerted_at TIMESTAMPTZ;
-- Baseline: everything already in the table counts as handled, so switching
-- the alarm on doesn't email about old history. (Only rows from before this
-- migration; it's a no-op on a re-run because they're already set.)
UPDATE messages SET unanswered_alerted_at = created_at
 WHERE direction = 'inbound' AND unanswered_alerted_at IS NULL
   AND created_at < now() - interval '10 minutes';
CREATE INDEX IF NOT EXISTS messages_unanswered_idx ON messages (created_at)
    WHERE direction = 'inbound' AND unanswered_alerted_at IS NULL;

-- 2. Which outbound messages were automatic nudges (not replies), so nudges
--    are counted exactly and the weekly review can report them.
ALTER TABLE messages ADD COLUMN IF NOT EXISTS nudge BOOLEAN NOT NULL DEFAULT false;

-- Claimed by a scheduler run just before nudging. Two overlapping runs can't
-- both nudge the same lead: a claim younger than 30 minutes is skipped.
ALTER TABLE leads ADD COLUMN IF NOT EXISTS nudge_claimed_at TIMESTAMPTZ;

-- ---------------------------------------------------------------------------
-- 3. Daily booking-link check. links_ok (008) is what Lead Brain obeys; these
--    say what was found and when, so an email goes out only when it CHANGES.
ALTER TABLE studios ADD COLUMN IF NOT EXISTS link_problem     TEXT;         -- NULL = all links fine
ALTER TABLE studios ADD COLUMN IF NOT EXISTS links_checked_at TIMESTAMPTZ;

-- ---------------------------------------------------------------------------
-- 4. Demo studio cadence. Instagram only allows a business to message someone
--    within 24h of THEIR last message. The final nudge was set for 24h after
--    the first one, i.e. always outside that window, so it could never be
--    sent. 20h keeps it inside. Only changes the old default value.
UPDATE studios SET followup_final_after_minutes = 1200
 WHERE id = 2 AND followup_final_after_minutes = 1440;

INSERT INTO schema_migrations (version) VALUES ('014_followups_and_monitors')
ON CONFLICT (version) DO NOTHING;
