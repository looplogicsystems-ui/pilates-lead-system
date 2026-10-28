#!/usr/bin/env python3
"""Generate the NEW workflows added by the pilot build (vault: Final System Changes).

  workflows/send-email.json         Send Email — every alert in the system goes through this
  workflows/ops-test-email.json     Ops: Test Email — run by hand to prove alerts reach both phones

Run: python3 workflows/build/build_pilot.py <scratch-dir>

Same contract as the other generators: a redacted copy (credential ids ->
placeholders) is written into workflows/ and committed; a live copy with real
ids goes to <scratch-dir> for push.py / deploy.sh.

NEW WORKFLOW IDS: a workflow's id is None until it exists on the mini. push.py
creates it with POST, n8n assigns the id, and that id is pasted into WF_IDS
below. From then on every push and every deploy.sh import targets the same
workflow — never a duplicate.

Email goes out through Resend's HTTP API (https://api.resend.com/emails), from
our own verified subdomain alerts.looplogicsystems.com — no SMTP anywhere. Gmail
was tried first (2026-10-24) and rejected three app passwords at the server, so
it was dropped. The API key is an n8n Header Auth credential restricted to
api.resend.com.

Founders' addresses are NOT in this file (the repo is public): they come from
$env.FOUNDER_ALERT_EMAILS in the mini's ~/looplogic/.env.
"""
import json
import os
import sys

from ig_common import IG_APP_ID, IG_REDIRECT_URI, GRAPH, TOKEN_CRYPTO_JS, GRAPH_JS

WF_IDS = {
    "send-email":     "PQWBdER3x291uWeU",
    "ops-test-email": "oh01IIck8xhZWADI",
    "demo-booking-page": "nxW4e7Mbf1dT3lqH",
    "ops-voice-lab":  "EeLbVJcxMU52wHen",
    "owner-actions":  "C3KkwuJYPyrwGUZS",
    "link-redirect":  "6Owetg7CkG7wRK2t",
    "ig-connect":     "C5M1lM4n3FgKO0J1",
}

LEAD_BRAIN_ID = "fvHhAUDCbVN9ELb0"
# sha256 of the voice-lab token. The token itself lives only in
# ~/.config/looplogic/voicelab.token on the build machine (mode 600).
VOICE_LAB_TOKEN_SHA256 = "124df4a67bfe4d1d2a19b855935999c32f3e09109213981958ce165f7dd33a2b"

# placeholder -> live credential id. None = credential not created on the mini yet;
# workflows that need it are written redacted but skipped for the live build.
CREDS = {
    "RESEND_CRED_ID": "VAp4NUeGTiCv51IF",     # n8n Header Auth: Authorization: Bearer <Resend API key>
    "POSTGRES_CRED_ID": "VVIxtEmT1GWsEZn9",
}

ERROR_HANDLER_ID = "4L4K72aSSwiBcnjX"

SETTINGS = {
    "executionOrder": "v1",
    "saveDataErrorExecution": "all",
    "saveDataSuccessExecution": "none",
    "saveManualExecutions": True,
    "callerPolicy": "workflowsFromSameOwner",
    "errorWorkflow": ERROR_HANDLER_ID,
}


def conn(*pairs):
    out = {}
    for a, b in pairs:
        out.setdefault(a, {"main": [[]]})["main"][0].append({"node": b, "type": "main", "index": 0})
    return out


# ---------------------------------------------------------------------------
# Send Email
#
# Called with Execute Workflow. Input (one item per email):
#   to       "founders"  -> $env.FOUNDER_ALERT_EMAILS
#            or one or more addresses, comma-separated (e.g. studios.owner_alert_email)
#   subject  required
#   heading  optional, big line at the top
#   lines    paragraph strings (plain text; escaped here, never trusted as HTML)
#   buttons  [{label, url}] — https only (Pause / Resume / Mark booked)
#   note     optional small grey line at the bottom
#
# CALLERS: set the Execute Workflow node to continue on error. An alert that
# fails must never stop a lead from getting her reply; the failure still reaches
# the Error Handler (dead letter + Slack) through this workflow's errorWorkflow.

BUILD_EMAIL_JS = r"""
const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// Our own verified Resend domain — not personal, so it can live in the repo.
const FROM = 'Loop Logic Alerts <noreply@alerts.looplogicsystems.com>';

const out = [];
for (const item of $input.all()) {
  const j = item.json || {};

  // "founders" may appear on its own or in a list ("sarah@studio.com, founders").
  const to = String(j.to || '').split(',').map((a) => a.trim()).filter(Boolean)
    .flatMap((a) => a === 'founders' ? String($env.FOUNDER_ALERT_EMAILS || '').split(',') : [a])
    .map((a) => a.trim()).filter(Boolean).join(', ');
  if (!to) throw new Error(`Send Email: no recipient (to="${j.to}")`);
  if (!j.subject) throw new Error('Send Email: no subject');

  const lines = Array.isArray(j.lines) ? j.lines : (j.lines ? [j.lines] : []);
  // https only: a button is a link a studio owner will tap on her phone.
  const buttons = (Array.isArray(j.buttons) ? j.buttons : [])
    .filter((b) => b && b.label && /^https:\/\//.test(String(b.url || '')));

  const btnHtml = buttons.map((b) =>
    `<a href="${esc(b.url)}" style="display:inline-block;margin:0 8px 8px 0;padding:10px 18px;` +
    `background:#1f2937;color:#ffffff;text-decoration:none;border-radius:6px;font-weight:600">${esc(b.label)}</a>`
  ).join('');

  const html =
    `<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;font-size:15px;line-height:1.5;color:#111;max-width:560px">` +
    (j.heading ? `<p style="font-size:18px;font-weight:700;margin:0 0 12px">${esc(j.heading)}</p>` : '') +
    lines.map((l) => `<p style="margin:0 0 10px">${esc(l)}</p>`).join('') +
    (btnHtml ? `<div style="margin:16px 0 8px">${btnHtml}</div>` : '') +
    (j.note ? `<p style="margin:16px 0 0;font-size:12px;color:#6b7280">${esc(j.note)}</p>` : '') +
    `</div>`;

  const text = [
    j.heading || '',
    ...lines,
    ...buttons.map((b) => `${b.label}: ${b.url}`),
    j.note || '',
  ].filter(Boolean).join('\n\n');

  // Resend takes an array of recipients (max 50).
  const toList = to.split(',').map((a) => a.trim()).filter(Boolean);
  out.push({ json: { to: toList, from: FROM, subject: String(j.subject), html, text } });
}
return out;
"""


