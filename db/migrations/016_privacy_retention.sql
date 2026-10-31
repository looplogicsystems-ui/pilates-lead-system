-- Migration 016: data retention and "delete my data" (security floor §J, privacy).
--
-- The privacy page (Legal Pages workflow, /webhook/privacy) promises a lead can
-- DM "delete my data" or email us, and that her stored messages and
-- identifiers are removed within 30 days. This adds the two functions that do
-- it and a log that proves it happened:
--   forget_lead(lead_id, kind, via)  delete ONE lead and everything tied to her
--   apply_retention(days)            the daily job: leads idle longer than
--                                    `days` (default 180), leads of studios that
--                                    ended 30+ days ago, old dead letters, etc.
-- Policy and reasoning: vault `Company/Compliance & Privacy.md`.
--
-- ADDITIVE ONLY. Idempotent: safe to re-run. Nothing is deleted by applying it;
-- deletion happens only when the functions are called.
--
-- ROLLBACK:
--   DROP FUNCTION IF EXISTS apply_retention(int);
--   DROP FUNCTION IF EXISTS forget_lead(int, text, text);
--   DROP TABLE IF EXISTS data_deletions;
--   ALTER TABLE studios DROP COLUMN IF EXISTS ended_at;
--   DELETE FROM schema_migrations WHERE version = '016_privacy_retention';

-- What was deleted, when and why. Counts and ids only: never names, usernames
-- or message text (that would defeat the deletion).
CREATE TABLE IF NOT EXISTS data_deletions (
    id        BIGSERIAL PRIMARY KEY,
    at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind      TEXT NOT NULL,     -- 'request' | 'retention' | 'studio_ended' | 'retention_run'
    lead_id   INT,               -- the deleted lead's old id (the row itself is gone)
    studio_id INT,
    detail    JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS data_deletions_at_idx ON data_deletions (at);

-- Set when a studio's pilot or contract ends (with channel_accounts.active =
-- false). Its leads' data is deleted 30 days later by apply_retention().
ALTER TABLE studios ADD COLUMN IF NOT EXISTS ended_at TIMESTAMPTZ;

-- ---------------------------------------------------------------------------
-- Delete one lead and everything tied to her. FKs to leads don't cascade, so
-- children go first, in order. Returns what was deleted, as counts.
CREATE OR REPLACE FUNCTION forget_lead(p_lead_id INT, p_kind TEXT, p_via TEXT DEFAULT NULL)
RETURNS JSONB LANGUAGE plpgsql AS $$
DECLARE
    l   leads%ROWTYPE;
    n   JSONB := '{}'::jsonb;
    c   INT;
BEGIN
    SELECT * INTO l FROM leads WHERE id = p_lead_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('found', false);
    END IF;

    DELETE FROM link_hits WHERE short_code IN (SELECT short_code FROM booking_links
                                                WHERE lead_id = l.id AND short_code IS NOT NULL);
    GET DIAGNOSTICS c = ROW_COUNT; n := n || jsonb_build_object('link_hits', c);
    DELETE FROM booking_links WHERE lead_id = l.id;
    GET DIAGNOSTICS c = ROW_COUNT; n := n || jsonb_build_object('booking_links', c);
    DELETE FROM bookings WHERE lead_id = l.id;
    GET DIAGNOSTICS c = ROW_COUNT; n := n || jsonb_build_object('bookings', c);
    DELETE FROM escalations WHERE lead_id = l.id;
    GET DIAGNOSTICS c = ROW_COUNT; n := n || jsonb_build_object('escalations', c);
    DELETE FROM messages WHERE lead_id = l.id;
    GET DIAGNOSTICS c = ROW_COUNT; n := n || jsonb_build_object('messages', c);
    -- Legacy AI memory (no longer written; keyed "<channel>:<external id>").
    DELETE FROM n8n_chat_histories WHERE session_id = l.channel || ':' || l.external_id;
    GET DIAGNOSTICS c = ROW_COUNT; n := n || jsonb_build_object('chat_histories', c);
    -- Failed runs keep the message they were handling. Instagram ids are long
    -- numbers, so this match can't hit an unrelated row by accident.
    IF length(l.external_id) >= 8 THEN
        DELETE FROM dead_letters WHERE payload::text LIKE '%' || l.external_id || '%';
        GET DIAGNOSTICS c = ROW_COUNT; n := n || jsonb_build_object('dead_letters', c);
    END IF;
    IF to_regclass('public.phantom_nudges_backup_20261030') IS NOT NULL THEN
        EXECUTE 'DELETE FROM phantom_nudges_backup_20261030 WHERE lead_id = $1' USING l.id;
    END IF;
    DELETE FROM leads WHERE id = l.id;

    INSERT INTO data_deletions (kind, lead_id, studio_id, detail)
    VALUES (p_kind, l.id, l.studio_id,
            n || jsonb_build_object('channel', l.channel, 'via', p_via,
                                    'lead_created', l.created_at::date, 'last_active', l.updated_at::date));
    RETURN n || jsonb_build_object('found', true, 'lead_id', l.id, 'studio_id', l.studio_id);
