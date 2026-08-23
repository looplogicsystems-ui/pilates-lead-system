#!/usr/bin/env python3
"""Regenerate the two ops workflows, preserving their existing ids.

  workflows/error-handler.json    id 4L4K72aSSwiBcnjX  (referenced as errorWorkflow everywhere)
  workflows/ig-token-monitor.json id n56lLK7WRdYFesdW

Run: python3 workflows/build/build_ops.py <scratch-dir>
"""
import json
import os
import sys

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


def token_monitor():
    nodes = [
        {"id": "weekly-check", "name": "Weekly Check",
         "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2,
         "position": [-460, 0],
         "parameters": {"rule": {"interval": [{"field": "weeks", "weeksInterval": 1,
                                               "triggerAtDay": [1], "triggerAtHour": 9}]}}},

        # Was a single hardcoded TOKEN_ISSUED date in a Code node. Now every
        # active Instagram account is checked against its own issue date.
        {"id": "load-ig-accounts", "name": "Load IG Accounts",
         "type": "n8n-nodes-base.postgres", "typeVersion": 2.6,
         "position": [-240, 0],
         "parameters": {
             "operation": "executeQuery",
             "query": """SELECT ca.id, ca.account_ref, ca.credential_ref,
       ca.token_issued_at, ca.token_lifetime_days, ca.alert_within_days,
       s.name AS studio_name, s.slack_owner_channel_id,
       (ca.token_issued_at + make_interval(days => ca.token_lifetime_days)) AS expires_at,
       EXTRACT(DAY FROM (ca.token_issued_at
                         + make_interval(days => ca.token_lifetime_days)
                         - now()))::int AS days_left
  FROM channel_accounts ca
  JOIN studios s ON s.id = ca.studio_id
 WHERE ca.channel = 'instagram'
   AND ca.active
   AND s.active
   AND ca.token_issued_at IS NOT NULL;""",
             "options": {}},
         "credentials": {"postgres": {"id": "POSTGRES_CRED_ID", "name": "Postgres account"}},
         "alwaysOutputData": True, "retryOnFail": True},

        {"id": "expiring-soon", "name": "Expiring Soon?",
         "type": "n8n-nodes-base.filter", "typeVersion": 2.2,
         "position": [-20, 0],
         "parameters": {
             "conditions": {"options": {"version": 2, "leftValue": "", "caseSensitive": True,
                                        "typeValidation": "loose"},
                            "conditions": [{"id": "soon",
                                            "leftValue": "={{ $json.days_left }}",
                                            "rightValue": "={{ $json.alert_within_days }}",
                                            "operator": {"type": "number", "operation": "lte"}}],
                            "combinator": "and"},
             "options": {}}},

        {"id": "alert-token-expiry", "name": "Alert Token Expiry",
         "type": "n8n-nodes-base.slack", "typeVersion": 2.3,
         "position": [200, 0],
         "parameters": {
             "select": "channel",
             "channelId": {"__rl": True, "value": "={{ $json.slack_owner_channel_id }}",
                           "mode": "id"},
             "text": ("=:key: *Instagram token expiring* for {{ $json.studio_name }}\n"
                      "Account: {{ $json.account_ref }}\n"
                      "Expires: {{ $json.expires_at }} ({{ $json.days_left }} days left)\n"
                      "Reconnect the token in the Meta app, update the n8n credential, then set "
                      "`channel_accounts.token_issued_at = now()` for this account — otherwise "
                      "outbound replies stop silently."),
             "otherOptions": {}},
         "credentials": {"slackApi": {"id": "SLACK_OPS_BOT_CRED_ID", "name": "Lead System Monitor"}},
         "onError": "continueRegularOutput", "retryOnFail": True},
    ]
    conns = {
        "Weekly Check": {"main": [[{"node": "Load IG Accounts", "type": "main", "index": 0}]]},
        "Load IG Accounts": {"main": [[{"node": "Expiring Soon?", "type": "main", "index": 0}]]},
        "Expiring Soon?": {"main": [[{"node": "Alert Token Expiry", "type": "main", "index": 0}]]},
    }
    return {"id": "n56lLK7WRdYFesdW", "name": "IG Token Expiry Monitor",
            "active": False, "versionId": "00000000-0000-4000-8000-00000000c002",
            "nodes": nodes, "connections": conns, "settings": dict(SETTINGS)}


def main():
    scratch = sys.argv[1]
    os.makedirs(scratch, exist_ok=True)
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(os.path.dirname(here))

    for wf, filename in [(error_handler(), "error-handler.json"),
                         (token_monitor(), "ig-token-monitor.json")]:
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
