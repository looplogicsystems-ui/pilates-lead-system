#!/usr/bin/env python3
"""Regenerate the two ops workflows, preserving their existing ids.

  workflows/error-handler.json    id 4L4K72aSSwiBcnjX  (referenced as errorWorkflow everywhere)
  workflows/ig-token-refresh.json id n56lLK7WRdYFesdW  (was ig-token-monitor.json)

Run: python3 workflows/build/build_ops.py <scratch-dir>
"""
import json
import os
import sys

from ig_common import TOKEN_CRYPTO_JS, GRAPH_JS

# Send Email sub-workflow (build_pilot.py). Not a secret.
SEND_EMAIL_WF_ID = "PQWBdER3x291uWeU"

# New workflows get their id from n8n on first push (push.py prints it).
MONITOR_IDS = {"unanswered": "EgCJIgrxK5wyNEab", "links": "7LiRAztbJZSSzvfq", "weekly": "RMZ5c58Vo8CpDVRs",
               "retention": "0Qm7mxhOwgStcGN4"}

CREDS = {
    "POSTGRES_CRED_ID":      "VVIxtEmT1GWsEZn9",
    "SLACK_OPS_BOT_CRED_ID": "jpK7Kf8K06tnadpR",
}

SETTINGS = {
    "executionOrder": "v1",
    "saveDataErrorExecution": "all",
    "saveDataSuccessExecution": "none",
    "saveManualExecutions": True,
    "callerPolicy": "workflowsFromSameOwner",
}


ERROR_EMAIL_JS = r"""
// H3: every workflow error emails the founders, at most once per workflow per
// 10 minutes (an outage can fire dozens of errors). Slack still gets each one.
// Never emails about Send Email's OWN failures: Send Email reports errors to
// this workflow, so emailing through it again could loop forever.
const e = $('On Workflow Error').first().json;
const dl = $('Persist Dead Letter').first().json || {};
const wf = (e.workflow && e.workflow.name) || 'unknown workflow';
const wfId = (e.workflow && e.workflow.id) || '';
const ex = e.execution || {};
if (wfId === '__SEND_EMAIL_WF_ID__' || wf === 'Send Email') return [];
// recent_same is unknown when Postgres itself is down: email anyway.
if (Number(dl.recent_same) > 0) return [];
const msg = String((ex.error && ex.error.message) || 'no message');
return [{ json: {
  to: 'founders',
  subject: `System error in "${wf}"`,
  heading: `"${wf}" failed${ex.lastNodeExecuted ? ' at "' + ex.lastNodeExecuted + '"' : ''}.`,
  lines: [
    `Error: ${msg.slice(0, 500)}`,
    dl.dead_letter_id ? `Saved as dead letter #${dl.dead_letter_id}.` : 'It could NOT be saved to dead_letters (is Postgres down?).',
    'If a lead was mid-conversation, check she got a reply. More errors from this workflow in the next 10 minutes go to Slack only.',
  ],
  buttons: (wfId && ex.id) ? [{ label: 'Open the failed run', url: `https://n8n.looplogicsystems.com/workflow/${wfId}/executions/${ex.id}` }] : [],
} }];
"""


