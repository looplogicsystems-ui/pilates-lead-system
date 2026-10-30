#!/usr/bin/env python3
"""Generate the channel adapters and the follow-up scheduler.

  workflows/channel-adapter-instagram.json
  workflows/channel-adapter-slack.json
  workflows/followup-scheduler.json

Each is written twice: redacted into workflows/ (committed) and with live
credential ids into the scratch dir (imported into n8n). Run:

    python3 workflows/build/build_adapters.py <scratch-dir> <lead-brain-id>
"""
import json
import os
import sys

from ig_common import GRAPH, TOKEN_CRYPTO_JS, GRAPH_JS, LEAD_NAME_JS, SEND_IG_JS, LINK_BASE

CREDS = {
    "POSTGRES_CRED_ID":        "VVIxtEmT1GWsEZn9",
    "OPENAI_CRED_ID":          "2dmtKOJYL68BMOyG",
    "SLACK_LEAD_BOT_CRED_ID":  "4juRUVuQfSLuSC9y",
    "SLACK_OWNER_BOT_CRED_ID": "AL4h8l7yMMNZHFBE",
}

# Send Email sub-workflow (build_pilot.py). Not a secret.
SEND_EMAIL_WF_ID = "PQWBdER3x291uWeU"

BASE_SETTINGS = {
    "executionOrder": "v1",
    "saveDataErrorExecution": "all",
    "saveDataSuccessExecution": "none",
    "saveManualExecutions": True,
    "saveExecutionProgress": True,
    "callerPolicy": "workflowsFromSameOwner",
    "errorWorkflow": "4L4K72aSSwiBcnjX",
}


class WF:
    def __init__(self, name, version_uuid, workflow_id):
        self.name = name
        self.nodes = []
        self.conns = {}
        self.version_uuid = version_uuid
        # Pinned so `import:workflow` updates in place rather than minting a
        # new workflow (and a stale published version) on every deploy.
        self.workflow_id = workflow_id

    def node(self, name, typ, ver, pos, params, **kw):
        n = {"id": name.lower().replace(" ", "-").replace(":", "").replace("?", "").replace("(", "").replace(")", ""),
             "name": name, "type": typ, "typeVersion": ver, "position": pos,
             "parameters": params}
        n.update(kw)
        self.nodes.append(n)
        return n

    def connect(self, src, dst, out=0, kind="main"):
        c = self.conns.setdefault(src, {}).setdefault(kind, [])
        while len(c) <= out:
            c.append([])
        c[out].append({"node": dst, "type": kind, "index": 0})

    def pg(self, name, pos, query, replacement=None, **kw):
        params = {"operation": "executeQuery", "query": query, "options": {}}
        if replacement:
            params["options"]["queryReplacement"] = replacement
        return self.node(name, "n8n-nodes-base.postgres", 2.6, pos, params,
                         credentials={"postgres": {"id": "POSTGRES_CRED_ID",
                                                   "name": "Postgres account"}}, **kw)

    def slack(self, name, pos, channel_expr, text, cred, **kw):
        return self.node(name, "n8n-nodes-base.slack", 2.3, pos, {
            "select": "channel",
            "channelId": {"__rl": True, "value": channel_expr, "mode": "id"},
            "text": text, "otherOptions": {}},
            credentials={"slackApi": {"id": cred, "name": "Slack"}},
            onError="continueRegularOutput", retryOnFail=True, **kw)

    def send_email(self, name, pos):
        # An alert that fails must never stop anything else: continue on error.
        return self.node(name, "n8n-nodes-base.executeWorkflow", 1.2, pos, {
            "workflowId": {"__rl": True, "mode": "id", "value": SEND_EMAIL_WF_ID},
            "options": {"waitForSubWorkflow": True}},
            onError="continueRegularOutput", alwaysOutputData=True)

    def call_brain(self, name, pos, brain_id):
        # mode 'each' is the whole point: one sub-execution per message.
        # The old monolith collapsed every message in a batch into a single
        # AI turn (Format Schedule returned one item and everything downstream
        # used .first()), so the second person to message in the same poll or
        # webhook batch was logged and then never answered.
        return self.node(name, "n8n-nodes-base.executeWorkflow", 1.2, pos, {
            "workflowId": {"__rl": True, "value": brain_id, "mode": "list",
                           "cachedResultName": "Lead Brain"},
            "mode": "each",
            "workflowInputs": {"mappingMode": "defineBelow", "value": {},
                               "matchingColumns": [], "schema": [],
                               "attemptToConvertTypes": False,
                               "convertFieldsToString": True},
            "options": {"waitForSubWorkflow": True}},
            onError="continueRegularOutput", retryOnFail=True)

    def build(self, active=False):
        return {"id": self.workflow_id, "name": self.name, "active": active,
                "versionId": self.version_uuid,
                "nodes": self.nodes, "connections": self.conns,
                "settings": dict(BASE_SETTINGS)}