def send_email():
    nodes = [
        {"id": "se-trigger", "name": "When Called", "type": "n8n-nodes-base.executeWorkflowTrigger",
         "typeVersion": 1.1, "position": [0, 0], "parameters": {"inputSource": "passthrough"}},
        {"id": "se-build", "name": "Build Email", "type": "n8n-nodes-base.code", "typeVersion": 2,
         "position": [240, 0], "parameters": {"jsCode": BUILD_EMAIL_JS.strip()}},
        # Resend returns {"id": "..."} on success and a 4xx/5xx with a message on
        # failure; a failure stops here and reaches the Error Handler.
        {"id": "se-send", "name": "Send via Resend", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
         "position": [480, 0],
         "parameters": {
             "method": "POST",
             "url": "https://api.resend.com/emails",
             "authentication": "genericCredentialType",
             "genericAuthType": "httpHeaderAuth",
             "sendBody": True,
             "specifyBody": "json",
             "jsonBody": "={{ JSON.stringify({ from: $json.from, to: $json.to, subject: $json.subject, html: $json.html, text: $json.text }) }}",
             "options": {},
         },
         "credentials": {"httpHeaderAuth": {"id": "RESEND_CRED_ID", "name": "Resend API (alerts)"}}},
        # Every terminal branch of a sub-workflow must emit an item, or Execute
        # Workflow's retry fires it again (vault: n8n Gotchas).
        {"id": "se-done", "name": "Sent", "type": "n8n-nodes-base.set", "typeVersion": 3.4,
         "position": [720, 0],
         "parameters": {"assignments": {"assignments": [
             {"id": "d1", "name": "sent", "type": "boolean", "value": "={{ true }}"},
             {"id": "d4", "name": "resend_id", "type": "string", "value": "={{ $json.id }}"},
             {"id": "d2", "name": "to", "type": "string", "value": "={{ $('Build Email').item.json.to.join(', ') }}"},
             {"id": "d3", "name": "subject", "type": "string", "value": "={{ $('Build Email').item.json.subject }}"},
         ]}, "options": {}}},
    ]
    return {"id": WF_IDS["send-email"], "name": "Send Email", "active": False,
            "versionId": "00000000-0000-4000-8000-00000000d001",
            "nodes": nodes,
            "connections": conn(("When Called", "Build Email"), ("Build Email", "Send via Resend"),
                                ("Send via Resend", "Sent")),
            "settings": dict(SETTINGS)}


# ---------------------------------------------------------------------------
# Ops: Test Email — manual only, no public endpoint. Run on the mini with
#   docker exec n8n n8n execute --id=<id>
# It proves the whole alert path (env vars, Resend key + domain, delivery,
# both founders' phones) — the go-live gate item "alerts reach both phones".

def ops_test_email():
    nodes = [
        {"id": "te-manual", "name": "Run Test", "type": "n8n-nodes-base.manualTrigger",
         "typeVersion": 1, "position": [0, 0], "parameters": {}},
        {"id": "te-msg", "name": "Test Message", "type": "n8n-nodes-base.set", "typeVersion": 3.4,
         "position": [240, 0],
         "parameters": {"mode": "raw", "jsonOutput": json.dumps({
             "to": "founders",
             "subject": "Loop Logic — alert test",
             "heading": "If you can read this on your phone, alerts work.",
             "lines": ["This is a test from the Ops: Test Email workflow on the Mac mini.",
                       "Check it landed in the INBOX, not spam. If it is in spam, mark it 'not spam' once."],
             "buttons": [{"label": "Open n8n", "url": "https://n8n.looplogicsystems.com/"}],
             "note": "Sent by the pilot build, step 3 (email alerts).",
         }), "options": {}}},
        {"id": "te-call", "name": "Send Email", "type": "n8n-nodes-base.executeWorkflow", "typeVersion": 1.2,
         "position": [480, 0],
         "parameters": {"workflowId": {"__rl": True, "mode": "id", "value": "SEND_EMAIL_WF_ID"},
                        "options": {"waitForSubWorkflow": True}}},
    ]
    return {"id": WF_IDS["ops-test-email"], "name": "Ops: Test Email", "active": False,
            "versionId": "00000000-0000-4000-8000-00000000d002",
            "nodes": nodes,
            "connections": conn(("Run Test", "Test Message"), ("Test Message", "Send Email")),
            "settings": dict(SETTINGS)}


# ---------------------------------------------------------------------------
# Demo Booking Page — GET https://n8n.looplogicsystems.com/webhook/demo/book
#
# The fictional demo studio's "booking software". The AI sends leads here, so
# for the demo it must work — and it must show the SAME classes and intro
# price the AI recommends. So it reads studio_classes / studio_offers live:
# an edit in Beekeeper shows up here and in the AI's replies at once.
# Real studios never use this — their links go to their own Mindbody/Momence.
# Nothing is booked or paid: "Reserve" only shows a demo confirmation.

DEMO_STUDIO_ID = 2

DEMO_QUERY = """
SELECT s.name AS studio_name, s.timezone,
       s.prompt_vars->>'address' AS address,
       (SELECT json_agg(json_build_object('id', c.id, 'name', c.name, 'weekday', c.weekday,
                                          'time', to_char(c.start_local, 'HH24:MI'),
                                          'minutes', c.duration_minutes, 'level', c.level)
                        ORDER BY c.weekday, c.start_local)
          FROM studio_classes c WHERE c.studio_id = s.id AND c.active) AS classes,
       (SELECT json_build_object('name', o.name, 'price', o.price, 'currency', o.currency, 'details', o.details)
          FROM studio_offers o WHERE o.studio_id = s.id AND o.active AND o.kind = 'intro'
         ORDER BY o.sort_order LIMIT 1) AS intro
  FROM studios s WHERE s.id = """ + str(DEMO_STUDIO_ID) + ";"