def error_handler():
    nodes = [
        {"id": "on-workflow-error", "name": "On Workflow Error",
         "type": "n8n-nodes-base.errorTrigger", "typeVersion": 1,
         "position": [-260, 0], "parameters": {}},

        # Persist first, then shout: a Slack post or email alone is no record.
        # Always returns one row; recent_same = errors from this workflow in
        # the last 10 minutes BEFORE this one (the insert isn't visible to it).
        {"id": "persist-dead-letter", "name": "Persist Dead Letter",
         "type": "n8n-nodes-base.postgres", "typeVersion": 2.6,
         "position": [-40, 0],
         "parameters": {
             "operation": "executeQuery",
             "query": """WITH ins AS (
  INSERT INTO dead_letters (workflow_id, execution_id, reason, payload, error)
  VALUES ($1, $2, 'execution_error', $3::jsonb, $4)
  RETURNING id
)
SELECT (SELECT id FROM ins) AS dead_letter_id,
       (SELECT count(*) FROM dead_letters
         WHERE reason = 'execution_error' AND workflow_id = $1
           AND created_at > now() - interval '10 minutes') AS recent_same;""",
             "options": {"queryReplacement":
                         "={{ [ $json.workflow && $json.workflow.id, "
                         "String(($json.execution && $json.execution.id) ?? ''), "
                         "JSON.stringify($json), "
                         "[($json.execution && $json.execution.lastNodeExecuted), "
                         "($json.execution && $json.execution.error && $json.execution.error.message)]"
                         ".filter(Boolean).join(' — ') ] }}"},
         },
         "credentials": {"postgres": {"id": "POSTGRES_CRED_ID", "name": "Postgres account"}},
         "onError": "continueRegularOutput", "retryOnFail": True, "alwaysOutputData": True},

        {"id": "alert-owner-slack", "name": "Alert Owner (Slack)",
         "type": "n8n-nodes-base.slack", "typeVersion": 2.3,
         "position": [180, -100],
         "parameters": {
             "select": "channel",
             "channelId": {"__rl": True, "value": "={{ $env.OPS_SLACK_CHANNEL_ID }}",
                           "mode": "id"},
             "text": ("=:rotating_light: *Lead system error* in \"{{ $('On Workflow Error').first().json.workflow.name }}\"\n"
                      "Node: {{ $('On Workflow Error').first().json.execution.lastNodeExecuted }}\n"
                      "Error: {{ $('On Workflow Error').first().json.execution.error && $('On Workflow Error').first().json.execution.error.message }}\n"
                      "Execution: {{ $('On Workflow Error').first().json.execution.id }}\n"
                      "_Persisted to dead_letters for replay._"),
             "otherOptions": {}},
         "credentials": {"slackApi": {"id": "SLACK_OPS_BOT_CRED_ID", "name": "Lead System Monitor"}},
         "onError": "continueRegularOutput", "retryOnFail": True},

        {"id": "error-email", "name": "Founder Email?", "type": "n8n-nodes-base.code", "typeVersion": 2,
         "position": [180, 100], "parameters": {"jsCode": ERROR_EMAIL_JS.replace("__SEND_EMAIL_WF_ID__", SEND_EMAIL_WF_ID).strip()}},
        {"id": "error-send", "name": "Email Founders", "type": "n8n-nodes-base.executeWorkflow", "typeVersion": 1.2,
         "position": [400, 100], "onError": "continueRegularOutput",
         "parameters": {"workflowId": {"__rl": True, "mode": "id", "value": SEND_EMAIL_WF_ID},
                        "options": {"waitForSubWorkflow": True}}},
    ]
    conns = {
        "On Workflow Error": {"main": [[{"node": "Persist Dead Letter", "type": "main", "index": 0}]]},
        "Persist Dead Letter": {"main": [[{"node": "Alert Owner (Slack)", "type": "main", "index": 0},
                                          {"node": "Founder Email?", "type": "main", "index": 0}]]},
        "Founder Email?": {"main": [[{"node": "Email Founders", "type": "main", "index": 0}]]},
    }
    return {"id": "4L4K72aSSwiBcnjX", "name": "Lead System — Error Handler",
            "active": False, "versionId": "00000000-0000-4000-8000-00000000c001",
            "nodes": nodes, "connections": conns, "settings": dict(SETTINGS)}


# ---------------------------------------------------------------------------
# IG Token Refresh (was "IG Token Expiry Monitor"; same id). Daily at 10:00 UTC.
#
# Instagram long-lived tokens last ~60 days and can be refreshed once they are
# at least 24 hours old. Every token last renewed more than 7 days ago is
# refreshed (so each is renewed ~weekly, and a failure is retried the next day
# instead of next week). Founders get ONE email if any refresh failed or any
# token is within 10 days of expiring. Nobody renews tokens by hand any more.

LOAD_TOKENS_SQL = """SELECT ca.id, ca.account_ref, ca.ig_username, s.name AS studio_name,
       encode(ca.access_token_enc, 'base64') AS token_enc,
       ca.token_issued_at, ca.token_expires_at,
       (ca.token_issued_at IS NULL OR ca.token_issued_at < now() - interval '7 days') AS refresh_due
  FROM channel_accounts ca
  JOIN studios s ON s.id = ca.studio_id
 WHERE ca.channel = 'instagram' AND ca.active AND s.active
   AND ca.access_token_enc IS NOT NULL;"""

