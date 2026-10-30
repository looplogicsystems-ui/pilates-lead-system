BEGIN;
-- Follow-up Scheduler "Log Nudge" guard. Expected:
-- 1) logs the nudge; 2) she wrote after the claim -> nothing; 3) paused -> nothing; 4) skip flag -> nothing.
INSERT INTO leads (external_id, channel, studio_id, status, created_at, updated_at)
VALUES ('lognudge-1', 'instagram', 2, 'engaged', now(), now());
INSERT INTO messages (lead_id, direction, channel, body, author, created_at)
SELECT id, 'inbound', 'instagram', 'hello', 'lead', now() - interval '10 minutes' FROM leads WHERE external_id = 'lognudge-1';
SELECT l.id AS lid, max(m.id) AS lin FROM leads l JOIN messages m ON m.lead_id = l.id WHERE l.external_id = 'lognudge-1' GROUP BY l.id \gset
PREPARE lognudge(int, text, text, int, text, text, boolean, text, text, text, boolean) AS {{QUERY:followup-scheduler.json:Log Nudge}};
\echo 1) normal -> logged (1 row):
EXECUTE lognudge(:lid, 'instagram', 'Still keen on Saturday?', :lin, 'L1', 'quiet', false, '', 'cipher', '', false);
\echo 2) she wrote again after the claim -> nothing (0 rows):
INSERT INTO messages (lead_id, direction, channel, body, author, created_at) VALUES (:lid, 'inbound', 'instagram', 'actually yes!', 'lead', now());
EXECUTE lognudge(:lid, 'instagram', 'Still keen?', :lin, 'L1', 'quiet', false, '', 'cipher', '', false);
\echo 3) paused -> nothing (0 rows):
UPDATE leads SET ai_paused = true WHERE id = :lid;
SELECT max(id) AS lin2 FROM messages WHERE lead_id = :lid AND direction = 'inbound' \gset
EXECUTE lognudge(:lid, 'instagram', 'Still keen?', :lin2, 'L1', 'quiet', false, '', 'cipher', '', false);
\echo 4) skip flag (AI failed) -> nothing (0 rows):
UPDATE leads SET ai_paused = false WHERE id = :lid;
EXECUTE lognudge(:lid, 'instagram', '', :lin2, 'L1', 'quiet', false, '', 'cipher', '', true);
ROLLBACK;
