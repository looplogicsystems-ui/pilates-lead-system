-- Migration 004: lead status model + human-handoff flag.
--
-- Two live bugs this unblocks:
--
-- 1. Escalating a lead left `status = 'engaged'`, and `Find Stale Leads` selects
--    on exactly that. So a lead who asked "is reformer safe with a herniated
--    disc?" — deliberately routed to a human — got a cheerful automated nudge
--    an hour later. Tone problem and a liability problem. Needs 'escalated'.
--
-- 2. There was no way for the owner to take over a thread. `ai_paused` is that
--    switch: inbound on a paused lead skips the AI entirely and notifies the
--    owner instead.
--
-- The CHECK constraint is added NOT VALID first and validated separately, so
-- the migration cannot fail on pre-existing rows and takes no long table lock.
--
-- ROLLBACK:
--   ALTER TABLE leads DROP CONSTRAINT IF EXISTS leads_status_allowed;
--   ALTER TABLE leads DROP COLUMN IF EXISTS ai_paused;
--   DROP INDEX IF EXISTS leads_followup_scan_idx;
--   DELETE FROM schema_migrations WHERE version = '004_leads_lifecycle';

ALTER TABLE leads ADD COLUMN IF NOT EXISTS ai_paused BOOLEAN NOT NULL DEFAULT false;

-- Normalise anything unexpected before constraining (no-op on a clean DB).
UPDATE leads
   SET status = 'engaged'
 WHERE status NOT IN ('new', 'engaged', 'booked', 'cold', 'escalated', 'paused');

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'leads_status_allowed') THEN
        ALTER TABLE leads ADD CONSTRAINT leads_status_allowed
            CHECK (status IN ('new', 'engaged', 'booked', 'cold', 'escalated', 'paused'))
            NOT VALID;
        ALTER TABLE leads VALIDATE CONSTRAINT leads_status_allowed;
    END IF;
END $$;

-- Find Stale Leads scans on status and excludes paused leads every 15 minutes.
CREATE INDEX IF NOT EXISTS leads_followup_scan_idx
    ON leads (status, updated_at) WHERE NOT ai_paused;

INSERT INTO schema_migrations (version) VALUES ('004_leads_lifecycle')
ON CONFLICT (version) DO NOTHING;
