# Demonstration Guide — Lead Capture & Booking (v.2.0)

This is a script for demonstrating the working prototype. The system answers inbound leads in the
studio owner's voice, holds a real conversation, books trial classes to Google Calendar, alerts the
owner on Slack, and chases leads who go quiet — across a **real Instagram DM channel** and a
**two-way Slack channel**.

Workflow: `Lead Capture & Booking — Core Loop v.2.0` (n8n, id `t7y3ISrzAcj8fCmA`).
Mock studio: **Reform Collective Pilates** (Lahore, PKT). Agent persona: "Mia", the owner.

---

## What to show (5 acts)

### Act 1 — Instagram DM lead → live reply + booking
1. From a second Instagram account (an app tester/role account while the app is not public), **DM the
   studio's Instagram Business account**, e.g. *"Hi! What times do you have for beginner reformer
   this week?"*
2. Within seconds, **Mia replies in the Instagram inbox** with the studio's real open slots.
3. Reply to pick a time, e.g. *"Book me Saturday 10am."* — she confirms it.
4. **Show the payoff:**
   - Google Calendar: a new event **`Trial: Beginner Reformer`** at the correct date/time in
     **Pakistan Standard Time (UTC+5)**.
   - Slack owner channel: alert **`:tada: New trial booked: Beginner Reformer at ... (lead ...)`**.
   - Postgres: rows in `leads` (channel `instagram`, status `booked`), `messages` (inbound +
     outbound), `bookings` (with the calendar event id).

> Verified live: a real Instagram DM produced booking #21 — `Beginner Reformer`,
> `2026-08-01 10:00 PKT`, with the calendar event created and the owner alerted. The inbound webhook
> verifies Meta's `X-Hub-Signature-256` (a forged signature is dropped, no lead created) and
> de-dupes retried message IDs (a replayed `mid` produces no second reply).

### Act 2 — Slack two-way conversation (second live channel)
1. In the Slack lead channel, post **as yourself** (a human): *"hey, do you have evening reformer classes?"*
2. Within ~1 minute the **lead bot** replies in-channel (the system polls Slack every minute).
3. Continue the back-and-forth until you give a day/time — it confirms and books, exactly like Act 1.
4. Point out: the agent **remembers the conversation** (per-user memory, keyed `channel:external_id`),
   so it adapts turn to turn — and the *same brain* serves both Instagram and Slack.

### Act 3 — Escalation (human handoff)
1. As a lead, ask something it shouldn't answer: *"I have a herniated disc, is reformer safe for me?"* (medical) or *"can you do it cheaper?"* (price negotiation).
2. The agent returns a friendly holding message and **does not** try to answer.
3. Slack owner channel: **`:rotating_light: Lead needs you ...`** — the owner is pulled in.

### Act 4 — Follow-up / ghost handling
1. Start a conversation, then **go quiet** (don't reply).
2. After the lead is idle past the threshold, the system sends a nudge in the studio's voice.
   (Delivered to the lead on **Slack**; for **Instagram** leads the nudge is logged + owner-notified
   only — auto-DMing IG leads past Meta's 24-hour window is a deferred item.)
3. A second nudge follows; after that the lead is marked **`cold`** and left alone.

> The nudge is AI-written in Mia's voice and references what the lead last said (with a persuasive
> template fallback if the model call fails). **Production cadence: first nudge ~1h after silence, a
> final gentler nudge ~24h later, then `cold`.** For a live demo you can temporarily lower the
> intervals in `Find Stale Leads` (and the `Follow-up Scheduler` from 15 min) to see it fire in minutes.

### Act 5 — Reliability (the safety net)
- Every step logs to Postgres, so the full transcript and outcome of each lead is auditable.
- A separate **`Lead System — Error Handler`** workflow catches *any* execution error and alerts
  the owner on Slack — no lead is ever silently dropped.
- An **`IG Token Expiry Monitor`** workflow warns the owner before the Instagram token's ~60-day
  lifetime lapses, so outbound replies never silently stop.

---

## What is real vs simulated (be upfront)

| Piece | In this demo | In production |
|---|---|---|
| AI conversation, booking logic, memory, logging | **Real** | same |
| Google Calendar booking + PKT timezone | **Real** | same (per-studio calendar) |
| Slack owner alerts | **Real** | same |
| Instagram DM channel (inbound webhook + outbound Graph API) | **Real** | same; public messaging needs Meta App Review + Business Verification |
| Slack lead channel | **Real** (polled, ~1 min) | retained, or swapped for more DM channels |
| Studio calendar/availability | A dedicated **studio schedule calendar** (recurring class events) read live each turn, with real seats-left from `Trial:` bookings | Same, per-studio calendar ID |

## Known limitations (honest framing for the demo)
- **Instagram app not yet public.** While unpublished / in the current access tier, only accounts
  with a role on the Meta app can DM the bot. Messaging the general public needs **App Review +
  Business Verification**.
- **IG follow-up nudges aren't auto-sent yet.** Slack leads get auto-nudges; IG leads' nudges are
  logged + owner-alerted (Meta's 24-hour window / message tags handled in a later phase).
- **Availability is calendar-verified**, but **booking doesn't yet hard-lock a seat** — two leads
  booking the same slot in the same instant could both be confirmed; a free/busy re-check at create
  time closes this (tracked).
- **Single studio.** Multi-studio config (per-studio prompt/timezone/calendar via DB + a
  `channel_accounts` router) is the next core-productionization step.
- **Hosting** is currently a local Docker Desktop instance behind a Cloudflare tunnel; a VPS move
  (queue mode, backups, uptime monitoring) is the production-hardening step.

## Reset between demos
- Delete test calendar events (`Trial: ...`) from Google Calendar.
- Clear/ignore synthetic test leads in Postgres (delete children in `messages`/`bookings`/
  `escalations` first, then the `leads` row — a bare `DELETE` on `leads` violates the foreign key).
