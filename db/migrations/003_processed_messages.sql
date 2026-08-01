-- Migration 003: `processed_messages` — durable idempotency.
--
-- Replaces the in-memory `Dedupe IG` Code node, which kept a rolling array of
-- the last 500 message IDs in `$getWorkflowStaticData('global')`. That array is
-- lost on every n8n restart and is not shared between workers, so a Meta retry
-- arriving after a restart produced a SECOND reply to the lead. Meta retries
-- aggressively, and n8n on Docker Desktop restarts often.
--
-- USAGE — write first, then check. The adapter must do:
--
--   INSERT INTO processed_messages (provider, provider_msg_id)
--   VALUES ($1, $2)
--   ON CONFLICT (provider, provider_msg_id) DO NOTHING
--   RETURNING 1 AS is_new;
--
-- and stop when nothing is returned. A read-then-write ("have I seen this?"
-- followed by "record it") has a race window between the two statements that
-- two concurrent deliveries of the same message slip straight through; letting
-- the primary key be the arbiter closes it atomically.
--
-- ROLLBACK: DROP TABLE IF EXISTS processed_messages;
--           DELETE FROM schema_migrations WHERE version = '003_processed_messages';

CREATE TABLE IF NOT EXISTS processed_messages (
    provider        TEXT NOT NULL,         -- 'instagram' | 'slack'
    provider_msg_id TEXT NOT NULL,         -- IG `mid` / Slack `ts`
    processed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (provider, provider_msg_id)
);

-- Supports pruning old rows (this table only ever grows otherwise).
CREATE INDEX IF NOT EXISTS processed_messages_processed_at_idx
    ON processed_messages (processed_at);

INSERT INTO schema_migrations (version) VALUES ('003_processed_messages')
ON CONFLICT (version) DO NOTHING;
