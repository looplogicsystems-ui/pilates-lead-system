#!/usr/bin/env python3
"""Generate the `Lead Brain` workflow JSON.

Two outputs from one source of truth:
  workflows/lead-brain.json          redacted (credential ids -> placeholders), committed
  <scratch>/lead-brain.live.json     real credential ids, imported into n8n

Run:  python3 workflows/build/build_lead_brain.py <scratch-dir>

Why a generator rather than hand-written JSON: the redacted copy in the repo and
the copy that actually runs must never drift, and n8n's export carries operator
PII that has to be stripped every time. Generating both from one definition makes
that mechanical instead of a checklist.
"""
import json
import os
import sys

# --- credential placeholders -> live ids -------------------------------------
CREDS = {
    "POSTGRES_CRED_ID":         "VVIxtEmT1GWsEZn9",
    "OPENAI_CRED_ID":           "2dmtKOJYL68BMOyG",
    "GOOGLE_CAL_CRED_ID":       "Uv734NiOuCu7zMp1",
    "SLACK_OWNER_BOT_CRED_ID":  "AL4h8l7yMMNZHFBE",
}

# Shorthands used inside n8n expressions.
IN = "$('When Called').first().json"        # the inbound contract
CFG = "$('Resolve Studio').first().json"    # resolved studio config
AGENT = "$('Parse Agent JSON').first().json"

nodes = []
conns = {}


def node(name, typ, ver, pos, params, **kw):
    n = {"id": name.lower().replace(" ", "-").replace(":", "").replace("?", ""),
         "name": name, "type": typ, "typeVersion": ver, "position": pos,
         "parameters": params}
    n.update(kw)
    nodes.append(n)
    return n


def connect(src, dst, out=0, kind="main"):
    c = conns.setdefault(src, {}).setdefault(kind, [])
    while len(c) <= out:
        c.append([])
    c[out].append({"node": dst, "type": kind, "index": 0})


def pg(name, pos, query, replacement=None, **kw):
    params = {"operation": "executeQuery", "query": query, "options": {}}
    if replacement:
        params["options"]["queryReplacement"] = replacement
    return node(name, "n8n-nodes-base.postgres", 2.6, pos, params,
                credentials={"postgres": {"id": "POSTGRES_CRED_ID",
                                          "name": "Postgres account"}}, **kw)


def slack(name, pos, channel_expr, text, cred="SLACK_OWNER_BOT_CRED_ID", **kw):
    return node(name, "n8n-nodes-base.slack", 2.3, pos, {
        "select": "channel",
        "channelId": {"__rl": True, "value": channel_expr, "mode": "id"},
        "text": text, "otherOptions": {},
    }, credentials={"slackApi": {"id": cred, "name": "Owner Alerts Bot (Slack)"}},
        onError="continueRegularOutput", retryOnFail=True, **kw)


# ---------------------------------------------------------------------------
# 1. Entry + studio resolution
# ---------------------------------------------------------------------------
node("When Called", "n8n-nodes-base.executeWorkflowTrigger", 1.1, [-460, 300],
     {"inputSource": "passthrough"})

pg("Resolve Studio", [-240, 300], """-- Route the message to its studio. `account_ref` is the account the lead
-- messaged (IG entry.id / Slack channel id), NOT the lead's own id.
--
-- LEFT JOINs from a one-row source so this ALWAYS returns exactly one row:
-- an unmatched account comes back with studio_id NULL rather than an empty
-- result set. An inner join returned zero rows, which meant the node emitted
-- no item at all and every downstream node — including the dead-letter branch
-- that is supposed to catch exactly this case — was silently skipped.
SELECT (s.id IS NOT NULL) AS studio_found,   -- explicit: see note below
       s.id  AS studio_id,
       s.name AS studio_name,
       s.timezone,
       s.class_capacity,
       s.booking_duration_minutes,
       s.gcal_schedule_id,
       s.gcal_booking_id,
       s.slack_lead_channel_id,
       s.slack_owner_channel_id,
       s.prompt_vars,
       s.offers,
       ca.credential_ref
  FROM (SELECT 1) AS anchor
  LEFT JOIN channel_accounts ca
         ON ca.channel = $1 AND ca.account_ref = $2 AND ca.active
  LEFT JOIN studios s
         ON s.id = ca.studio_id AND s.active
 LIMIT 1;""",
   replacement="={{ [$json.channel, $json.account_ref] }}",
   alwaysOutputData=True, retryOnFail=True)
connect("When Called", "Resolve Studio")

node("Studio Found?", "n8n-nodes-base.if", 2.2, [-20, 300], {
    "conditions": {"options": {"version": 2, "leftValue": "", "caseSensitive": True,
                               "typeValidation": "loose"},
                   # Tests an explicit boolean from the query rather than
                   # "does studio_id exist". n8n's `exists` operator treats a
                   # SQL NULL as present, so an unmatched account would have
                   # sailed down the true branch and been upserted against
                   # whatever studio_id happened to be in scope.
                   "conditions": [{"id": "has-studio",
                                   "leftValue": "={{ $json.studio_found }}",
                                   "rightValue": "true",
                                   "operator": {"type": "boolean", "operation": "true",
                                                "singleValue": True}}],
                   "combinator": "and"},
    "options": {}})
