# Plan: Productionizing & Monetizing the Lead-Capture System (Managed Service, VPS, Instagram-first)

## Context

Phase 1 is **built and working**: a single n8n workflow (`Lead Capture & Booking — Core Loop`, id `t7y3ISrzAcj8fCmA`) that takes an inbound message (website Form / webhook), upserts a lead, logs to Postgres, runs an OpenAI agent in the owner's voice with Postgres chat memory, and on intent routes to Google Calendar booking + Slack alerts, then logs the outbound reply. It's **synchronous** (reply returned in the HTTP/Form response) and **single-studio** (hardcoded `America/New_York`, placeholder prompt vars), running on **WSL/localhost (`172.28.96.1:5678`) — not publicly reachable**.

The goal now: make this a **monetizeable managed service** for small/medium studios, **self-hosted on a VPS**, that captures **time-sensitive inbound leads from Instagram DMs (first), then Facebook Messenger, Facebook Lead Ads, and Twilio SMS**, replies within ~1 minute 24/7, and is production-grade (reliable, secure, compliant, observable, multi-studio).

**Decisions locked:** operating model = **managed service** (you configure/operate per studio); hosting = **self-host on a VPS**; first channel = **Instagram DMs** (website Form stays as a free always-on channel).

## The core architectural shift: synchronous → async, adapter-based

Instagram/Messenger/SMS are async and two-sided. The webhook only returns `200 OK`; the reply is a separate outbound API call. So we split the monolith into:

```
[Per-channel INBOUND adapter]  ──▶  [Lead Brain sub-workflow]  ──▶  [Per-channel OUTBOUND send]
  webhook + verify + signature        (channel-agnostic core:        IG Graph API / Twilio /
  + dedupe + normalize to the         upsert, log, agent+memory,     FB Send API / Form response
  common contract                     booking/escalation, returns    / email)
                                      {reply, channel, external_id,
                                       actions})
```

- **Lead Brain** = today's core, refactored into a callable sub-workflow (n8n *Execute Sub-workflow*) that takes the normalized contract `{ studio_id, external_id, channel, name, message }` and **returns** `{ reply, intent, booking?, escalate? }` instead of responding to a webhook. This is the reusable, channel-agnostic engine.
- **Inbound adapters** (one tiny workflow per channel) own that channel's webhook, signature verification, dedupe, and field mapping into the contract, then call Lead Brain, then send the reply on that channel.
- **Website Form** stays as-is (synchronous) — it's a special adapter that just returns the reply in the Form Ending screen.

This matches the original "Component 1: the catchers" vision and means each new channel is a thin, isolated add-on.

## Multi-studio (managed, light multi-tenancy)

One platform, many studios — but **you** onboard each (no self-serve UI needed for managed).

- **`studios`** gains real config (today's placeholders become columns/JSON): `timezone`, `slack_channel_id`, `gcal_id`, and `prompt_vars` (name, location, type, offer, schedule, voice). The hardcoded `America/New_York` in **Parse Agent JSON** and the `{{STUDIO_NAME}}`/`{{OFFER}}`/`{{SCHEDULE}}` placeholders in the **AI Agent** system message move into per-studio config, loaded at the start of Lead Brain.
- **New `channel_accounts` table**: `(id, studio_id, channel, account_ref, credential_ref)`. `account_ref` = the IG account id / FB Page id / Twilio number. **On every inbound, look up `account_ref → studio_id`** so the right studio's prompt/timezone/calendar/Slack/credentials are used. This is the multi-tenant router.
- **Per-studio credentials** (managed model): you hold each studio's Page access token, Twilio subaccount/number, Google Calendar OAuth, Slack channel — stored as n8n credentials and/or a secrets table keyed by `studio_id`.

## Instagram DMs — concrete integration (first channel)

**Prerequisites (lead time — start early):**
- IG account must be **Business/Creator**, linked to a **Facebook Page**.
- Create a **Meta App** (Business type); add **Instagram** + **Messenger (IG messaging)** products.
- Permissions: `instagram_basic`, `instagram_manage_messages`, `pages_manage_metadata`, `pages_messaging`.
- **App Review + Business Verification** required to message users beyond app testers — this can take days-to-weeks, so kick it off first.

**Inbound:** subscribe the app's **Instagram webhook** to the `messages` field. Meta does a GET verification handshake (echo `hub.challenge` with your verify token), then POSTs message events. Adapter must verify the **`X-Hub-Signature-256`** HMAC (app secret). Payload: `entry[].messaging[]` with `sender.id` (IGSID = Instagram-scoped user id → `external_id`), `message.text`, `message.mid` (→ dedupe key). Lead name needs a separate `GET /{igsid}?fields=name` call (optional).

**Outbound:** `POST https://graph.facebook.com/v21.0/me/messages` with `{ recipient:{id:IGSID}, message:{text} }` using the **Page access token**. **24-hour standard messaging window** — freeform replies only within 24h of the lead's last message (fine for live conversations; cold-lead nudges past 24h need message tags or won't deliver — handle in the follow-up phase).

## Speed & reliability (the "respond within minutes" SLA)