# ===========================================================================
# 1. Channel Adapter: Instagram
# ===========================================================================
def instagram(brain_id):
    w = WF("Channel Adapter: Instagram", "00000000-0000-4000-8000-00000000a001",
           "OQMXzwDyu5YAmRTo")

    # --- GET handshake -----------------------------------------------------
    w.node("IG Verify", "n8n-nodes-base.webhook", 2.1, [-460, -120],
           {"path": "instagram", "responseMode": "responseNode", "options": {}},
           webhookId="4f6d1a2b-7c3e-4d51-9a80-1e2f3a4b5c6d",
           onError="continueRegularOutput")

    w.node("Verify Token OK?", "n8n-nodes-base.if", 2.2, [-240, -120], {
        "conditions": {"options": {"version": 2, "leftValue": "", "caseSensitive": True,
                                   "typeValidation": "loose"},
                       "conditions": [{"id": "c-verify",
                                       "leftValue": "={{ $json.query['hub.verify_token'] }}",
                                       "rightValue": "={{ $env.IG_VERIFY_TOKEN }}",
                                       "operator": {"type": "string", "operation": "equals"}}],
                       "combinator": "and"}, "options": {}})
    w.connect("IG Verify", "Verify Token OK?")

    w.node("Respond Challenge", "n8n-nodes-base.respondToWebhook", 1.5, [-20, -200], {
        "respondWith": "text",
        "responseBody": "={{ $('IG Verify').first().json.query['hub.challenge'] }}",
        "options": {}})
    w.connect("Verify Token OK?", "Respond Challenge", out=0)

    w.node("Respond Forbidden", "n8n-nodes-base.respondToWebhook", 1.5, [-20, -40], {
        "respondWith": "text", "responseBody": "Forbidden",
        "options": {"responseCode": 403}})
    w.connect("Verify Token OK?", "Respond Forbidden", out=1)

    # --- POST events -------------------------------------------------------
    w.node("IG Events", "n8n-nodes-base.webhook", 2.1, [-460, 160], {
        "httpMethod": "POST", "path": "instagram",
        "options": {"rawBody": True, "responseData": "EVENT_RECEIVED"}},
        webhookId="8b1c2d3e-4f5a-6b7c-8d9e-0a1b2c3d4e5f",
        onError="continueRegularOutput")

    w.node("Verify IG Signature", "n8n-nodes-base.code", 2, [-240, 160], {"jsCode": r"""
// Verifies Meta's X-Hub-Signature-256 over the EXACT raw bytes that were signed.
// Meta may sign with either the main app secret or the Instagram-product app
// secret depending on how the subscription was created, so we accept either.
// Anything failing verification is dropped silently (returns no items).
const crypto = require('crypto');

const secrets = [$env.IG_APP_SECRET, $env.IG_APP_SECRET_INSTAGRAM].filter(Boolean);
const items = $input.all();
const out = [];

const matches = (raw, sig) => secrets.some((secret) => {
  const expected = 'sha256=' + crypto.createHmac('sha256', secret).update(raw).digest('hex');
  const a = Buffer.from(expected, 'utf8');
  const b = Buffer.from(String(sig), 'utf8');
  return a.length === b.length && crypto.timingSafeEqual(a, b);
});

for (let i = 0; i < items.length; i++) {
  const item = items[i];
  const headers = item.json.headers ?? {};
  const sig = headers['x-hub-signature-256'] ?? headers['X-Hub-Signature-256'];

  let raw = null;
  const b64 = item.binary?.data?.data;
  if (b64) {
    raw = Buffer.from(b64, 'base64');
  } else {
    try { raw = await $helpers.getBinaryDataBuffer(i, 'data'); } catch (e) { raw = null; }
  }

  if (!secrets.length || !sig || !raw) continue;
  if (!matches(raw, sig)) continue;

  let body;
  try { body = JSON.parse(raw.toString('utf8')); } catch (e) { continue; }
  out.push({ json: { body } });
}

return out;
"""})
    w.connect("IG Events", "Verify IG Signature")

    w.node("Extract IG Events", "n8n-nodes-base.code", 2, [-20, 160], {"jsCode": r"""
// Flattens Meta's batched webhook payload into one item per event we act on:
//   kind 'inbound' — a lead's text DM to the studio
//   kind 'echo'    — a message SENT FROM the studio account: either our own
//                    reply coming back, or staff replying in the Instagram app
// `entry` and `messaging` are both arrays: one POST can carry several people.
const describe = (atts) => {
  const t = (atts && atts[0] && atts[0].type) || '';
  return ({ image: '[photo]', audio: '[voice note]', video: '[video]', file: '[file]', share: '[shared post]',
            story_mention: '[story mention]', ig_reel: '[reel]' })[t] || '[attachment]';
};
const out = [];
for (const item of $input.all()) {
  const body = item.json.body ?? {};
  if (body.object !== 'instagram') continue;
  for (const entry of body.entry ?? []) {
    for (const ev of entry.messaging ?? []) {
      const msg = ev.message;
      if (!msg || msg.is_deleted) continue;          // receipts, reactions, unsends
      const received_at = ev.timestamp ? new Date(ev.timestamp).toISOString() : new Date().toISOString();
      if (msg.is_echo) {
        out.push({ json: {
          kind: 'echo',
          account_ref: String(ev.sender?.id ?? entry.id ?? ''),   // the studio account that sent it
          lead_ref: String(ev.recipient?.id ?? ''),               // the person it was sent to
          ig_mid: msg.mid,
          text: msg.text ?? null,                                 // our replies are always text
          body: msg.text ?? describe(msg.attachments),
          received_at,
        } });
        continue;
      }
      if (!msg.text) continue;     // a lead's photo / sticker / voice note: not answered (unchanged)
      out.push({ json: {
        kind: 'inbound',
        ig_sender: String(ev.sender?.id ?? ''),
        ig_mid: msg.mid,
        text: msg.text,
        account_ref: String(ev.recipient?.id ?? entry.id ?? ''),  // the account the lead messaged
        received_at,
      } });
    }
  }
}
return out;
"""})
    w.connect("Verify IG Signature", "Extract IG Events")

    # Durable dedupe. Write-then-check: let the primary key arbitrate, so two
    # concurrent deliveries of the same mid cannot both pass.
    w.pg("Claim Message IDs", [200, 160], """INSERT INTO processed_messages (provider, provider_msg_id)
VALUES ('instagram', $1)
ON CONFLICT (provider, provider_msg_id) DO NOTHING
RETURNING provider_msg_id;""",
         replacement="={{ [$json.ig_mid] }}",
         alwaysOutputData=True, retryOnFail=True)
    w.connect("Extract IG Events", "Claim Message IDs")

    w.node("Drop Replays", "n8n-nodes-base.code", 2, [420, 160], {"jsCode": r"""
// Keep only the events whose mid we actually just claimed. A mid already in
// processed_messages returns no row, so it never runs twice — this is what
// makes a Meta retry (or a replay after an n8n restart) a no-op.
const claimed = new Set(
  $input.all().map((i) => i.json.provider_msg_id).filter(Boolean)
);
return $('Extract IG Events').all().filter((i) => claimed.has(i.json.ig_mid));
"""})
    w.connect("Claim Message IDs", "Drop Replays")

    w.node("Inbound or Echo?", "n8n-nodes-base.switch", 3.4, [640, 160], {
        "rules": {"values": [
            {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict",
                                        "version": 3},
                            "conditions": [{"id": f"kind-{k}", "leftValue": "={{ $json.kind }}", "rightValue": k,
                                            "operator": {"type": "string", "operation": "equals"}}],
                            "combinator": "and"},
             "renameOutput": True, "outputKey": k} for k in ("inbound", "echo")]},
        "options": {}})
    w.connect("Drop Replays", "Inbound or Echo?")

    # --- inbound: who is she? (looked up once), then the brain -------------
    w.pg("Load Account & Lead", [860, 60], """-- Always exactly one row per message. Carries the message fields through
-- (a Postgres node outputs only its query result) plus this account's
-- ENCRYPTED token and whether this lead's profile still needs looking up.
SELECT $1::text AS account_ref, $2::text AS ig_sender, $3::text AS ig_mid,
       $4::text AS text, $5::text AS received_at,
       encode(ca.access_token_enc, 'base64') AS token_enc,
       (l.username IS NULL
        AND (l.profile_checked_at IS NULL OR l.profile_checked_at < now() - interval '7 days')) AS profile_due
  FROM (SELECT 1) AS anchor
  LEFT JOIN channel_accounts ca ON ca.channel = 'instagram' AND ca.account_ref = $1 AND ca.active
  LEFT JOIN leads l ON l.channel = 'instagram' AND l.external_id = $2;""",
         replacement="={{ [$json.account_ref, $json.ig_sender, $json.ig_mid, $json.text, $json.received_at] }}",
         alwaysOutputData=True, retryOnFail=True)
    w.connect("Inbound or Echo?", "Load Account & Lead", out=0)

    w.node("Look Up Profile", "n8n-nodes-base.code", 2, [1080, 60], {"jsCode": TOKEN_CRYPTO_JS + GRAPH_JS + r"""
// Instagram's webhook gives only a numeric id. Once per lead (retried at most
// weekly if it fails) we ask Instagram for her @username and name, so owner
// emails can say who she is. Builds the channel-agnostic contract the Lead
// Brain accepts — no studio_id: the brain resolves that from account_ref.
// Never outputs the token.
const out = [];
for (const item of $input.all()) {
  const j = item.json;
  let username = null, name = null, profile_checked = false;
  if (j.profile_due && j.token_enc) {
    profile_checked = true;
    try {
      const p = await graphCall(this, { what: 'profile lookup', url: `__GRAPH__/${encodeURIComponent(j.ig_sender)}`,
        qs: { fields: 'name,username' }, token: decryptToken(j.token_enc), tries: 2 });
      username = p.username || null;
      name = p.name || null;
    } catch (e) { /* she still gets her reply; the lookup is retried next week */ }
  }
  out.push({ json: {
    channel: 'instagram',
    account_ref: j.account_ref,
    external_id: String(j.ig_sender),
    name, username, profile_checked,
    message: String(j.text ?? ''),
    provider_message_id: j.ig_mid,
    received_at: j.received_at,
  } });
}
return out;
""".replace("__GRAPH__", GRAPH)})
    w.connect("Load Account & Lead", "Look Up Profile")

    w.call_brain("Call Lead Brain", [1300, 60], brain_id)
    w.connect("Look Up Profile", "Call Lead Brain")

    w.node("Should Reply?", "n8n-nodes-base.if", 2.2, [1520, 60], {
        "conditions": {"options": {"version": 2, "leftValue": "", "caseSensitive": True,
                                   "typeValidation": "loose"},
                       "conditions": [
                           {"id": "send-flag", "leftValue": "={{ $json.send }}",
                            "rightValue": "true",
                            "operator": {"type": "boolean", "operation": "true",
                                         "singleValue": True}},
                           {"id": "has-text", "leftValue": "={{ $json.reply }}",
                            "rightValue": "",
                            "operator": {"type": "string", "operation": "notEmpty",
                                         "singleValue": True}}],
                       "combinator": "and"}, "options": {}})
    w.connect("Call Lead Brain", "Should Reply?")

    w.node("No Reply Sent", "n8n-nodes-base.noOp", 1, [1740, 200], {})
    w.connect("Should Reply?", "No Reply Sent", out=1)

    # --- send: ONE path for every studio, with that studio's own token ------
    w.pg("Load Send Token", [1740, 0], """-- Always one row. Re-reads the lead's pause state at the last moment: if
-- staff replied in the Instagram app while the AI was writing, the AI's
-- reply must not go out on top of theirs.
SELECT $1::int AS lead_id, $2::text AS external_id, $3::text AS reply,
       NULLIF($4::text, '')::int AS outbound_message_id, $5::text AS account_ref,
       encode(ca.access_token_enc, 'base64') AS token_enc,
       l.paused_by AS paused_by_now, l.username, l.name, s.name AS studio_name
  FROM (SELECT 1) AS anchor
  LEFT JOIN channel_accounts ca ON ca.channel = 'instagram' AND ca.account_ref = $5 AND ca.active
  LEFT JOIN leads l ON l.id = $1::int
  LEFT JOIN studios s ON s.id = l.studio_id;""",
         replacement=("={{ [$json.lead_id, $json.external_id, $json.reply, "
                      "$json.outbound_message_id ?? '', $json.account_ref] }}"),
         alwaysOutputData=True, retryOnFail=True)
    w.connect("Should Reply?", "Load Send Token", out=0)

    w.node("Send IG Reply", "n8n-nodes-base.code", 2, [1960, 0], {"jsCode": TOKEN_CRYPTO_JS + GRAPH_JS + r"""
// Sends the AI's reply with THIS studio's token (decrypted here, never output)
// to Instagram's one fixed endpoint. No per-studio nodes or switches: a new
// studio is just a channel_accounts row. Never retried — a retry after an
// unclear failure could deliver the message twice.
const out = [];
for (const item of $input.all()) {
  const j = item.json;
  const base = { lead_id: j.lead_id, external_id: j.external_id, outbound_message_id: j.outbound_message_id,
                 account_ref: j.account_ref, username: j.username, name: j.name, studio_name: j.studio_name,
                 reply: j.reply };
  if (j.paused_by_now === 'staff_reply') { out.push({ json: { ...base, status: 'skipped' } }); continue; }
  try {
    if (!j.token_enc) throw new Error('no Instagram token stored for this account (reconnect it with a connect link)');
    const r = await graphCall(this, { what: 'send reply', method: 'POST', url: '__GRAPH__/me/messages',
      token: decryptToken(j.token_enc), json: { recipient: { id: j.external_id }, message: { text: j.reply } } });
    out.push({ json: { ...base, status: 'sent', mid: r.message_id || null } });
  } catch (e) {
    out.push({ json: { ...base, status: 'failed', error: String(e.message).slice(0, 400),
                       token_problem: e.metaCode === 190 || /token/i.test(String(e.message)) } });
  }
}
return out;
""".replace("__GRAPH__", GRAPH)})
    w.connect("Load Send Token", "Send IG Reply")

    w.node("Sent?", "n8n-nodes-base.switch", 3.4, [2180, 0], {
        "rules": {"values": [
            {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict",
                                        "version": 3},
                            "conditions": [{"id": f"st-{k}", "leftValue": "={{ $json.status }}", "rightValue": k,
                                            "operator": {"type": "string", "operation": "equals"}}],
                            "combinator": "and"},
             "renameOutput": True, "outputKey": k} for k in ("sent", "skipped", "failed")]},
        "options": {}})
    w.connect("Send IG Reply", "Sent?")

    # Instagram's id for our message: how its echo is recognised as OURS.
    w.pg("Record Message Id", [2400, -120], """UPDATE messages SET provider_message_id = $1
 WHERE id = NULLIF($2::text, '')::int;""",
         replacement="={{ [$json.mid, $json.outbound_message_id ?? ''] }}",
         onError="continueRegularOutput", retryOnFail=True)
    w.connect("Sent?", "Record Message Id", out=0)

    # Staff took over while the AI was writing: the reply never went out, so
    # it must not sit in the thread as something the AI "said".
    w.pg("Drop Unsent Reply", [2400, 0], """DELETE FROM messages
 WHERE id = NULLIF($1::text, '')::int AND author = 'ai' AND provider_message_id IS NULL;""",
         replacement="={{ [$json.outbound_message_id ?? ''] }}",
         onError="continueRegularOutput", retryOnFail=True)
    w.connect("Sent?", "Drop Unsent Reply", out=1)

    w.pg("Dead Letter: Send Failed", [2400, 140], """-- The lead got NO reply. Keep the text for a human to send, take it out of
-- the thread (it was never delivered), and flag the account if it was a token
-- problem. One statement, one row back.
WITH dl AS (
  INSERT INTO dead_letters (workflow_id, execution_id, reason, payload, error)
  VALUES ($1, $2, 'ig_send_failed', $3::jsonb, $4)
  RETURNING id
), unsent AS (
  DELETE FROM messages WHERE id = NULLIF($5::text, '')::int AND author = 'ai' AND provider_message_id IS NULL
  RETURNING id
), acct AS (
  UPDATE channel_accounts SET token_error = $4
   WHERE channel = 'instagram' AND account_ref = $6 AND $7::boolean
  RETURNING id
)
SELECT (SELECT id FROM dl) AS dead_letter_id;""",
         replacement=("={{ [$workflow.id, $execution.id, "
                      "JSON.stringify({ lead_id: $json.lead_id, reply: $json.reply, account_ref: $json.account_ref }), "
                      "$json.error, $json.outbound_message_id ?? '', $json.account_ref, !!$json.token_problem] }}"),
         onError="continueRegularOutput", retryOnFail=True, alwaysOutputData=True)
    w.connect("Sent?", "Dead Letter: Send Failed", out=2)

    w.node("Email: Send Failed", "n8n-nodes-base.code", 2, [2620, 140], {"jsCode": LEAD_NAME_JS + r"""
return $('Send IG Reply').all().filter((i) => i.json.status === 'failed').map((i) => {
  const j = i.json;
  const who = leadLabel({ channel: 'instagram', username: j.username, name: j.name });
  return { json: {
    to: 'founders',
    subject: `Instagram reply NOT delivered: ${who} (${j.studio_name || 'unknown studio'})`,
    heading: `The AI wrote a reply to ${who}, but Instagram refused it, so she has had no answer.`,
    lines: [`Error: ${j.error}`,
            `The reply that didn't go out: "${String(j.reply || '').slice(0, 600)}"`,
            j.token_problem ? 'This looks like a token problem: send the studio a new connect link (ig_connect_link.py).' : 'Send it to her by hand from the studio’s Instagram if it still makes sense.',
            'Saved in dead_letters.'],
    buttons: [chatButton({ channel: 'instagram', username: j.username })].filter(Boolean),
  } };
});
"""})
    w.connect("Dead Letter: Send Failed", "Email: Send Failed")
    w.send_email("Send: Send Failed", [2840, 140])
    w.connect("Email: Send Failed", "Send: Send Failed")

    # --- echo: was it us, or did staff reply in the Instagram app? ----------
    w.pg("Handle Echo", [860, 300], """-- One statement, always one row. Our own reply comes back as an echo too:
-- it is recognised by Instagram's message id (stored when we sent it) or, if
-- the echo beat that write, by the same text to the same lead in the last 2
-- minutes. ANY other echo to a known lead is a person replying from the
-- Instagram app: log it as staff (so the AI reads it after Resume) and pause
-- the AI for THAT lead only. Echoes to people who never messaged the studio
-- (staff DMing a friend) are ignored entirely.
WITH acct AS (
  SELECT studio_id FROM channel_accounts
   WHERE channel = 'instagram' AND account_ref = $1 AND active
), lead AS (
  SELECT l.id, l.username, l.name FROM leads l
   WHERE l.channel = 'instagram' AND l.external_id = $2
     AND l.studio_id = (SELECT studio_id FROM acct)
), ours AS (
  SELECT 1 FROM messages m
   WHERE m.lead_id = (SELECT id FROM lead) AND m.direction = 'outbound' AND m.author = 'ai'
     AND (m.provider_message_id = $3
          OR (m.provider_message_id IS NULL AND $4 <> '' AND m.body = $4
              AND m.created_at > now() - interval '2 minutes'))
), human AS (
  SELECT id AS lead_id FROM lead WHERE NOT EXISTS (SELECT 1 FROM ours)
), logged AS (
  INSERT INTO messages (lead_id, direction, channel, body, author, provider_message_id, created_at)
  SELECT lead_id, 'outbound', 'instagram', $5, 'human', $3, now() FROM human
  RETURNING id
), paused AS (
  UPDATE leads SET ai_paused = true, paused_by = 'staff_reply', paused_at = now(),
                   pause_alerted_at = now(), updated_at = now()
   WHERE id = (SELECT lead_id FROM human) AND NOT ai_paused
  RETURNING id
)
SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM acct)   THEN 'unknown_account'
            WHEN NOT EXISTS (SELECT 1 FROM lead)   THEN 'not_a_lead'
            WHEN EXISTS (SELECT 1 FROM ours)       THEN 'ours'
            WHEN EXISTS (SELECT 1 FROM paused)     THEN 'paused_now'
            ELSE 'already_paused' END AS outcome,
       (SELECT id FROM lead) AS lead_id,
       (SELECT username FROM lead) AS username,
       (SELECT name FROM lead) AS name,
       (SELECT s.name FROM studios s WHERE s.id = (SELECT studio_id FROM acct)) AS studio_name,
       (SELECT s.owner_alert_email FROM studios s WHERE s.id = (SELECT studio_id FROM acct)) AS owner_alert_email,
       $5::text AS staff_body;""",
         replacement="={{ [$json.account_ref, $json.lead_ref, $json.ig_mid, $json.text ?? '', $json.body] }}",
         alwaysOutputData=True, retryOnFail=True)
    w.connect("Inbound or Echo?", "Handle Echo", out=1)

    w.node("Staff Took Over?", "n8n-nodes-base.filter", 2.2, [1080, 300], {
        "conditions": {"options": {"version": 2, "leftValue": "", "caseSensitive": True,
                                   "typeValidation": "strict"},
                       "conditions": [{"id": "took-over", "leftValue": "={{ $json.outcome }}",
                                       "rightValue": "paused_now",
                                       "operator": {"type": "string", "operation": "equals"}}],
                       "combinator": "and"}, "options": {}})
    w.connect("Handle Echo", "Staff Took Over?")

    w.node("Email: Staff Took Over", "n8n-nodes-base.code", 2, [1300, 300], {"jsCode": LEAD_NAME_JS + r"""
// Signed Resume link: same scheme as Lead Brain and the Owner Actions workflow.
const crypto = require('crypto');
const secret = String($env.OWNER_LINK_SECRET || '');
return $input.all().map((i) => {
  const j = i.json;
  const who = leadLabel({ channel: 'instagram', username: j.username, name: j.name });
  const t = secret ? crypto.createHmac('sha256', secret).update(`resume:${j.lead_id}`).digest('hex').slice(0, 32) : null;
  return { json: {
    to: j.owner_alert_email || 'founders',
    subject: `You replied to ${who}, so the AI has stepped back`,
    heading: `You (or your team) replied to ${who} in Instagram, so the AI has stopped answering her.`,
    lines: [`Your message: "${String(j.staff_body || '').slice(0, 400)}"`,
            'The AI won’t message her again until you tap Resume. Every other lead is still answered as normal.',
            'When you’re done chatting, tap Resume and the AI picks up from where you left off. It reads what you said.'],
    buttons: [t ? { label: 'Resume AI', url: `https://n8n.looplogicsystems.com/webhook/owner?a=resume&l=${j.lead_id}&t=${t}` } : null,
              chatButton({ channel: 'instagram', username: j.username })].filter(Boolean),
  } };
});
"""})
    w.connect("Staff Took Over?", "Email: Staff Took Over")
    w.send_email("Send: Staff Took Over", [1520, 300])
    w.connect("Email: Staff Took Over", "Send: Staff Took Over")

    return w