connect("Resolve Studio", "Studio Found?")

# --- unknown account: dead-letter and stop. Never guess a studio. -----------
pg("Dead Letter: Unknown Account", [200, 480], """INSERT INTO dead_letters (workflow_id, execution_id, reason, payload, error)
VALUES ($1, $2, 'unknown_account', $3::jsonb, $4);""",
   replacement=("={{ [$workflow.id, $execution.id, "
                f"JSON.stringify({IN}), "
                f"'No active channel_accounts row for channel=' + {IN}.channel + "
                f"' account_ref=' + {IN}.account_ref] }}}}"),
   onError="continueRegularOutput", retryOnFail=True)
connect("Studio Found?", "Dead Letter: Unknown Account", out=1)

slack("Alert Ops: Unknown Account", [420, 480],
      "={{ $env.OPS_SLACK_CHANNEL_ID }}",
      ("=:no_entry: *Unrecognised account* — message dropped, no studio matched.\n"
       f"Channel: {{{{ {IN}.channel }}}}\n"
       f"Account ref: {{{{ {IN}.account_ref }}}}\n"
       f"From: {{{{ {IN}.external_id }}}}\n"
       "Dead-lettered. Add a `channel_accounts` row to route this account."))
connect("Dead Letter: Unknown Account", "Alert Ops: Unknown Account")

# Must RETURN an item, not just stop. Execute Sub-workflow treats a
# sub-execution that yields no output as a failure and retries it, so a NoOp
# here produced three dead-letter rows and three ops alerts per bad message.
node("Return: Unknown Account", "n8n-nodes-base.set", 3.4, [640, 480], {
    "assignments": {"assignments": [
        {"id": "u1", "name": "reply", "type": "string", "value": ""},
        {"id": "u2", "name": "intent", "type": "string", "value": "unknown_account"},
        {"id": "u3", "name": "send", "type": "boolean", "value": "={{ false }}"},
        {"id": "u4", "name": "channel", "type": "string", "value": f"={{{{ {IN}.channel }}}}"},
        {"id": "u5", "name": "external_id", "type": "string",
         "value": f"={{{{ {IN}.external_id }}}}"},
    ]}, "options": {}})
connect("Alert Ops: Unknown Account", "Return: Unknown Account")

# ---------------------------------------------------------------------------
# 2. Lead upsert with lifecycle rules
# ---------------------------------------------------------------------------
pg("Upsert Lead", [200, 260], """-- Idempotent per (channel, external_id). The status CASE is the lifecycle:
--   cold/new  -> engaged   (a returning lead re-enters the funnel; previously
--                           a cold lead was stuck cold forever)
--   escalated -> escalated (stays with the human until they resolve it)
--   booked    -> booked    (already converted; must not re-enter nudging)
-- and nothing moves at all while ai_paused is set.
INSERT INTO leads (external_id, channel, name, studio_id, status, source_offer, created_at, updated_at)
VALUES ($1, $2, $3, $4, 'engaged', $5, now(), now())
ON CONFLICT (channel, external_id) DO UPDATE SET
    name         = COALESCE(EXCLUDED.name, leads.name),
    source_offer = COALESCE(leads.source_offer, EXCLUDED.source_offer),
    status       = CASE
                     WHEN leads.ai_paused              THEN leads.status
                     WHEN leads.status IN ('cold','new') THEN 'engaged'
                     ELSE leads.status
                   END,
    updated_at   = now()
RETURNING id, status, ai_paused;""",
   replacement=("={{ [" + f"{IN}.external_id, {IN}.channel, {IN}.name, "
                f"{CFG}.studio_id, {CFG}.offers" + "] }}"),
   retryOnFail=True)
connect("Studio Found?", "Upsert Lead", out=0)

pg("Log Inbound", [420, 260], """INSERT INTO messages (lead_id, direction, channel, body, created_at)
VALUES ($1, 'inbound', $2, $3, now());""",
   replacement="={{ [$json.id, " + f"{IN}.channel, {IN}.message] }}}}",
   onError="continueRegularOutput", retryOnFail=True, alwaysOutputData=True)
connect("Upsert Lead", "Log Inbound")

node("AI Paused?", "n8n-nodes-base.if", 2.2, [640, 260], {
    "conditions": {"options": {"version": 2, "leftValue": "", "caseSensitive": True,
                               "typeValidation": "loose"},
                   "conditions": [{"id": "paused",
                                   "leftValue": "={{ $('Upsert Lead').first().json.ai_paused }}",
                                   "rightValue": "true",
                                   "operator": {"type": "boolean", "operation": "true",
                                                "singleValue": True}}],
                   "combinator": "and"},
    "options": {}})
connect("Log Inbound", "AI Paused?")

