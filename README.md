# Pilates Lead-Capture & Booking System (n8n + OpenAI + Postgres)

An AI lead-response system for boutique fitness studios: captures inbound leads, replies in the
owner's voice, holds a real conversation until it books a trial class or the lead goes cold,
books against Google Calendar, alerts the owner on bookings/escalations via Slack, and logs
everything to Postgres.

## Status

**Phase 1 (core conversational loop) is built and running** in a self-hosted n8n instance
(workflow `Lead Capture & Booking — Core Loop`, id `t7y3ISrzAcj8fCmA`). Current inbound channel
is an n8n hosted **Form** (website lead capture). See `PLAN.md` for the full Phase 2 production +
monetization roadmap (multi-channel: Instagram DMs → Messenger → FB Lead Ads → Twilio SMS;
VPS hosting; multi-studio; reliability/compliance).

## Repo layout

- `PLAN.md` — the production & monetization plan (the working design doc; refined via Ultraplan).
- `workflows/lead-capture-core-loop.json` — importable n8n workflow export (the **corrected**
  Phase 1 graph: studio-timezone-aware booking, branches converge before the form reply).
  Credentials and account-specific ids are redacted to placeholders
  (`CALENDAR_EMAIL`, `SLACK_CHANNEL_ID`); re-select them after importing.
- `db/lead_system_schema.sql` — Postgres DDL (`studios`, `leads`, `messages`, `bookings`,
  `escalations`); `n8n_chat_histories` is auto-created by the Postgres Chat Memory node.
- `docs-project-outline.txt` — the original project brief.

## Architecture (Phase 1)

```
Lead Form → Normalize Lead → Upsert Lead → Log Inbound → AI Agent (OpenAI gpt-4o-mini,
  Postgres chat memory, Google Calendar read tool) → Parse Agent JSON → Route by Intent
    ├─ booking:   Create Calendar Event → Insert Booking → Mark Lead Booked → Notify (Slack)
    ├─ escalate:  Insert Escalation → Notify (Slack)
    └─ continue:  (no side effect)
  → Log Outbound → Show Reply (form completion screen)
```

The deterministic side-effects (lead upsert, logging, booking, owner alerts) are driven off the
agent's **structured JSON output**, not side-effecting agent tools — making them reliable and
auditable. The agent only gets *read* tools (calendar availability).

## Setup notes

- n8n app v2.1.4 (self-hosted). Form/`form` nodes pinned to **typeVersion 2.2** (2.5 isn't
  available on this version and fails activation).
- Booking timezone is set via the `ZONE` constant in the **Parse Agent JSON** node
  (currently `America/New_York`) — this becomes per-studio config in Phase 2.
- Credentials needed in n8n: Postgres, OpenAI, Google Calendar OAuth2, Slack.