# ===========================================================================
# 2. Channel Adapter: Slack
# ===========================================================================
def slack_adapter(brain_id):
    w = WF("Channel Adapter: Slack", "00000000-0000-4000-8000-00000000a002",
           "px8F683kj1I8Sb9X")

    w.node("Slack Poller", "n8n-nodes-base.scheduleTrigger", 1.2, [-460, 0], {
        "rule": {"interval": [{"field": "minutes", "minutesInterval": 1}]}})

    w.pg("Load Slack Accounts", [-240, 0], """-- Which Slack channels are we listening to, and for which studio.
SELECT ca.account_ref AS channel_id, ca.studio_id
  FROM channel_accounts ca
  JOIN studios s ON s.id = ca.studio_id
 WHERE ca.channel = 'slack' AND ca.active AND s.active;""",
         alwaysOutputData=True, retryOnFail=True)
    w.connect("Slack Poller", "Load Slack Accounts")

    w.node("Get Slack Messages", "n8n-nodes-base.slack", 2.3, [-20, 0], {
        "resource": "channel", "operation": "history",
        "channelId": {"__rl": True, "value": "={{ $json.channel_id }}", "mode": "id"},
        "limit": 20, "filters": {}},
        credentials={"slackApi": {"id": "SLACK_LEAD_BOT_CRED_ID",
                                  "name": "Studio Lead Bot (Slack)"}},
        onError="continueRegularOutput", retryOnFail=True, alwaysOutputData=True)
    w.connect("Load Slack Accounts", "Get Slack Messages")

    w.node("Filter New Lead Messages", "n8n-nodes-base.code", 2, [200, 0], {"jsCode": r"""
// Cursor keeps the poll cheap; processed_messages (next node) is what actually
// guarantees we never answer the same message twice across restarts.
const sd = $getWorkflowStaticData('global');
const items = $input.all();

// First run after activation: jump the cursor to newest and skip the backlog,
// so activating the workflow does not blast replies at old channel history.
if (sd.slackLastTs === undefined) {
  let mx = 0;
  for (const it of items) { const t = parseFloat(it.json.ts); if (t > mx) mx = t; }
  sd.slackLastTs = String(mx || (Date.now() / 1000));
  return [];
}

const lastTs = parseFloat(sd.slackLastTs);
let maxTs = lastTs;
const out = [];
for (const it of items) {
  const m = it.json;
  const ts = parseFloat(m.ts);
  if (!ts || ts <= lastTs) continue;
  if (m.bot_id || m.app_id || m.subtype) continue; // ignore bots / apps / system events
  if (!m.user || !m.text) continue;
  if (ts > maxTs) maxTs = ts;
  out.push({ json: {
    slack_user: m.user,
    slack_ts: m.ts,
    text: m.text,
    account_ref: m.channel ?? $('Get Slack Messages').first().json.channel ?? '',
  } });
}
sd.slackLastTs = String(maxTs);
return out;
"""})
    w.connect("Get Slack Messages", "Filter New Lead Messages")

    w.pg("Claim Message IDs", [420, 0], """INSERT INTO processed_messages (provider, provider_msg_id)
VALUES ('slack', $1)
ON CONFLICT (provider, provider_msg_id) DO NOTHING
RETURNING provider_msg_id;""",
         replacement="={{ [$json.slack_ts] }}",
         alwaysOutputData=True, retryOnFail=True)
    w.connect("Filter New Lead Messages", "Claim Message IDs")

    w.node("Drop Replays", "n8n-nodes-base.code", 2, [640, 0], {"jsCode": r"""
const claimed = new Set(
  $input.all().map((i) => i.json.provider_msg_id).filter(Boolean)
);
return $('Filter New Lead Messages').all().filter((i) => claimed.has(i.json.slack_ts));
"""})
    w.connect("Claim Message IDs", "Drop Replays")

    w.node("Build Inbound Contract", "n8n-nodes-base.code", 2, [860, 0], {
        "mode": "runOnceForEachItem", "jsCode": r"""
// Slack's conversations.history rows do not carry the channel id, so fall back
// to the account we polled for this batch.
const acct = $json.account_ref || $('Load Slack Accounts').first().json.channel_id;
return { json: {
  channel: 'slack',
  account_ref: String(acct),
  external_id: String($json.slack_user ?? 'slack-unknown'),
  name: null,
  message: String($json.text ?? ''),
  provider_message_id: $json.slack_ts,
  received_at: new Date(Number($json.slack_ts) * 1000).toISOString(),
} };
"""})
    w.connect("Drop Replays", "Build Inbound Contract")

    w.call_brain("Call Lead Brain", [1080, 0], brain_id)
    w.connect("Build Inbound Contract", "Call Lead Brain")

    w.node("Should Reply?", "n8n-nodes-base.if", 2.2, [1300, 0], {
        "conditions": {"options": {"version": 2, "leftValue": "", "caseSensitive": True,
                                   "typeValidation": "loose"},
                       "conditions": [
                           {"id": "send-flag", "leftValue": "={{ $json.send }}",
                            "rightValue": "true",
                            "operator": {"type": "boolean", "operation": "true",
                                         "singleValue": True}},
                           {"id": "has-text", "leftValue": "={{ $json.reply }}",
                            "rightValue": "",
                            "operator": {"type": "string", "operation": "notEmpty",
                                         "singleValue": True}}],
                       "combinator": "and"}, "options": {}})
    w.connect("Call Lead Brain", "Should Reply?")

    w.pg("Load Reply Channel", [1520, -60], """SELECT slack_lead_channel_id FROM studios WHERE id = $1;""",
         replacement="={{ [$json.studio_id] }}", retryOnFail=True)
    w.connect("Should Reply?", "Load Reply Channel", out=0)

    w.slack("Slack Reply", [1740, -60],
            "={{ $json.slack_lead_channel_id }}",
            "={{ $('Call Lead Brain').item.json.reply }}",
            "SLACK_LEAD_BOT_CRED_ID")
    w.connect("Load Reply Channel", "Slack Reply")

    w.node("No Reply Sent", "n8n-nodes-base.noOp", 1, [1520, 100], {})
    w.connect("Should Reply?", "No Reply Sent", out=1)

    return w


