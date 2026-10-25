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

WF_IDS = {
    "send-email":     "PQWBdER3x291uWeU",
    "ops-test-email": "oh01IIck8xhZWADI",
    "demo-booking-page": "nxW4e7Mbf1dT3lqH",
}

# placeholder -> live credential id. None = credential not created on the mini yet;
# workflows that need it are written redacted but skipped for the live build.
CREDS = {
    "RESEND_CRED_ID": "VAp4NUeGTiCv51IF",
    "POSTGRES_CRED_ID": "VVIxtEmT1GWsEZn9",   # n8n Header Auth: Authorization: Bearer <Resend API key>
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
                         (demo_booking_page(), "demo-booking-page.json")]:
        redacted = json.dumps(wf, indent=2, ensure_ascii=False)
        with open(os.path.join(repo, "workflows", filename), "w") as f:
            f.write(redacted + "\n")

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
