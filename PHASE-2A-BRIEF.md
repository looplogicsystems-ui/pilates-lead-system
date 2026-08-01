# Phase 2a — Productionize the Core (Channel-Agnostic Lead Brain)

## Your task

Refactor the existing single-workflow n8n system into a channel-agnostic, multi-tenant
core, and fix the correctness bugs that become significantly harder to fix after that
refactor. Work from `PLAN.md` (Phase 2a section) and `lead-booking-workflow-critique.md`,
both in the repo.

**Read before writing anything:**
- `PLAN.md` — the roadmap; Phase 2a is the target
- `README.md` — current architecture (v2.0)
- `lead-booking-workflow-critique.md` — the bug list this brief draws from
- `workflows/lead-capture-core-loop.json` — the live 45-node graph
- `db/lead_system_schema.sql` — current DDL

---

## Current state (accurate as of v2.0)

One n8n workflow, `Lead Capture & Booking — Core Loop v.2.0`, id `t7y3ISrzAcj8fCmA`,
45 nodes. Self-hosted n8n 2.1.4 in Docker Compose, public HTTPS via Cloudflare tunnel.

**Working and verified end-to-end:**
- Instagram DM inbound: GET verify (echoes `hub.challenge`) + POST events on
  `/webhook/instagram`, HMAC `X-Hub-Signature-256` verification against the app secret,
  byte-exact signature from the raw binary body
- Instagram DM outbound via `POST graph.instagram.com/v25.0/me/messages` using a
  long-lived Instagram User access token stored as an n8n Header Auth credential
- Slack lead-bot poller (1 min) as a second real two-way channel
- AI agent (`gpt-5.4-mini`) with Postgres chat memory, structured JSON output
  `{reply, intent, booking, escalate}`
- Booking to Google Calendar, Slack owner alerts, full Postgres logging
- Follow-up scheduler (15 min) with AI-written nudges, template fallback, `cold` marking
- Separate error handler workflow `4L4K72aSSwiBcnjX`
- `IG Token Expiry Monitor` workflow `n56lLK7WRdYFesdW`

**Already done — do NOT redo these** (the project board still lists some as open; it is
stale):
- Channel Adapter: Instagram — built and verified
- End-to-end test from a real IG account — done
- Error Trigger workflow — exists (`4L4K72aSSwiBcnjX`); only needs the dead-letter
  persistence check in task 6 below

---

## Scope boundary — read this twice

**IN scope:** everything in the numbered tasks below.

**OUT of scope. Do not build, do not scaffold, do not "prepare for":**
- Any new channel (WhatsApp, SMS/Twilio, Messenger, Lead Ads, email, web form)
- The client-facing dashboard or onboarding UI
- VPS migration, queue mode, Redis
- Reminders, no-show tracking, trial→paid conversion tracking
- Funnel reporting or digests
- Consent/opt-out flows, CAPTCHA, rate limiting
- Reschedule/cancel handling
- Nudge cadence changes to day-1/day-3/day-7 (defer; see task 8 note)

If you believe something out of scope is a hard blocker, stop and say so rather than
building it.

---

## Target architecture

```
[Inbound adapter]  →  [Lead Brain sub-workflow]  →  [Outbound send]
 per channel:            channel-agnostic core:       per channel:
 webhook, verify,        resolve studio, upsert,      IG Graph API /
 signature, dedupe,      log, agent + memory,         Slack
 normalize to contract   booking/escalation,
                         RETURNS a result
```

The Lead Brain must not know or care which channel a message came from, and must not
respond to a webhook directly.

**Inbound contract** (adapter → Lead Brain):
```json
{
  "studio_id": 1,
  "channel": "instagram",
  "external_id": "<IGSID or Slack user id>",
  "name": "Sarah Chen",
  "message": "hey do you have anything thursday evening?",
  "provider_message_id": "<mid or Slack ts>",
  "received_at": "2026-08-01T14:22:00Z"
}
```

**Outbound contract** (Lead Brain → adapter):
```json
{
  "reply": "...",
  "intent": "booking|escalate|continue",
  "lead_id": 42,
  "studio_id": 1,
  "channel": "instagram",
  "external_id": "...",
  "booking": { "created": true, "event_id": "...", "start": "..." },
  "escalate": { "reason": "medical" }
}
```

---

## Tasks

### 1. Schema migrations

Convert `db/lead_system_schema.sql` into **versioned, ordered migration files**
(`db/migrations/001_*.sql`, `002_*.sql`, …) rather than one setup script. Each migration
must be idempotent (`IF NOT EXISTS` / guarded `ALTER`). Include a rollback note in a
comment at the top of each.

Migrations to write:

**`studios` — real config.** Add columns (or a single `config` JSONB, your call — justify
the choice in a comment):
- `timezone` (text, e.g. `Asia/Karachi`)
- `slack_owner_channel_id`, `slack_lead_channel_id` (text)
- `gcal_schedule_id`, `gcal_booking_id` (text)
- `class_capacity` (int, currently the hardcoded `CAP=8`)
- `prompt_vars` (JSONB: `name`, `location`, `type`, `offer`, `schedule_summary`,
  `voice`, `persona_name`)