REFRESH_JS = TOKEN_CRYPTO_JS + GRAPH_JS + r"""
// One output item per account. The new token is encrypted here and never output.
const out = [];
for (const item of $input.all()) {
  const j = item.json;
  if (!j.id) continue;                         // a zero-row SELECT still emits {success:true}
  const base = { id: j.id, account_ref: j.account_ref, ig_username: j.ig_username, studio_name: j.studio_name,
                 old_expires_at: j.token_expires_at };
  if (!j.refresh_due) { out.push({ json: { ...base, status: 'not_due', token_enc: '', expires_in: 0, error: '' } }); continue; }
  try {
    const r = await graphCall(this, { what: 'token refresh', url: 'https://graph.instagram.com/refresh_access_token',
      qs: { grant_type: 'ig_refresh_token', access_token: decryptToken(j.token_enc) }, tries: 2 });
    if (!r.access_token) throw new Error('token refresh: no token in the response');
    out.push({ json: { ...base, status: 'refreshed', token_enc: encryptToken(r.access_token),
                       expires_in: Number(r.expires_in) || 5184000, error: '' } });
  } catch (e) {
    out.push({ json: { ...base, status: 'failed', token_enc: '', expires_in: 0, error: String(e.message).slice(0, 400) } });
  }
}
return out;
"""

SAVE_REFRESH_SQL = """-- Writes only what changed. Always returns the account's current state.
UPDATE channel_accounts SET
       access_token_enc = CASE WHEN $2 = 'refreshed' THEN decode($3, 'base64') ELSE access_token_enc END,
       token_issued_at  = CASE WHEN $2 = 'refreshed' THEN now() ELSE token_issued_at END,
       token_expires_at = CASE WHEN $2 = 'refreshed' THEN now() + make_interval(secs => $4::int)
                               ELSE token_expires_at END,
       token_error      = CASE WHEN $2 = 'refreshed' THEN NULL
                               WHEN $2 = 'failed'    THEN $5
                               ELSE token_error END
 WHERE id = $1::int
RETURNING id, $2::text AS status, account_ref, ig_username, token_expires_at, token_error,
          (SELECT name FROM studios WHERE studios.id = channel_accounts.studio_id) AS studio_name;"""

REPORT_JS = r"""
// One email, only if something needs a human.
const rows = $input.all().map((i) => i.json).filter((r) => r.id);
const soon = (r) => r.token_expires_at && (new Date(r.token_expires_at) - Date.now()) < 10 * 86400e3;
const bad = rows.filter((r) => r.status === 'failed' || soon(r));
if (!bad.length) return [];
const who = (r) => `${r.ig_username ? '@' + r.ig_username : r.account_ref} (${r.studio_name})`;
return [{ json: {
  to: 'founders',
  subject: `Instagram token problem: ${bad.map(who).join(', ')}`,
  heading: 'An Instagram token needs attention. If it expires, that studio\u2019s leads get no replies.',
  lines: [
    ...bad.map((r) => `${who(r)}: ` + (r.status === 'failed' ? `refresh FAILED (${r.token_error}). ` : '') +
                     (r.token_expires_at ? `Expires ${String(r.token_expires_at).slice(0, 10)}.` : 'Expiry unknown.')),
    'The job retries every day. If it keeps failing, send the studio a new connect link: python3 workflows/build/ig_connect_link.py <studio id>.',
  ],
} }];
"""


