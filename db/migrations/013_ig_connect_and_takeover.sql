-- Migration 013: Instagram connect links, encrypted tokens with automatic
-- refresh, staff takeover (auto-pause), and the lead's Instagram username.
--
-- Spec: vault `Resources/Final System Changes.md` §C2–C5 and §D; pilot build
-- step 6. Tokens themselves are encrypted in n8n (AES-256-GCM, IG_TOKEN_KEY)
-- before they reach this database — see workflows/build/ig_common.py.
--
-- ADDITIVE ONLY. Idempotent: safe to re-run.
--
-- ROLLBACK:
--   DROP TABLE IF EXISTS ig_connect_uses;
--   ALTER TABLE channel_accounts DROP COLUMN IF EXISTS token_expires_at,
--     DROP COLUMN IF EXISTS token_error;
--   ALTER TABLE leads DROP CONSTRAINT IF EXISTS leads_paused_by_allowed,
--     DROP COLUMN IF EXISTS username, DROP COLUMN IF EXISTS profile_checked_at,
--     DROP COLUMN IF EXISTS paused_by, DROP COLUMN IF EXISTS paused_at,
--     DROP COLUMN IF EXISTS pause_alerted_at;
--   DELETE FROM schema_migrations WHERE version = '013_ig_connect_and_takeover';

-- ---------------------------------------------------------------------------
-- 1. Token lifetime from Instagram itself (expires_in), and the last problem
--    with this account's token (NULL = healthy). The daily refresh job keeps
--    token_expires_at ~60 days out; token_issued_at = when it was last renewed.
ALTER TABLE channel_accounts ADD COLUMN IF NOT EXISTS token_expires_at TIMESTAMPTZ;
ALTER TABLE channel_accounts ADD COLUMN IF NOT EXISTS token_error      TEXT;

-- ---------------------------------------------------------------------------
-- 2. Who the lead is. Instagram's webhook carries only a numeric id; the
--    adapter looks the profile up once and stores it here, so owner emails
--    can say "@jess.moves (Jess Carter)" instead of "An Instagram lead".
ALTER TABLE leads ADD COLUMN IF NOT EXISTS username           TEXT;         -- without the @
ALTER TABLE leads ADD COLUMN IF NOT EXISTS profile_checked_at TIMESTAMPTZ;  -- last lookup attempt (retry at most weekly)

-- 3. Why and since when the AI is paused for a lead, and when the owner was
--    last emailed about it (so a paused conversation sends at most one
--    "she wrote again" email per 12 hours, not one per message).
ALTER TABLE leads ADD COLUMN IF NOT EXISTS paused_by        TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS paused_at        TIMESTAMPTZ;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS pause_alerted_at TIMESTAMPTZ;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'leads_paused_by_allowed') THEN
        ALTER TABLE leads ADD CONSTRAINT leads_paused_by_allowed
            CHECK (paused_by IS NULL OR paused_by IN ('owner', 'escalation', 'staff_reply'));
    END IF;
END $$;
-- Backfill leads paused before this migration.
UPDATE leads SET paused_by = CASE WHEN status = 'escalated' THEN 'escalation' ELSE 'owner' END,
                 paused_at = updated_at,
                 pause_alerted_at = updated_at
 WHERE ai_paused AND paused_by IS NULL;

-- ---------------------------------------------------------------------------
-- 4. Connect links are single-use: the link's random nonce is recorded when an
--    account is connected with it. Also the audit trail of every connection.
CREATE TABLE IF NOT EXISTS ig_connect_uses (
    nonce       TEXT PRIMARY KEY,
    studio_id   INT NOT NULL REFERENCES studios(id),
    account_ref TEXT NOT NULL,          -- Instagram user_id the link connected
    ig_username TEXT,
    used_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO schema_migrations (version) VALUES ('013_ig_connect_and_takeover')
ON CONFLICT (version) DO NOTHING;