END $$;

-- ---------------------------------------------------------------------------
-- The daily job (Data Retention workflow). Always logs one 'retention_run' row,
-- even when nothing was due, so it's visible that it ran.
CREATE OR REPLACE FUNCTION apply_retention(p_days INT DEFAULT 180)
RETURNS JSONB LANGUAGE plpgsql AS $$
DECLARE
    r          RECORD;
    idle_leads INT := 0;
    ended      INT := 0;
    c          INT;
    s          JSONB := '{}'::jsonb;
BEGIN
    IF p_days < 30 THEN RAISE EXCEPTION 'retention under 30 days refused (got %)', p_days; END IF;

    -- Leads with no activity for p_days (their last message or last update).
    FOR r IN
        SELECT l.id FROM leads l
         WHERE GREATEST(l.updated_at, l.created_at,
                        COALESCE((SELECT max(m.created_at) FROM messages m WHERE m.lead_id = l.id), l.created_at))
               < now() - make_interval(days => p_days)
    LOOP
        PERFORM forget_lead(r.id, 'retention', 'daily job');
        idle_leads := idle_leads + 1;
    END LOOP;

    -- Every lead of a studio that ended more than 30 days ago.
    FOR r IN
        SELECT l.id FROM leads l JOIN studios st ON st.id = l.studio_id
         WHERE st.ended_at IS NOT NULL AND st.ended_at < now() - interval '30 days'
    LOOP
        PERFORM forget_lead(r.id, 'studio_ended', 'daily job');
        ended := ended + 1;
    END LOOP;

    DELETE FROM dead_letters WHERE created_at < now() - interval '90 days';
    GET DIAGNOSTICS c = ROW_COUNT; s := s || jsonb_build_object('old_dead_letters', c);
    DELETE FROM processed_messages WHERE processed_at < now() - interval '30 days';
    GET DIAGNOSTICS c = ROW_COUNT; s := s || jsonb_build_object('old_processed_ids', c);
    DELETE FROM link_hits WHERE at < now() - make_interval(days => p_days);
    GET DIAGNOSTICS c = ROW_COUNT; s := s || jsonb_build_object('old_link_hits', c);
    -- Legacy AI memory whose lead no longer exists.
    DELETE FROM n8n_chat_histories h
     WHERE NOT EXISTS (SELECT 1 FROM leads l WHERE h.session_id = l.channel || ':' || l.external_id);
    GET DIAGNOSTICS c = ROW_COUNT; s := s || jsonb_build_object('orphan_chat_histories', c);

    s := s || jsonb_build_object('days', p_days, 'idle_leads_deleted', idle_leads,
                                 'ended_studio_leads_deleted', ended);
    INSERT INTO data_deletions (kind, detail) VALUES ('retention_run', s);
    RETURN s;
END $$;

INSERT INTO schema_migrations (version) VALUES ('016_privacy_retention')
ON CONFLICT (version) DO NOTHING;