# ===========================================================================
# 3. Follow-up Scheduler
# ===========================================================================
def followup():
    """Pilot build step 7 (§G). Every 15 minutes:
      quiet lead  (status engaged): nudge 1 at followup_first_after_minutes after
                  the AI's last message, nudge 2 at followup_final_after_minutes
                  after the first; max_nudges in total; after the final one -> cold.
      link nudge  (status link_sent): ONE nudge ~3h after the booking link if it
                  wasn't tapped; the same tracked link is attached again.
    Instagram only lets a business message someone within 24h of THEIR last
    message: inside it the nudge is sent (8am-9pm studio time only); once it has
    closed, nothing is sent and the owner is told instead, once."""
    w = WF("Follow-up Scheduler", "00000000-0000-4000-8000-00000000a003",
           "fyk6eYsVGtUVS1dh")

    w.node("Every 15 Minutes", "n8n-nodes-base.scheduleTrigger", 1.2, [-460, 0], {
        "rule": {"interval": [{"field": "minutes", "minutesInterval": 15}]}})

    w.pg("Claim Due Leads", [-240, 0], """-- Who is due a nudge or a hand-over RIGHT NOW, claimed in the same statement
-- so an overlapping run can't nudge them too. Leads the AI didn't speak to last
-- (she replied, or staff did) are never nudged. Only conversations from the
-- last 48h are considered, so old history is never acted on.
WITH last_in AS (
  SELECT lead_id, max(created_at) AS t FROM messages WHERE direction = 'inbound' GROUP BY lead_id
), last_msg AS (
  SELECT DISTINCT ON (lead_id) lead_id, created_at AS t, direction, author
    FROM messages ORDER BY lead_id, created_at DESC
), nudges AS (
  SELECT m.lead_id, count(*) AS n
    FROM messages m JOIN last_in li ON li.lead_id = m.lead_id
   WHERE m.direction = 'outbound' AND m.nudge AND m.created_at > li.t
   GROUP BY m.lead_id
), cand AS (
  SELECT l.id AS lead_id, l.external_id, l.channel, l.status, l.username, l.name,
         s.id AS studio_id, s.name AS studio_name, s.prompt_vars, s.owner_alert_email, s.owner_name,
         s.slack_lead_channel_id, s.max_nudges,
         li.t AS last_inbound_at, lm.t AS last_msg_at, COALESCE(n.n, 0) AS nudges_sent,
         bl.id AS link_id, bl.short_code,
         CASE
           WHEN l.status = 'link_sent' THEN
             CASE WHEN bl.id IS NOT NULL AND bl.clicked_at IS NULL AND bl.nudged_at IS NULL
                       AND bl.lead_said_booked_at IS NULL AND bl.owner_confirmed_at IS NULL
                       AND bl.sent_at < now() - interval '3 hours' THEN 'link' END
           WHEN COALESCE(n.n, 0) = 0
                AND lm.t < now() - make_interval(mins => s.followup_first_after_minutes) THEN 'quiet'
           WHEN COALESCE(n.n, 0) BETWEEN 1 AND s.max_nudges - 1
                AND lm.t < now() - make_interval(mins => s.followup_final_after_minutes) THEN 'quiet'
         END AS kind,
         -- Instagram's 24h window (30 min of margin); Slack has none.
         (l.channel <> 'instagram' OR li.t > now() - interval '23 hours 30 minutes') AS window_open,
         (EXTRACT(HOUR FROM now() AT TIME ZONE s.timezone) BETWEEN 8 AND 20) AS daytime,
         encode(ca.access_token_enc, 'base64') AS token_enc,
         (SELECT json_agg(th ORDER BY th.created_at)
            FROM (SELECT direction, author, body, created_at FROM messages
                   WHERE lead_id = l.id ORDER BY created_at DESC LIMIT 12) th) AS thread
    FROM leads l
    JOIN studios s   ON s.id = l.studio_id AND s.active
    JOIN last_in li  ON li.lead_id = l.id
    JOIN last_msg lm ON lm.lead_id = l.id
    LEFT JOIN nudges n ON n.lead_id = l.id
    LEFT JOIN LATERAL (SELECT * FROM booking_links b WHERE b.lead_id = l.id
                        ORDER BY b.sent_at DESC LIMIT 1) bl ON true
    -- one Instagram account per studio during the pilot
    LEFT JOIN LATERAL (SELECT access_token_enc FROM channel_accounts c
                        WHERE c.studio_id = l.studio_id AND c.channel = 'instagram' AND c.active
                        ORDER BY c.connected_at DESC NULLS LAST LIMIT 1) ca ON l.channel = 'instagram'
   WHERE NOT l.ai_paused
     AND l.channel IN ('instagram', 'slack')
     AND l.status IN ('engaged', 'link_sent')
     AND lm.direction = 'outbound' AND lm.author = 'ai'
     AND li.t > now() - interval '48 hours'
     AND (l.nudge_claimed_at IS NULL OR l.nudge_claimed_at < now() - interval '30 minutes')
), due AS (
  SELECT c.*, CASE WHEN NOT c.window_open THEN 'hand_over' ELSE 'send' END AS action,
         (c.kind = 'quiet' AND c.nudges_sent + 1 >= c.max_nudges) AS is_final
    FROM cand c
   WHERE c.kind IS NOT NULL
     AND (NOT c.window_open OR c.daytime)        -- inside the window but at night: wait
   ORDER BY c.last_msg_at
   LIMIT 20
)
UPDATE leads SET nudge_claimed_at = now()
  FROM due WHERE leads.id = due.lead_id
RETURNING due.*;""", alwaysOutputData=True, retryOnFail=True)
    w.connect("Every 15 Minutes", "Claim Due Leads")
    w.node("Run Now", "n8n-nodes-base.manualTrigger", 1, [-460, 160], {})   # tests / manual runs
    w.connect("Run Now", "Claim Due Leads")

    w.node("Send or Hand Over?", "n8n-nodes-base.switch", 3.4, [-20, 0], {
        "rules": {"values": [
            {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict",
                                        "version": 3},
                            "conditions": [{"id": f"act-{k}", "leftValue": "={{ $json.action }}", "rightValue": k,
                                            "operator": {"type": "string", "operation": "equals"}}],
                            "combinator": "and"},
             "renameOutput": True, "outputKey": k} for k in ("send", "hand_over")]},
        "options": {}})
    w.connect("Claim Due Leads", "Send or Hand Over?")

    # --- send: the AI writes it, in the studio's voice, from the real thread --
    w.node("Compose Prompt", "n8n-nodes-base.code", 2, [200, -100], {
        "mode": "runOnceForEachItem", "jsCode": r"""
const j = $json;
const v = (typeof j.prompt_vars === 'string') ? JSON.parse(j.prompt_vars) : (j.prompt_vars || {});
const persona = v.persona_name || 'the studio assistant';
const studio = v.studio_name || j.studio_name;
const owner = j.owner_name || 'the owner';
const thread = Array.isArray(j.thread) ? j.thread : [];
const who = (m) => m.direction === 'inbound' ? 'Lead' : (m.author === 'human' ? 'Studio staff' : 'You');
const history = thread.map((m) => `${who(m)}: ${String(m.body || '').replace(/\s+/g, ' ').slice(0, 500)}`).join('\n');
const task = j.kind === 'link'
  ? `About 3 hours ago you sent her the booking link and she hasn't opened it yet. Write a light check-in: did she get a chance to grab a spot? The system attaches the same link under your message, so don't write a link and don't say "here's the link again" in a robotic way.`
  : (j.is_final
      ? `She went quiet after your last message, and you already followed up once. This is the LAST follow-up: extra gentle, no pressure, leave the door open for whenever she's ready.`
      : `She went quiet after your last message. Write a light, friendly check-in that picks up where you left off.`);
const systemMessage = [
`You are ${persona}, the AI assistant for ${studio}${v.type ? ', a ' + v.type : ''}${v.city ? ' in ' + v.city : ''}. You text leads on Instagram in the owner's (${owner}'s) voice: warm, casual, specific, like a real person texting.`,
``,
task,
``,
`Rules:`,
`- One or two short sentences. No greeting like "Hey!" or "Hi again!", no sign-off.`,
`- Never pushy: no guilt, no fake urgency, no "just following up", "circling back", "touching base" or "I wanted to reach out".`,
`- Use the conversation: refer to the class or question she was actually considering. Don't repeat what was already said.`,
`- Never say she is booked and never claim a spot is free. Don't quote prices unless she asked about price.`,
`- Write times like "5:30pm". No em dashes. No URLs. At most one emoji, usually none.`,
`- At most one easy question.`,
``,
`Output ONLY the message text.`,
].join('\n');
const userMessage = `Conversation so far:\n${history || '(none)'}\n\nWrite the follow-up now.`;
return { json: { ...j, systemMessage, userMessage } };
"""})
    w.connect("Send or Hand Over?", "Compose Prompt", out=0)

    w.node("Write Nudge (AI)", "@n8n/n8n-nodes-langchain.agent", 3.1, [420, -100], {
        "promptType": "define", "text": "={{ $json.userMessage }}",
        "options": {"systemMessage": "={{ $json.systemMessage }}"}},
        onError="continueRegularOutput")
    w.connect("Compose Prompt", "Write Nudge (AI)")

    w.node("OpenAI (Nudge)", "@n8n/n8n-nodes-langchain.lmChatOpenAi", 1.3, [420, 100], {
        "model": {"__rl": True, "value": "gpt-5.4", "mode": "list", "cachedResultName": "gpt-5.4"},
        "builtInTools": {}, "options": {}},
        credentials={"openAiApi": {"id": "OPENAI_CRED_ID", "name": "OpenAI account"}})
    w.connect("OpenAI (Nudge)", "Write Nudge (AI)", kind="ai_languageModel")

    w.node("Finish Nudge", "n8n-nodes-base.code", 2, [640, -100], {
        "mode": "runOnceForEachItem", "jsCode": r"""
// Cleans the AI's text and attaches the link BY CODE (the AI never types one).
// If the AI failed or wrote nothing usable, nothing is sent: the lead stays
// due and the next run (15 min) tries again while the window is open.
const c = $('Compose Prompt').item.json;
let text = String($json.output || '').trim()
  .replace(/^["'“”]+|["'“”]+$/g, '')
  .replace(/https?:\/\/\S+/g, '')
  .replace(/\s*—\s*/g, ', ')
  .replace(/[ \t]+/g, ' ').trim();
if (!text || text.length > 400) return { json: { ...c, skip: true, skip_reason: text ? 'too long' : ($json.error ? 'AI error' : 'empty') } };
if (c.kind === 'link' && c.short_code) text = `${text}\n__LINK_BASE__${c.short_code}`;
const { systemMessage, userMessage, thread, prompt_vars, ...keep } = c;
return { json: { ...keep, text, skip: false } };
""".replace("__LINK_BASE__", LINK_BASE)})
    w.connect("Write Nudge (AI)", "Finish Nudge")

    # Logged BEFORE sending, as the AI's own message: when Instagram echoes the
    # nudge back, Handle Echo must recognise it as ours (or it would look like
    # a staff reply and pause her). The INSERT is also the last-moment guard:
    # nothing is logged (so nothing sent) if she was paused or wrote back.
    w.pg("Log Nudge", [860, -100], """INSERT INTO messages (lead_id, direction, channel, body, author, nudge, created_at)
SELECT $1::int, 'outbound', $2, $3, 'ai', true, now()
 WHERE NOT $11::boolean
   AND NOT (SELECT ai_paused FROM leads WHERE id = $1::int)
   AND NOT EXISTS (SELECT 1 FROM messages WHERE lead_id = $1::int AND direction = 'inbound'
                    AND created_at > $4::timestamptz)
RETURNING id AS message_id, lead_id, channel, body AS text, $5::text AS external_id, $6::text AS kind,
          $7::boolean AS is_final, NULLIF($8::text, '')::int AS link_id, $9::text AS token_enc,
          $10::text AS slack_channel;""",
         replacement=("={{ [$json.lead_id, $json.channel, $json.text ?? '', $json.last_inbound_at, $json.external_id, "
                      "$json.kind, !!$json.is_final, $json.link_id ?? '', $json.token_enc ?? '', "
                      "$json.slack_lead_channel_id ?? '', !!$json.skip] }}"),
         alwaysOutputData=True, retryOnFail=True)
    w.connect("Finish Nudge", "Log Nudge")

    w.node("Which Channel?", "n8n-nodes-base.switch", 3.4, [1080, -100], {
        "rules": {"values": [
            {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict",
                                        "version": 3},
                            "conditions": [{"id": f"ch-{k}", "leftValue": "={{ $json.channel }}", "rightValue": k,
                                            "operator": {"type": "string", "operation": "equals"}}],
                            "combinator": "and"},
             "renameOutput": True, "outputKey": k} for k in ("instagram", "slack")]},
        "options": {}})
    w.connect("Log Nudge", "Which Channel?")

    w.node("Send IG Nudge", "n8n-nodes-base.code", 2, [1300, -200], {"jsCode": TOKEN_CRYPTO_JS + GRAPH_JS + SEND_IG_JS + r"""
const out = [];
for (const item of $input.all()) {
  const j = item.json;
  if (!j.message_id) continue;
  const base = { message_id: j.message_id, lead_id: j.lead_id, kind: j.kind, is_final: j.is_final,
                 link_id: j.link_id, text: j.text, channel: j.channel };
  try { out.push({ json: { ...base, status: 'sent', mid: await sendIG(this, j.token_enc, j.external_id, j.text) } }); }
  catch (e) { out.push({ json: { ...base, status: 'failed', mid: '', error: String(e.message).slice(0, 400),
                                 token_problem: e.metaCode === 190 || /token/i.test(String(e.message)) } }); }
}
return out;
"""})
    w.connect("Which Channel?", "Send IG Nudge", out=0)

    w.slack("Send Slack Nudge", [1300, 0], "={{ $json.slack_channel }}", "={{ $json.text }}",
            "SLACK_LEAD_BOT_CRED_ID")
    w.connect("Which Channel?", "Send Slack Nudge", out=1)
    w.node("Slack Sent", "n8n-nodes-base.code", 2, [1520, 0], {"jsCode": r"""
return $('Which Channel?').all(1).filter((i) => i.json.channel === 'slack' && i.json.message_id)   // output 1 = slack
  .map((i) => ({ json: { ...i.json, status: 'sent', mid: '' } }));
"""})
    w.connect("Send Slack Nudge", "Slack Sent")

    w.node("Nudge Sent?", "n8n-nodes-base.if", 2.2, [1520, -200], {
        "conditions": {"options": {"version": 2, "leftValue": "", "caseSensitive": True, "typeValidation": "strict"},
                       "conditions": [{"id": "sent", "leftValue": "={{ $json.status }}", "rightValue": "sent",
                                       "operator": {"type": "string", "operation": "equals"}}],
                       "combinator": "and"}, "options": {}})
    w.connect("Send IG Nudge", "Nudge Sent?")

    w.pg("Record Nudge", [1740, -120], """-- Instagram's id (so the echo is recognised as ours), the link nudge spent,
-- and the lead marked cold after her final nudge.
WITH m AS (
  UPDATE messages SET provider_message_id = NULLIF($2, '') WHERE id = $1::int RETURNING id
), b AS (
  UPDATE booking_links SET nudged_at = now() WHERE id = NULLIF($3::text, '')::int AND $4 = 'link' RETURNING id
), l AS (
  UPDATE leads SET status = 'cold', updated_at = now()
   WHERE id = $5::int AND $6::boolean AND status = 'engaged' RETURNING id
)
SELECT (SELECT id FROM m) AS message_id;""",
         replacement="={{ [$json.message_id, $json.mid ?? '', $json.link_id ?? '', $json.kind, $json.lead_id, !!$json.is_final] }}",
         onError="continueRegularOutput", retryOnFail=True)
    w.connect("Nudge Sent?", "Record Nudge", out=0)
    w.connect("Slack Sent", "Record Nudge")

    w.pg("Dead Letter: Nudge Failed", [1740, -280], """-- Not delivered: take it out of the thread and keep it as a dead letter.
WITH dl AS (
  INSERT INTO dead_letters (workflow_id, execution_id, reason, payload, error)
  VALUES ($1, $2, 'ig_nudge_failed', $3::jsonb, $4) RETURNING id
), unsent AS (
  DELETE FROM messages WHERE id = $5::int AND provider_message_id IS NULL RETURNING id
)
SELECT (SELECT id FROM dl) AS dead_letter_id;""",
         replacement=("={{ [$workflow.id, $execution.id, JSON.stringify({ lead_id: $json.lead_id, kind: $json.kind, text: $json.text }), "
                      "$json.error, $json.message_id] }}"),
         onError="continueRegularOutput", retryOnFail=True, alwaysOutputData=True)
    w.connect("Nudge Sent?", "Dead Letter: Nudge Failed", out=1)

    w.node("Email: Nudge Failed", "n8n-nodes-base.code", 2, [1960, -280], {"jsCode": r"""
const failed = $('Send IG Nudge').all().filter((i) => i.json.status === 'failed');
if (!failed.length) return [];
return [{ json: {
  to: 'founders',
  subject: `${failed.length} follow-up nudge${failed.length > 1 ? 's' : ''} NOT delivered`,
  heading: 'Instagram refused a follow-up nudge.',
  lines: [...failed.map((i) => `Lead ${i.json.lead_id} (${i.json.kind}): ${i.json.error}`),
          failed.some((i) => i.json.token_problem) ? 'Looks like a token problem: send the studio a new connect link.' : 'Saved in dead_letters.'],
} }];
"""})
    w.connect("Dead Letter: Nudge Failed", "Email: Nudge Failed")
    w.send_email("Send: Nudge Failed", [2180, -280])
    w.connect("Email: Nudge Failed", "Send: Nudge Failed")

    # --- hand over: the 24h window closed, so the owner decides ---------------
    w.pg("Hand Over", [200, 160], """-- Nothing can be sent any more. Stop automation for this lead (once) and
-- tell the owner. Quiet leads go cold; a link nudge is marked spent.
WITH b AS (
  UPDATE booking_links SET nudged_at = now() WHERE id = NULLIF($2::text, '')::int AND $3 = 'link' RETURNING id
), l AS (
  UPDATE leads SET status = CASE WHEN status = 'engaged' THEN 'cold' ELSE status END, updated_at = now()
   WHERE id = $1::int RETURNING id
)
SELECT $1::int AS lead_id, $3::text AS kind;""",
         replacement="={{ [$json.lead_id, $json.link_id ?? '', $json.kind] }}",
         retryOnFail=True)
    w.connect("Send or Hand Over?", "Hand Over", out=1)

    w.node("Email: Over to You", "n8n-nodes-base.code", 2, [420, 160], {"jsCode": LEAD_NAME_JS + r"""
return $('Send or Hand Over?').all(1).filter((i) => i.json.action === 'hand_over').map((i) => {   // output 1 = hand_over
  const j = i.json;
  const who = leadLabel(j);
  const what = j.kind === 'link' ? 'was sent your booking link but never opened it' : 'stopped replying';
  return { json: {
    to: j.owner_alert_email || 'founders',
    subject: `${who} went quiet — over to you if you want to follow up`,
    heading: `${who} ${what}.`,
    lines: ['Instagram only lets a business message someone within 24 hours of THEIR last message, and that window has closed, so the AI can’t follow up.',
            'If you know her or want to try once more, message her yourself. If she writes back, the AI picks the conversation up again.'],
    buttons: [chatButton(j)].filter(Boolean),
  } };
});
"""})
    w.connect("Hand Over", "Email: Over to You")
    w.send_email("Send: Over to You", [640, 160])
    w.connect("Email: Over to You", "Send: Over to You")

    return w


