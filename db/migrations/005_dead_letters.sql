-- Migration 005: `dead_letters` — failures that survive a restart.
--
-- The error handler workflow (`Lead System — Error Handler`, id 4L4K72aSSwiBcnjX)
-- is two nodes: an Error Trigger and a Slack post. If Slack is down, or the
-- alert is simply missed in a busy channel, the failed lead is gone — there is
-- no record anywhere of which lead was dropped or what the payload was. That
-- makes the "no lead is ever silently dropped" claim untrue in the one case
-- where it matters.
--
-- Rows here are the queue of things a human still owes a reply to. `resolved_at`
-- is set by hand (or by a future replay workflow) once dealt with.
--
-- Also written to by the studio router: an inbound message on an account with
-- no `channel_accounts` row lands here rather than being silently guessed at.
--
-- ROLLBACK: DROP TABLE IF EXISTS dead_letters;
--           DELETE FROM schema_migrations WHERE version = '005_dead_letters';

CREATE TABLE IF NOT EXISTS dead_letters (
    id           SERIAL PRIMARY KEY,
    workflow_id  TEXT,
    execution_id TEXT,
    reason       TEXT,                     -- 'execution_error' | 'unknown_account' | ...
    payload      JSONB,
    error        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at  TIMESTAMPTZ
);

-- "What is still outstanding?" — the only query that runs against this often.
CREATE INDEX IF NOT EXISTS dead_letters_unresolved_idx
    ON dead_letters (created_at) WHERE resolved_at IS NULL;

INSERT INTO schema_migrations (version) VALUES ('005_dead_letters')
ON CONFLICT (version) DO NOTHING;