Seed Reform Collective from the values currently hardcoded in the workflow. **Resolve the
config drift while you do this**: the seeded studio row says `35 USD`, the agent prompt
says `PKR 3,500`. Use `PKR 3,500` and make it single-source.

**`channel_accounts` — the multi-tenant router.**
```sql
CREATE TABLE IF NOT EXISTS channel_accounts (
  id             SERIAL PRIMARY KEY,
  studio_id      INT NOT NULL REFERENCES studios(id),
  channel        TEXT NOT NULL,          -- 'instagram' | 'slack'
  account_ref    TEXT NOT NULL,          -- IG account id / Slack channel id
  credential_ref TEXT,                   -- key into the private credentials store
  active         BOOLEAN NOT NULL DEFAULT true,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (channel, account_ref)
);
```

**`processed_messages` — durable idempotency.**
```sql
CREATE TABLE IF NOT EXISTS processed_messages (
  provider        TEXT NOT NULL,         -- 'instagram' | 'slack'
  provider_msg_id TEXT NOT NULL,
  processed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (provider, provider_msg_id)
);
```

**`leads` — status and state fixes.**
- Extend the status model to include `escalated` and `paused` alongside existing values
- Add `ai_paused BOOLEAN NOT NULL DEFAULT false` (human handoff flag)
- Ensure `source_offer` is actually populated on insert (currently always null)

**`dead_letters`** — if the error handler doesn't already persist failures, add:
```sql
CREATE TABLE IF NOT EXISTS dead_letters (
  id          SERIAL PRIMARY KEY,
  workflow_id TEXT,
  execution_id TEXT,
  payload     JSONB,
  error       TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at TIMESTAMPTZ
);
```

---

### 2. Extract the Lead Brain sub-workflow

Create a new workflow `Lead Brain` with an **Execute Workflow Trigger**. Move into it:
Normalize → Upsert Lead → Log Inbound → Get Schedule → Format Schedule → AI Agent →
Parse Agent JSON → Route by Intent → (booking / escalation side effects) → Log Outbound.

It must **return** the outbound contract, not respond to a webhook.

Critical n8n implementation notes:
- Postgres `executeQuery` nodes do not pass items through — the existing graph fans
  side-effects out in parallel from the upstream data node. Preserve that pattern.
- The `ZONE` constant currently hardcoded in **Parse Agent JSON** must come from
  `studios.timezone`, loaded at the top of the Lead Brain.
- The `{{STUDIO_NAME}}` / `{{OFFER}}` / `{{SCHEDULE}}` placeholders in the AI Agent system
  message must be populated from `studios.prompt_vars`.
- The workflow-level timezone setting also drives the Google Calendar event tag. Since
  n8n's workflow timezone is static, either set the timezone explicitly on the Calendar
  node from studio config, or document clearly why the workflow-level setting is still
  acceptable for now.

---

### 3. Studio resolution at the top of the brain

First step inside Lead Brain: look up `channel_accounts` by `(channel, account_ref)` to
get `studio_id`, then load the full `studios` row into a single config object referenced
by every downstream node.

If no matching `channel_accounts` row exists: log it, write a dead-letter row, alert the
admin Slack channel, and **stop**. Do not fall back to `studio_id = 1`. A silent fallback
is how one client's leads end up in another client's calendar.

---

### 4. Rebuild the Instagram adapter as a thin wrapper

The Instagram workflow keeps: GET verify, POST events, signature verification, extraction.
It then:
1. Looks up `processed_messages` for `(instagram, mid)` — if present, return 200 and stop
2. Inserts the `mid` (let the PK conflict be the dedupe mechanism, not a read-then-write
   race)
3. Normalizes to the inbound contract
4. Calls Lead Brain via Execute Sub-workflow
5. Sends the returned `reply` via the Graph API

**Replace the rolling static-data dedupe entirely.** It's in-memory, dies on restart, and
breaks under multiple workers. `processed_messages` is the replacement.

**Token handling must become dynamic.** The current static Header Auth credential works
for exactly one client. Change the send node to an HTTP Request node that reads the
token for this studio from the credentials store and sets the `Authorization` header via
expression. Store tokens encrypted in a **schema not exposed over any API** — if the DB
is Supabase, that means a `private` schema, never `public`.

Apply the same treatment to the Slack adapter.

---

### 5. Update `IG Token Expiry Monitor`

It currently checks one token. Make it iterate every active `channel_accounts` row of
channel `instagram` and alert per-studio.

---

### 6. Error handler + dead letters

Verify `4L4K72aSSwiBcnjX` persists the failed payload to `dead_letters`, not just Slack.
If it only alerts, add the persistence.

---

### 7. Booking integrity fixes

These are cheap now and painful later:

- **`Create Calendar Event` has `onError: continueRegularOutput`.** If the calendar write
  fails, the flow still inserts the booking as `confirmed`, marks the lead `booked`, tells
  the lead they're booked, and pings the owner — with no event. Guard so the booking row,
  the lead status change, the confirmation reply, and the owner alert **only** happen if
  the event was verifiably created. On failure: escalate to the owner instead.
- **`Get Schedule` has no `timeMin`.** Past classes can leak into the list offered to
  leads. Set `timeMin` to now in the studio's timezone.
- **Seat counting matches classes by exact start-timestamp equality.** A 9:05 booking
  against a 9:00 class never decrements the count. Match within a tolerance window
  (±15 min) or against the calendar event ID.
- **Capacity comes from `studios.class_capacity`,** not the hardcoded `CAP=8`.
- **The system prompt contradicts itself** on full classes — it says both "a class is FULL
  at 8 `Trial:` bookings, offer the next open class" and "Do NOT claim a class is full…
  never say a class is taken." Pick the first policy, delete the second, since the agent is
  fed real seat counts.

Note: race-condition locking on concurrent bookings is **out of scope** for 2a — leave a
`TODO` comment at the point where the lock belongs.

---

### 8. Lead lifecycle state fixes

- **Escalation must pause automation.** Escalating currently leaves `status = 'engaged'`,
  so a lead flagged for a medical or pricing question gets a cheerful nudge minutes later.
  Set `status = 'escalated'` and exclude escalated and `ai_paused` leads from
  `Find Stale Leads`.
- **Cold leads must be able to re-enter.** `Upsert Lead`'s `ON CONFLICT` updates name and
  `updated_at` but not status, so a returning `cold` lead is stuck forever. Reset to
  `engaged` on new inbound — but do **not** reset if `ai_paused` is true.
- **Respect `ai_paused` on inbound:** skip the AI entirely and notify the owner.

Nudge *cadence* stays as-is for now (that's Phase 2d) — but leave the interval values as
studio config rather than magic numbers, so changing it later is a config edit.

---

### 9. Cleanup

- Delete the dead code in `Compose Follow-up` — it builds two hardcoded nudge templates
  that `Assemble Nudge` then overwrites with AI output. Keep the template fallback path
  if it's genuinely reachable on AI failure; delete it if not.
- Populate `leads.source_offer` on insert.

---

### 10. Environment externalization

Every environment-specific value must come from env vars, not the workflow JSON: calendar
IDs, Slack channel IDs, app secret, verify token, database host, tunnel base URL.

Goal: the same exported workflow JSON imports cleanly into a fresh environment with only
a different `.env` — no hand-editing of node parameters. Update the redaction placeholder
list in `README.md` to match.

---

## Verification — must all pass before you call this done

1. **Two studios route correctly.** Seed a second studio with a different timezone, Slack
   channel, calendar, and persona. Two inbound messages on different `channel_accounts`
   rows produce two conversations with the right prompt, the right calendar, the right
   Slack channel, and correctly localized times. Nothing crosses over.
2. **No hardcoded config remains.** `grep` the workflow JSON for `Asia/Karachi`,
   `Reform`, `Gulberg`, `3500`, `CAP`, `studio_id.*1` — all should come from config.
3. **Replay is a no-op.** Re-POST an identical Instagram event with the same `mid` →
   exactly one reply sent, one row in `processed_messages`. Then restart n8n and replay
   again → still no duplicate. (This is the test the old static-data dedupe fails.)
4. **Unknown account is refused.** An inbound event whose `account_ref` has no
   `channel_accounts` row → dead-letter row + admin alert, no AI call, no reply.
5. **Failed calendar write blocks the booking.** Point the calendar node at an invalid ID.
   Result: no `bookings` row, lead not marked `booked`, no "you're booked" reply to the
   lead, owner alerted to the failure.
6. **Escalated leads are not nudged.** Escalate a lead, run the follow-up scheduler → no
   nudge sent, lead excluded from `Find Stale Leads`.
7. **Cold lead revives.** Mark a lead `cold`, send new inbound → status returns to
   `engaged` and the conversation continues.
8. **`ai_paused` halts the AI.** Set the flag, send inbound → no AI call, owner notified.
9. **Real IG DM still works end to end.** DM → AI reply in the Instagram inbox → booking
   on the calendar → owner Slack alert. The existing behaviour must not regress.
10. **Migrations run clean on an empty database** and are idempotent when re-run.

---

## How to work

- **Branch per task group.** Don't do this as one commit.
- **Export the workflow JSON after each working change** and commit it — n8n's editor
  state is not source control.
- **Keep a rollback path.** The current v2.0 workflow is live and verified; do not
  overwrite it in place. Duplicate it, work on the copy, and only swap the webhook path
  over when verification passes.
- **Ask before deviating.** If the plan and the code disagree, surface it rather than
  picking one silently.
- **Update `README.md` and `PLAN.md`** at the end to reflect the new architecture and mark
  Phase 2a done.