# --- handed to a human: log it, tell the owner, send nothing ---------------
slack("Notify Owner: Paused Lead", [860, 440],
      f"={{{{ {CFG}.slack_owner_channel_id }}}}",
      ("=:raising_hand: *Lead you took over just replied* — AI is paused, so nothing was sent.\n"
       f"Lead: {{{{ {IN}.external_id }}}} ({{{{ {IN}.channel }}}})\n"
       f"They said: \"{{{{ {IN}.message }}}}\""))
connect("AI Paused?", "Notify Owner: Paused Lead", out=0)

node("Return: Paused", "n8n-nodes-base.set", 3.4, [1080, 440], {
    "assignments": {"assignments": [
        {"id": "p1", "name": "reply", "value": "", "type": "string"},
        {"id": "p2", "name": "intent", "value": "paused", "type": "string"},
        {"id": "p3", "name": "send", "value": "={{ false }}", "type": "boolean"},
        {"id": "p4", "name": "lead_id", "value": "={{ $('Upsert Lead').first().json.id }}", "type": "number"},
        {"id": "p5", "name": "studio_id", "value": f"={{{{ {CFG}.studio_id }}}}", "type": "number"},
        {"id": "p6", "name": "channel", "value": f"={{{{ {IN}.channel }}}}", "type": "string"},
        {"id": "p7", "name": "external_id", "value": f"={{{{ {IN}.external_id }}}}", "type": "string"},
    ]}, "options": {}})
connect("Notify Owner: Paused Lead", "Return: Paused")

# ---------------------------------------------------------------------------
# 3. Real availability
# ---------------------------------------------------------------------------
# This node deliberately has NO onError override. It used to carry
# `continueRegularOutput` + `alwaysOutputData`, which meant a dead Google
# credential produced an EMPTY event list instead of an error — and because
# availability is computed as "open hours minus the calendar", an empty list
# reads as "nothing is booked" and the agent offers the entire 08:00-21:00
# grid as verified availability. That is exactly what happened between
# 2026-08-22 and 2026-09-05: the refresh token expired (the OAuth app was in
# `Testing`, which caps refresh tokens at 7 days), the read failed silently,
# the agent invented slots, and only the calendar WRITE failed loudly.
# Failing outright dead-letters the message and alerts the owner instead.
node("Get Schedule", "n8n-nodes-base.googleCalendar", 1.3, [860, 200], {
    "operation": "getAll",
    "calendar": {"__rl": True, "value": f"={{{{ {CFG}.gcal_schedule_id }}}}", "mode": "id"},
    "returnAll": True,
    "options": {
        # Without timeMin the node returned past classes, which the agent then
        # offered to leads. Bounded to [now, +14d] in the studio's timezone.
        "timeMin": f"={{{{ $now.setZone({CFG}.timezone).toISO() }}}}",
        "timeMax": f"={{{{ $now.setZone({CFG}.timezone).plus({{ days: 14 }}).toISO() }}}}",
        "recurringEventHandling": "expand",
    }},
     credentials={"googleCalendarOAuth2Api": {"id": "GOOGLE_CAL_CRED_ID",
                                              "name": "Google Calendar account"}},
     retryOnFail=True)
connect("AI Paused?", "Get Schedule", out=1)