def token_refresh():
    nodes = [
        {"id": "daily-check", "name": "Daily Check", "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2,
         "position": [-460, 0],
         "parameters": {"rule": {"interval": [{"field": "days", "daysInterval": 1, "triggerAtHour": 10}]}}},
        {"id": "run-now", "name": "Run Now", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1,
         "position": [-460, 160], "parameters": {}},
        {"id": "load-tokens", "name": "Load Tokens", "type": "n8n-nodes-base.postgres", "typeVersion": 2.6,
         "position": [-240, 0],
         "parameters": {"operation": "executeQuery", "query": LOAD_TOKENS_SQL, "options": {}},
         "credentials": {"postgres": {"id": "POSTGRES_CRED_ID", "name": "Postgres account"}},
         "alwaysOutputData": True, "retryOnFail": True},
        {"id": "refresh", "name": "Refresh Tokens", "type": "n8n-nodes-base.code", "typeVersion": 2,
         "position": [-20, 0], "parameters": {"jsCode": REFRESH_JS.strip()}},
        {"id": "save-refresh", "name": "Save", "type": "n8n-nodes-base.postgres", "typeVersion": 2.6,
         "position": [200, 0],
         "parameters": {"operation": "executeQuery", "query": SAVE_REFRESH_SQL,
                        "options": {"queryReplacement":
                                    "={{ [$json.id, $json.status, $json.token_enc, $json.expires_in, $json.error] }}"}},
         "credentials": {"postgres": {"id": "POSTGRES_CRED_ID", "name": "Postgres account"}},
         "retryOnFail": True},
        {"id": "report", "name": "Anything Wrong?", "type": "n8n-nodes-base.code", "typeVersion": 2,
         "position": [420, 0], "parameters": {"jsCode": REPORT_JS.strip()}},
        {"id": "send-email", "name": "Email Founders", "type": "n8n-nodes-base.executeWorkflow", "typeVersion": 1.2,
         "position": [640, 0], "onError": "continueRegularOutput",
         "parameters": {"workflowId": {"__rl": True, "mode": "id", "value": SEND_EMAIL_WF_ID},
                        "options": {"waitForSubWorkflow": True}}},
    ]
    one = lambda a, b: {a: {"main": [[{"node": b, "type": "main", "index": 0}]]}}
    conns = {**one("Daily Check", "Load Tokens"), **one("Run Now", "Load Tokens"),
             **one("Load Tokens", "Refresh Tokens"), **one("Refresh Tokens", "Save"),
             **one("Save", "Anything Wrong?"), **one("Anything Wrong?", "Email Founders")}
    settings = dict(SETTINGS, errorWorkflow="4L4K72aSSwiBcnjX")
    return {"id": "n56lLK7WRdYFesdW", "name": "IG Token Refresh",
            "active": False, "versionId": "00000000-0000-4000-8000-00000000c002",
            "nodes": nodes, "connections": conns, "settings": settings}


# ---------------------------------------------------------------------------
# Monitors (pilot build step 7, Final System Changes §I).

def _wf(wid, name, version, nodes, conns, settings=None):
    return {"id": wid, "name": name, "active": False, "versionId": version,
            "nodes": nodes, "connections": conns,
            "settings": settings or dict(SETTINGS, errorWorkflow="4L4K72aSSwiBcnjX")}


def _pg(name, pos, query, replacement=None, **kw):
    n = {"id": name.lower().replace(" ", "-").replace("?", ""), "name": name,
         "type": "n8n-nodes-base.postgres", "typeVersion": 2.6, "position": pos,
         "parameters": {"operation": "executeQuery", "query": query.strip(), "options": {}},
         "credentials": {"postgres": {"id": "POSTGRES_CRED_ID", "name": "Postgres account"}},
         "retryOnFail": True}
    if replacement:
        n["parameters"]["options"]["queryReplacement"] = replacement
    n.update(kw)
    return n


def _code(name, pos, js, **kw):
    n = {"id": name.lower().replace(" ", "-").replace("?", ""), "name": name, "type": "n8n-nodes-base.code",
         "typeVersion": 2, "position": pos, "parameters": {"jsCode": js.strip()}}
    n.update(kw)
    return n


def _send(name, pos):
    return {"id": name.lower().replace(" ", "-"), "name": name, "type": "n8n-nodes-base.executeWorkflow",
            "typeVersion": 1.2, "position": pos, "onError": "continueRegularOutput",
            "parameters": {"workflowId": {"__rl": True, "mode": "id", "value": SEND_EMAIL_WF_ID},
                           "options": {"waitForSubWorkflow": True}}}


def _triggers(rule):
    return [{"id": "schedule", "name": "Schedule", "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2,
             "position": [-460, 0], "parameters": {"rule": {"interval": [rule]}}},
            {"id": "run-now", "name": "Run Now", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1,
             "position": [-460, 160], "parameters": {}}]


def _chain(*names):
    conns = {}
    for a, b in zip(names, names[1:]):
        conns.setdefault(a, {"main": [[]]})["main"][0].append({"node": b, "type": "main", "index": 0})
    return conns


LEAD_LABEL = r"""
const leadLabel = (l) => l.username ? `@${l.username}${l.name ? ' (' + l.name + ')' : ''}`
  : (l.name || (l.channel === 'instagram' ? 'An Instagram lead' : `A ${l.channel} lead`));
"""

