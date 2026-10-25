-- Migration 008: the pilot build — booking by link, studio timetables, tracked
-- links, encrypted per-studio Instagram tokens, and message authorship.
--
-- Spec: vault `Resources/Final System Changes.md` §B. Decision: booking is by a
-- link into the studio's own software (Mindbody, Momence, ...); Google Calendar
-- is being removed (vault Decisions, 2026-10-14).
--
-- ADDITIVE ONLY. Every existing studio keeps `availability_source = 'gcal'`, so
-- Lead Brain's current path is untouched until a studio is explicitly flipped to
-- 'timetable'. Safe to apply to production before any workflow changes.
-- Idempotent: safe to re-run.
--
-- ROLLBACK (destroys pilot data — snapshot first):
--   DROP TABLE IF EXISTS booking_links, studio_classes;
--   ALTER TABLE studios DROP CONSTRAINT IF EXISTS studios_timetable_needs_links,
--     DROP CONSTRAINT IF EXISTS studios_availability_source_allowed,
--     DROP COLUMN IF EXISTS availability_source, DROP COLUMN IF EXISTS booking_software,
--     DROP COLUMN IF EXISTS booking_url, DROP COLUMN IF EXISTS new_client_url,
--     DROP COLUMN IF EXISTS owner_name, DROP COLUMN IF EXISTS owner_alert_email,
--     DROP COLUMN IF EXISTS staffed_hours, DROP COLUMN IF EXISTS links_ok;
--   ALTER TABLE messages DROP COLUMN IF EXISTS provider_message_id, DROP COLUMN IF EXISTS author;
--   ALTER TABLE channel_accounts DROP COLUMN IF EXISTS access_token_enc,
--     DROP COLUMN IF EXISTS ig_username, DROP COLUMN IF EXISTS connected_at;
--   ALTER TABLE leads DROP CONSTRAINT leads_status_allowed;
--   ALTER TABLE leads ADD CONSTRAINT leads_status_allowed
--     CHECK (status IN ('new','engaged','booked','cold','escalated','paused'));
--   DELETE FROM schema_migrations WHERE version = '008_pilot';
--   (pgcrypto is left installed; it is harmless.)

-- Token encryption (pgp_sym_encrypt / pgp_sym_decrypt) and random short codes.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- The studio's weekly timetable. Used ONLY to recommend a real class and time;
-- live availability is in her own booking software, which the AI never claims
-- to know. Times are LOCAL wall-clock times in studios.timezone, so a class at
-- 18:00 stays at 18:00 across a clock change.
CREATE TABLE IF NOT EXISTS studio_classes (
    id               SERIAL PRIMARY KEY,
    studio_id        INT NOT NULL REFERENCES studios(id),
    name             TEXT NOT NULL,                 -- 'Beginner Reformer'
    weekday          SMALLINT NOT NULL,             -- ISO / Luxon: 1 = Monday ... 7 = Sunday
    start_local      TIME NOT NULL,                 -- 18:00
    duration_minutes INT NOT NULL DEFAULT 50,
    level            TEXT,                          -- 'beginner-friendly', 'all levels', ...
    booking_url      TEXT,                          -- only if her software has a STABLE per-class link
    active           BOOLEAN NOT NULL DEFAULT true,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS studio_classes_studio_idx ON studio_classes (studio_id) WHERE active;

-- ---------------------------------------------------------------------------
-- Studio booking config. Typed columns (not prompt_vars) because code branches
-- on them — migration 001's rule. Prompt-only text (booking_steps,
-- first_visit_tips, escalation_line, ai_disclosure) goes in prompt_vars.
ALTER TABLE studios ADD COLUMN IF NOT EXISTS availability_source TEXT NOT NULL DEFAULT 'gcal';
ALTER TABLE studios ADD COLUMN IF NOT EXISTS booking_software  TEXT;     -- 'Momence', 'Mindbody', ...
ALTER TABLE studios ADD COLUMN IF NOT EXISTS booking_url       TEXT;     -- main schedule page (returning clients, fallback)
ALTER TABLE studios ADD COLUMN IF NOT EXISTS new_client_url    TEXT;     -- intro-offer page (what a NEW lead actually needs)
ALTER TABLE studios ADD COLUMN IF NOT EXISTS owner_name        TEXT;     -- 'Sarah' — "Sarah will send it to you"
ALTER TABLE studios ADD COLUMN IF NOT EXISTS owner_alert_email TEXT;
ALTER TABLE studios ADD COLUMN IF NOT EXISTS staffed_hours     JSONB;    -- {"mon":["07:00","19:00"], ...} for the after-hours report line
ALTER TABLE studios ADD COLUMN IF NOT EXISTS links_ok          BOOLEAN NOT NULL DEFAULT true;  -- false = daily check found a broken link

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'studios_availability_source_allowed') THEN
        ALTER TABLE studios ADD CONSTRAINT studios_availability_source_allowed
            CHECK (availability_source IN ('gcal', 'timetable'));
    END IF;
    -- A studio on booking-by-link with no link to send, or nobody to alert, is
    -- a pilot that silently doesn't work. Fail at the database instead.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'studios_timetable_needs_links') THEN
        ALTER TABLE studios ADD CONSTRAINT studios_timetable_needs_links
            CHECK (availability_source <> 'timetable'
                   OR (booking_url IS NOT NULL AND owner_alert_email IS NOT NULL));
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- Every booking link the AI sends. The URL the lead sees is
-- https://go.looplogicsystems.com/<short_code>; the redirect records the click.
-- short_code is RANDOM (it maps to a person), never sequential.
CREATE TABLE IF NOT EXISTS booking_links (
    id                  SERIAL PRIMARY KEY,
    lead_id             INT NOT NULL REFERENCES leads(id),
    studio_id           INT NOT NULL REFERENCES studios(id),
    class_id            INT REFERENCES studio_classes(id),   -- the class recommended, if any
    kind                TEXT NOT NULL CHECK (kind IN ('new_client', 'main', 'class')),
    target_url          TEXT NOT NULL,
    short_code          TEXT NOT NULL UNIQUE,
    sent_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    clicked_at          TIMESTAMPTZ,                         -- first real click (preview crawlers excluded)
    click_count         INT NOT NULL DEFAULT 0,
    lead_said_booked_at TIMESTAMPTZ,
    owner_confirmed_at  TIMESTAMPTZ,
    nudged_at           TIMESTAMPTZ                          -- the one ~3h follow-up
);
CREATE INDEX IF NOT EXISTS booking_links_lead_idx ON booking_links (lead_id, sent_at DESC);
-- The follow-up scan: links with no click, no "booked", no nudge yet.
CREATE INDEX IF NOT EXISTS booking_links_nudge_idx ON booking_links (sent_at)
    WHERE clicked_at IS NULL AND lead_said_booked_at IS NULL AND nudged_at IS NULL;

