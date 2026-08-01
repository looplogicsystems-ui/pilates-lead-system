# Pilates Lead-Capture & Booking System (n8n + OpenAI + Postgres)

An AI lead-response system for boutique fitness studios: captures inbound leads, replies in the
owner's voice, holds a real conversation until it books a trial class or the lead goes cold,
books against Google Calendar (checking real availability + class capacity), alerts the owner on
bookings/escalations via Slack, chases quiet leads with AI-written follow-ups, and logs everything
to Postgres.

## Status

**Phase 1, the Instagram channel, and the Phase 2a productionization refactor are built and
running** on self-hosted n8n 2.1.4, reachable on public HTTPS via a Cloudflare tunnel
(`n8n.looplogicsystems.com`).

The system is no longer one workflow. It is a **channel-agnostic core with thin per-channel
adapters**, driven by per-studio configuration in Postgres:

| Workflow | id | Role |
|---|---|---|
| `Lead Brain` | `fvHhAUDCbVN9ELb0` | Channel-agnostic core. Resolves the studio, upserts the lead, runs the agent, books/escalates, **returns** a result. Never touches a webhook. |
| `Channel Adapter: Instagram` | `OQMXzwDyu5YAmRTo` | GET verify + POST events on `/webhook/instagram`, HMAC signature, durable dedupe, calls the brain, sends via Graph API. |
| `Channel Adapter: Slack` | `px8F683kj1I8Sb9X` | Polls the lead channel, same contract, replies in-channel. |
| `Follow-up Scheduler` | `fyk6eYsVGtUVS1dh` | Chases quiet leads with AI-written nudges on the studio's cadence. |
| `Lead System — Error Handler` | `4L4K72aSSwiBcnjX` | Any execution error → a `dead_letters` row **and** an ops Slack alert. |
| `IG Token Expiry Monitor` | `n56lLK7WRdYFesdW` | Weekly, per studio, warns before an Instagram token lapses. |
| `Legal Pages (Privacy Policy)` | `SqkuTaitCtgEWn7O` | Serves the Meta-required privacy page. |

```
[Inbound adapter]        →  [Lead Brain sub-workflow]        →  [Outbound send]
 webhook, verify,           resolve studio from                 IG Graph API /
 signature, dedupe,         channel_accounts, upsert,           Slack
 normalize to contract      log, agent + memory,
                            booking/escalation,
                            RETURNS a result
```

**Inbound contract** (adapter → brain). Note there is no `studio_id`: an adapter never asserts
which studio a message belongs to — the brain resolves it.

```json
{
  "channel": "instagram",
  "account_ref": "<IG account id / Slack channel id>",
  "external_id": "<IGSID or Slack user id>",
  "name": null,
  "message": "hey do you have anything thursday evening?",
  "provider_message_id": "<mid or Slack ts>",
  "received_at": "2026-08-01T14:22:00Z"
}
```

**Outbound contract** (brain → adapter): `{ reply, intent, send, lead_id, studio_id, channel,
external_id, booking_created, credential_ref }`. `send: false` means "produced no reply on
purpose" — the lead is paused, or the account was unknown.

## What changed in this refactor

- **Multi-studio is real.** `channel_accounts (channel, account_ref) → studio_id` routes every
  inbound message; `studios` holds timezone, calendars, Slack channels, capacity, follow-up
  cadence and the persona (`prompt_vars`). Verified with two studios on different timezones — each
  got its own persona, its own config, and correctly localized class times, with nothing crossing
  over. **There is no fallback studio**: an unrecognised account dead-letters and stops.
- **Batched messages no longer drop leads.** The old monolith collapsed every message in a webhook
  batch or Slack poll into a single AI turn (`Format Schedule` emitted one item; everything
  downstream used `.first()`), so the second person to message in the same batch was logged and
  then never answered. Adapters now call the brain with **`mode: each`** — one sub-execution per
  message. Verified: two senders in one POST get two correct, different replies.
- **Durable idempotency.** `processed_messages` with `INSERT … ON CONFLICT DO NOTHING RETURNING`
  replaces the in-memory dedupe array that was lost on every restart. A replayed `mid` is a no-op
  even across a restart.
- **A failed calendar write can no longer fake a booking.** `Create Calendar Event` routes failures
  down an error branch: no `bookings` row, lead not marked `booked`, owner alerted, and the lead
  gets an honest holding reply instead of silence.
- **Escalation actually pauses automation** (`status = 'escalated'`), and `ai_paused` lets the owner
  take over a thread — inbound then skips the AI and notifies them.
- **Cold leads can re-enter.** `Upsert Lead`'s `ON CONFLICT` resets `cold`/`new` → `engaged`, unless
  the lead is paused.
