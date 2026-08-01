-- Migration 002: `channel_accounts` — the multi-tenant router.
--
-- Every inbound message carries the ID of the account it was sent TO (for
-- Instagram that is `entry.id`, for Slack the channel ID). Looking that up here
-- is what tells the Lead Brain which studio it is acting as. Before this table,
-- `Normalize Lead` hardcoded `studio_id: 1` in all three of its branches.
--
-- There is deliberately NO default studio. A silent fallback to studio 1 is how
-- one client's leads end up in another client's calendar; an unrecognised
-- account must dead-letter and stop instead.
--
-- `credential_ref` names the n8n credential used for OUTBOUND sends on this
-- account (n8n credential IDs are opaque handles, not secrets — the secret
-- itself stays in n8n's encrypted credential store and never touches this DB).
-- n8n cannot select a credential by expression, so the adapter switches on this
-- value to pick a send branch. That is fine for the handful of studios this
-- managed service targets; past that, n8n's dynamic-credentials resolver
-- (`dynamic_credential_resolver` table, licensed feature) is the upgrade path.
--
-- ROLLBACK: DROP TABLE IF EXISTS channel_accounts;
--           DELETE FROM schema_migrations WHERE version = '002_channel_accounts';

CREATE TABLE IF NOT EXISTS channel_accounts (
    id             SERIAL PRIMARY KEY,
    studio_id      INT NOT NULL REFERENCES studios(id),
    channel        TEXT NOT NULL,          -- 'instagram' | 'slack'
    account_ref    TEXT NOT NULL,          -- IG account id (entry.id) / Slack channel id
    credential_ref TEXT,                   -- n8n credential id used for outbound
    active         BOOLEAN NOT NULL DEFAULT true,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT channel_accounts_channel_ref_uniq UNIQUE (channel, account_ref)
);

-- The router's hot path: resolve (channel, account_ref) -> studio on every inbound.
CREATE INDEX IF NOT EXISTS channel_accounts_lookup_idx
    ON channel_accounts (channel, account_ref) WHERE active;

CREATE INDEX IF NOT EXISTS channel_accounts_studio_idx
    ON channel_accounts (studio_id);

INSERT INTO schema_migrations (version) VALUES ('002_channel_accounts')
ON CONFLICT (version) DO NOTHING;