node("Format Schedule", "n8n-nodes-base.code", 2, [1080, 200], {"jsCode": r"""
// Availability is COMPUTED from the studio's open hours minus whatever is on the
// calendar. It used to be READ from pre-created class events, which meant an empty
// calendar produced zero offerable slots and the agent could only stall.
//
// A slot is offerable when it (a) sits inside open hours, (b) starts at least
// LEAD_MIN from now, (c) is not overlapped by a non-booking event, and (d) still
// has seats left after counting overlapping 'Trial:' bookings.
const cfg  = $('Resolve Studio').first().json;
const ZONE = cfg.timezone || 'UTC';
const CAP  = Number(cfg.class_capacity) || 8;
const DUR  = Number(cfg.booking_duration_minutes) || 50;

// Per-studio override, if someone later adds studios.prompt_vars.open_hours.
let pv = {};
try {
  pv = (typeof cfg.prompt_vars === 'string') ? JSON.parse(cfg.prompt_vars) : (cfg.prompt_vars || {});
} catch (e) { pv = {}; }
const oh = (pv && pv.open_hours) || {};

const OPEN_HOUR  = Number(oh.open_hour     ?? 8);    // 08:00 local
const CLOSE_HOUR = Number(oh.close_hour    ?? 21);   // 21:00 local — a slot must END by this
const STEP_MIN   = Number(oh.step_minutes  ?? 60);   // slots on the hour
const LEAD_MIN   = Number(oh.lead_minutes  ?? 120);  // earliest bookable = now + 2h
const HORIZON    = Number(oh.horizon_days  ?? 7);

const now      = DateTime.now().setZone(ZONE);
const earliest = now.plus({ minutes: LEAD_MIN });

// ---- classify what is already on the calendar ---------------------------
const blocks = [];   // anything not a booking: blocks the slot outright
const trials = [];   // our own 'Trial:' bookings: consume one seat each

for (const it of $input.all()) {
  const j = it.json || {};
  if (String(j.status || '').toLowerCase() === 'cancelled') continue;   // was never filtered
  const st = j.start || {}, en = j.end || {};
  let s, e;
  if (st.dateTime) {
    s = DateTime.fromISO(st.dateTime, { zone: ZONE });
    e = en.dateTime ? DateTime.fromISO(en.dateTime, { zone: ZONE }) : s.plus({ minutes: DUR });
  } else if (st.date) {
    // All-day event blocks the whole LOCAL day. Parsing this as a bare Date made it
    // UTC midnight, which lands in the previous evening in western timezones and
    // invented a phantom block.
    s = DateTime.fromISO(st.date, { zone: ZONE }).startOf('day');
    e = en.date ? DateTime.fromISO(en.date, { zone: ZONE }).startOf('day') : s.plus({ days: 1 });
  } else continue;
  if (!s.isValid || !e.isValid) continue;
  const rec = { s, e, summary: String(j.summary || '').trim() };
  if (/^trial\b/i.test(rec.summary)) trials.push(rec); else blocks.push(rec);
}

const overlaps = (aS, aE, bS, bE) => aS < bE && bS < aE;

// ---- generate candidate slots and subtract ------------------------------
const lines = [];
let slotCount = 0;

for (let d = 0; d <= HORIZON; d++) {
  const day   = now.plus({ days: d }).startOf('day');
  const open  = day.set({ hour: OPEN_HOUR,  minute: 0, second: 0, millisecond: 0 });
  const close = day.set({ hour: CLOSE_HOUR, minute: 0, second: 0, millisecond: 0 });

  const parts = [];
  for (let t = open; t.plus({ minutes: DUR }) <= close; t = t.plus({ minutes: STEP_MIN })) {
    const tEnd = t.plus({ minutes: DUR });
    if (t < earliest) continue;
    if (blocks.some((b) => overlaps(t, tEnd, b.s, b.e))) continue;
    const taken = trials.filter((b) => overlaps(t, tEnd, b.s, b.e)).length;
    const left  = CAP - taken;
    if (left <= 0) continue;
    const label = t.minute === 0 ? t.toFormat('h a') : t.toFormat('h:mm a');
    parts.push(left < CAP ? `${label} (${left} left)` : label);
    slotCount++;
  }
  if (parts.length) lines.push(`- ${day.toFormat('cccc dd LLL')}: ${parts.join(', ')}`);
}

return [{ json: {
  scheduleText: lines.length
    ? lines.join('\n')
    : `(no open slots in the next ${HORIZON} days)`,
  classCount: slotCount,
  slotMinutes: DUR,
} }];
"""})
connect("Get Schedule", "Format Schedule")