-- ---------------------------------------------------------------------------
-- Messages: who wrote each one, and Instagram's id for our own sends.
-- provider_message_id lets the adapter recognise the echo of OUR reply; any
-- other echo means a human replied from the Instagram app -> pause the AI.
ALTER TABLE messages ADD COLUMN IF NOT EXISTS provider_message_id TEXT;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS author TEXT;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'messages_author_allowed') THEN
        ALTER TABLE messages ADD CONSTRAINT messages_author_allowed
            CHECK (author IS NULL OR author IN ('lead', 'ai', 'human'));
    END IF;
END $$;
-- Backfill: before this migration nothing captured human replies, so every
-- inbound was the lead and every outbound was the AI.
UPDATE messages SET author = CASE direction WHEN 'inbound' THEN 'lead' ELSE 'ai' END
 WHERE author IS NULL;
CREATE INDEX IF NOT EXISTS messages_provider_mid_idx ON messages (provider_message_id)
    WHERE provider_message_id IS NOT NULL;
-- Recent-thread lookups ("last N messages for this lead") and the unanswered-DM check.
CREATE INDEX IF NOT EXISTS messages_lead_created_idx ON messages (lead_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- Per-studio Instagram tokens, encrypted at rest with IG_TOKEN_KEY (in the
-- mini's .env and Bitwarden, never in this database). Same model as n8n's own
-- credential store. A restored backup is useless without that key.
ALTER TABLE channel_accounts ADD COLUMN IF NOT EXISTS access_token_enc BYTEA;
ALTER TABLE channel_accounts ADD COLUMN IF NOT EXISTS ig_username      TEXT;
ALTER TABLE channel_accounts ADD COLUMN IF NOT EXISTS connected_at     TIMESTAMPTZ;

-- ---------------------------------------------------------------------------
-- Lead lifecycle gains 'link_sent': the AI sent a booking link and we're
-- waiting to see whether she books. Same NOT VALID -> VALIDATE pattern as 004,
-- so it never fails on existing rows and takes no long lock.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'leads_status_allowed'
           AND pg_get_constraintdef(oid) LIKE '%link_sent%'
    ) THEN
        ALTER TABLE leads DROP CONSTRAINT IF EXISTS leads_status_allowed;
        ALTER TABLE leads ADD CONSTRAINT leads_status_allowed
            CHECK (status IN ('new', 'engaged', 'link_sent', 'booked', 'cold', 'escalated', 'paused'))
            NOT VALID;
        ALTER TABLE leads VALIDATE CONSTRAINT leads_status_allowed;
    END IF;
END $$;

INSERT INTO schema_migrations (version) VALUES ('008_pilot')
ON CONFLICT (version) DO NOTHING;
