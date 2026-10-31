BEGIN;
-- forget_lead() and apply_retention() (migration 016). Expected:
--   P1 forget_lead(A): every table cleared for A only; the log row has no identifiers
--   P2 apply_retention(180): idle B and ended-studio D deleted; active C and
--      recently-ended E kept; one retention_run row
--   P3 apply_retention(10): refused
CREATE TEMP TABLE ids(k text PRIMARY KEY, id int);
INSERT INTO studios (name, timezone, active, ended_at) VALUES ('privtest ended 40d', 'America/Chicago', false, now() - interval '40 days');
INSERT INTO ids SELECT 'st_old', currval(pg_get_serial_sequence('studios','id'));
INSERT INTO studios (name, timezone, active, ended_at) VALUES ('privtest ended 10d', 'America/Chicago', false, now() - interval '10 days');
INSERT INTO ids SELECT 'st_new', currval(pg_get_serial_sequence('studios','id'));

INSERT INTO leads (external_id, channel, studio_id, status, username, created_at, updated_at) VALUES
 ('9000000000000001','instagram',2,'link_sent','privtest_a', now()-interval '3 days',  now()-interval '1 day'),
 ('9000000000000002','instagram',2,'cold',     'privtest_b', now()-interval '220 days',now()-interval '200 days'),
 ('9000000000000003','instagram',2,'engaged',  'privtest_c', now()-interval '220 days',now()-interval '2 days'),
 ('9000000000000004','instagram',(SELECT id FROM ids WHERE k='st_old'),'engaged','privtest_d', now()-interval '50 days', now()-interval '45 days'),
 ('9000000000000005','instagram',(SELECT id FROM ids WHERE k='st_new'),'engaged','privtest_e', now()-interval '20 days', now()-interval '15 days'),
 ('9000000000000006','instagram',2,'engaged',  'privtest_f', now()-interval '1 day',  now()-interval '1 hour');
INSERT INTO ids SELECT 'L'||right(external_id,1), id FROM leads WHERE external_id LIKE '900000000000000_';

INSERT INTO messages (lead_id, direction, channel, body, author, created_at)
SELECT l.id, 'inbound', 'instagram', 'privtest hello ' || l.username, 'lead', l.updated_at FROM leads l WHERE l.username LIKE 'privtest_%';
INSERT INTO messages (lead_id, direction, channel, body, author, created_at)
SELECT id, 'outbound', 'instagram', 'privtest reply', 'ai', now()-interval '1 day' FROM ids WHERE k='L1';
INSERT INTO booking_links (lead_id, studio_id, kind, target_url, short_code, sent_at)
SELECT id, 2, 'new_client', 'https://example.com/book', 'privA1', now() FROM ids WHERE k='L1';
INSERT INTO link_hits (short_code, at, user_agent, counted) VALUES ('privA1', now(), 'privtest UA', true), ('privA1', now(), 'privtest UA 2', false);
INSERT INTO escalations (lead_id, reason, resolved, created_at) SELECT id, 'privtest injury', false, now() FROM ids WHERE k='L1';
INSERT INTO dead_letters (workflow_id, reason, payload, error) VALUES
 ('privtest', 'privtest', '{"external_id":"9000000000000001","message":"privtest"}', 'privtest'),
 ('privtest', 'privtest', '{"external_id":"9000000000000003","message":"privtest"}', 'privtest');
INSERT INTO n8n_chat_histories (session_id, message) VALUES ('instagram:9000000000000001', '{"privtest":1}');

\echo -- P1 forget_lead(A):
SELECT forget_lead((SELECT id FROM ids WHERE k='L1'), 'request', 'dm');
SELECT 'left for A' AS check,
  (SELECT count(*) FROM leads WHERE external_id='9000000000000001') AS leads,
  (SELECT count(*) FROM messages WHERE lead_id=(SELECT id FROM ids WHERE k='L1')) AS msgs,
  (SELECT count(*) FROM booking_links WHERE short_code='privA1') AS links,
  (SELECT count(*) FROM link_hits WHERE short_code='privA1') AS hits,
  (SELECT count(*) FROM escalations WHERE lead_id=(SELECT id FROM ids WHERE k='L1')) AS esc,
  (SELECT count(*) FROM dead_letters WHERE payload::text LIKE '%9000000000000001%') AS dead,
  (SELECT count(*) FROM n8n_chat_histories WHERE session_id='instagram:9000000000000001') AS hist;
SELECT 'C untouched' AS check, (SELECT count(*) FROM dead_letters WHERE payload::text LIKE '%9000000000000003%') AS c_dead_letter;
SELECT 'log has no ids/text' AS check, kind, lead_id IS NOT NULL AS has_lead_id,
       detail::text LIKE '%9000000000000001%' OR detail::text LIKE '%privtest%' AS leaks_pii, detail
  FROM data_deletions WHERE lead_id = (SELECT id FROM ids WHERE k='L1');
SELECT 'forget again' AS check, forget_lead((SELECT id FROM ids WHERE k='L1'), 'request', 'dm');

\echo -- P4 Lead Brain's own Forget Lead query: F (found), then a stranger (nothing held, still logged):
PREPARE fl AS {{QUERY:lead-brain.json:Forget Lead}};
EXECUTE fl('instagram', '9000000000000006', 2);
EXECUTE fl('instagram', '9999999999999999', 2);
SELECT 'F gone' AS check, count(*) FROM leads WHERE external_id = '9000000000000006';
SELECT 'stranger logged' AS check, kind, lead_id, detail FROM data_deletions WHERE detail->>'found' = 'false' AND at > now() - interval '1 minute';

\echo -- P2 apply_retention(180) (counts include REAL old rows: what the first live run would delete):
SELECT apply_retention(180);
SELECT username, (SELECT count(*) FROM leads l2 WHERE l2.id = i.id) AS still_there
  FROM ids i LEFT JOIN (VALUES ('L2','privtest_b -> deleted (idle 200d)'),('L3','privtest_c -> KEPT (active)'),
                               ('L4','privtest_d -> deleted (studio ended 40d)'),('L5','privtest_e -> KEPT (ended 10d)'))
                       AS v(k, username) USING (k)
 WHERE i.k IN ('L2','L3','L4','L5') ORDER BY i.k;
SELECT 'runs logged' AS check, count(*) FROM data_deletions WHERE kind='retention_run' AND at > now() - interval '1 minute';

\echo -- P3 apply_retention(10) must be refused:
SAVEPOINT p3;
SELECT apply_retention(10);
ROLLBACK TO SAVEPOINT p3;
ROLLBACK;
