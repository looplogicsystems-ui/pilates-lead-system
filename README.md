# Pilates Lead-Capture & Booking System (n8n + OpenAI + Postgres)

An AI lead-response system for boutique fitness studios: captures inbound leads, replies in the
owner's voice, holds a real conversation until it books a trial class or the lead goes cold,
books against Google Calendar (checking real availability + class capacity), alerts the owner on
bookings/escalations via Slack, chases quiet leads with AI-written follow-ups, and logs everything
to Postgres.

## Status

**Phase 1 (core conversational loop) is built, finalized, and running** in a self-hosted n8n
instance (workflow `Lead Capture & Booking — Core Loop v.1.5`, id `t7y3ISrzAcj8fCmA`).

Inbound channels: an n8n hosted **Form** (website lead capture) **and a two-way Slack channel**
(a "lead bot" polls the channel every minute and replies in it — simulating an Instagram/Facebook
DM, and able to deliver **proactive follow-ups** a webchat couldn't). The agent holds a
back-and-forth until it books a class or the lead goes quiet; a **Follow-up Scheduler** then sends
up to 2 AI-written nudges and marks the lead `cold`. A separate **`Lead System — Error Handler`**
workflow (id `4L4K72aSSwiBcnjX`) catches any execution error and alerts an ops channel, so no lead
is silently dropped. See `PLAN.md` for the Phase 2 roadmap and `DEMO.md` for the demo script.

The mock studio used in the agent prompt is **"Reform Collective Pilates"** (Gulberg, Lahore;
2-week intro pass; reformer & mat classes), persona "Mia". Booking timezone is **`Asia/Karachi`
(PKT, UTC+5)** — set as the workflow timezone (drives the Google Calendar event tag) and as the
`ZONE` constant in **Parse Agent JSON**. The agent is given the current date each turn (so it books
real future dates, never a past year) and only offers/confirms classes that actually exist on the
schedule calendar.

### v.1.5 highlights (Phase 1 finalized)
- **Real availability, not prompt guesswork.** A dedicated **studio schedule calendar** of recurring
  class events is read deterministically each turn (`Get Schedule` → `Format Schedule`), expanded with
  `singleEvents`, with per-class **capacity** derived from `Trial:` bookings. The agent is fed the real
  open slots + seats-left and can only offer/confirm those (never fabricates "fully booked").
- **Two-way Slack lead channel** via polling (no public URL needed); replies and follow-ups delivered
  to the lead in-channel. Separate **lead bot** and **owner-alerts bot** (distinct Slack apps/channels).
- **AI-written follow-ups.** A quiet lead gets an OpenAI-generated nudge in Mia's voice that references
  what they last said, with a persuasive **template fallback** if the model call fails (lead never dropped).
  Cadence: first nudge after ~1h of silence, a final gentler nudge ~24h later, then marked `cold`.
- **Timezone + date** correctness: real future dates, PKT booking tag.
- **Error Handler** workflow (dead-letter alert) so no lead is silently dropped.
- Model: **`gpt-5.4-mini`** for both the conversation agent and the follow-up writer.
- The in-editor Chat box (`Chat Trigger`) is retained but **disabled** (superseded by Slack).

## Repo layout

- `PLAN.md` — the production & monetization plan (the working design doc; refined via Ultraplan).
- `workflows/lead-capture-core-loop.json` — importable n8n workflow export (the **live v.1.5** graph,
  39 nodes). Credentials, Slack channel IDs, and the calendar ID are redacted to placeholders
  (`POSTGRES_CRED_ID`, `SLACK_LEAD_CHANNEL_ID`, `STUDIO_SCHEDULE_CALENDAR_ID`, …); re-select/replace
  them after importing.
- `db/lead_system_schema.sql` — Postgres DDL (`studios`, `leads`, `messages`, `bookings`,
  `escalations`); `n8n_chat_histories` is auto-created by the Postgres Chat Memory node.
- `DEMO.md` — a 5-act demonstration script for the working prototype.
- `docs-project-outline.txt` — the original project brief.

## Architecture (Phase 1, v.1.5)

```
INBOUND
  Lead Form ─┐
  Slack poll ┼─▶ Normalize Lead ─▶ Upsert Lead ─▶ Log Inbound ─▶ Get Schedule ─▶ Format Schedule
  (Chat, off)┘                                                                          │
                                                                                        ▼
   AI Agent (gpt-5.4-mini, Postgres chat memory, real schedule injected) ─▶ Parse Agent JSON
     ─▶ Route by Intent
          ├─ booking:   Create Calendar Event ─▶ Insert Booking ─▶ Mark Booked ─▶ Notify Owner
          ├─ escalate:  Insert Escalation ─▶ Notify Owner
          └─ continue:  (no side effect)
     ─▶ Log Outbound ─▶ Route Reply ── Form screen / Slack reply / Chat response

FOLLOW-UP  (every 15 min)
  Find Stale Leads ─▶ Has Stale Leads? ─▶ Compose Follow-up ─▶ Write Nudge (AI) ─▶ Assemble Nudge
     ─▶ (parallel) Log Follow-up · Mark Cold if Final · Notify Owner · Send to Lead (Slack)
```

The deterministic side-effects (lead upsert, logging, booking, owner alerts) are driven off the
agent's **structured JSON output**, not side-effecting agent tools — making them reliable and
auditable. The agent gets only *read* context (the pre-fetched real schedule), so it can't invent
availability. Postgres `executeQuery` nodes don't pass items through, so side-effects fan out in
**parallel** from the upstream data node rather than chaining.

## Setup notes

- n8n app v2.1.4 (self-hosted). Form/`form` nodes pinned to **typeVersion 2.2** (2.5 isn't available
  on this version and fails activation).
- Booking timezone is the workflow timezone setting (`Asia/Karachi`) plus the `ZONE` constant in
  **Parse Agent JSON** — both become per-studio config in Phase 2.
- Slack inbound uses a **polling adapter** (Schedule Trigger → `channel:history` → dedup Code node)
  because the local instance isn't publicly reachable; real-time Events-API webhooks come with the
  Phase 2 VPS.
- Credentials needed in n8n: Postgres, OpenAI, Google Calendar OAuth2, and two Slack apps (lead bot
  + owner-alerts bot). Point `Get Schedule` / `Create Calendar Event` at the studio schedule calendar.