DEMO_PAGE_JS = r"""
const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const row = $input.first().json || {};
const zone = row.timezone || 'America/Chicago';
const classes = Array.isArray(row.classes) ? row.classes : [];
const intro = row.intro || null;
const highlightIntro = String(($('Booking Page Request').first().json.query || {}).offer || '') === 'intro';

// Next 7 days in the studio's own timezone (Luxon handles the clock change).
const now = DateTime.now().setZone(zone);
const days = [];
for (let d = 0; d < 7; d++) {
  const day = now.startOf('day').plus({ days: d });
  const slots = classes.filter((c) => c.weekday === day.weekday).map((c) => {
    const [h, m] = c.time.split(':').map(Number);
    const at = day.set({ hour: h, minute: m });
    return { ...c, at };
  }).filter((c) => c.at > now);
  if (slots.length) days.push({ day, slots });
}

const money = (p, cur) => (p == null ? '' : (cur === 'USD' || !cur ? '$' : cur + ' ') + Number(p).toFixed(0));
const introHtml = intro ? `
  <div class="card intro${highlightIntro ? ' hl' : ''}">
    <div class="tag">New to ${esc(row.studio_name)}?</div>
    <div class="title">${esc(intro.name)}</div>
    ${intro.details ? `<div class="muted">${esc(intro.details)}</div>` : ''}
    <button class="btn" onclick="buyIntro()">Get it — ${esc(money(intro.price, intro.currency))}</button>
  </div>` : '';

const dayHtml = days.map(({ day, slots }) => `
  <div class="day">${esc(day.toFormat('cccc d LLL'))}</div>
  ${slots.map((c) => `
    <div class="card slot">
      <div><div class="title">${esc(c.at.toFormat('h:mm a'))} · ${esc(c.name)}</div>
        <div class="muted">${esc(c.minutes)} min${c.level ? ' · ' + esc(c.level) : ''}</div></div>
      <button class="btn small" onclick="reserve('${esc(c.name)}', '${esc(c.at.toFormat('cccc d LLL, h:mm a'))}')">Reserve</button>
    </div>`).join('')}`).join('');

const html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Book a class — ${esc(row.studio_name)}</title>
<style>
  body{margin:0;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:#f6f4f1;color:#1f1d1a}
  header{background:#1f1d1a;color:#fff;padding:18px 16px}
  header .name{font-size:20px;font-weight:700} header .addr{font-size:13px;opacity:.75;margin-top:2px}
  main{max-width:560px;margin:0 auto;padding:14px}
  .card{background:#fff;border-radius:12px;padding:14px;margin:10px 0;box-shadow:0 1px 2px rgba(0,0,0,.06)}
  .intro{border:2px solid transparent} .intro.hl{border-color:#c2410c}
  .tag{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:#c2410c;font-weight:700}
  .title{font-weight:650;margin:4px 0} .muted{font-size:13px;color:#6b6560}
  .slot{display:flex;justify-content:space-between;align-items:center;gap:10px}
  .day{margin:18px 4px 4px;font-weight:700;font-size:14px;color:#6b6560}
  .btn{background:#1f1d1a;color:#fff;border:0;border-radius:8px;padding:11px 16px;font-size:15px;font-weight:600;margin-top:10px;cursor:pointer}
  .btn.small{margin:0;padding:9px 14px;font-size:14px;white-space:nowrap}
  #ok{display:none;position:fixed;inset:0;background:rgba(0,0,0,.45);align-items:center;justify-content:center;padding:20px}
  #ok .card{max-width:380px;text-align:center}
  footer{font-size:11px;color:#8a837c;text-align:center;padding:24px 16px}
</style></head><body>
<header><div class="name">${esc(row.studio_name)}</div><div class="addr">${esc(row.address || '')}</div></header>
<main>
  ${introHtml}
  ${dayHtml || '<div class="card muted">No classes on the timetable this week.</div>'}
</main>
<div id="ok"><div class="card"><div class="title" id="okt"></div><div class="muted" id="okd"></div>
  <button class="btn" onclick="document.getElementById('ok').style.display='none'">Done</button></div></div>
<footer>Demo booking page for a fictional studio. Nothing is booked and no payment is taken.</footer>
<script>
  function show(t, d){ document.getElementById('okt').textContent=t; document.getElementById('okd').textContent=d;
    document.getElementById('ok').style.display='flex'; }
  function buyIntro(){ show('Intro pass added', 'Now pick your first class below.'); }
  function reserve(n, w){ show("You're booked!", n + ' — ' + w + '. See you there.'); }
</script></body></html>`;

return [{ json: { html } }];
"""


def demo_booking_page():
    nodes = [
        {"id": "db-hook", "name": "Booking Page Request", "type": "n8n-nodes-base.webhook", "typeVersion": 2.1,
         "position": [0, 0], "webhookId": "5d3e1b7a-2c94-4f0e-9a61-8b7d2e4c0f19",
         "parameters": {"httpMethod": "GET", "path": "demo/book", "responseMode": "responseNode", "options": {}}},
        {"id": "db-load", "name": "Load Timetable & Intro", "type": "n8n-nodes-base.postgres", "typeVersion": 2.6,
         "position": [240, 0],
         "parameters": {"operation": "executeQuery", "query": DEMO_QUERY.strip(), "options": {}},
         "credentials": {"postgres": {"id": "POSTGRES_CRED_ID", "name": "Postgres account"}}},
        {"id": "db-html", "name": "Build Page", "type": "n8n-nodes-base.code", "typeVersion": 2,
         "position": [480, 0], "parameters": {"jsCode": DEMO_PAGE_JS.strip()}},
        {"id": "db-respond", "name": "Respond", "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.5,
         "position": [720, 0],
         "parameters": {"respondWith": "text", "responseBody": "={{ $json.html }}",
                        "options": {"responseHeaders": {"entries": [
                            {"name": "Content-Type", "value": "text/html; charset=utf-8"},
                            {"name": "Cache-Control", "value": "no-store"}]}}}},
    ]
    return {"id": WF_IDS["demo-booking-page"], "name": "Demo Booking Page", "active": False,
            "versionId": "00000000-0000-4000-8000-00000000d003",
            "nodes": nodes,
            "connections": conn(("Booking Page Request", "Load Timetable & Intro"),
                                ("Load Timetable & Intro", "Build Page"), ("Build Page", "Respond")),
            "settings": dict(SETTINGS)}


# ---------------------------------------------------------------------------
# Ops: Voice Lab — POST /webhook/ops/voice-lab   (header X-Voice-Lab-Token)
#
# Runs a message through the REAL Lead Brain as a lead on the 'test' channel
# (channel_accounts: test / voice-lab -> the demo studio) and returns its
# result, so the AI's voice can be tuned over whole conversations without
# anyone sending DMs. Owner emails for 'test' leads go to Resend's sink
# (Lead Brain Decide). Locked by a token whose sha256 is the only thing here.

VOICE_LAB_GATE_JS = r"""
const crypto = require('crypto');
const h = $input.first().json;
const token = String((h.headers || {})['x-voice-lab-token'] || '');
const digest = crypto.createHash('sha256').update(token).digest('hex');
const expected = '__TOKEN_SHA256__';
const ok = token.length > 20 && digest.length === expected.length
  && crypto.timingSafeEqual(Buffer.from(digest), Buffer.from(expected));
const b = h.body || {};
if (!ok || !b.lead || !b.message) return [{ json: { allowed: false } }];
return [{ json: { allowed: true, inbound: {
  channel: 'test', account_ref: 'voice-lab',
  external_id: 'lab-' + String(b.lead).replace(/[^A-Za-z0-9_-]/g, '').slice(0, 40),
  name: b.name || null, message: String(b.message).slice(0, 2000),
  provider_message_id: 'lab-' + crypto.randomBytes(8).toString('hex'),
  received_at: new Date().toISOString(),
} } }];
"""