# ---------------------------------------------------------------------------
# 4. Prompt assembly (was hardcoded literals in the system message)
# ---------------------------------------------------------------------------
node("Build Agent Prompt", "n8n-nodes-base.code", 2, [1300, 200], {"jsCode": r"""
// Everything the persona says about itself now comes from studios.prompt_vars.
// Previously the studio name, city, offer and class list were string literals
// inside the system message, so a second studio meant a second workflow.
const cfg = $('Resolve Studio').first().json;
const inb = $('When Called').first().json;
const v   = (typeof cfg.prompt_vars === 'string')
              ? JSON.parse(cfg.prompt_vars) : (cfg.prompt_vars || {});

const ZONE = cfg.timezone || 'UTC';
const tzLabel = v.timezone_label || ZONE;
const nowLocal = DateTime.now().setZone(ZONE).toFormat('cccc yyyy-MM-dd HH:mm');

const systemMessage = [
`You are ${v.persona_name || 'the studio host'}, ${v.persona_role || 'the owner'} of ${v.studio_name || cfg.studio_name}, a ${v.studio_type || 'fitness studio'} in ${v.city || v.location || ''}. You personally reply to inbound leads who message the studio — ${v.voice || 'warm, casual and human'}.`,
``,
`Studio facts you can use:`,
`- Classes: ${v.classes || 'ask which one they are interested in'} (small groups, max ${cfg.class_capacity}).`,
`- Intro offer: ${v.offer || cfg.offers || ''}.`,
`- Location: ${v.location || ''}.${v.amenities ? ' ' + v.amenities + '.' : ''}`,
`- Studio timezone: ${tzLabel}.`,
``,
`Availability rules — the calendar is the single source of truth:`,
`- The user message lists the studio's genuinely OPEN times, already checked against the calendar. ONLY offer or confirm a time from that list.`,
`- NEVER invent a day or time, and never offer a time that is not in that list. Times not listed are unavailable — do not explain why, just offer the nearest listed alternative.`,
`- Sessions are ${cfg.booking_duration_minutes} minutes. Offer two or three specific times rather than the whole list, and let the lead pick.`,
`- '(N left)' means only N seats remain at that time. A time with no marker is wide open.`,
``,
`How you talk:`,
`- Sound like a real person, never a template. No 'Hi [NAME], thanks for your interest!'.`,
`- Keep it short — a line or two. Ask exactly ONE question that moves toward booking a class.`,
`- Use the conversation history; adapt to what the lead actually says. Don't repeat yourself.`,
`- Goal: get them booked into a specific class. Gently keep steering toward a day and time.`,
`- If they give a day/time that exists on the schedule and has seats, confirm the TIME back and ask which class they want. The booking is not made until they answer that.`,
`- NEVER say a lead is booked — no 'you're booked', 'you're in', 'I've got you down', 'you're set' — unless you are setting intent to 'booking' in this same response. Saying it earlier is a lie: nothing has been written to the calendar yet.`,
`- ESCALATE (don't answer) medical/injury questions or price negotiation.`,
``,
`You MUST respond with ONLY a JSON object — no prose, no code fences:`,
`{`,
`  "reply": "<message to send the lead>",`,
`  "intent": "continue" | "booking" | "escalate",`,
`  "booking": { "class_type": "...", "start_time": "YYYY-MM-DDTHH:MM:SS", "end_time": "YYYY-MM-DDTHH:MM:SS" } | null,`,
`  "escalate": { "reason": "..." } | null`,
`}`,
`Set intent to 'booking' ONLY when the lead has explicitly agreed BOTH (a) a specific date and time from the open-times list AND (b) a specific class, named by them. If either is still unsettled, intent stays 'continue'. NEVER choose the class for them and NEVER fall back to a default — class_type must be a class the lead actually named, because setting intent to 'booking' writes a real event to a real calendar and consumes a seat. (Booking a defaulted class on the turn where you also ask which class they want is what produced two events for one lead on 2026-09-05.) Duration is ${cfg.booking_duration_minutes} minutes. Output booking start_time/end_time as the studio's LOCAL wall-clock time in the format YYYY-MM-DDTHH:MM:SS with NO 'Z' and NO timezone offset. Always resolve dates against the current date and time given at the top of the user's message, output a FUTURE date in the correct current year, and NEVER use a past year. Set intent to 'escalate' for medical/injury or price-negotiation, include escalate.reason, and put a brief friendly holding message in reply. Otherwise intent is 'continue' with booking and escalate set to null.`,
].join('\n');

const userMessage = [
`Current date and time: ${nowLocal} (${tzLabel}). Resolve any relative or unspecified dates against this, and NEVER schedule a booking in the past.`,
``,
`The studio's REAL open times, already checked against the calendar — only ever offer or confirm a time from THIS list:`,
$('Format Schedule').first().json.scheduleText,
``,
`Lead's message: ${inb.message}`,
].join('\n');

return [{ json: { systemMessage, userMessage } }];
"""})
connect("Format Schedule", "Build Agent Prompt")

# ---------------------------------------------------------------------------
# 5. The agent
# ---------------------------------------------------------------------------
node("AI Agent", "@n8n/n8n-nodes-langchain.agent", 3.1, [1520, 200], {
    "promptType": "define",
    "text": "={{ $json.userMessage }}",
    "options": {"systemMessage": "={{ $json.systemMessage }}"},
}, retryOnFail=True, maxTries=3, waitBetweenTries=2000)
connect("Build Agent Prompt", "AI Agent")

node("OpenAI Chat Model", "@n8n/n8n-nodes-langchain.lmChatOpenAi", 1.3, [1460, 400], {
    "model": {"__rl": True, "value": "gpt-5.4-mini", "mode": "list",
              "cachedResultName": "gpt-5.4-mini"},
    "builtInTools": {}, "options": {}},
     credentials={"openAiApi": {"id": "OPENAI_CRED_ID", "name": "OpenAI account"}})
connect("OpenAI Chat Model", "AI Agent", kind="ai_languageModel")

node("Postgres Chat Memory", "@n8n/n8n-nodes-langchain.memoryPostgresChat", 1.3, [1620, 400], {
    "sessionIdType": "customKey",
    "sessionKey": f"={{{{ {IN}.channel + ':' + {IN}.external_id }}}}"},
     credentials={"postgres": {"id": "POSTGRES_CRED_ID", "name": "Postgres account"}})
connect("Postgres Chat Memory", "AI Agent", kind="ai_memory")

node("Parse Agent JSON", "n8n-nodes-base.code", 2, [1740, 200], {
    "mode": "runOnceForEachItem",
    "jsCode": r"""
const raw = $json.output ?? $json.text ?? '';
let data;
try {
  const m = String(raw).match(/\{[\s\S]*\}/);
  data = JSON.parse(m ? m[0] : raw);
} catch (e) {
  data = { reply: String(raw), intent: 'continue' };
}

// Timezone comes from studio config; it used to be a hardcoded ZONE constant.
const ZONE = $('Resolve Studio').first().json.timezone || 'UTC';
const toZoned = (s) => {
  try {
    if (typeof s !== 'string' || !s) return s;
    const naive = s.replace(/(?:Z|[+-]\d{2}:?\d{2})$/, '');
    const dt = DateTime.fromISO(naive, { zone: ZONE });
    return dt.isValid ? dt.toISO() : naive;
  } catch (e) { return s; }
};

let booking = data.booking ?? null;
if (booking && typeof booking === 'object') {
  booking = { ...booking, start_time: toZoned(booking.start_time), end_time: toZoned(booking.end_time) };
}
return { json: {
  reply: data.reply ?? '',
  intent: data.intent ?? 'continue',
  booking,
  escalate: data.escalate ?? null,
} };
"""})
connect("AI Agent", "Parse Agent JSON")

