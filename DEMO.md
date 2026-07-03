# Phase 1 Demonstration Guide — Lead Capture & Booking (v.1.5)

This is a script for demonstrating the working Phase 1 prototype. The system answers inbound
leads in the studio owner's voice, holds a real conversation, books trial classes to Google
Calendar, alerts the owner on Slack, and chases leads who go quiet — across a **website form**
and a **two-way Slack channel**.

Workflow: `Lead Capture & Booking — Core Loop v.1.5` (n8n, id `t7y3ISrzAcj8fCmA`).
Mock studio: **Reform Collective Pilates** (Lahore, PKT). Agent persona: "Mia", the owner.

---

## What to show (5 acts)

### Act 1 — Website form lead → instant reply + booking
1. Open the form: `http://<n8n-host>:5678/form/<form-webhookId>`.
2. Submit as a lead, e.g. *"I'd like to book the Saturday 10am beginner reformer class this week — please confirm. Name: Ayesha."*
3. The completion screen returns Mia's reply within seconds:
   *"Hi Ayesha! You're all set for the Beginner Reformer class this Saturday at 10am."*
4. **Show the payoff:**
   - Google Calendar: a new event **`Trial: Beginner Reformer`** at the correct date/time in **Pakistan Standard Time (UTC+5)**.
   - Slack `#new-leads`: owner alert **`:tada: New trial booked: Beginner Reformer at ... (lead ...)`**.
   - Postgres: rows in `leads` (status `booked`), `messages` (inbound + outbound), `bookings`.

> Verified live: execution #585 — agent booked `2026-06-27T10:00:00+05:00`, event `dfjnk1mbaltlar0kikvs5jm60o`.

### Act 2 — Slack two-way conversation (simulating an Instagram/FB DM)
1. In the Slack lead channel, post **as yourself** (a human): *"hey, do you have evening reformer classes?"*
2. Within ~1 minute the **lead bot** replies in-channel (the system polls Slack every minute).
3. Continue the back-and-forth until you give a day/time — it confirms and books, exactly like Act 1.
4. Point out: the agent **remembers the conversation** (per-user memory), so it adapts turn to turn.

### Act 3 — Escalation (human handoff)
1. As a lead, ask something it shouldn't answer: *"I have a herniated disc, is reformer safe for me?"* (medical) or *"can you do it cheaper?"* (price negotiation).
2. The agent returns a friendly holding message and **does not** try to answer.
3. Slack `#new-leads`: **`:rotating_light: Lead needs you ...`** — the owner is pulled in.

### Act 4 — Follow-up / ghost handling
1. Start a conversation, then **go quiet** (don't reply).
2. After the lead is idle past the threshold, the system sends a nudge in the studio's voice
   (delivered to the lead's Slack; logged + owner-notified for form/chat leads).
3. A second nudge follows; after that the lead is marked **`cold`** and left alone.

> Verified live: nudge #1 → nudge #2 → `cold`, with no duplicate/phantom nudges. The nudge is
> AI-written in Mia's voice and references what the lead last said (with a persuasive template
> fallback if the model call fails). **Production cadence: first nudge ~1h after silence, a final
> gentler nudge ~24h later, then `cold`.** For a live demo you can temporarily lower the intervals
> in `Find Stale Leads` (and the `Follow-up Scheduler` from 15 min) to see it fire in minutes.

### Act 5 — Reliability (the safety net)
- Every step logs to Postgres, so the full transcript and outcome of each lead is auditable.
- A separate **`Lead System — Error Handler`** workflow catches *any* execution error and alerts
  the owner on Slack — no lead is ever silently dropped.

---

## What is real vs simulated (be upfront)

| Piece | In this demo | In production |
|---|---|---|
| AI conversation, booking logic, memory, logging | **Real** | same |
| Google Calendar booking + PKT timezone | **Real** | same (per-studio calendar) |
| Slack owner alerts | **Real** | same |
| Lead channel | **Slack** (polled) + website form | Instagram/Messenger/SMS via their APIs |
| Studio calendar/availability | A dedicated **studio schedule calendar** (recurring class events) read live each turn, with real seats-left from `Trial:` bookings | Same, per-studio calendar ID |

## Known limitations (honest framing for the demo)
- **Availability is now calendar-verified.** The agent is fed the studio's real upcoming classes
  (expanded recurring events) with seats-left each turn and only offers/confirms those — it won't
  invent a time or a "fully booked". Capacity is derived from `Trial:` bookings against a cap of 8.
- **Booking doesn't yet hard-lock a seat.** Two leads booking the exact same slot in the same instant
  could both be confirmed; a free/busy re-check at create time closes this (tracked for Phase 2).
- **Slack inbound is polled (~1 min latency)**, because the local instance isn't publicly reachable.
  Real-time delivery comes with the Phase 2 VPS (Events API webhook).
- **Single studio.** Multi-studio config (per-studio prompt/timezone/calendar via DB) is Phase 2a.

## Reset between demos
- Delete test calendar events (`Trial: ...`) from Google Calendar.
- Clear/ignore test leads in Postgres (`ayesha_demo_*`, `fix_verify_lead`, `sara_test_*`, `tz_fix_test`).
