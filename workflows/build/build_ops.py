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


def error_handler():
    nodes = [
        {"id": "on-workflow-error", "name": "On Workflow Error",
         "type": "n8n-nodes-base.errorTrigger", "typeVersion": 1,
         "position": [-260, 0], "parameters": {}},

        # A Slack post was the ONLY record of a failure. If Slack is down or the
        # alert scrolls past, the dropped lead is unrecoverable — there was
        # nowhere to look up what failed. Persist first, then shout.
        {"id": "persist-dead-letter", "name": "Persist Dead Letter",
         "type": "n8n-nodes-base.postgres", "typeVersion": 2.6,
         "position": [-40, 0],
         "parameters": {
             "operation": "executeQuery",
             "query": """INSERT INTO dead_letters (workflow_id, execution_id, reason, payload, error)
VALUES ($1, $2, 'execution_error', $3::jsonb, $4);""",
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
         "position": [180, 0],
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
    ]
    conns = {
        "On Workflow Error": {"main": [[{"node": "Persist Dead Letter", "type": "main", "index": 0}]]},
        "Persist Dead Letter": {"main": [[{"node": "Alert Owner (Slack)", "type": "main", "index": 0}]]},
    }
    # Imported inactive: n8n 2.x publishes through workflow_publish_history, and
    # import:workflow cannot mint a published version for a workflow that is
    # already live. Activation happens at cutover.
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


def main():
    scratch = sys.argv[1]
    os.makedirs(scratch, exist_ok=True)
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(os.path.dirname(here))

    for wf, filename in [(error_handler(), "error-handler.json"),
                         (token_refresh(), "ig-token-refresh.json")]:
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
        print(f"{wf['name']:32} {len(wf['nodes']):>3} nodes -> workflows/{filename}")


if __name__ == "__main__":
    main()
