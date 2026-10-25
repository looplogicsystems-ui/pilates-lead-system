-- Migration 009: per-studio offers & prices, one booking model, the demo studio
-- reproducible from git, and a restricted editing role.
--
-- THE DATABASE IS THE MASTER COPY of each studio's timetable (studio_classes),
-- prices & offers (studio_offers) and booking settings (studios). They are
-- edited directly in the tables — through a desktop DB client over the SSH
-- tunnel, logged in as the restricted `studio_editor` role created below.
-- Lead Brain reads them fresh on every message: an edit is live on the next reply.
--
-- Google Calendar is being removed (vault Decisions 2026-10-14; @talha
-- 2026-10-25: "remove it outright"). So 008's gcal/timetable switch is pointless
-- and is dropped here, along with new_client_url (the intro offer row now
-- carries its own purchase link — one place to set it).
--
-- SEED RULE: the demo seed only fills what is MISSING and never overwrites.
-- Migrations get re-run (fresh-server rebuild, migrate.sh), and a re-run must
-- not silently reset prices or classes someone edited by hand.
--
-- ROLLBACK:
--   DROP TABLE IF EXISTS studio_offers;
--   ALTER TABLE studio_classes DROP CONSTRAINT IF EXISTS studio_classes_weekday_valid,
--     DROP CONSTRAINT IF EXISTS studio_classes_duration_positive,
--     DROP CONSTRAINT IF EXISTS studio_classes_url_https;
--   ALTER TABLE studios ADD COLUMN new_client_url TEXT,
--     ADD COLUMN availability_source TEXT NOT NULL DEFAULT 'gcal';
--   REVOKE ALL ON studios, studio_classes FROM studio_editor; DROP ROLE IF EXISTS studio_editor;
--   (the demo studio row and its seed data are left in place)
--   DELETE FROM schema_migrations WHERE version = '009_offers_and_demo_seed';

-- ---------------------------------------------------------------------------
-- 1. Prices & offers, one row each. The AI quotes ONLY these rows.
CREATE TABLE IF NOT EXISTS studio_offers (
    id              SERIAL PRIMARY KEY,
    studio_id       INT NOT NULL REFERENCES studios(id),
    kind            TEXT NOT NULL CHECK (kind IN ('intro', 'drop_in', 'pack', 'membership', 'other')),
    name            TEXT NOT NULL,                 -- '2-week unlimited intro pass'
    price           NUMERIC(10,2),                 -- NULL = "ask the studio" (the AI escalates instead of guessing)
    currency        TEXT NOT NULL DEFAULT 'USD',
    details         TEXT,                          -- 'first-timers only, unlimited classes for 14 days'
    url             TEXT,                          -- where to buy it; the INTRO offer's url is the new-client booking link
    for_new_clients BOOLEAN NOT NULL DEFAULT false,
    active          BOOLEAN NOT NULL DEFAULT true,
    sort_order      INT NOT NULL DEFAULT 100,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT studio_offers_url_https CHECK (url IS NULL OR url ~ '^https://'),
    CONSTRAINT studio_offers_price_nonneg CHECK (price IS NULL OR price >= 0)
);
CREATE INDEX IF NOT EXISTS studio_offers_studio_idx ON studio_offers (studio_id, sort_order) WHERE active;

-- 2. Guard rails on the timetable now that people edit it by hand.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'studio_classes_weekday_valid') THEN
        ALTER TABLE studio_classes ADD CONSTRAINT studio_classes_weekday_valid CHECK (weekday BETWEEN 1 AND 7);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'studio_classes_duration_positive') THEN
        ALTER TABLE studio_classes ADD CONSTRAINT studio_classes_duration_positive CHECK (duration_minutes > 0);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'studio_classes_url_https') THEN
        ALTER TABLE studio_classes ADD CONSTRAINT studio_classes_url_https CHECK (booking_url IS NULL OR booking_url ~ '^https://');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'studios_booking_url_https') THEN
        ALTER TABLE studios ADD CONSTRAINT studios_booking_url_https CHECK (booking_url IS NULL OR booking_url ~ '^https://');
    END IF;
END $$;

-- 3. One booking model: drop 008's switch and the separate new-client link.
ALTER TABLE studios DROP CONSTRAINT IF EXISTS studios_timetable_needs_links;
ALTER TABLE studios DROP CONSTRAINT IF EXISTS studios_availability_source_allowed;
ALTER TABLE studios DROP COLUMN IF EXISTS availability_source;
ALTER TABLE studios DROP COLUMN IF EXISTS new_client_url;

-- ---------------------------------------------------------------------------
-- 4. The demo studio (id 2, "Reform Collective", Austin) — FICTIONAL, so it is
--    safe in a public repo. Before this it existed only in production, from a
--    hand edit on 2026-08-23. Created only if missing.
INSERT INTO studios (id, name, location, studio_type, timezone, active, prompt_vars)
VALUES (2, 'Reform Collective', 'South Congress, Austin, TX', 'Pilates', 'America/Chicago', true,
        '{"persona_name":"Mia","studio_name":"Reform Collective","how_we_refer":"Reform",
          "type":"boutique reformer Pilates studio","city":"Austin, TX",
          "location":"South Congress, Austin, TX","address":"1412 S Congress Ave, Austin, TX 78704"}'::jsonb)
ON CONFLICT (id) DO NOTHING;
SELECT setval(pg_get_serial_sequence('studios', 'id'), GREATEST((SELECT MAX(id) FROM studios), 1));