# ===========================================================================
def main():
    scratch = sys.argv[1]
    os.makedirs(scratch, exist_ok=True)
    brain_id = sys.argv[2]
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(os.path.dirname(here))

    targets = [
        (instagram(brain_id), "channel-adapter-instagram.json"),
        (slack_adapter(brain_id), "channel-adapter-slack.json"),
        (followup(), "followup-scheduler.json"),
    ]

    for w, filename in targets:
        wf = w.build(active=False)
        incoming = {c["node"] for outs in wf["connections"].values() for kind in outs.values()
                    for out in kind for c in out}
        # AI sub-nodes (model, memory) plug INTO another node, so they have no input.
        subnodes = {src for src, kinds in wf["connections"].items() if any(k != "main" for k in kinds)}
        orphans = [n["name"] for n in wf["nodes"] if n["name"] not in incoming | subnodes
                   and not any(t in n["type"] for t in ("Trigger", "webhook", "scheduleTrigger", "manualTrigger"))]
        assert not orphans, f"{wf['name']}: nodes with nothing feeding them: {orphans}"
        names = [n["name"] for n in wf["nodes"]]
        assert len(names) == len(set(names)), f"{wf['name']}: duplicate node names"
        redacted = json.dumps(wf, indent=2, ensure_ascii=False)
        with open(os.path.join(repo, "workflows", filename), "w") as f:
            f.write(redacted + "\n")

        live = redacted
        for placeholder, real in CREDS.items():
            live = live.replace(placeholder, real)
        for placeholder in CREDS:
            assert placeholder not in live, f"{placeholder} not substituted in {filename}"
        for real in CREDS.values():
            assert real not in redacted, f"live credential leaked into {filename}"

        with open(os.path.join(scratch, filename.replace(".json", ".live.json")), "w") as f:
            f.write(live + "\n")
        print(f"{w.name:32} {len(w.nodes):>3} nodes -> workflows/{filename}")


if __name__ == "__main__":
    main()
