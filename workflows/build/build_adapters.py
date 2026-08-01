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

CREDS = {
    "POSTGRES_CRED_ID":        "VVIxtEmT1GWsEZn9",
    "OPENAI_CRED_ID":          "2dmtKOJYL68BMOyG",
    "SLACK_LEAD_BOT_CRED_ID":  "4juRUVuQfSLuSC9y",
    "SLACK_OWNER_BOT_CRED_ID": "AL4h8l7yMMNZHFBE",
    "IG_TOKEN_CRED_ID":        "DQWn7WWwGN2XkfSV",
}

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

    w.node("Extract IG Messages", "n8n-nodes-base.code", 2, [-20, 160], {"jsCode": r"""
// Flattens Meta's batched webhook payload into one item per inbound text DM.
// `entry` and `messaging` are both arrays: a single POST can legitimately carry
// messages from several different people.
const out = [];

for (const item of $input.all()) {
  const body = item.json.body ?? {};
  if (body.object !== 'instagram') continue;

  for (const entry of body.entry ?? []) {
    for (const ev of entry.messaging ?? []) {
      const msg = ev.message;
      if (!msg) continue;          // delivery / read receipts
      if (msg.is_echo) continue;   // our own outbound DM echoed back
      if (!msg.text) continue;     // reactions, attachments, stickers

      out.push({ json: {
        ig_sender: ev.sender?.id,
        ig_mid: msg.mid,
        text: msg.text,
        // The account the lead messaged. This is what resolves the studio.
        account_ref: String(ev.recipient?.id ?? entry.id ?? ''),
        received_at: ev.timestamp ? new Date(ev.timestamp).toISOString() : new Date().toISOString(),
      } });
    }
  }
}

return out;
"""})
    w.connect("Verify IG Signature", "Extract IG Messages")

    # Durable dedupe. Write-then-check: let the primary key arbitrate, so two
    # concurrent deliveries of the same mid cannot both pass.
    w.pg("Claim Message IDs", [200, 160], """INSERT INTO processed_messages (provider, provider_msg_id)
VALUES ('instagram', $1)
ON CONFLICT (provider, provider_msg_id) DO NOTHING
RETURNING provider_msg_id;""",
         replacement="={{ [$json.ig_mid] }}",
         alwaysOutputData=True, retryOnFail=True)
    w.connect("Extract IG Messages", "Claim Message IDs")

    w.node("Drop Replays", "n8n-nodes-base.code", 2, [420, 160], {"jsCode": r"""
// Keep only the messages whose mid we actually just claimed. A mid already in
// processed_messages returns no row, so it never reaches the brain — this is
// what makes a Meta retry (or a replay after an n8n restart) a no-op.
const claimed = new Set(
  $input.all().map((i) => i.json.provider_msg_id).filter(Boolean)
);
return $('Extract IG Messages').all().filter((i) => claimed.has(i.json.ig_mid));
"""})
    w.connect("Claim Message IDs", "Drop Replays")

    w.node("Build Inbound Contract", "n8n-nodes-base.code", 2, [640, 160], {
        "mode": "runOnceForEachItem", "jsCode": r"""
// The channel-agnostic contract the Lead Brain accepts. Note there is no
// studio_id: the brain resolves that from (channel, account_ref) itself, so an
// adapter can never assert which studio a message belongs to.
return { json: {
  channel: 'instagram',
  account_ref: $json.account_ref,
  external_id: String($json.ig_sender),
  name: null,
  message: String($json.text ?? ''),
  provider_message_id: $json.ig_mid,
  received_at: $json.received_at,
} };
"""})
    w.connect("Drop Replays", "Build Inbound Contract")

    w.call_brain("Call Lead Brain", [860, 160], brain_id)
    w.connect("Build Inbound Contract", "Call Lead Brain")

    w.node("Should Reply?", "n8n-nodes-base.if", 2.2, [1080, 160], {
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

    # NOTE (multi-studio): n8n cannot pick a credential by expression, so this
    # node uses one static Instagram token. `channel_accounts.credential_ref`
    # already records which credential each studio should use; adding studio #2
    # means adding a Switch on that value plus one send node per credential.
    w.node("Send IG Reply", "n8n-nodes-base.httpRequest", 4.2, [1300, 100], {
        "method": "POST",
        "url": "https://graph.instagram.com/v25.0/me/messages",
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "sendHeaders": True,
        "headerParameters": {"parameters": [{"name": "Content-Type",
                                             "value": "application/json"}]},
        "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ recipient: { id: $json.external_id }, message: { text: $json.reply } }) }}",
        "options": {}},
        credentials={"httpHeaderAuth": {"id": "IG_TOKEN_CRED_ID",
                                        "name": "Instagram DM Token"}},
        onError="continueRegularOutput", retryOnFail=True)
    w.connect("Should Reply?", "Send IG Reply", out=0)

    w.node("No Reply Sent", "n8n-nodes-base.noOp", 1, [1300, 240], {})
    w.connect("Should Reply?", "No Reply Sent", out=1)

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
    w = WF("Follow-up Scheduler", "00000000-0000-4000-8000-00000000a003",
           "fyk6eYsVGtUVS1dh")

    w.node("Follow-up Scheduler", "n8n-nodes-base.scheduleTrigger", 1.2, [-460, 0], {
        "rule": {"interval": [{"field": "minutes", "minutesInterval": 15}]}})

    w.pg("Find Stale Leads", [-240, 0], """-- Leads who went quiet and are still fair game for an automated nudge.
--
-- Exclusions that did not exist before: `escalated` leads (handed to a human --
-- they used to sit at 'engaged' and get nudged an hour after asking a medical
-- or pricing question) and `ai_paused` leads (the owner has taken the thread
-- over). Cadence comes from studios, not from literals in this query.
WITH last_inbound AS (
  SELECT lead_id, MAX(created_at) AS t FROM messages WHERE direction = 'inbound' GROUP BY lead_id
),
last_msg AS (
  SELECT lead_id, MAX(created_at) AS t FROM messages GROUP BY lead_id
),
out_since AS (
  SELECT m.lead_id, COUNT(*) AS n
    FROM messages m JOIN last_inbound li ON li.lead_id = m.lead_id
   WHERE m.direction = 'outbound' AND m.created_at > li.t
   GROUP BY m.lead_id
)
SELECT l.id, l.external_id, l.channel, l.name,
       COALESCE(os.n, 0) AS nudges_sent,
       (SELECT m.body FROM messages m
         WHERE m.lead_id = l.id AND m.direction = 'inbound'
         ORDER BY m.created_at DESC LIMIT 1) AS last_msg,
       s.id AS studio_id, s.timezone, s.prompt_vars, s.offers, s.class_capacity,
       s.slack_lead_channel_id, s.slack_owner_channel_id, s.max_nudges,
       (COALESCE(os.n, 0) >= s.max_nudges) AS is_final
  FROM leads l
  JOIN studios s  ON s.id = l.studio_id
  JOIN last_msg lm ON lm.lead_id = l.id
  LEFT JOIN out_since os ON os.lead_id = l.id
 WHERE l.status = 'engaged'
   AND NOT l.ai_paused
   AND s.active
   AND COALESCE(os.n, 0) BETWEEN 1 AND s.max_nudges
   AND lm.t < now() - make_interval(mins => CASE
                                              WHEN COALESCE(os.n, 0) = 1
                                                THEN s.followup_first_after_minutes
                                              ELSE s.followup_final_after_minutes
                                            END)
 ORDER BY lm.t ASC
 LIMIT 20;""", alwaysOutputData=True, retryOnFail=True)
    w.connect("Follow-up Scheduler", "Find Stale Leads")

    w.node("Has Stale Leads?", "n8n-nodes-base.filter", 2.2, [-20, 0], {
        "conditions": {"options": {"version": 2, "leftValue": "", "caseSensitive": True,
                                   "typeValidation": "loose"},
                       "conditions": [{"id": "has-id", "leftValue": "={{ $json.id }}",
                                       "rightValue": "",
                                       "operator": {"type": "string", "operation": "notEmpty",
                                                    "singleValue": True}}],
                       "combinator": "and"}, "options": {}})
    w.connect("Find Stale Leads", "Has Stale Leads?")

    w.node("Compose Follow-up", "n8n-nodes-base.code", 2, [200, 0], {
        "mode": "runOnceForEachItem", "jsCode": r"""
// Builds the AI prompt AND a persuasive template fallback.
//
// The fallback is genuinely reachable, not dead code: Write Nudge (AI) runs
// with onError=continueRegularOutput, so an OpenAI failure passes this item
// straight through with no `output` field and Assemble Nudge falls back to
// `followup_text` below.
const v = (typeof $json.prompt_vars === 'string')
            ? JSON.parse($json.prompt_vars) : ($json.prompt_vars || {});
const first = $json.name ? (' ' + String($json.name).trim().split(/\s+/)[0]) : '';
const persona = v.persona_name || 'the studio';
const offer = v.offer || $json.offers || 'our intro offer';
const cap = $json.class_capacity;
const isFinal = $json.is_final === true || $json.is_final === 'true';

const msgs = [
  `Hey${first} 😊 no rush at all — I know starting something new can feel a bit daunting, especially if you're not sure it's for you yet. Our beginner classes are small (max ${cap}) and built for total first-timers, so you'd be in really good hands. Your ${offer} is still open — want me to suggest an easy time to come try one class? No commitment, just see how it feels.`,
  `Hey${first}, just checking in one last time 💛 honestly, most of our members were on the fence before their first class and now they can't imagine their week without it. If you're even a little curious, just tell me which day suits and I'll hold a spot for you — nothing to lose. And if the timing isn't right, no worries at all, I'm here whenever you're ready.`,
];

const systemMessage = [
`You are ${persona}, the warm, friendly owner of ${v.studio_name || 'the studio'}, a ${v.studio_type || 'fitness studio'} in ${v.location || ''}. You are writing a short follow-up DM to a lead who asked about classes but went quiet — they seem interested but unsure about committing. Re-engage them and make trying a class feel easy, WITHOUT being pushy or salesy.`,
``,
`Rules:`,
`- 1 to 3 short sentences, warm and human, like a real text from a person. No 'Dear', no formal sign-off, no subject line.`,
`- Use what they last said to gently address their specific hesitation.`,
`- Naturally remind them of the value: ${offer}, small classes (max ${cap})${v.amenities ? ', ' + v.amenities.toLowerCase() : ''}.`,
`- End with ONE easy, low-friction question or invite.`,
`- If it is the FINAL nudge, be extra gentle and leave the door open.`,
`- Output ONLY the message text. No quotes, no labels, no JSON. At most one tasteful emoji.`,
].join('\n');

const userMessage = [
`Write a follow-up message — nudge #${Number($json.nudges_sent)}${isFinal ? ' (FINAL nudge: extra gentle, leave the door open)' : ''}.`,
`Lead's first name: ${$json.name || 'there'}`,
`What they last said to us: "${$json.last_msg ?? ''}"`,
`Write the message now.`,
].join('\n');

return { json: {
  id: $json.id,
  external_id: $json.external_id,
  channel: $json.channel,
  studio_id: $json.studio_id,
  slack_lead_channel_id: $json.slack_lead_channel_id,
  slack_owner_channel_id: $json.slack_owner_channel_id,
  name: $json.name ?? null,
  last_msg: $json.last_msg ?? '',
  followup_text: msgs[Math.min(Math.max(Number($json.nudges_sent) - 1, 0), msgs.length - 1)],
  nudge_number: Number($json.nudges_sent),
  is_final: isFinal,
  systemMessage,
  userMessage,
} };
"""})
    w.connect("Has Stale Leads?", "Compose Follow-up")

    w.node("Write Nudge (AI)", "@n8n/n8n-nodes-langchain.agent", 3.1, [420, 0], {
        "promptType": "define", "text": "={{ $json.userMessage }}",
        "options": {"systemMessage": "={{ $json.systemMessage }}"}},
        onError="continueRegularOutput", retryOnFail=True)
    w.connect("Compose Follow-up", "Write Nudge (AI)")

    w.node("OpenAI (Nudge)", "@n8n/n8n-nodes-langchain.lmChatOpenAi", 1.3, [420, 200], {
        "model": {"__rl": True, "value": "gpt-5.4-mini", "mode": "list",
                  "cachedResultName": "gpt-5.4-mini"},
        "builtInTools": {}, "options": {}},
        credentials={"openAiApi": {"id": "OPENAI_CRED_ID", "name": "OpenAI account"}})
    w.connect("OpenAI (Nudge)", "Write Nudge (AI)", kind="ai_languageModel")

    w.node("Assemble Nudge", "n8n-nodes-base.set", 3.4, [640, 0], {
        "assignments": {"assignments": [
            {"id": "a1", "name": "followup_text", "type": "string",
             "value": "={{ $json.output || $('Compose Follow-up').item.json.followup_text }}"},
            {"id": "a2", "name": "id", "type": "number",
             "value": "={{ $('Compose Follow-up').item.json.id }}"},
            {"id": "a3", "name": "external_id", "type": "string",
             "value": "={{ $('Compose Follow-up').item.json.external_id }}"},
            {"id": "a4", "name": "channel", "type": "string",
             "value": "={{ $('Compose Follow-up').item.json.channel }}"},
            {"id": "a5", "name": "nudge_number", "type": "number",
             "value": "={{ $('Compose Follow-up').item.json.nudge_number }}"},
            {"id": "a6", "name": "is_final", "type": "boolean",
             "value": "={{ $('Compose Follow-up').item.json.is_final }}"},
            {"id": "a7", "name": "slack_lead_channel_id", "type": "string",
             "value": "={{ $('Compose Follow-up').item.json.slack_lead_channel_id }}"},
            {"id": "a8", "name": "slack_owner_channel_id", "type": "string",
             "value": "={{ $('Compose Follow-up').item.json.slack_owner_channel_id }}"},
        ]}, "options": {}})
    w.connect("Write Nudge (AI)", "Assemble Nudge")

    w.pg("Log Follow-up (outbound)", [860, -180], """INSERT INTO messages (lead_id, direction, channel, body, created_at)
VALUES ($1, 'outbound', $2, $3, now());""",
         replacement="={{ [$json.id, $json.channel, $json.followup_text] }}",
         onError="continueRegularOutput", retryOnFail=True)

    w.pg("Mark Cold if Final", [860, -60], """UPDATE leads
   SET status = CASE WHEN $2 THEN 'cold' ELSE status END, updated_at = now()
 WHERE id = $1;""",
         replacement="={{ [$json.id, $json.is_final] }}",
         onError="continueRegularOutput", retryOnFail=True)

    w.slack("Notify Owner - Follow-up", [860, 60],
            "={{ $json.slack_owner_channel_id }}",
            ("=:wave: Auto follow-up #{{ $json.nudge_number }} for {{ $json.external_id }} "
             "({{ $json.channel }}): \"{{ $json.followup_text }}\""
             "{{ $json.channel === 'slack' ? '' : '\\n:information_source: Not auto-sent — "
             "Instagram nudges outside Meta''s 24h window need a message tag, so this one is "
             "logged for you to send.' }}"),
            "SLACK_OWNER_BOT_CRED_ID")

    w.node("Is Slack Lead?", "n8n-nodes-base.if", 2.2, [860, 200], {
        "conditions": {"options": {"version": 2, "leftValue": "", "caseSensitive": True,
                                   "typeValidation": "loose"},
                       "conditions": [{"id": "is-slack", "leftValue": "={{ $json.channel }}",
                                       "rightValue": "slack",
                                       "operator": {"type": "string", "operation": "equals"}}],
                       "combinator": "and"}, "options": {}})

    for dst in ["Log Follow-up (outbound)", "Mark Cold if Final",
                "Notify Owner - Follow-up", "Is Slack Lead?"]:
        w.connect("Assemble Nudge", dst)

    w.slack("Send Follow-up to Lead (Slack)", [1080, 200],
            "={{ $json.slack_lead_channel_id }}",
            "={{ $json.followup_text }}", "SLACK_LEAD_BOT_CRED_ID")
    w.connect("Is Slack Lead?", "Send Follow-up to Lead (Slack)", out=0)

    w.node("IG Nudge (deferred)", "n8n-nodes-base.noOp", 1, [1080, 320], {})
    w.connect("Is Slack Lead?", "IG Nudge (deferred)", out=1)

    return w


# ===========================================================================
def main():
    scratch = sys.argv[1]
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