-- Booking settings: fill only what is empty. 'founders' = the addresses in the
-- server's FOUNDER_ALERT_EMAILS (Send Email resolves it) — the demo's "owner".
UPDATE studios SET
    booking_software  = COALESCE(booking_software, 'Demo booking page'),
    booking_url       = COALESCE(booking_url, 'https://n8n.looplogicsystems.com/webhook/demo/book'),
    owner_name        = COALESCE(owner_name, 'Jenna'),
    owner_alert_email = COALESCE(owner_alert_email, 'founders'),
    staffed_hours     = COALESCE(staffed_hours,
        '{"mon":["06:00","19:00"],"tue":["06:00","19:00"],"wed":["06:00","19:00"],"thu":["06:00","19:00"],
          "fri":["06:00","19:00"],"sat":["07:30","11:30"],"sun":["08:30","11:30"]}'::jsonb)
WHERE id = 2;

-- Prompt settings the new Lead Brain reads; added only if absent.
UPDATE studios SET prompt_vars =
       jsonb_build_object(
         'booking_steps',    'Tap the link, grab the 2-week intro pass, then pick your class — you''ll see the live spots there.',
         'first_visit_tips', 'Come about 10 minutes early for a quick waiver and a walkthrough of the springs, and bring grip socks (or grab a pair at the front desk for $18).',
         'ai_disclosure',    '— Mia, Reform''s AI assistant',
         'escalation_line',  'That one I want Jenna to answer properly rather than me guessing — she''ll come back to you today. Anything else I can help with in the meantime?')
       || prompt_vars
WHERE id = 2;

-- (Retiring the old duplicate price/schedule fields is migration 010, applied
-- together with the new Lead Brain: today's live Lead Brain still reads them.)

-- Timetable: seeded only if the demo has no classes yet (never overwrites edits).
INSERT INTO studio_classes (studio_id, name, weekday, start_local, duration_minutes, level)
SELECT 2, c.name, d.wd, c.t::time, 50, c.level
  FROM (VALUES ('Reformer Flow', '06:15', 'beginner-friendly'),
               ('Mat Basics',    '09:00', 'beginner-friendly'),
               ('Sculpt 45',     '12:00', 'needs some reformer experience'),
               ('Reformer Flow', '17:30', 'beginner-friendly'),
               ('Sculpt 45',     '18:30', 'needs some reformer experience')) AS c(name, t, level)
 CROSS JOIN generate_series(1, 5) AS d(wd)
 WHERE NOT EXISTS (SELECT 1 FROM studio_classes WHERE studio_id = 2)
UNION ALL
SELECT 2, c.name, c.wd, c.t::time, 50, c.level
  FROM (VALUES ('Reformer Flow', 6, '08:00', 'beginner-friendly'),
               ('Mat Basics',    6, '09:15', 'beginner-friendly'),
               ('Sculpt 45',     6, '10:30', 'needs some reformer experience'),
               ('Reformer Flow', 7, '09:00', 'beginner-friendly'),
               ('Mat Basics',    7, '10:15', 'beginner-friendly')) AS c(name, wd, t, level)
 WHERE NOT EXISTS (SELECT 1 FROM studio_classes WHERE studio_id = 2);

-- Offers & prices: seeded only if the demo has none yet. One consistent set,
-- from the studio's own offer line (decided 2026-10-25).
INSERT INTO studio_offers (studio_id, kind, name, price, details, url, for_new_clients, sort_order)
SELECT 2, o.kind, o.name, o.price, o.details, o.url, o.new_clients, o.sort
  FROM (VALUES
    ('intro',      '2-week unlimited intro pass', 89.00,  'First-timers only. Unlimited classes for 14 days.',
                   'https://n8n.looplogicsystems.com/webhook/demo/book?offer=intro', true, 10),
    ('drop_in',    'Single class',                34.00,  NULL, NULL, false, 20),
    ('pack',       '5-class pack',                155.00, 'Valid for 3 months.', NULL, false, 30),
    ('pack',       '10-class pack',               290.00, 'Valid for 6 months.', NULL, false, 40),
    ('membership', 'Unlimited monthly',           199.00, 'Month to month, cancel any time.', NULL, false, 50)
  ) AS o(kind, name, price, details, url, new_clients, sort)
 WHERE NOT EXISTS (SELECT 1 FROM studio_offers WHERE studio_id = 2);

-- ---------------------------------------------------------------------------
-- 5. studio_editor: the role a person uses to edit studio settings in a DB
--    client. It can change timetables, offers and studio settings — and CANNOT
--    read or touch leads, messages, bookings, tokens or anything else.
--    Created WITHOUT a login here (no password in a public repo); it is given
--    one on the server with:  ALTER ROLE studio_editor LOGIN PASSWORD '...';
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'studio_editor') THEN
        CREATE ROLE studio_editor NOLOGIN;
    END IF;
END $$;
GRANT CONNECT ON DATABASE postgres TO studio_editor;
GRANT USAGE ON SCHEMA public TO studio_editor;
GRANT SELECT, INSERT, UPDATE, DELETE ON studio_classes, studio_offers TO studio_editor;
GRANT USAGE, SELECT ON SEQUENCE studio_classes_id_seq, studio_offers_id_seq TO studio_editor;
-- studios: read the row, edit only the settings columns — not prompt_vars
-- (persona/voice, owned by the build) and not id/active/timezone.
GRANT SELECT (id, name, timezone, active, booking_software, booking_url, owner_name,
              owner_alert_email, staffed_hours, links_ok, prompt_vars) ON studios TO studio_editor;
GRANT UPDATE (booking_software, booking_url, owner_name, owner_alert_email, staffed_hours) ON studios TO studio_editor;

INSERT INTO schema_migrations (version) VALUES ('009_offers_and_demo_seed')
ON CONFLICT (version) DO NOTHING;