def ops_voice_lab():
    nodes = [
        {"id": "vl-hook", "name": "Voice Lab Request", "type": "n8n-nodes-base.webhook", "typeVersion": 2.1,
         "position": [0, 0], "webhookId": "8a1f2c3d-4e5b-46a7-9c8d-0e1f2a3b4c5d",
         "parameters": {"httpMethod": "POST", "path": "ops/voice-lab", "responseMode": "responseNode",
                        "options": {}}},
        {"id": "vl-gate", "name": "Check Token", "type": "n8n-nodes-base.code", "typeVersion": 2,
         "position": [220, 0],
         "parameters": {"jsCode": VOICE_LAB_GATE_JS.strip().replace("__TOKEN_SHA256__", VOICE_LAB_TOKEN_SHA256)}},
        {"id": "vl-if", "name": "Allowed?", "type": "n8n-nodes-base.if", "typeVersion": 2.2,
         "position": [440, 0],
         "parameters": {"conditions": {"options": {"version": 2, "leftValue": "", "caseSensitive": True,
                                                   "typeValidation": "loose"},
                                       "conditions": [{"id": "ok", "leftValue": "={{ $json.allowed }}",
                                                       "rightValue": "true",
                                                       "operator": {"type": "boolean", "operation": "true",
                                                                    "singleValue": True}}],
                                       "combinator": "and"}, "options": {}}},
        {"id": "vl-in", "name": "Inbound", "type": "n8n-nodes-base.code", "typeVersion": 2,
         "position": [660, -100], "parameters": {"jsCode": "return [{ json: $input.first().json.inbound }];"}},
        {"id": "vl-brain", "name": "Lead Brain", "type": "n8n-nodes-base.executeWorkflow", "typeVersion": 1.2,
         "position": [880, -100],
         "parameters": {"workflowId": {"__rl": True, "mode": "id", "value": LEAD_BRAIN_ID},
                        "options": {"waitForSubWorkflow": True}}},
        {"id": "vl-ok", "name": "Respond Result", "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.5,
         "position": [1100, -100],
         "parameters": {"respondWith": "json", "responseBody": "={{ JSON.stringify($json) }}", "options": {}}},
        {"id": "vl-no", "name": "Respond Forbidden", "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.5,
         "position": [660, 120],
         "parameters": {"respondWith": "text", "responseBody": "Forbidden", "options": {"responseCode": 403}}},
    ]
    conns = conn(("Voice Lab Request", "Check Token"), ("Check Token", "Allowed?"),
                 ("Inbound", "Lead Brain"), ("Lead Brain", "Respond Result"))
    conns["Allowed?"] = {"main": [[{"node": "Inbound", "type": "main", "index": 0}],
                                  [{"node": "Respond Forbidden", "type": "main", "index": 0}]]}
    return {"id": WF_IDS["ops-voice-lab"], "name": "Ops: Voice Lab", "active": False,
            "versionId": "00000000-0000-4000-8000-00000000d004",
            "nodes": nodes, "connections": conns, "settings": dict(SETTINGS)}


def respond(name, pos, body_expr, code=200, ctype="text/html; charset=utf-8", extra_headers=()):
    headers = [{"name": "Content-Type", "value": ctype}, {"name": "Cache-Control", "value": "no-store"},
               {"name": "X-Robots-Tag", "value": "noindex"}] + list(extra_headers)
    params = {"respondWith": "text", "responseBody": body_expr,
              "options": {"responseCode": code, "responseHeaders": {"entries": headers}}}
    return {"id": name.lower().replace(" ", "-").replace(":", ""), "name": name,
            "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.5, "position": pos,
            "parameters": params}


def switch(name, pos, field, keys):
    return {"id": name.lower().replace(" ", "-"), "name": name, "type": "n8n-nodes-base.switch",
            "typeVersion": 3.4, "position": pos,
            "parameters": {"rules": {"values": [
                {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict",
                                            "version": 3},
                                "conditions": [{"id": f"{name}-{k}", "leftValue": f"={{{{ $json.{field} }}}}",
                                                "rightValue": k,
                                                "operator": {"type": "string", "operation": "equals"}}],
                                "combinator": "and"},
                 "renameOutput": True, "outputKey": k} for k in keys]}, "options": {}}}


def pg_node(name, pos, query, replacement, **kw):
    n = {"id": name.lower().replace(" ", "-").replace("&", "and"), "name": name,
         "type": "n8n-nodes-base.postgres", "typeVersion": 2.6, "position": pos,
         "parameters": {"operation": "executeQuery", "query": query.strip(),
                        "options": {"queryReplacement": replacement}},
         "credentials": {"postgres": {"id": "POSTGRES_CRED_ID", "name": "Postgres account"}}}
    n.update(kw)
    return n


# ---------------------------------------------------------------------------
# Owner Actions — the Pause / Resume / Mark-booked buttons in owner emails.
#
#   GET  /webhook/owner?a=<pause|resume|booked>&l=<lead id>&t=<signature>
#        -> a CONFIRM page with one button. Nothing changes on GET: email
#           security scanners open every link in a message, and a plain GET
#           link would pause leads on its own.
#   POST /webhook/owner   (the confirm button) -> verify again, apply, "Done".
#
# t = first 32 hex of HMAC-SHA256(OWNER_LINK_SECRET, "<action>:<lead id>").
# Lead Brain signs with the same function, so an edited lead id or action is
# refused (403). The secret is in the mini's .env, never in this repo.