# I1 — every 15 minutes: a lead's message with no reply for 5 minutes.
UNANSWERED_SQL = """
-- Marks what it reports in the same statement, so each message alerts ONCE.
-- Only her LATEST message counts, only from the last 24h, never paused or
-- escalated leads (a human has those) and never the voice lab.
WITH due AS (
  SELECT m.id
    FROM messages m
    JOIN leads l ON l.id = m.lead_id
   WHERE m.direction = 'inbound'
     AND m.unanswered_alerted_at IS NULL
     AND m.created_at < now() - interval '5 minutes'
     AND m.created_at > now() - interval '24 hours'
     AND l.channel <> 'test' AND NOT l.ai_paused AND l.status <> 'escalated'
     AND NOT EXISTS (SELECT 1 FROM messages o WHERE o.lead_id = m.lead_id
                      AND o.direction = 'outbound' AND o.created_at >= m.created_at)
     AND NOT EXISTS (SELECT 1 FROM messages n WHERE n.lead_id = m.lead_id
                      AND n.direction = 'inbound' AND n.created_at > m.created_at)
), marked AS (
  UPDATE messages SET unanswered_alerted_at = now() FROM due WHERE messages.id = due.id
  RETURNING messages.id, messages.lead_id, messages.body, messages.created_at
)
SELECT mk.id, mk.lead_id, mk.body, round(extract(epoch FROM now() - mk.created_at) / 60) AS minutes,
       l.channel, l.username, l.name, s.name AS studio_name
  FROM marked mk JOIN leads l ON l.id = mk.lead_id JOIN studios s ON s.id = l.studio_id;
"""

UNANSWERED_EMAIL_JS = LEAD_LABEL + r"""
const rows = $input.all().map((i) => i.json).filter((r) => r.id);
if (!rows.length) return [];
return [{ json: {
  to: 'founders',
  subject: `${rows.length === 1 ? leadLabel(rows[0]) + ' has' : rows.length + ' leads have'} had NO reply for ${Math.max(...rows.map((r) => r.minutes))}+ minutes`,
  heading: 'The AI promised a reply within a minute, and these messages got none.',
  lines: [
    ...rows.map((r) => `${leadLabel(r)} (${r.studio_name}), ${r.minutes} min ago: "${String(r.body || '').slice(0, 200)}"`),
    'Likely causes: a workflow error, an Instagram token problem, or n8n down. Check the error emails and dead_letters. Reply to her by hand if it still matters.',
  ],
} }];
"""

# I2 — daily: every booking link a studio has given us.
LINKS_SQL = """
SELECT s.id AS studio_id, s.name AS studio_name, s.owner_alert_email, s.links_ok, s.link_problem,
       json_agg(json_build_object('what', x.what, 'url', x.url)) AS links
  FROM studios s
  JOIN (SELECT id AS studio_id, 'main booking page' AS what, booking_url AS url
          FROM studios WHERE booking_url IS NOT NULL
        UNION ALL
        SELECT studio_id, 'offer "' || name || '"', url FROM studio_offers WHERE active AND url IS NOT NULL
        UNION ALL
        SELECT studio_id, 'class "' || name || '"', booking_url FROM studio_classes
         WHERE active AND booking_url IS NOT NULL) x ON x.studio_id = s.id
 WHERE s.active
 GROUP BY s.id;
"""

