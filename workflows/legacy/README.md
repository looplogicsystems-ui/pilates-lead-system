# Legacy workflow exports

## `lead-capture-core-loop-v2.0.json`

The single 45-node workflow that ran the whole system through Phase 2a
(`Lead Capture & Booking — Core Loop v.2.0`, live id `t7y3ISrzAcj8fCmA`).

Superseded by the adapter + Lead Brain split in `../`. It is kept for two
reasons:

1. **Rollback.** The workflow still exists in n8n, unpublished. If the new
   architecture has to be backed out, republish `t7y3ISrzAcj8fCmA` and
   unpublish `Channel Adapter: Instagram` — they share the webhook path
   `instagram`, so only one may be active at a time and the Meta callback URL
   does not change either way.
2. **Reference.** It is the last version with the old behaviour, which several
   migration comments refer back to.

Do not import this expecting it to work against the current schema: it
hardcodes `studio_id = 1`, `Asia/Karachi`, `CAP = 8` and the studio persona,
and it predates `channel_accounts` / `processed_messages`.