OWNER_VERIFY_JS = r"""
const crypto = require('crypto');
const j = $input.first().json;
const isPost = !!(j.body && j.body.a);
const src = isPost ? j.body : (j.query || {});
const a = String(src.a || ''), l = String(src.l || ''), t = String(src.t || '');
const secret = String($env.OWNER_LINK_SECRET || '');

const LABELS = {
  pause:  { ask: 'Pause the AI for this lead?', go: 'Yes, pause it',
            done: 'Paused. The AI won’t reply to this lead until you tap Resume in one of its emails.' },
  resume: { ask: 'Hand this lead back to the AI?', go: 'Yes, resume',
            done: 'Resumed. The AI will answer this lead’s next message.' },
  booked: { ask: 'Mark this lead as booked?', go: 'Yes, she’s booked',
            done: 'Marked as booked. It will count in your day-30 report.' },
};
const sign = (act, lead) => crypto.createHmac('sha256', secret).update(`${act}:${lead}`).digest('hex').slice(0, 32);
let valid = false;
if (secret && LABELS[a] && /^\d{1,10}$/.test(l) && /^[0-9a-f]{32}$/.test(t)) {
  const want = Buffer.from(sign(a, l)), got = Buffer.from(t);
  valid = want.length === got.length && crypto.timingSafeEqual(want, got);
}

const page = (title, body) => `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>${title}</title>
<style>body{margin:0;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:#f6f4f1;color:#1f1d1a}
main{max-width:420px;margin:12vh auto;padding:24px;background:#fff;border-radius:14px;box-shadow:0 1px 3px rgba(0,0,0,.08);text-align:center}
h1{font-size:20px;margin:0 0 14px} p{color:#6b6560;line-height:1.5}
button{background:#1f1d1a;color:#fff;border:0;border-radius:8px;padding:13px 22px;font-size:16px;font-weight:600;cursor:pointer}</style>
</head><body><main>${body}</main></body></html>`;

let route, html;
if (!valid) {
  route = 'deny';
  html = page('Link not valid', '<h1>This link isn’t valid</h1><p>It may have been changed or cut off. Use the button in the original email.</p>');
} else if (!isPost) {
  route = 'confirm';
  html = page(LABELS[a].ask, `<h1>${LABELS[a].ask}</h1>
    <form method="POST" action=""><input type="hidden" name="a" value="${a}"><input type="hidden" name="l" value="${l}">
    <input type="hidden" name="t" value="${t}"><button type="submit">${LABELS[a].go}</button></form>`);
} else {
  route = 'apply';
  html = page('Done', `<h1>Done ✓</h1><p>${LABELS[a].done}</p>`);
}
return [{ json: { route, html, a, l } }];
"""

OWNER_APPLY_SQL = """
-- One statement. Exactly one branch matches $1, so no row is updated twice.
WITH p AS (
  -- pause_alerted_at NULL: her next message still emails the owner once.
  UPDATE leads SET ai_paused = true, paused_by = 'owner', paused_at = now(),
                   pause_alerted_at = NULL, updated_at = now()
   WHERE id = $2::int AND $1 = 'pause' RETURNING id
), r AS (
  -- Resume hands the lead back: unpause, and an escalated lead re-enters normal flow.
  UPDATE leads SET ai_paused = false, paused_by = NULL, paused_at = NULL, pause_alerted_at = NULL,
                   status = CASE WHEN status = 'escalated' THEN 'engaged' ELSE status END,
                   updated_at = now()
   WHERE id = $2::int AND $1 = 'resume' RETURNING id
), re AS (
  UPDATE escalations SET resolved = true
   WHERE lead_id = $2::int AND $1 = 'resume' AND NOT resolved RETURNING id
), bl AS (
  UPDATE booking_links SET owner_confirmed_at = COALESCE(owner_confirmed_at, now())
   WHERE $1 = 'booked'
     AND id = (SELECT id FROM booking_links WHERE lead_id = $2::int ORDER BY sent_at DESC LIMIT 1)
  RETURNING id
), b AS (
  UPDATE leads SET status = 'booked', updated_at = now()
   WHERE id = $2::int AND $1 = 'booked' RETURNING id
)
SELECT $1 AS action,
       (SELECT count(*) FROM p) + (SELECT count(*) FROM r) + (SELECT count(*) FROM b) AS leads_changed;
"""


def owner_actions():
    nodes = [
        {"id": "oa-get", "name": "Action Link", "type": "n8n-nodes-base.webhook", "typeVersion": 2.1,
         "position": [0, 0], "webhookId": "b7c1e2d3-4f5a-4b6c-8d7e-9f0a1b2c3d4e",
         "parameters": {"httpMethod": "GET", "path": "owner", "responseMode": "responseNode", "options": {}}},
        {"id": "oa-post", "name": "Action Confirm", "type": "n8n-nodes-base.webhook", "typeVersion": 2.1,
         "position": [0, 200], "webhookId": "c8d2f3e4-5a6b-4c7d-9e8f-0a1b2c3d4e5f",
         "parameters": {"httpMethod": "POST", "path": "owner", "responseMode": "responseNode", "options": {}}},
        {"id": "oa-verify", "name": "Verify", "type": "n8n-nodes-base.code", "typeVersion": 2,
         "position": [240, 100], "parameters": {"jsCode": OWNER_VERIFY_JS.strip()}},
        switch("Route", [460, 100], "route", ["apply", "confirm", "deny"]),
        pg_node("Apply Action", [680, -40], OWNER_APPLY_SQL, "={{ [$json.a, $json.l] }}"),
        respond("Respond Done", [900, -40], "={{ $('Verify').first().json.html }}"),
        respond("Respond Confirm", [680, 100], "={{ $json.html }}"),
        respond("Respond Denied", [680, 240], "={{ $json.html }}", code=403),
    ]
    conns = conn(("Action Link", "Verify"), ("Action Confirm", "Verify"), ("Verify", "Route"),
                 ("Apply Action", "Respond Done"))
    conns["Route"] = {"main": [[{"node": "Apply Action", "type": "main", "index": 0}],
                               [{"node": "Respond Confirm", "type": "main", "index": 0}],
                               [{"node": "Respond Denied", "type": "main", "index": 0}]]}
    return {"id": WF_IDS["owner-actions"], "name": "Owner Actions", "active": False,
            "versionId": "00000000-0000-4000-8000-00000000d005",
            "nodes": nodes, "connections": conns, "settings": dict(SETTINGS)}


# ---------------------------------------------------------------------------
# Link Redirect — GET /webhook/go?c=<short code>  (go.looplogicsystems.com/<code>
# forwards here through a Cloudflare Worker). Records a click, then 302s to the
# studio's real booking page. Unknown code -> plain 404.
#
# Preview robots are NOT counted: Instagram builds the link preview by fetching
# the URL with Meta's crawler, so without this every link would read "clicked"
# before the lead touched it. NOTE: "Instagram" itself is deliberately NOT in the
# list — a real person tapping the link inside Instagram's in-app browser sends a
# user agent containing "Instagram", and excluding it would drop every real click.

CLASSIFY_JS = r"""
const j = $input.first().json;
const code = String((j.query || {}).c || '');
const ua = String((j.headers || {})['user-agent'] || '');
const crawler = /facebookexternalhit|facebot|meta-externalagent|meta-externalfetcher|twitterbot|slackbot|linkedinbot|discordbot|telegrambot|whatsapp|googlebot|bingbot|applebot|crawler|spider|\bbot\b|preview/i.test(ua)
  || ua === '';
return [{ json: { code: /^[A-Za-z0-9_-]{4,16}$/.test(code) ? code : '', crawler } }];
"""