node("Route by Intent", "n8n-nodes-base.switch", 3.4, [1960, 200], {
    "rules": {"values": [
        {"conditions": {"options": {"caseSensitive": True, "leftValue": "",
                                    "typeValidation": "strict", "version": 3},
                        "conditions": [{"id": "r-booking", "leftValue": "={{ $json.intent }}",
                                        "rightValue": "booking",
                                        "operator": {"type": "string", "operation": "equals"}}],
                        "combinator": "and"},
         "renameOutput": True, "outputKey": "booking"},
        {"conditions": {"options": {"caseSensitive": True, "leftValue": "",
                                    "typeValidation": "strict", "version": 3},
                        "conditions": [{"id": "r-escalate", "leftValue": "={{ $json.intent }}",
                                        "rightValue": "escalate",
                                        "operator": {"type": "string", "operation": "equals"}}],
                        "combinator": "and"},
         "renameOutput": True, "outputKey": "escalate"},
    ]}, "options": {"fallbackOutput": "extra"}})
connect("Parse Agent JSON", "Route by Intent")

# ---------------------------------------------------------------------------
# 6. Booking — the calendar write is now a gate, not a side note
# ---------------------------------------------------------------------------
# Previously this node had onError: continueRegularOutput, so a failed calendar
# write still inserted a 'confirmed' booking, marked the lead booked, told the
# lead they were booked and pinged the owner — with no event on the calendar.
# continueErrorOutput splits the failure onto its own branch instead.
node("Create Calendar Event", "n8n-nodes-base.googleCalendar", 1.3, [2180, 60], {
    "calendar": {"__rl": True, "value": f"={{{{ {CFG}.gcal_booking_id }}}}", "mode": "id"},
    "start": f"={{{{ {AGENT}.booking.start_time }}}}",
    "end": f"={{{{ {AGENT}.booking.end_time }}}}",
    "additionalFields": {
        "summary": f"={{{{ 'Trial: ' + ({AGENT}.booking.class_type || 'Class') }}}}",
        "description": f"=Booked via AI for lead {{{{ {IN}.external_id }}}} ({{{{ {IN}.channel }}}})",
    }},
     credentials={"googleCalendarOAuth2Api": {"id": "GOOGLE_CAL_CRED_ID",
                                              "name": "Google Calendar account"}},
     onError="continueErrorOutput", retryOnFail=True)
# --- duplicate guard -------------------------------------------------------
# The prompt rules above are the primary defence; this is the structural one.
# On 2026-09-05 the agent set intent 'booking' with a DEFAULTED class, wrote the
# event, then asked which class the lead wanted and booked again 30 seconds
# later — two calendar events and two rows for one lead in one slot, two seats
# consumed. Nothing between `Route by Intent` and the calendar write ever asked
# whether this lead already had that slot. Now it does.
pg("Existing Booking?", [2020, -60], """SELECT id
FROM bookings
WHERE lead_id = $1 AND start_time = $2 AND status = 'confirmed'
LIMIT 1;""",
   replacement=("={{ [$('Upsert Lead').first().json.id, "
                f"{AGENT}.booking.start_time] }}}}"),
   alwaysOutputData=True, onError="continueRegularOutput", retryOnFail=True)
connect("Route by Intent", "Existing Booking?", out=0)

node("Already Booked?", "n8n-nodes-base.if", 2.2, [2120, -60], {
    "conditions": {"options": {"version": 2, "leftValue": "", "caseSensitive": True,
                               "typeValidation": "loose"},
                   "conditions": [{"id": "dupe-slot",
                                   "leftValue": "={{ $json.id }}",
                                   "rightValue": "",
                                   "operator": {"type": "string", "operation": "notEmpty",
                                                "singleValue": True}}],
                   "combinator": "and"},
    "options": {}})
connect("Existing Booking?", "Already Booked?")
# Already booked: send the agent's reply, write nothing. `Continue (no action)`
# is already wired to Log Outbound, so the lead is never left in silence.
connect("Already Booked?", "Continue (no action)", out=0)
connect("Already Booked?", "Create Calendar Event", out=1)

node("Event Really Created?", "n8n-nodes-base.if", 2.2, [2400, -60], {
    "conditions": {"options": {"version": 2, "leftValue": "", "caseSensitive": True,
                               "typeValidation": "loose"},
                   "conditions": [{"id": "has-event-id",
                                   "leftValue": "={{ $json.id }}",
                                   "rightValue": "",
                                   "operator": {"type": "string", "operation": "notEmpty",
                                                "singleValue": True}}],
                   "combinator": "and"},
    "options": {}})
connect("Create Calendar Event", "Event Really Created?", out=0)