- **Availability is bounded and correctly counted.** `Get Schedule` sets `timeMin`/`timeMax` and
  `returnAll`; `Format Schedule` drops past classes, de-duplicates events, and matches `Trial:`
  bookings to classes within ±15 minutes instead of requiring exact timestamp equality.
- **The prompt no longer contradicts itself** about full classes, and the persona is templated from
  `studios.prompt_vars` instead of being string literals.
- **Errors survive Slack.** The error handler persists to `dead_letters` before alerting.

## Repo layout

- `PLAN.md` — the production & monetization plan.
- `PHASE-2A-BRIEF.md` — the brief this refactor was built against.
- `db/migrations/` — ordered, idempotent migrations tracked in `schema_migrations`.
- `db/migrate.sh` — applies all of them (works on a bare database).
- `db/seed/local-ids.sql.example` — deployment-specific ids; copy to `local-ids.sql` (gitignored).
- `workflows/*.json` — the live graphs, credential ids redacted to placeholders
  (`POSTGRES_CRED_ID`, `OPENAI_CRED_ID`, `GOOGLE_CAL_CRED_ID`, `SLACK_LEAD_BOT_CRED_ID`,
  `SLACK_OWNER_BOT_CRED_ID`, `SLACK_OPS_BOT_CRED_ID`, `IG_TOKEN_CRED_ID`).
- `workflows/build/` — the generators that produce those JSON files, plus `deploy.sh`.
- `workflows/legacy/` — the retired v2.0 monolith, kept as the rollback path.
- `DEMO.md` — demonstration script.

### Why the workflows are generated

`workflows/build/*.py` emit each graph twice: redacted into `workflows/` (committed) and with real
credential ids into a scratch directory (imported into n8n). That keeps the committed copy and the
running copy from drifting, and makes redaction mechanical rather than a checklist — the generators
assert that no live credential id can reach the committed file.

`workflows/build/deploy.sh` regenerates, imports, republishes and reloads everything. n8n 2.x keeps
the draft (`workflow_entity`) separate from the published graph (`workflow_history` +
`activeVersionId`), and `import:workflow` only writes the draft — so the deploy script also
refreshes the history row and publishes it. Without that step an import silently appears to do
nothing.

## Setup notes

- **Database.** The lead-system tables live in the **`postgres`** database (n8n's own tables are in
  `n8n`). `db/migrate.sh` targets `postgres` by default.
- **Env vars** (host): `IG_VERIFY_TOKEN`, `IG_APP_SECRET`, `IG_APP_SECRET_INSTAGRAM`,
  `OPS_SLACK_CHANNEL_ID`, plus `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` (nodes need `$env`) and
  `NODE_FUNCTION_ALLOW_BUILTIN=crypto` (signature check). Per-studio values are **not** env vars —
  they live in `studios` / `channel_accounts`.
- **`N8N_ENCRYPTION_KEY` and `N8N_USER_MANAGEMENT_JWT_SECRET`.** If the JWT secret is unset, n8n
  derives it from the encryption key — so rotating the encryption key invalidates every public-API
  key and logs everyone out. Pin the JWT secret explicitly so the two are independent.
- **Meta app**: *Instagram API with Instagram Login* (Business/Creator IG account, no Facebook
  Page). Must be Published/Live with the account's webhook subscription toggle On. Publishing needs
  a Privacy Policy URL (served at `/webhook/privacy`). App Review + Business Verification are only
  needed to message the general public.
- **Instagram token** is long-lived (~60 days). After reconnecting it, update the n8n credential
  **and** set `channel_accounts.token_issued_at = now()` so the monitor stays accurate.
- **Google Calendar OAuth**: add the tunnel redirect URI
  `https://<tunnel>/rest/oauth2-credential/callback` and publish the consent screen so the refresh
  token doesn't expire weekly.

## Known limitations

- **One Instagram token per deployment.** n8n cannot select a credential by expression, so the send
  node uses a static credential. `channel_accounts.credential_ref` already records which credential
  each studio should use; a second Instagram studio needs a Switch on that value plus one send node
  per credential (or n8n's licensed dynamic-credentials resolver).
- **No booking lock.** Two leads confirming the same last seat in the same instant can both succeed.
  A `TODO` marks where the free/busy re-check and row lock belong.
- **IG follow-up nudges are not auto-sent.** Slack leads get auto-nudges; Instagram leads' nudges are
  logged and the owner alerted, because Meta's 24-hour window needs a message tag.
- **Hosting** is a local Docker Desktop instance behind a Cloudflare tunnel; the VPS move (queue
  mode, backups, uptime monitoring) is still outstanding.