CHECK_LINKS_JS = r"""
// Opens every link like a phone browser would. BROKEN (switches the AI's links
// off): page not found / gone, site unreachable, or still 5xx on a retry.
// Sites that block automated visitors (401/403/429) are only WARNED about:
// switching links off on a false alarm would stop every booking.
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1';
const probe = async (url) => {
  try {
    const r = await this.helpers.httpRequest({ method: 'GET', url, headers: { 'User-Agent': UA, Accept: 'text/html' },
      returnFullResponse: true, ignoreHttpStatusErrors: true, timeout: 20000, json: false });
    return { status: r.statusCode };
  } catch (e) { return { status: 0, error: String(e.code || e.message).slice(0, 80) }; }
};
const verdict = (p) => (p.status >= 200 && p.status < 400) ? 'ok'
  : ([401, 403, 429].includes(p.status) ? 'blocked' : 'broken');
const out = [];
for (const item of $input.all()) {
  const s = item.json;
  if (!s.studio_id) continue;
  const seen = new Map();
  for (const l of (s.links || [])) {
    if (!seen.has(l.url)) seen.set(l.url, []);
    seen.get(l.url).push(l.what);
  }
  const broken = [], blocked = [];
  for (const [url, whats] of seen) {
    let p = await probe(url);
    if (verdict(p) !== 'ok') { await sleep(4000); p = await probe(url); }   // one retry
    const v = verdict(p);
    const line = `${whats.join(', ')}: ${url} -> ${p.status ? 'HTTP ' + p.status : (p.error || 'unreachable')}`;
    if (v === 'broken') broken.push(line);
    if (v === 'blocked') blocked.push(line);
  }
  const problem = [...broken.map((l) => 'BROKEN ' + l), ...blocked.map((l) => 'COULD NOT CHECK ' + l)].join('\n');
  out.push({ json: { studio_id: s.studio_id, studio_name: s.studio_name, owner_alert_email: s.owner_alert_email,
    links_ok: broken.length === 0, problem, old_problem: s.link_problem || '', old_links_ok: s.links_ok,
    broken: broken.length, blocked: blocked.length, checked: seen.size } });
}
return out;
"""

SAVE_LINKS_SQL = """
UPDATE studios SET links_ok = $2::boolean, link_problem = NULLIF($3, ''), links_checked_at = now()
 WHERE id = $1::int
RETURNING id;
"""

LINK_EMAIL_JS = r"""
// Only when something CHANGED since the last check (no daily repeats).
const out = [];
for (const i of $('Check Links').all()) {
  const s = i.json;
  if (s.problem === s.old_problem && s.links_ok === s.old_links_ok) continue;
  const owner = s.owner_alert_email || 'founders';
  if (s.broken) {
    out.push({ json: { to: owner === 'founders' ? 'founders' : `${owner}, founders`,
      subject: `Broken booking link — ${s.studio_name}`,
      heading: 'A booking link we send to leads is broken, so the AI has stopped sending links.',
      lines: [...s.problem.split('\n'),
              'Until it is fixed, leads who want to book get a holding reply ("we’ll send it to you") and you get an email for each one.',
              'Fix the link (or tell us the new one). The next daily check switches links back on by itself.'] } });
  } else if (s.blocked) {
    out.push({ json: { to: 'founders', subject: `Couldn't check a booking link — ${s.studio_name}`,
      heading: 'A booking site refused our automated check. Links are still being sent.',
      lines: [...s.problem.split('\n'), 'Open it on a phone to make sure it works.'] } });
  } else if (s.old_problem) {
    out.push({ json: { to: owner === 'founders' ? 'founders' : `${owner}, founders`,
      subject: `Booking links working again — ${s.studio_name}`,
      heading: 'All booking links opened fine today. The AI is sending links again.',
      lines: [`Checked ${s.checked} link${s.checked === 1 ? '' : 's'}.`] } });
  }
}
return out;
"""