# TODO(booking lock): a free/busy re-check plus a row lock belongs here — two
# leads confirming the same last seat in the same instant can both succeed.
# Out of scope for Phase 2a (tracked for the lifecycle phase).
pg("Insert Booking", [2620, -160], """INSERT INTO bookings (lead_id, class_type, start_time, end_time, gcal_event_id, status, created_at)
VALUES ($1, $2, $3, $4, $5, 'confirmed', now());""",
   replacement=("={{ [$('Upsert Lead').first().json.id, "
                f"{AGENT}.booking.class_type, {AGENT}.booking.start_time, "
                f"{AGENT}.booking.end_time, $json.id] }}}}"),
   retryOnFail=True)
connect("Event Really Created?", "Insert Booking", out=0)

pg("Mark Lead Booked", [2840, -160], """UPDATE leads SET status = 'booked', updated_at = now() WHERE id = $1;""",
   replacement="={{ [$('Upsert Lead').first().json.id] }}",
   onError="continueRegularOutput", retryOnFail=True)
connect("Insert Booking", "Mark Lead Booked")

slack("Notify Owner - Booking", [3060, -160],
      f"={{{{ {CFG}.slack_owner_channel_id }}}}",
      (f"=:tada: New trial booked at {{{{ {CFG}.studio_name }}}}: "
       f"{{{{ {AGENT}.booking.class_type }}}} at {{{{ {AGENT}.booking.start_time }}}} "
       f"(lead {{{{ {IN}.external_id }}}} via {{{{ {IN}.channel }}}})"))
connect("Mark Lead Booked", "Notify Owner - Booking")

# --- calendar write failed: owner picks it up, lead gets an honest holding line
slack("Alert Owner - Booking Failed", [2620, 120],
      f"={{{{ {CFG}.slack_owner_channel_id }}}}",
      (f"=:warning: *Booking could NOT be placed on the calendar* — needs you.\n"
       f"Lead: {{{{ {IN}.external_id }}}} ({{{{ {IN}.channel }}}})\n"
       f"Wanted: {{{{ {AGENT}.booking && {AGENT}.booking.class_type }}}} at "
       f"{{{{ {AGENT}.booking && {AGENT}.booking.start_time }}}}\n"
       "No booking was recorded and the lead was NOT told they are booked."))
connect("Create Calendar Event", "Alert Owner - Booking Failed", out=1)
connect("Event Really Created?", "Alert Owner - Booking Failed", out=1)

node("Soft-fail Reply", "n8n-nodes-base.set", 3.4, [2840, 120], {
    "assignments": {"assignments": [
        # Blocking the booking must not leave the lead in silence — Route Reply
        # sits downstream of this whole chain.
        {"id": "sf1", "name": "final_reply", "type": "string",
         "value": "=Let me just double-check that slot with the studio and come straight back to you — give me a few minutes."},
        {"id": "sf2", "name": "booking_failed", "type": "boolean", "value": "={{ true }}"},
    ]}, "options": {}})
# A caught calendar failure is NOT an execution error, so the Error Handler
# never fires and nothing reaches `dead_letters`. Combined with
# `saveDataSuccessExecution: none` below, that left literally no trace: bookings
# were failing from ~2026-08-22 and the only signal was a Slack line nobody
# read. Persist it, so "has booking been failing?" is one SQL query.
_dl_payload = ("JSON.stringify({ lead_id: $('Upsert Lead').first().json.id, external_id: "
               + IN + ".external_id, channel: " + IN + ".channel, booking: "
               + AGENT + ".booking })")
pg("Log Booking Failure", [2730, 220],
   """INSERT INTO dead_letters (workflow_id, execution_id, reason, payload, error, created_at)
VALUES ($1, $2, 'booking_write_failed', $3::jsonb, $4, now());""",
   replacement=("={{ [$workflow.id, String($execution.id), " + _dl_payload
                + ", 'Calendar write failed or returned no event id'] }}"),
   onError="continueRegularOutput", retryOnFail=True, alwaysOutputData=True)
connect("Alert Owner - Booking Failed", "Log Booking Failure")
connect("Log Booking Failure", "Soft-fail Reply")

# ---------------------------------------------------------------------------
# 7. Escalation — now actually pauses automation
# ---------------------------------------------------------------------------
pg("Insert Escalation", [2180, 300], """INSERT INTO escalations (lead_id, reason, resolved, created_at)
VALUES ($1, $2, false, now());""",
   replacement=("={{ [$('Upsert Lead').first().json.id, "
                f"({AGENT}.escalate && {AGENT}.escalate.reason) || 'unspecified'] }}}}"),
   onError="continueRegularOutput", retryOnFail=True)
connect("Route by Intent", "Insert Escalation", out=1)

pg("Mark Lead Escalated", [2400, 300], """-- Escalation used to leave status = 'engaged', so Find Stale Leads picked the
-- lead straight back up and nudged someone who had just asked a medical or
-- pricing question. 'escalated' takes them out of automation.
UPDATE leads SET status = 'escalated', updated_at = now() WHERE id = $1;""",
   replacement="={{ [$('Upsert Lead').first().json.id] }}",
   onError="continueRegularOutput", retryOnFail=True)
connect("Insert Escalation", "Mark Lead Escalated")

