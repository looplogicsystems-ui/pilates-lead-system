-- Migration 015: record every request to a tracked booking link.
--
-- Found 2026-10-30: real Instagram link previews were counted as taps (both
-- live links showed a "tap" ~8 seconds after sending). Logged hits showed why:
-- besides facebookexternalhit (already filtered), Meta's scanners open the
-- link posing as ordinary Android/Windows Chrome. Each hit's user agent and
-- network are kept as evidence for every click the funnel counts.
--
-- ADDITIVE ONLY. Idempotent.
-- ROLLBACK: DROP TABLE IF EXISTS link_hits;
--           DELETE FROM schema_migrations WHERE version = '015_link_hits';

CREATE TABLE IF NOT EXISTS link_hits (
    id         BIGSERIAL PRIMARY KEY,
    short_code TEXT NOT NULL,
    at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_agent TEXT,              -- truncated to 400 chars
    counted    BOOLEAN NOT NULL   -- false = recognised as a preview robot
);
-- The visitor's network, from Cloudflare (the go worker passes request.cf.asn
-- / asOrganization). Meta's link scanners pose as ordinary Chrome browsers but
-- come from Meta's own network (AS32934), which is how they are recognised.
ALTER TABLE link_hits ADD COLUMN IF NOT EXISTS asn TEXT;
ALTER TABLE link_hits ADD COLUMN IF NOT EXISTS network TEXT;
CREATE INDEX IF NOT EXISTS link_hits_code_idx ON link_hits (short_code, at);

INSERT INTO schema_migrations (version) VALUES ('015_link_hits')
ON CONFLICT (version) DO NOTHING;