REDIRECT_SQL = """
-- Always exactly one row: target_url is NULL for an unknown code.
WITH hit AS (
  UPDATE booking_links
     SET click_count = click_count + 1,
         clicked_at  = COALESCE(clicked_at, now())
   WHERE short_code = $1 AND NOT $2::boolean
  RETURNING target_url
)
SELECT COALESCE((SELECT target_url FROM hit),
                (SELECT target_url FROM booking_links WHERE short_code = $1)) AS target_url;
"""

NOT_FOUND_HTML = ("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' "
                  "content='width=device-width, initial-scale=1'><title>Link not found</title></head>"
                  "<body style='font-family:-apple-system,Arial,sans-serif;text-align:center;margin-top:20vh'>"
                  "<h1>Link not found</h1><p>This booking link isn't valid any more. Message the studio and "
                  "they'll send you a new one.</p></body></html>")


def link_redirect():
    nodes = [
        {"id": "lr-hook", "name": "Link Opened", "type": "n8n-nodes-base.webhook", "typeVersion": 2.1,
         "position": [0, 0], "webhookId": "d9e3a4f5-6b7c-4d8e-8f9a-1b2c3d4e5f60",
         "parameters": {"httpMethod": "GET", "path": "go", "responseMode": "responseNode", "options": {}}},
        {"id": "lr-classify", "name": "Classify", "type": "n8n-nodes-base.code", "typeVersion": 2,
         "position": [220, 0], "parameters": {"jsCode": CLASSIFY_JS.strip()}},
        pg_node("Look Up & Count", [440, 0], REDIRECT_SQL, "={{ [$json.code, $json.crawler] }}",
                alwaysOutputData=True),
        {"id": "lr-found", "name": "Found?", "type": "n8n-nodes-base.if", "typeVersion": 2.2,
         "position": [660, 0],
         "parameters": {"conditions": {"options": {"version": 2, "leftValue": "", "caseSensitive": True,
                                                   "typeValidation": "loose"},
                                       "conditions": [{"id": "has-target", "leftValue": "={{ $json.target_url }}",
                                                       "rightValue": "",
                                                       "operator": {"type": "string", "operation": "notEmpty",
                                                                    "singleValue": True}}],
                                       "combinator": "and"}, "options": {}}},
        respond("Redirect", [880, -80], "=", code=302, ctype="text/plain",
                extra_headers=[{"name": "Location", "value": "={{ $json.target_url }}"}]),
        respond("Not Found", [880, 80], NOT_FOUND_HTML, code=404),
    ]
    redirect = nodes[4]
    redirect["parameters"]["respondWith"] = "noData"     # a 302 carries no body
    redirect["parameters"].pop("responseBody", None)
    conns = conn(("Link Opened", "Classify"), ("Classify", "Look Up & Count"), ("Look Up & Count", "Found?"))
    conns["Found?"] = {"main": [[{"node": "Redirect", "type": "main", "index": 0}],
                                [{"node": "Not Found", "type": "main", "index": 0}]]}
    return {"id": WF_IDS["link-redirect"], "name": "Link Redirect", "active": False,
            "versionId": "00000000-0000-4000-8000-00000000d006",
            "nodes": nodes, "connections": conns, "settings": dict(SETTINGS)}


# ---------------------------------------------------------------------------
# IG Connect — GET /webhook/ig/connect   (Meta redirects here after she approves)
#
# A studio connects her Instagram from her own phone: we send her a connect link
# (workflows/build/ig_connect_link.py <studio id>), she logs in on Instagram's
# own screen and taps Allow, and Instagram sends her browser back here with a
# one-time `code` and our signed `state`. No password is shared and nothing is
# edited in n8n: the account becomes a channel_accounts row.
#
# state = base64url({"s": studio id, "e": expiry (unix s), "n": random nonce})
#         + "." + first 32 hex of HMAC-SHA256(OWNER_LINK_SECRET, "ig-connect|" + payload)
# A link works once (ig_connect_uses) and until it expires (7 days by default).

PAGE_JS = r"""
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const page = (title, body) => `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>${esc(title)}</title>
<style>body{margin:0;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:#f6f4f1;color:#1f1d1a}
main{max-width:440px;margin:12vh auto;padding:28px 24px;background:#fff;border-radius:14px;box-shadow:0 1px 3px rgba(0,0,0,.08);text-align:center}
h1{font-size:21px;margin:0 0 14px} p{color:#5f5953;line-height:1.55;margin:0 0 10px} small{color:#9a938c}</style>
</head><body><main>${body}<p><small>Loop Logic</small></p></main></body></html>`;
"""

CHECK_LINK_JS = PAGE_JS + r"""
const crypto = require('crypto');
const q = $input.first().json.query || {};
const stop = (title, msg) => [{ json: { route: 'stop', html: page(title, `<h1>${esc(title)}</h1><p>${esc(msg)}</p>`) } }];

// She tapped "Cancel" / "Not now" on Instagram's screen.
if (q.error) return stop('Not connected', 'Instagram wasn’t connected. If that was a mistake, just tap the link again.');

const secret = String($env.OWNER_LINK_SECRET || '');
const [p64, sig] = String(q.state || '').split('.');
let valid = false, payload = null;
if (secret && p64 && /^[0-9a-f]{32}$/.test(sig || '')) {
  const want = Buffer.from(crypto.createHmac('sha256', secret).update(`ig-connect|${p64}`).digest('hex').slice(0, 32));
  valid = crypto.timingSafeEqual(want, Buffer.from(sig));
  if (valid) { try { payload = JSON.parse(Buffer.from(p64, 'base64url').toString('utf8')); } catch (e) { valid = false; } }
}
if (!valid || !payload || !Number.isInteger(payload.s) || !/^[A-Za-z0-9_-]{16,64}$/.test(String(payload.n || '')))
  return stop('This link isn’t valid', 'It may have been changed or cut off. Use the link exactly as we sent it, or ask us for a new one.');
if (Date.now() / 1000 > Number(payload.e))
  return stop('This link has expired', 'Connect links work for 7 days. Ask us for a new one and it takes a minute.');
if (!q.code || String(q.code).length > 2000)
  return stop('Something went wrong', 'Instagram didn’t send back what we expected. Please tap the link again.');
return [{ json: { route: 'go', studio_id: payload.s, nonce: String(payload.n), code: String(q.code).replace(/#_$/, '') } }];
"""

LINK_STATUS_SQL = """
-- Always exactly one row.
SELECT $1::int AS studio_id, $2::text AS nonce, $3::text AS code,
       (SELECT name FROM studios WHERE id = $1::int AND active) AS studio_name,
       EXISTS (SELECT 1 FROM ig_connect_uses WHERE nonce = $2) AS link_used;
"""

