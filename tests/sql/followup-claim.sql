BEGIN;
-- Follow-up Scheduler: who gets nudged / handed over. Expected:
--   A quiet nudge 1, C link nudge, E hand over, H final nudge; B D F G I nothing; run 2 nothing.
-- scenario leads on the demo studio (2); created_at offsets are relative to now()
CREATE TEMP TABLE scen(code text, ext text, status text, paused bool, channel text);
INSERT INTO scen VALUES ('A quiet 2h ago -> nudge 1','tA','engaged',false,'instagram'),
 ('B replied 30m ago -> not yet','tB','engaged',false,'instagram'),
 ('C link 4h, not tapped -> link nudge','tC','link_sent',false,'instagram'),
 ('D link 4h, tapped -> nothing','tD','link_sent',false,'instagram'),
 ('E quiet 25h -> hand over','tE','engaged',false,'instagram'),
 ('F paused -> nothing','tF','engaged',true,'instagram'),
 ('G she wrote last -> nothing','tG','engaged',false,'instagram'),
 ('H nudged 21h ago -> final nudge','tH','engaged',false,'instagram'),
 ('I voice-lab -> nothing','tI','engaged',false,'test');
INSERT INTO leads (external_id, channel, studio_id, status, ai_paused, created_at, updated_at)
SELECT 'claimtest-'||ext, channel, 2, status, paused, now(), now() FROM scen;
CREATE TEMP TABLE msg(ext text, dir text, author text, ago interval, nudge bool);
INSERT INTO msg VALUES
 ('tA','inbound','lead','2h 5m',false),('tA','outbound','ai','2h',false),
 ('tB','inbound','lead','35m',false),('tB','outbound','ai','30m',false),
 ('tC','inbound','lead','4h 5m',false),('tC','outbound','ai','4h',false),
 ('tD','inbound','lead','4h 5m',false),('tD','outbound','ai','4h',false),
 ('tE','inbound','lead','25h 5m',false),('tE','outbound','ai','25h',false),
 ('tF','inbound','lead','2h 5m',false),('tF','outbound','ai','2h',false),
 ('tG','outbound','ai','3h',false),('tG','inbound','lead','2h',false),
 ('tH','inbound','lead','22h',false),('tH','outbound','ai','21h 55m',false),('tH','outbound','ai','21h',true),
 ('tI','inbound','lead','2h 5m',false),('tI','outbound','ai','2h',false);
INSERT INTO messages (lead_id, direction, channel, body, author, nudge, created_at)
SELECT l.id, m.dir, l.channel, 'test', m.author, m.nudge, now() - m.ago FROM msg m JOIN leads l ON l.external_id = 'claimtest-'||m.ext;
INSERT INTO booking_links (lead_id, studio_id, kind, target_url, short_code, sent_at, clicked_at)
SELECT id, 2, 'new_client', 'https://example.com', 'ct'||external_id, now() - interval '4 hours',
       CASE WHEN external_id = 'claimtest-tD' THEN now() - interval '3 hours' END
  FROM leads WHERE external_id IN ('claimtest-tC','claimtest-tD');
\echo -- studio local hour now (daytime = 8..20):
SELECT EXTRACT(HOUR FROM now() AT TIME ZONE timezone) AS local_hour FROM studios WHERE id=2;
PREPARE claim AS {{QUERY:followup-scheduler.json:Claim Due Leads|RETURNING=replace(due.external_id,'claimtest-','') AS lead, due.kind, due.action, due.is_final, due.window_open}};
\echo -- run 1:
SELECT code FROM scen ORDER BY ext;
EXECUTE claim;
\echo -- run 2 straight after (everything just claimed -> nothing):
EXECUTE claim;
ROLLBACK;