# I4 — Monday: the 5-minute review, delivered instead of remembered.
WEEKLY_SQL = """
SELECT s.name AS studio_name,
  (SELECT count(*) FROM leads l WHERE l.studio_id = s.id AND l.channel <> 'test'
      AND l.created_at > now() - interval '7 days') AS new_leads,
  (SELECT count(*) FROM booking_links b JOIN leads l ON l.id = b.lead_id
    WHERE b.studio_id = s.id AND l.channel <> 'test' AND b.sent_at > now() - interval '7 days') AS links_sent,
  (SELECT count(*) FROM booking_links b JOIN leads l ON l.id = b.lead_id
    WHERE b.studio_id = s.id AND l.channel <> 'test' AND b.sent_at > now() - interval '7 days'
      AND b.clicked_at IS NOT NULL) AS links_tapped,
  (SELECT count(*) FROM booking_links b JOIN leads l ON l.id = b.lead_id
    WHERE b.studio_id = s.id AND l.channel <> 'test' AND b.sent_at > now() - interval '7 days'
      AND b.lead_said_booked_at IS NOT NULL) AS said_booked,
  (SELECT count(*) FROM booking_links b JOIN leads l ON l.id = b.lead_id
    WHERE b.studio_id = s.id AND l.channel <> 'test' AND b.sent_at > now() - interval '7 days'
      AND b.owner_confirmed_at IS NOT NULL) AS owner_confirmed,
  (SELECT count(*) FROM escalations e JOIN leads l ON l.id = e.lead_id
    WHERE l.studio_id = s.id AND l.channel <> 'test' AND e.created_at > now() - interval '7 days') AS escalations,
  (SELECT count(*) FROM messages m JOIN leads l ON l.id = m.lead_id
    WHERE l.studio_id = s.id AND l.channel <> 'test' AND m.nudge AND m.created_at > now() - interval '7 days') AS nudges,
  (SELECT count(*) FROM messages m JOIN leads l ON l.id = m.lead_id
    WHERE l.studio_id = s.id AND m.unanswered_alerted_at > now() - interval '7 days'
      AND m.unanswered_alerted_at > m.created_at + interval '1 minute') AS unanswered,
  (SELECT string_agg(coalesce('@' || l.username, l.name, 'lead ' || l.id) || ' (' || coalesce(l.paused_by, '?') || ')', ', ')
     FROM leads l WHERE l.studio_id = s.id AND l.channel <> 'test' AND l.ai_paused) AS paused_now,
  s.link_problem,
  (SELECT count(*) FROM dead_letters WHERE resolved_at IS NULL) AS open_dead_letters,
  (SELECT count(*) FROM dead_letters WHERE created_at > now() - interval '7 days') AS dead_letters_week,
  (SELECT count(*) FROM data_deletions WHERE kind = 'request' AND at > now() - interval '7 days') AS deletion_requests_week,
  (SELECT count(*) FROM data_deletions WHERE kind IN ('retention', 'studio_ended') AND at > now() - interval '7 days') AS retention_deleted_week,
  (SELECT count(*) FROM data_deletions WHERE kind = 'retention_run' AND at > now() - interval '7 days') AS retention_runs_week
  FROM studios s
 WHERE s.active
 ORDER BY s.id;
"""

WEEKLY_EMAIL_JS = r"""
const rows = $input.all().map((i) => i.json).filter((r) => r.studio_name);
if (!rows.length) return [];
const pct = (a, b) => (Number(b) ? ` (${Math.round(100 * Number(a) / Number(b))}%)` : '');
const lines = [];
for (const r of rows) {
  lines.push(`${r.studio_name}: ${r.new_leads} new leads. Links sent ${r.links_sent} → tapped ${r.links_tapped}${pct(r.links_tapped, r.links_sent)} → said booked ${r.said_booked} → owner confirmed ${r.owner_confirmed}.`);
  lines.push(`   Escalations ${r.escalations}, nudges sent ${r.nudges}, unanswered-DM alarms ${r.unanswered}. Paused right now: ${r.paused_now || 'nobody'}.${r.link_problem ? ' LINK PROBLEM: ' + r.link_problem.split('\n')[0] : ''}`);
}
const r0 = rows[0];
lines.push(`System: ${r0.dead_letters_week} errors this week, ${r0.open_dead_letters} unresolved in dead_letters.`);
lines.push(`Privacy: ${r0.deletion_requests_week || 0} "delete my data" requests carried out, ${r0.retention_deleted_week || 0} leads deleted by the retention rule. The daily retention job ran on ${r0.retention_runs_week || 0} of 7 days${Number(r0.retention_runs_week || 0) < 6 ? ': CHECK IT' : ''}.`);
lines.push('The 5-minute review: (1) any unresolved errors? (2) links sent → tapped → booked look sane? (3) every escalation answered? (4) anyone paused who should be resumed?');
return [{ json: { to: 'founders', subject: `Weekly review — ${new Date().toISOString().slice(0, 10)}`,
                  heading: 'Last 7 days', lines } }];
"""


def unanswered_check():
    nodes = _triggers({"field": "minutes", "minutesInterval": 15}) + [
        _pg("Find Unanswered", [-240, 0], UNANSWERED_SQL, alwaysOutputData=True),
        _code("Build Alert", [-20, 0], UNANSWERED_EMAIL_JS),
        _send("Email Founders", [200, 0])]
    conns = _chain("Schedule", "Find Unanswered", "Build Alert", "Email Founders")
    conns.update(_chain("Run Now", "Find Unanswered"))
    return _wf(MONITOR_IDS["unanswered"], "Monitor: Unanswered DMs", "00000000-0000-4000-8000-00000000c003", nodes, conns)


