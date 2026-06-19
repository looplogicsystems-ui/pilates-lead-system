-- Lead Capture & Booking System — Postgres schema (Phase 1)
-- Run this against the same Postgres database NocoDB is connected to.
-- NocoDB will surface these tables automatically as grids/views.

-- Reference data: one row per studio/client
CREATE TABLE IF NOT EXISTS studios (
    id           SERIAL PRIMARY KEY,
    name         TEXT NOT NULL,
    location     TEXT,
    studio_type  TEXT,           -- e.g. 'Pilates'
    offers       TEXT,           -- e.g. '2-week unlimited pass for $35'
    class_schedule TEXT,         -- free text or JSON of recurring slots
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Leads (one row per person per channel)
CREATE TABLE IF NOT EXISTS leads (
    id           SERIAL PRIMARY KEY,
    external_id  TEXT NOT NULL,              -- sender handle / phone / email
    channel      TEXT NOT NULL,              -- 'webhook', 'instagram', 'sms', ...
    name         TEXT,
    studio_id    INTEGER REFERENCES studios(id),
    status       TEXT NOT NULL DEFAULT 'new',-- new | engaged | booked | cold
    source_offer TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT leads_channel_external_uniq UNIQUE (channel, external_id)
);

-- Full raw transcript: every inbound and outbound message
CREATE TABLE IF NOT EXISTS messages (
    id          SERIAL PRIMARY KEY,
    lead_id     INTEGER NOT NULL REFERENCES leads(id),
    direction   TEXT NOT NULL,               -- inbound | outbound
    channel     TEXT,
    body        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS messages_lead_id_idx ON messages(lead_id);

-- Confirmed bookings
CREATE TABLE IF NOT EXISTS bookings (
    id            SERIAL PRIMARY KEY,
    lead_id       INTEGER NOT NULL REFERENCES leads(id),
    class_type    TEXT,
    start_time    TIMESTAMPTZ,
    end_time      TIMESTAMPTZ,
    gcal_event_id TEXT,
    status        TEXT NOT NULL DEFAULT 'confirmed', -- confirmed | cancelled
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS bookings_lead_id_idx ON bookings(lead_id);

-- Things the AI handed off to the owner
CREATE TABLE IF NOT EXISTS escalations (
    id          SERIAL PRIMARY KEY,
    lead_id     INTEGER NOT NULL REFERENCES leads(id),
    reason      TEXT,
    resolved    BOOLEAN NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- NOTE: table `n8n_chat_histories` is created automatically by the
-- "Postgres Chat Memory" node the first time the agent runs. Do not create it by hand.

-- Seed the first studio (edit values to match the real client)
INSERT INTO studios (name, location, studio_type, offers, class_schedule)
VALUES (
    'Your Studio Name',
    'City, State',
    'Pilates',
    '2-week unlimited intro pass for $35',
    'Tue 9:00am, Thu 6:30pm, Sat 10:00am (reformer); Mon/Wed 7:00am (mat)'
)
ON CONFLICT DO NOTHING;