CONNECT_ACCOUNT_JS = TOKEN_CRYPTO_JS + GRAPH_JS + r"""
const j = $input.first().json;
const base = { studio_id: j.studio_id, studio_name: j.studio_name, nonce: j.nonce };
if (!j.studio_name) return [{ json: { ...base, ok: false, outcome: 'no_studio' } }];
if (j.link_used)    return [{ json: { ...base, ok: false, outcome: 'link_used' } }];

const secret = String($env.IG_APP_SECRET_INSTAGRAM || '');
try {
  if (!secret) throw new Error('IG_APP_SECRET_INSTAGRAM is missing on the server');
  // 1. one-time code -> short-lived token (never retried: the code works once)
  const short = await graphCall(this, { what: 'code exchange', method: 'POST',
    url: 'https://api.instagram.com/oauth/access_token',
    form: { client_id: '__IG_APP_ID__', client_secret: secret, grant_type: 'authorization_code',
            redirect_uri: '__IG_REDIRECT_URI__', code: j.code } });
  const s = Array.isArray(short.data) ? short.data[0] : short;
  const perms = Array.isArray(s.permissions) ? s.permissions : String(s.permissions || '').split(',');
  if (!perms.map((p) => p.trim()).includes('instagram_business_manage_messages'))
    return [{ json: { ...base, ok: false, outcome: 'no_message_permission' } }];
  // 2. short-lived -> long-lived (~60 days)
  const long = await graphCall(this, { what: 'long-lived token', url: 'https://graph.instagram.com/access_token',
    qs: { grant_type: 'ig_exchange_token', client_secret: secret, access_token: s.access_token }, tries: 2 });
  // 3. which account is this? user_id is what webhooks carry as the account id.
  const me = await graphCall(this, { what: 'account lookup', url: '__GRAPH__/me',
    qs: { fields: 'user_id,username,name,account_type' }, token: long.access_token, tries: 3 });
  if (!me.user_id) throw new Error('account lookup: no user_id returned');
  // 4. deliver this account's DMs to our webhook (the dashboard's per-account toggle)
  let subscribed = false, subscribe_error = null;
  try {
    const sub = await graphCall(this, { what: 'webhook subscription', method: 'POST', url: '__GRAPH__/me/subscribed_apps',
      qs: { subscribed_fields: 'messages' }, token: long.access_token, tries: 2 });
    subscribed = !!(sub && sub.success);
  } catch (e) { subscribe_error = e.message; }
  return [{ json: { ...base, ok: true, outcome: 'pending_save',
    account_ref: String(me.user_id), username: me.username || null, name: me.name || null,
    account_type: me.account_type || null, subscribed, subscribe_error,
    expires_in: Number(long.expires_in) || 5184000,
    token_enc: encryptToken(long.access_token) } }];   // ciphertext only, never the token
} catch (e) {
  return [{ json: { ...base, ok: false, outcome: 'error', error: String(e.message).slice(0, 400) } }];
}
"""

SAVE_ACCOUNT_SQL = """
-- One statement, always one row. Refuses to move an account that is already
-- connected to a DIFFERENT studio; re-connecting to the same studio just
-- replaces the token (that is also how a revoked token is fixed: a new link).
WITH clash AS (
  SELECT 1 FROM channel_accounts
   WHERE channel = 'instagram' AND account_ref = $3 AND studio_id <> $2::int
), used AS (
  INSERT INTO ig_connect_uses (nonce, studio_id, account_ref, ig_username)
  SELECT $1, $2::int, $3, $4 WHERE NOT EXISTS (SELECT 1 FROM clash)
  ON CONFLICT (nonce) DO NOTHING
  RETURNING nonce
), acct AS (
  INSERT INTO channel_accounts (studio_id, channel, account_ref, access_token_enc, ig_username,
                                connected_at, token_issued_at, token_expires_at, token_error, active)
  SELECT $2::int, 'instagram', $3, decode($5, 'base64'), $4,
         now(), now(), now() + make_interval(secs => $6::int), NULL, true
   WHERE EXISTS (SELECT 1 FROM used)
  ON CONFLICT (channel, account_ref) DO UPDATE SET
         access_token_enc = EXCLUDED.access_token_enc, ig_username = EXCLUDED.ig_username,
         connected_at = now(), token_issued_at = now(),
         token_expires_at = EXCLUDED.token_expires_at, token_error = NULL, active = true
   WHERE channel_accounts.studio_id = EXCLUDED.studio_id
  RETURNING id
)
SELECT CASE WHEN EXISTS (SELECT 1 FROM acct)  THEN 'connected'
            WHEN EXISTS (SELECT 1 FROM clash) THEN 'other_studio'
            ELSE 'link_used' END AS outcome;
"""

RESULT_JS = PAGE_JS + r"""
const c = $('Connect Account').first().json;
const outcome = c.ok ? $json.outcome : c.outcome;
const who = c.username ? `@${c.username}` : 'Your Instagram account';
const P = {
  connected:     ['Connected ✓', `${who} is now connected to ${c.studio_name}. You can close this page.`],
  other_studio:  ['Already connected elsewhere', `${who} is already connected to another studio, so we didn’t change anything. We’ve been told and will be in touch.`],
  link_used:     ['This link was already used', 'Each connect link works once. If you need to connect again, ask us for a new one.'],
  no_studio:     ['This link isn’t valid', 'Ask us for a new connect link.'],
  no_message_permission: ['Almost there', 'Instagram didn’t give us permission to manage messages, which is what replies need. Tap the link again and leave every permission switched on.'],
  error:         ['Something went wrong', 'We couldn’t finish connecting. Nothing was changed on your account. We’ve been told and will send you a new link.'],
};
const [title, msg] = P[outcome] || P.error;
const lines = [
  `Studio: ${c.studio_name || '(unknown)'} (id ${c.studio_id})`,
  c.username ? `Account: @${c.username}${c.name ? ' (' + c.name + ')' : ''}, id ${c.account_ref}, type ${c.account_type || '?'}` : null,
  outcome === 'connected' ? `Token valid until about ${new Date(Date.now() + c.expires_in * 1000).toISOString().slice(0, 10)}; refreshed automatically.` : null,
  outcome === 'connected' && !c.subscribed ? `WARNING: subscribing this account to message webhooks failed (${c.subscribe_error || 'no success flag'}). Its DMs will not arrive until that is fixed.` : null,
  c.error ? `Error: ${c.error}` : null,
].filter(Boolean);
return [{ json: {
  html: page(title, `<h1>${esc(title)}</h1><p>${esc(msg)}</p>`),
  email: { to: 'founders',
           subject: outcome === 'connected' ? `Instagram connected: ${who} → ${c.studio_name}`
                                            : `Instagram connect FAILED (${outcome}) for ${c.studio_name || 'studio ' + c.studio_id}`,
           heading: outcome === 'connected' ? `${who} connected through a connect link.` : `A connect link didn’t work: ${outcome}.`,
           lines },
} }];
"""


