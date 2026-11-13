-- Migration 018: at most ONE active Instagram account per studio, enforced by
-- the database itself.
--
-- Follow-up nudges are sent with the studio's (single) active Instagram account
-- (Follow-up Scheduler [Claim Due Leads]). Since 2026-11-08 IG Connect's
-- [Save Account] refuses a second active account (outcome studio_has_account),
-- but a hand-written UPDATE/INSERT (Beekeeper, psql, a seed script) could still
-- leave a studio with two, and nudges would then quietly fail. This index makes
-- that impossible from any direction.
--
-- Only channel = 'instagram' counts: a studio may also have its 'test' row
-- (the voice lab, migration 011) active at the same time.
-- To switch a studio's account: set the old row inactive first, then connect
-- the new one (Onboarding Phase 2 in the vault).
--
-- ADDITIVE ONLY. Idempotent. Fails (and changes nothing) if a studio already
-- has two active Instagram accounts: fix the data first.
--
-- ROLLBACK:
--   DROP INDEX IF EXISTS channel_accounts_one_active_ig_per_studio;
--   DELETE FROM schema_migrations WHERE version = '018_one_instagram_account_per_studio';

CREATE UNIQUE INDEX IF NOT EXISTS channel_accounts_one_active_ig_per_studio
    ON channel_accounts (studio_id)
 WHERE active AND channel = 'instagram';

INSERT INTO schema_migrations (version) VALUES ('018_one_instagram_account_per_studio')
ON CONFLICT (version) DO NOTHING;
