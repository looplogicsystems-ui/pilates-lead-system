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
}

# placeholder -> live credential id. None = credential not created on the mini yet;
# workflows that need it are written redacted but skipped for the live build.
CREDS = {
    "RESEND_CRED_ID": "VAp4NUeGTiCv51IF",   # n8n Header Auth: Authorization: Bearer <Resend API key>
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

  let to = String(j.to || '').trim();
  if (to === 'founders') to = String($env.FOUNDER_ALERT_EMAILS || '').trim();
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


def main():
    scratch = sys.argv[1]
    os.makedirs(scratch, exist_ok=True)
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(os.path.dirname(here))

    # Workflow-id placeholders are resolved like credential ids. Not secrets,
    # but unknown until the target workflow exists on the mini.
    links = {"SEND_EMAIL_WF_ID": WF_IDS["send-email"]}

    for wf, filename in [(send_email(), "send-email.json"),
                         (ops_test_email(), "ops-test-email.json")]:
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
