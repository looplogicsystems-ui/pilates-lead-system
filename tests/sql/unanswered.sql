BEGIN;
-- Monitor: Unanswered DMs. Expected run 1: U1 and U7 (latest only); run 2: nothing.
CREATE TEMP TABLE scen(code text, ext text, status text, paused bool, channel text);
INSERT INTO scen VALUES ('U1 no reply 10m -> ALERT','u1','engaged',false,'instagram'),
 ('U2 answered -> no','u2','engaged',false,'instagram'),
 ('U3 only 2m ago -> no (too soon)','u3','engaged',false,'instagram'),
 ('U4 paused -> no','u4','engaged',true,'instagram'),
 ('U5 escalated -> no','u5','escalated',false,'instagram'),
 ('U6 voice lab -> no','u6','engaged',false,'test'),
 ('U7 two unanswered -> ALERT once (latest)','u7','engaged',false,'instagram');
INSERT INTO leads (external_id, channel, studio_id, status, ai_paused, created_at, updated_at)
SELECT 'unanstest-'||ext, channel, 2, status, paused, now(), now() FROM scen;
CREATE TEMP TABLE msg(ext text, dir text, body text, ago interval);
INSERT INTO msg VALUES ('u1','inbound','hello?','10m'),
 ('u2','inbound','hi','10m'),('u2','outbound','hey!','9m'),
 ('u3','inbound','hi','2m'),('u4','inbound','hi','10m'),('u5','inbound','hi','10m'),('u6','inbound','hi','10m'),
 ('u7','inbound','first','12m'),('u7','inbound','second','11m');
INSERT INTO messages (lead_id, direction, channel, body, author, created_at)
SELECT l.id, m.dir, l.channel, m.body, CASE m.dir WHEN 'inbound' THEN 'lead' ELSE 'ai' END, now() - m.ago
  FROM msg m JOIN leads l ON l.external_id = 'unanstest-'||m.ext;
PREPARE find AS {{QUERY:monitor-unanswered.json:Find Unanswered}};
\echo -- run 1 (only the test leads shown):
EXECUTE find;
\echo -- run 2 straight after (already alerted -> nothing for the test leads):
EXECUTE find;
ROLLBACK;
