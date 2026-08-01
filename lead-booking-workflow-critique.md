Lead Capture & Booking — Development Critique Summary
 
**Lead Capture ****&**** Booking System**
 
Development Critique — Summary
 
*Core Loop v1.5  ·  Proof-of-concept stage  ·  Reviewed July 3, 2026*
 
*This is a condensed version of the full development critique: a punch-list of what stands between the current working demo and a system a real studio could run on, ordered by business impact.*
 
# **1. Baseline — What Exists Today**
 
| **Field** | **Detail** |
| --- | --- |
| **Workflow** | “Lead Capture & Booking — Core Loop v1.5” (n8n, active) — ID t7y3ISrzAcj8fCmA |
| **Reviewed** | July 3, 2026 |
| **Stage** | Early development / proof-of-concept |
| **Persona** | “Mia” — AI concierge for Reform Collective (Pilates studio, Gulberg, Lahore) |
| **Real channel today** | Website form only. Instagram/Facebook are simulated via a Slack poller; the real Meta/chat trigger is disabled. |
| **What already works well** | Idempotent upserts, retry + continueOnError on side-effect nodes, a dedicated error workflow, correct timezone handling, calendar-as-source-of-truth capacity, per-lead chat memory. |
 
# **2. Critical Gaps — Revenue ****&**** Reputation Risk**
 
- **Follow-ups don****'****t reach most leads — **The form captures an email but no email node exists anywhere in the workflow. Web-form leads get one reply and then silence. Only Slack-simulated leads actually receive nudges — form and Instagram leads have their follow-up logged and the owner notified, but the lead itself never gets it.
 
- ***Fix: ****Add a real email sender and make follow-up delivery channel-aware for every lead type.*
 
- **No real channels; WhatsApp is missing entirely — **Instagram/Facebook are only simulated through a Slack poller — there's no real Meta Messaging integration. WhatsApp, realistically the #1 lead channel for a Lahore studio, isn't built at all. The only genuinely working end-to-end channel is the website form.
 
- ***Fix: ****Integrate WhatsApp Business API plus real Instagram/Facebook DM ingestion and email, prioritizing WhatsApp.*
 
- **Nothing happens after a booking is made — **No calendar invite or confirmation goes to the lead, no reminders are sent, and there's no no-show tracking or trial-to-membership step — the system stops tracking value at “booked.”
 
- ***Fix: ****Add the lead as a calendar attendee, automate reminders, and extend the status model to capture show-rate and conversion.*
 
# **3. High-Impact Gaps — Funnel Logic**
 
## **3.1  Nudge timing and gating**
 
- **Cadence is demo-speed, not production-speed — **Nudges fire after 3 minutes of quiet and a lead goes cold after two nudges (about 6 minutes) — production needs a day-1 / day-3 / day-7 style drip instead.
 
- **Escalated leads keep getting auto-nudged — **A lead flagged for a medical or pricing question still receives a cheerful automated nudge minutes later — a tone problem and a liability risk.
 
- ***Fix: ****Add a paused/escalated state that excludes a lead from automated nudges.*
 
- **Cold or booked leads can****'****t re-enter the funnel — **The upsert logic updates a lead's name but never resets their status, so a returning “cold” lead stays stuck.
 
- ***Fix: ****Reset status to “engaged” whenever a new inbound message arrives from a cold lead.*
 
## **3.2  Booking integrity**
 
- **Bookings can be confirmed with no real calendar event — **If the calendar-write call fails, the flow still records the booking as confirmed, tells the lead they're booked, and notifies the owner — with no event actually created, guaranteeing a no-show.
 
- ***Fix: ****Only record/confirm a booking if the calendar event was verifiably created.*
 
- **Race conditions and brittle seat counting — **Capacity is read from the calendar with no locking, so two simultaneous bookings can take the same last seat; seats are matched by exact start-time equality, so small timing differences break the count. The prompt also contradicts itself on whether the AI may ever say a class is full.
 
- ***Fix: ****Add booking locks, more tolerant seat-matching, a single consistent full-class policy, and real reschedule/cancel handling instead of creating duplicate events.*
 
# **4. Product ****&**** Data-Model Gaps**
 
- **Multi-studio support isn****'****t real yet — **A studios table exists but everything is hardcoded to one studio, with the seeded price ($35 USD) contradicting the prompt's stated price (PKR 3,500); source/campaign attribution is never populated.
 
- ***Fix: ****Drive persona, offer, timezone, and capacity from the studios table if reselling to other studios is the goal.*
 
- **Lead identity can fragment across channels — **A single field stores either an email or an IG handle, so the same person messaging through a different channel becomes a duplicate lead record.
 
- ***Fix: ****Add identity resolution / merge logic across channels.*
 
- **No ROI visibility — **There's no funnel reporting from lead → engaged → booked → showed → converted, and no owner-facing summary.
 
- ***Fix: ****Add a simple weekly digest workflow with conversion metrics.*
 
# **5. Compliance ****&**** Reliability Notes**
 
- **Consent ****&**** opt-out: **automated follow-ups have no opt-out mechanism — a policy risk on WhatsApp, Meta, and email.
 
- **Abuse protection: **no rate limiting or CAPTCHA on the open form; every submission triggers a paid LLM call and a calendar read.
 
- **PII handling: **emails and message content are stored with no retention or consent policy.
 
- **Minor reliability items: **synchronous form latency makes visitors wait on the full pipeline; the Slack poller caps at 10 messages/minute during bursts; some nudge-template code is written but never used.
 
# **6. Suggested Priority Order**
 
| **#** | **Focus** | **Why it matters** |
| --- | --- | --- |
| 1 | **Real send channels: WhatsApp + email** | Fixes silent follow-ups and the missing WhatsApp channel (2.1, 2.2) |
| 2 | **Post-booking reminders, no-show tracking, trial→paid step** | Closes the revenue loop after a booking is made (2.3) |
| 3 | **Fix nudge cadence; pause automation on escalation** | Moves cadence to a real drip; stops nudging medical/price escalations (3.1) |
| 4 | **Block booking confirmation on calendar-write failure** | Prevents "booked but no event" no-shows (3.2) |
| 5 | **Add reschedule/cancel handling; resolve capacity policy conflict** | Stops duplicate bookings and contradictory full/not-full logic (3.2) |
| 6 | **Make studio config data-driven; add source attribution** | Enables real multi-studio reuse instead of hardcoded values (4.1, 4.2) |
| 7 | **Reporting/digest, consent ****&**** opt-out, abuse protection** | Adds ROI visibility and compliance safeguards (4.3, 5) |
 
*Note: this review is based on the workflow definition itself. Pulling recent execution history would confirm real-world error rates and where runs stall in practice — recommended before locking final priorities.*
 
Page  of