slack("Notify Owner - Escalation", [2620, 300],
      f"={{{{ {CFG}.slack_owner_channel_id }}}}",
      (f"=:rotating_light: Lead needs you at {{{{ {CFG}.studio_name }}}} "
       f"({{{{ {IN}.external_id }}}} via {{{{ {IN}.channel }}}}): "
       f"{{{{ ({AGENT}.escalate && {AGENT}.escalate.reason) || 'unspecified' }}}}\n"
       "Automation is paused for this lead until you resolve it."))
connect("Mark Lead Escalated", "Notify Owner - Escalation")

node("Continue (no action)", "n8n-nodes-base.noOp", 1, [2180, 460], {})
connect("Route by Intent", "Continue (no action)", out=2)

# ---------------------------------------------------------------------------
# 8. Log + return the outbound contract
# ---------------------------------------------------------------------------
pg("Log Outbound", [3300, 120], """INSERT INTO messages (lead_id, direction, channel, body, created_at)
VALUES ($1, 'outbound', $2, $3, now());""",
   replacement=("={{ [$('Upsert Lead').first().json.id, "
                f"{IN}.channel, "
                f"($json.final_reply ?? {AGENT}.reply)] }}}}"),
   onError="continueRegularOutput", retryOnFail=True, alwaysOutputData=True)
for src in ["Notify Owner - Booking", "Soft-fail Reply",
            "Notify Owner - Escalation", "Continue (no action)"]:
    connect(src, "Log Outbound")

node("Return Result", "n8n-nodes-base.set", 3.4, [3520, 120], {
    "assignments": {"assignments": [
        {"id": "r1", "name": "reply", "type": "string",
         "value": f"={{{{ $('Soft-fail Reply').isExecuted ? $('Soft-fail Reply').first().json.final_reply : {AGENT}.reply }}}}"},
        {"id": "r2", "name": "intent", "type": "string", "value": f"={{{{ {AGENT}.intent }}}}"},
        {"id": "r3", "name": "send", "type": "boolean", "value": "={{ true }}"},
        {"id": "r4", "name": "lead_id", "type": "number",
         "value": "={{ $('Upsert Lead').first().json.id }}"},
        {"id": "r5", "name": "studio_id", "type": "number", "value": f"={{{{ {CFG}.studio_id }}}}"},
        {"id": "r6", "name": "channel", "type": "string", "value": f"={{{{ {IN}.channel }}}}"},
        {"id": "r7", "name": "external_id", "type": "string", "value": f"={{{{ {IN}.external_id }}}}"},
        {"id": "r8", "name": "booking_created", "type": "boolean",
         "value": "={{ $('Insert Booking').isExecuted }}"},
        {"id": "r9", "name": "credential_ref", "type": "string",
         "value": f"={{{{ {CFG}.credential_ref }}}}"},
    ]}, "options": {}})
connect("Log Outbound", "Return Result")

# ---------------------------------------------------------------------------
workflow = {
    # Pinned so `import:workflow` UPDATES this workflow instead of creating a
    # new one on every deploy (an import without an id mints a fresh workflow,
    # which leaves orphaned duplicates and a stale published version).
    "id": "fvHhAUDCbVN9ELb0",
    "name": "Lead Brain",
    # `import:workflow` writes these columns straight through and the DB
    # rejects NULL on both. A sub-workflow is never "active" (only triggers
    # are), and versionId just has to be a UUID n8n can bump from.
    "active": False,
    "versionId": "00000000-0000-4000-8000-00000000b001",
    "nodes": nodes,
    "connections": conns,
    "settings": {
        "executionOrder": "v1",
        "saveDataErrorExecution": "all",
        # Was 'all' on the main workflow, which overrode the compose-level
        # EXECUTIONS_DATA_SAVE_ON_SUCCESS=none and grew the n8n DB to 38 MB.
        "saveDataSuccessExecution": "none",
        "saveManualExecutions": True,
        "saveExecutionProgress": True,
        "callerPolicy": "workflowsFromSameOwner",
        "errorWorkflow": "4L4K72aSSwiBcnjX",
        # No workflow-level timezone: every time-sensitive value now derives
        # from studios.timezone at runtime instead.
    },
}


def main():
    scratch = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(scratch, exist_ok=True)
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(os.path.dirname(here))

    redacted = json.dumps(workflow, indent=2, ensure_ascii=False)
    with open(os.path.join(repo, "workflows", "lead-brain.json"), "w") as f:
        f.write(redacted + "\n")

    live = redacted
    for placeholder, real in CREDS.items():
        live = live.replace(placeholder, real)
    for placeholder in CREDS:
        assert placeholder not in live, f"{placeholder} not substituted"
    with open(os.path.join(scratch, "lead-brain.live.json"), "w") as f:
        f.write(live + "\n")

    for real in CREDS.values():
        assert real not in redacted, f"live credential {real} leaked into the redacted export"

    print(f"Lead Brain: {len(nodes)} nodes, {sum(len(v.get('main', [])) for v in conns.values())} connection groups")


if __name__ == "__main__":
    main()
