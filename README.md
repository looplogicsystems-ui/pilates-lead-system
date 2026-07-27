# Pilates Lead-Capture & Booking System (n8n + OpenAI + Postgres)

An AI lead-response system for boutique fitness studios: captures inbound leads, replies in the
owner's voice, holds a real conversation until it books a trial class or the lead goes cold,
books against Google Calendar (checking real availability + class capacity), alerts the owner on
bookings/escalations via Slack, chases quiet leads with AI-written follow-ups, and logs everything
to Postgres.

## Status

**Phase 1 (core loop) and Phase 2a (real Instagram DM channel) are built and running** in a
self-hosted n8n instance (workflow `Lead Capture & Booking — Core Loop v.2.0`, id
`t7y3ISrzAcj8fCmA`, 45 nodes). The instance is reachable on public HTTPS via a **Cloudflare tunnel**
(`n8n.looplogicsystems.com`).

Inbound channels:
- **Instagram DMs (real).** A hardened webhook receives DMs to the studio's Instagram Business
  account, verifies Meta's `X-Hub-Signature-256` signature, de-dupes retried message IDs, runs the
  brain, and **replies in the Instagram inbox** via the Graph API. Verified end-to-end from a real
  IG account (DM → AI reply → booking on the calendar → owner Slack alert).
- **Slack (real, retained).** A "lead bot" polls a channel every minute and replies in it — a
  two-way channel that also delivers **proactive follow-ups** a webchat couldn't.

The agent holds a back-and-forth until it books a class or the lead goes quiet; a **Follow-up
Scheduler** then sends up to 2 AI-written nudges and marks the lead `cold`. A separate
**`Lead System — Error Handler`** workflow (id `4L4K72aSSwiBcnjX`) catches any execution error and
alerts an ops channel, so no lead is silently dropped. An **`IG Token Expiry Monitor`** workflow
(id `n56lLK7WRdYFesdW`) Slack-alerts the owner ~12 days before the Instagram token's ~60-day
lifetime lapses, so the bot never goes silently dark. See `PLAN.md` for the roadmap and `DEMO.md`
for the demo script.

The mock studio used in the agent prompt is **"Reform Collective Pilates"** (Gulberg, Lahore;
2-week intro pass; reformer & mat classes), persona "Mia". Booking timezone is **`Asia/Karachi`
(PKT, UTC+5)** — set as the workflow timezone (drives the Google Calendar event tag) and as the
`ZONE` constant in **Parse Agent JSON**. The agent is given the current date each turn (so it books
real future dates, never a past year) and only offers/confirms classes that actually exist on the
schedule calendar.

### v.2.0 highlights (Phase 2a — real Instagram channel)
- **Instagram Messaging, inbound.** Two webhook nodes share the path `/webhook/instagram`: a GET
  **verify** endpoint that echoes Meta's `hub.challenge`, and a POST **events** endpoint. Events are
  HMAC-verified against the app secret (accepts either the main or the Instagram-product secret),
  the raw body is read from binary for a byte-exact signature, retried `mid`s are de-duped via
  rolling static data, and text DMs are normalized into the common lead contract (`channel:
  'instagram'`, `external_id` = the Instagram-scoped user id).
- **Instagram Messaging, outbound.** Replies are sent with `POST graph.instagram.com/v25.0/me/messages`
  using a long-lived **Instagram User access token** (Instagram API with Instagram Login — no
  Facebook Page), stored as an n8n Header Auth credential.
- **Form + in-editor Chat retired.** The website Form and the disabled Chat trigger were removed;
  the channel-agnostic brain (Normalize → Upsert → Log → schedule read → Agent → booking/escalation
  → Route Reply) is unchanged and now routes replies to **Slack** or **Instagram**.
- **Token longevity safeguard.** `IG Token Expiry Monitor` (weekly) warns before the token lapses.
- **Deferred (by design):** auto-sending follow-up nudges to Instagram leads. Because of Meta's
  24-hour messaging window, IG leads' nudges are currently logged + owner-alerted only; Slack leads
  still receive auto-nudges. (Next: send within 24h, use a message tag beyond it.)

### v.1.6 / v.1.5 highlights (Phase 1)
- **Real availability, not prompt guesswork.** A dedicated **studio schedule calendar** of recurring
  class events is read deterministically each turn (`Get Schedule` → `Format Schedule`), expanded with
  `singleEvents`, with per-class **capacity** derived from `Trial:` bookings. The agent is fed the real
  open slots + seats-left and can only offer/confirm those (never fabricates "fully booked").
- **AI-written follow-ups** in Mia's voice referencing what the lead last said, with a persuasive
  **template fallback** if the model call fails. Cadence: first nudge ~1h after silence, a final
  gentler nudge ~24h later, then `cold`.
- **Resilient AI calls** (3× retry with backoff), **timezone + date** correctness (real future dates,
  PKT booking tag), and an **Error Handler** dead-letter workflow.