def ig_connect():
    sub = lambda js: (js.replace("__IG_APP_ID__", IG_APP_ID).replace("__IG_REDIRECT_URI__", IG_REDIRECT_URI)
                        .replace("__GRAPH__", GRAPH))
    nodes = [
        {"id": "ic-hook", "name": "Connect Callback", "type": "n8n-nodes-base.webhook", "typeVersion": 2.1,
         "position": [0, 0], "webhookId": "f0a4b5c6-7d8e-4f9a-8b0c-1d2e3f4a5b6c",
         "parameters": {"httpMethod": "GET", "path": "ig/connect", "responseMode": "responseNode", "options": {}}},
        {"id": "ic-check", "name": "Check Link", "type": "n8n-nodes-base.code", "typeVersion": 2,
         "position": [220, 0], "parameters": {"jsCode": CHECK_LINK_JS.strip()}},
        switch("Link OK?", [440, 0], "route", ["go", "stop"]),
        pg_node("Studio & Link Status", [660, -100], LINK_STATUS_SQL,
                "={{ [$json.studio_id, $json.nonce, $json.code] }}", alwaysOutputData=True),
        respond("Respond Stopped", [660, 120], "={{ $json.html }}", code=400),
        {"id": "ic-connect", "name": "Connect Account", "type": "n8n-nodes-base.code", "typeVersion": 2,
         "position": [880, -100], "parameters": {"jsCode": sub(CONNECT_ACCOUNT_JS).strip()}},
        {"id": "ic-ok", "name": "Got Token?", "type": "n8n-nodes-base.if", "typeVersion": 2.2,
         "position": [1100, -100],
         "parameters": {"conditions": {"options": {"version": 2, "leftValue": "", "caseSensitive": True,
                                                   "typeValidation": "loose"},
                                       "conditions": [{"id": "ok", "leftValue": "={{ $json.ok }}", "rightValue": "true",
                                                       "operator": {"type": "boolean", "operation": "true",
                                                                    "singleValue": True}}],
                                       "combinator": "and"}, "options": {}}},
        pg_node("Save Account", [1320, -180], SAVE_ACCOUNT_SQL,
                "={{ [$json.nonce, $json.studio_id, $json.account_ref, $json.username, $json.token_enc, $json.expires_in] }}"),
        {"id": "ic-result", "name": "Result", "type": "n8n-nodes-base.code", "typeVersion": 2,
         "position": [1540, -100], "parameters": {"jsCode": RESULT_JS.strip()}},
        respond("Respond Result", [1760, -100], "={{ $json.html }}"),
        {"id": "ic-email", "name": "Founder Email", "type": "n8n-nodes-base.code", "typeVersion": 2,
         "position": [1980, -100], "parameters": {"jsCode": "return [{ json: $('Result').first().json.email }];"}},
        {"id": "ic-send", "name": "Send Email", "type": "n8n-nodes-base.executeWorkflow", "typeVersion": 1.2,
         "position": [2200, -100], "onError": "continueRegularOutput",
         "parameters": {"workflowId": {"__rl": True, "mode": "id", "value": "SEND_EMAIL_WF_ID"},
                        "options": {"waitForSubWorkflow": True}}},
    ]
    conns = conn(("Connect Callback", "Check Link"), ("Check Link", "Link OK?"),
                 ("Studio & Link Status", "Connect Account"), ("Connect Account", "Got Token?"),
                 ("Save Account", "Result"), ("Result", "Respond Result"),
                 ("Respond Result", "Founder Email"), ("Founder Email", "Send Email"))
    conns["Link OK?"] = {"main": [[{"node": "Studio & Link Status", "type": "main", "index": 0}],
                                  [{"node": "Respond Stopped", "type": "main", "index": 0}]]}
    conns["Got Token?"] = {"main": [[{"node": "Save Account", "type": "main", "index": 0}],
                                    [{"node": "Result", "type": "main", "index": 0}]]}
    return {"id": WF_IDS["ig-connect"], "name": "IG Connect", "active": False,
            "versionId": "00000000-0000-4000-8000-00000000d007",
            "nodes": nodes, "connections": conns, "settings": dict(SETTINGS)}


def main():
    scratch = sys.argv[1]
    os.makedirs(scratch, exist_ok=True)
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(os.path.dirname(here))

    # Workflow-id placeholders are resolved like credential ids. Not secrets,
    # but unknown until the target workflow exists on the mini.
    links = {"SEND_EMAIL_WF_ID": WF_IDS["send-email"]}

    for wf, filename in [(send_email(), "send-email.json"),
                         (ops_test_email(), "ops-test-email.json"),
                         (demo_booking_page(), "demo-booking-page.json"),
                         (ops_voice_lab(), "ops-voice-lab.json"),
                         (owner_actions(), "owner-actions.json"),
                         (link_redirect(), "link-redirect.json"),
                         (ig_connect(), "ig-connect.json")]:
        redacted = json.dumps(wf, indent=2, ensure_ascii=False)
        with open(os.path.join(repo, "workflows", filename), "w") as f:
            f.write(redacted + "\n")

        incoming = {c["node"] for outs in wf["connections"].values() for kind in outs.values()
                    for out in kind for c in out}
        orphans = [n["name"] for n in wf["nodes"] if n["name"] not in incoming
                   and not any(t in n["type"] for t in ("Trigger", "webhook", "manualTrigger"))]
        assert not orphans, f"{wf['name']}: nodes with nothing feeding them: {orphans}"
        mapping = {**CREDS, **links}
        missing = [p for p, real in mapping.items() if real is None and p in redacted]
        if missing:
            print(f"{wf['name']:32} redacted only — live build skipped, waiting on: {', '.join(missing)}")
            continue
        live = redacted
        for placeholder, real in mapping.items():
            if real is not None:
                live = live.replace(placeholder, real)
        for placeholder in mapping:
            assert placeholder not in live, f"{placeholder} not substituted"
        for real in CREDS.values():
            assert real is None or real not in redacted, f"live credential {real} leaked into the redacted export"
        with open(os.path.join(scratch, filename.replace(".json", ".live.json")), "w") as f:
            f.write(live + "\n")
        print(f"{wf['name']:32} {len(wf['nodes']):>3} nodes -> workflows/{filename}"
              + ("" if wf["id"] else "   (NEW: id assigned on first push)"))


if __name__ == "__main__":
    main()