- **n8n queue mode** (main + Redis + ≥1 worker) so concurrent leads process in parallel and spikes never block.
- **Idempotency / dedupe**: persist provider message id (`mid` / Twilio `MessageSid`) with a unique constraint (extend `messages` or a `processed_messages` table); ignore duplicate webhook deliveries (Meta/Twilio retry aggressively).
- **Error Trigger workflow + dead-letter**: on *any* execution error, alert an admin channel (Slack `#alerts`) and persist the failed lead so none is silently dropped — this is what protects the SLA.
- **Latency**: keep `gpt-4o-mini` (~1–3s); end-to-end inbound→sent typically <10s.
- **Debounce/batch** (v2): if a lead fires several quick DMs, a short keyed wait coalesces them into one AI turn.
- **Uptime monitoring**: external monitor (UptimeRobot/Better Uptime) on an n8n healthcheck endpoint; alert if a worker/main is down.

## Production-readiness checklist

- **Hosting**: VPS (e.g. Hetzner/DigitalOcean) running n8n in Docker (queue mode), behind **Caddy/Cloudflare for a domain + TLS** (public HTTPS is mandatory for all three providers' webhooks).
- **Data**: managed or backed-up Postgres (`pg_dump` cron or managed PG), connection pooling, and turn `lead_system_schema.sql` into **versioned migrations**.
- **Secrets/security**: env-based secrets, least-privilege tokens, n8n behind auth; verify **Meta `X-Hub-Signature-256`** and **Twilio request signatures**; verify tokens on webhook handshakes.
- **Compliance**: SMS **STOP/HELP/opt-out** handling (TCPA) and **A2P 10DLC** brand+campaign registration before SMS go-live; Meta Platform Terms + 24h window + content rules; a privacy policy, consent capture, and a data-retention policy.
- **AI guardrails**: structured-output (done); **validate real availability before confirming a booking** (use the agent's calendar read tool + a free/busy check at create time to prevent double-booking and invented slots); keep lead text separated from system prompt (injection resistance); max-turn cap; escalation on medical/pricing/abuse (partially done).
- **Human handoff**: a `leads.ai_paused` flag so the owner can take over a thread; inbound skips the AI when paused.
- **Staging**: a separate n8n + test Meta app + Twilio test creds; synthetic-inbound smoke tests asserting DB rows + an actual outbound send.

## Monetization (managed service)

- **Pricing**: setup fee + monthly retainer per studio (optionally + per-booking or per-conversation). One VPS serves many studios, so margin scales well.
- **Cost drivers / studio / month**: LLM (`gpt-4o-mini` ≈ pennies/conversation), Twilio (≈ $0.0079/SMS US + number rental + A2P fees), hosting (amortized across studios), your setup time. A few-hundred-$/mo retainer leaves healthy margin.
- **Value prop / ROI**: "every lead answered in <1 min, 24/7, in your voice, trials booked automatically." ROI = recovered-leads × trial-conversion × member LTV — easy to justify for a boutique studio.
- **Onboarding playbook (per studio)**: connect IG/Page + Calendar + Slack (you do it with their admin access), set offer/schedule/voice/timezone in `studios`, add their `channel_accounts` row, smoke-test, go live.

## Build order (concrete, on top of what's built)

**Phase 2a — Productionize the core (channel-agnostic):**
1. Refactor the current workflow's core into a **Lead Brain** sub-workflow that *returns* `{reply, intent, …}` (decouple from the Form/HTTP response).
2. Move studio config into **`studios`** (timezone, prompt vars, slack channel, calendar) + add **`channel_accounts`** routing table; load per-lead at the top of Lead Brain (replaces hardcoded NY timezone + prompt placeholders).
3. Add **idempotency** (message-id dedupe), an **Error Trigger** workflow + dead-letter + admin alert.

**Phase 2b — Stand up production + Instagram:**
4. VPS + domain + HTTPS + n8n queue mode + backed-up Postgres.
5. Meta App (Business), IG↔Page link, permissions, **webhook verify endpoint**, kick off **App Review + business verification**.
6. **`Channel Adapter: Instagram`** workflow: GET verify + POST events, signature check, dedupe, normalize → Lead Brain → **send via Graph API** (24h-window aware).
7. End-to-end test from a real IG account.

**Phase 2c — Add SMS + Facebook (same pattern):**
8. **Twilio SMS** adapter (+ A2P 10DLC, STOP/HELP), **FB Messenger** adapter, **FB Lead Ads** trigger.

**Phase 2d — Lifecycle & scale:**
9. Cold-lead follow-up (scheduled, 24h-window/message-tag aware), human-handoff flag, availability-validated booking, message debounce.
10. Uptime monitoring + verified backups + a runbook; onboard **studio #2** to prove the managed multi-studio config.

## Verification (per phase)

- **Core refactor**: Form + a synthetic inbound both flow through Lead Brain → correct lead/message rows; booking still creates a calendar event at the **studio's** timezone (not hardcoded).
- **Routing**: two studios with different `channel_accounts` route to the right prompt/calendar/Slack.
- **Instagram**: webhook GET handshake passes; signature verification rejects a tampered body; a real IG DM produces a reply **in the IG inbox**; replaying the same `mid` produces **no** duplicate reply; an induced error fires the admin alert + dead-letter.
- **SMS**: inbound SMS replies; **STOP** halts further messaging; signature validation on.
- **Booking**: agent only offers real open slots; concurrent bookings can't double-book the same slot.

## Out of scope (explicitly, for later)

- Self-serve **multi-tenant SaaS** (OAuth onboarding UI, Stripe self-checkout, tenant isolation) — revisit only if you outgrow the managed model.
- Channels beyond IG/Messenger/Lead Ads/SMS (WhatsApp, TikTok, web chat widget).
- Advanced analytics dashboard for studio owners (start with NocoDB/Slack).