- Model: **`gpt-5.4-mini`** for both the conversation agent and the follow-up writer.

## Repo layout

- `PLAN.md` — the production & monetization plan (the working design doc).
- `workflows/lead-capture-core-loop.json` — importable n8n workflow export (the **live v.2.0** graph,
  45 nodes). Credentials, Slack channel IDs, and the calendar ID are redacted to placeholders
  (`POSTGRES_CRED_ID`, `OPENAI_CRED_ID`, `GOOGLE_CAL_CRED_ID`, `SLACK_LEAD_BOT_CRED_ID`,
  `SLACK_OWNER_BOT_CRED_ID`, `IG_TOKEN_CRED_ID`, `SLACK_LEAD_CHANNEL_ID`, `SLACK_OWNER_CHANNEL_ID`,
  `STUDIO_SCHEDULE_CALENDAR_ID`); re-select/replace them after importing.
- `db/lead_system_schema.sql` — Postgres DDL (`studios`, `leads`, `messages`, `bookings`,
  `escalations`); `n8n_chat_histories` is auto-created by the Postgres Chat Memory node.
- `DEMO.md` — a demonstration script for the working prototype.
- `docs-project-outline.txt` — the original project brief.

## Architecture (v.2.0)

```
INBOUND
  Instagram webhook ─ GET verify (echo hub.challenge)
                    └ POST events ─▶ Verify Signature ─▶ Extract ─▶ Dedupe ─┐
  Slack poll ───────────────────────────────────────────────────────────────┼─▶ Normalize Lead
                                                                             ┘        │
   Upsert Lead ─▶ Log Inbound ─▶ Get Schedule ─▶ Format Schedule                      ▼
   AI Agent (gpt-5.4-mini, Postgres chat memory, real schedule injected) ─▶ Parse Agent JSON
     ─▶ Route by Intent
          ├─ booking:   Create Calendar Event ─▶ Insert Booking ─▶ Mark Booked ─▶ Notify Owner
          ├─ escalate:  Insert Escalation ─▶ Notify Owner
          └─ continue:  (no side effect)
     ─▶ Log Outbound ─▶ Route Reply ── Slack reply / Send IG Reply (Graph API)

FOLLOW-UP  (every 15 min)
  Find Stale Leads ─▶ Has Stale Leads? ─▶ Compose Follow-up ─▶ Write Nudge (AI) ─▶ Assemble Nudge
     ─▶ (parallel) Log Follow-up · Mark Cold if Final · Notify Owner · Send to Lead (Slack only)
```

The deterministic side-effects (lead upsert, logging, booking, owner alerts) are driven off the
agent's **structured JSON output**, not side-effecting agent tools — making them reliable and
auditable. The agent gets only *read* context (the pre-fetched real schedule), so it can't invent
availability. Postgres `executeQuery` nodes don't pass items through, so side-effects fan out in
**parallel** from the upstream data node rather than chaining.

## Setup notes

- n8n app v2.1.4 (self-hosted, Docker Compose). Public HTTPS via **Cloudflare tunnel**; set
  `WEBHOOK_URL` / `N8N_EDITOR_BASE_URL` to the tunnel base so Meta's callback URL matches.
- **Instagram env vars** (host): `IG_VERIFY_TOKEN` (webhook handshake), `IG_APP_SECRET` and
  `IG_APP_SECRET_INSTAGRAM` (HMAC — Meta may sign with either). n8n 2.x also needs
  `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` (so nodes can read `$env`) and
  `NODE_FUNCTION_ALLOW_BUILTIN=crypto` (so the signature Code node can `require('crypto')`).
- **Meta app**: uses *Instagram API with Instagram Login* (Business/Creator IG account, no Facebook
  Page). The app must be **Published/Live** and the account's **webhook subscription toggle On** to
  receive `messages` webhooks; publishing requires a **Privacy Policy URL** (served from n8n itself
  at `/webhook/privacy`, workflow id `SqkuTaitCtgEWn7O`). App Review + Business Verification are only
  needed to message the general public — app-role/tester accounts work without them.
- **The Instagram token** is long-lived (~60 days). Reconnect it before expiry (the `IG Token Expiry
  Monitor` workflow warns you) and update the `Instagram DM Token` Header Auth credential.
- Booking timezone is the workflow timezone setting (`Asia/Karachi`) plus the `ZONE` constant in
  **Parse Agent JSON** — both become per-studio config in the multi-studio phase.
- Credentials needed in n8n: Postgres, OpenAI, Google Calendar OAuth2, two Slack apps (lead bot +
  owner-alerts bot), and the Instagram DM Token. Point `Get Schedule` / `Create Calendar Event` at
  the studio schedule calendar. (Google Calendar OAuth: add the tunnel redirect URI
  `https://<tunnel>/rest/oauth2-credential/callback` and publish the consent screen so the refresh
  token doesn't expire weekly.)