def link_check():
    nodes = _triggers({"field": "days", "daysInterval": 1, "triggerAtHour": 13}) + [
        _pg("Load Links", [-240, 0], LINKS_SQL, alwaysOutputData=True),
        _code("Check Links", [-20, 0], CHECK_LINKS_JS),
        _pg("Save Result", [200, 0], SAVE_LINKS_SQL,
            "={{ [$json.studio_id, $json.links_ok, $json.problem] }}"),
        _code("Build Alerts", [420, 0], LINK_EMAIL_JS, executeOnce=True),
        _send("Send Alerts", [640, 0])]
    conns = _chain("Schedule", "Load Links", "Check Links", "Save Result", "Build Alerts", "Send Alerts")
    conns.update(_chain("Run Now", "Load Links"))
    return _wf(MONITOR_IDS["links"], "Monitor: Booking Links", "00000000-0000-4000-8000-00000000c004", nodes, conns)


def weekly_review():
    nodes = _triggers({"field": "weeks", "weeksInterval": 1, "triggerAtDay": [1], "triggerAtHour": 13}) + [
        _pg("Weekly Numbers", [-240, 0], WEEKLY_SQL, alwaysOutputData=True),
        _code("Build Review", [-20, 0], WEEKLY_EMAIL_JS),
        _send("Email Founders", [200, 0])]
    conns = _chain("Schedule", "Weekly Numbers", "Build Review", "Email Founders")
    conns.update(_chain("Run Now", "Weekly Numbers"))
    return _wf(MONITOR_IDS["weekly"], "Weekly Review", "00000000-0000-4000-8000-00000000c005", nodes, conns)


# Data retention (security floor §J; migration 016). Deletes leads idle for
# 180 days, leads of studios that ended 30+ days ago, and old system records.
# The policy and the reasoning: vault `Company/Compliance & Privacy.md`.
# No email (it would be daily noise): every run logs a 'retention_run' row and
# the Weekly Review reports how many days it ran. A failure reaches the Error
# Handler like any workflow.
RETENTION_DAYS = 180


def data_retention():
    nodes = _triggers({"field": "days", "daysInterval": 1, "triggerAtHour": 4}) + [
        _pg("Apply Retention", [-240, 0], f"SELECT apply_retention({RETENTION_DAYS}) AS summary;")]
    conns = _chain("Schedule", "Apply Retention")
    conns.update(_chain("Run Now", "Apply Retention"))
    return _wf(MONITOR_IDS["retention"], "Data Retention", "00000000-0000-4000-8000-00000000c006", nodes, conns)


def main():
    scratch = sys.argv[1]
    os.makedirs(scratch, exist_ok=True)
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(os.path.dirname(here))

    for wf, filename in [(error_handler(), "error-handler.json"),
                         (token_refresh(), "ig-token-refresh.json"),
                         (unanswered_check(), "monitor-unanswered.json"),
                         (link_check(), "monitor-booking-links.json"),
                         (weekly_review(), "weekly-review.json"),
                         (data_retention(), "data-retention.json")]:
        incoming = {c["node"] for outs in wf["connections"].values() for kind in outs.values()
                    for out in kind for c in out}
        orphans = [n["name"] for n in wf["nodes"] if n["name"] not in incoming
                   and not any(t in n["type"] for t in ("Trigger", "trigger"))]
        assert not orphans, f"{wf['name']}: nodes with nothing feeding them: {orphans}"
        redacted = json.dumps(wf, indent=2, ensure_ascii=False)
        with open(os.path.join(repo, "workflows", filename), "w") as f:
            f.write(redacted + "\n")
        live = redacted
        for placeholder, real in CREDS.items():
            live = live.replace(placeholder, real)
        for placeholder in CREDS:
            assert placeholder not in live
        for real in CREDS.values():
            assert real not in redacted
        with open(os.path.join(scratch, filename.replace(".json", ".live.json")), "w") as f:
            f.write(live + "\n")
        print(f"{wf['name']:32} {len(wf['nodes']):>3} nodes -> workflows/{filename}"
              + ("" if wf["id"] else "   (NEW: id assigned on first push)"))


if __name__ == "__main__":
    main()
