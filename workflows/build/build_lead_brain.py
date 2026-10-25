#!/usr/bin/env python3
"""Generate the `Lead Brain` workflow JSON.

Two outputs from one source of truth:
  workflows/lead-brain.json          redacted (credential ids -> placeholders), committed
  <scratch>/lead-brain.live.json     real credential ids, pushed/imported into n8n

Run:  python3 workflows/build/build_lead_brain.py <scratch-dir>

PILOT BUILD (2026-10-25): booking is by LINK into the studio's own booking
software. Google Calendar is gone — no schedule read, no calendar write, no
booking-failure branch. The AI recommends ONE real class from the studio's
timetable (studio_classes), quotes ONLY its prices (studio_offers), and when the
lead wants to book, the CODE attaches the right booking link. The AI never says
"you're booked": her booking software is the truth and shows live spots.

The model picks a class by SLOT ID ("S3") from the list it is shown. Decide
checks the id exists, so an invented class or time cannot reach a lead. The
model never types a URL; any it writes is stripped and the code appends the link.
"""
import json
import os
import sys

# --- credential placeholders -> live ids -------------------------------------
CREDS = {
    "POSTGRES_CRED_ID": "VVIxtEmT1GWsEZn9",
    "OPENAI_CRED_ID":   "2dmtKOJYL68BMOyG",
}
SEND_EMAIL_WF_ID = "PQWBdER3x291uWeU"     # Send Email sub-workflow (build_pilot.py)
ERROR_HANDLER_ID = "4L4K72aSSwiBcnjX"

# Shorthands used inside n8n expressions.
IN = "$('When Called').first().json"        # the inbound contract
CFG = "$('Resolve Studio').first().json"    # resolved studio config
CTX = "$('Build Context').first().json"     # timetable slots, prompt, readiness
DEC = "$('Decide').first().json"            # what the code decided to do

nodes = []
conns = {}


def node(name, typ, ver, pos, params, **kw):
    n = {"id": name.lower().replace(" ", "-").replace(":", "").replace("?", "").replace("&", "and"),
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


def code(name, pos, js, **kw):
    return node(name, "n8n-nodes-base.code", 2, pos, {"jsCode": js.strip()}, **kw)


def send_email(name, pos):
    """Call the Send Email sub-workflow with the current item ({to, subject, ...}).
    An alert that fails must NEVER stop the lead's reply: continue on error.
    The failure still reaches the Error Handler via Send Email's errorWorkflow."""
    return node(name, "n8n-nodes-base.executeWorkflow", 1.2, pos, {
        "workflowId": {"__rl": True, "mode": "id", "value": SEND_EMAIL_WF_ID},
        "options": {"waitForSubWorkflow": True},
    }, onError="continueRegularOutput", alwaysOutputData=True)


def if_true(name, pos, expr, cid):
    return node(name, "n8n-nodes-base.if", 2.2, pos, {
        "conditions": {"options": {"version": 2, "leftValue": "", "caseSensitive": True,
                                   "typeValidation": "loose"},
                       "conditions": [{"id": cid, "leftValue": expr, "rightValue": "true",
                                       "operator": {"type": "boolean", "operation": "true",
                                                    "singleValue": True}}],
                       "combinator": "and"},
        "options": {}})


# A readable name for a lead in an owner email. Instagram gives no name, only an
# id the owner can't use — so point her at the conversation instead.
LEAD_LABEL_JS = r"""
const leadLabel = (inb) => inb.name ? String(inb.name)
  : (inb.channel === 'instagram' ? 'An Instagram lead' : `A ${inb.channel} lead`);
"""

# ---------------------------------------------------------------------------
# 1. Entry + studio resolution (unchanged in spirit since Phase 2a)
# ---------------------------------------------------------------------------
node("When Called", "n8n-nodes-base.executeWorkflowTrigger", 1.1, [-460, 300],
     {"inputSource": "passthrough"})

pg("Resolve Studio", [-240, 300], """-- Route the message to its studio. `account_ref` is the account the lead
-- messaged (IG entry.id / Slack channel id), NOT the lead's own id.
--
-- LEFT JOINs from a one-row source so this ALWAYS returns exactly one row:
-- an unmatched account comes back with studio_found = false rather than an
-- empty result set (zero rows skipped every downstream node, including the
-- dead-letter branch built to catch exactly this case).
SELECT (s.id IS NOT NULL) AS studio_found,
       s.id  AS studio_id,
       s.name AS studio_name,
       s.timezone,
       s.prompt_vars,
       s.booking_url,
       s.owner_name,
       s.owner_alert_email,
       s.links_ok,
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

# Tests an explicit boolean: n8n's `exists` operator treats SQL NULL as present.
if_true("Studio Found?", [-20, 300], "={{ $json.studio_found }}", "has-studio")
connect("Resolve Studio", "Studio Found?")

# --- unknown account: dead-letter, tell the founders, stop. Never guess a studio.
pg("Dead Letter: Unknown Account", [200, 520], """INSERT INTO dead_letters (workflow_id, execution_id, reason, payload, error)
VALUES ($1, $2, 'unknown_account', $3::jsonb, $4);""",
   replacement=("={{ [$workflow.id, $execution.id, "
                f"JSON.stringify({IN}), "
                f"'No active channel_accounts row for channel=' + {IN}.channel + "
                f"' account_ref=' + {IN}.account_ref] }}}}"),
   onError="continueRegularOutput", retryOnFail=True, alwaysOutputData=True)
connect("Studio Found?", "Dead Letter: Unknown Account", out=1)

code("Email: Unknown Account", [420, 520], r"""
const inb = $('When Called').first().json;
return [{ json: {
  to: 'founders',
  subject: 'Unrecognised account — a message was dropped',
  heading: 'A message arrived on an account no studio is connected to.',
  lines: [
    `Channel: ${inb.channel}`,
    `Account it was sent to: ${inb.account_ref}`,
    `Message: "${String(inb.message || '').slice(0, 300)}"`,
    'Nothing was sent back and no studio was guessed. It is saved in dead_letters. ' +
    'If this account belongs to a studio, add a channel_accounts row for it.',
  ],
} }];
""")
connect("Dead Letter: Unknown Account", "Email: Unknown Account")
send_email("Send: Unknown Account", [640, 520])
connect("Email: Unknown Account", "Send: Unknown Account")

# Must RETURN an item: Execute Sub-workflow retries a sub-execution that yields
# nothing (that once produced three dead letters and three alerts per message).
node("Return: Unknown Account", "n8n-nodes-base.set", 3.4, [860, 520], {
    "assignments": {"assignments": [
        {"id": "u1", "name": "reply", "type": "string", "value": ""},
        {"id": "u2", "name": "intent", "type": "string", "value": "unknown_account"},
        {"id": "u3", "name": "send", "type": "boolean", "value": "={{ false }}"},
        {"id": "u4", "name": "channel", "type": "string", "value": f"={{{{ {IN}.channel }}}}"},
        {"id": "u5", "name": "external_id", "type": "string", "value": f"={{{{ {IN}.external_id }}}}"},
    ]}, "options": {}})
connect("Send: Unknown Account", "Return: Unknown Account")

# ---------------------------------------------------------------------------
# 2. Lead upsert with lifecycle rules, inbound log, pause check
# ---------------------------------------------------------------------------
pg("Upsert Lead", [200, 260], """-- Idempotent per (channel, external_id). The status CASE is the lifecycle:
--   cold/new  -> engaged   (a returning lead re-enters the funnel)
--   escalated -> escalated (stays with the human until they resolve it)
--   booked / link_sent     (kept: the conversation continues, the state stands)
-- and nothing moves at all while ai_paused is set.
INSERT INTO leads (external_id, channel, name, studio_id, status, created_at, updated_at)
VALUES ($1, $2, $3, $4, 'engaged', now(), now())
ON CONFLICT (channel, external_id) DO UPDATE SET
    name       = COALESCE(EXCLUDED.name, leads.name),
    status     = CASE
                   WHEN leads.ai_paused                THEN leads.status
                   WHEN leads.status IN ('cold','new') THEN 'engaged'
                   ELSE leads.status
                 END,
    updated_at = now()
RETURNING id, status, ai_paused;""",
   replacement=("={{ [" + f"{IN}.external_id, {IN}.channel, {IN}.name, {CFG}.studio_id" + "] }}"),
   retryOnFail=True)
connect("Studio Found?", "Upsert Lead", out=0)

pg("Log Inbound", [420, 260], """INSERT INTO messages (lead_id, direction, channel, body, author, created_at)
VALUES ($1, 'inbound', $2, $3, 'lead', now());""",
   replacement="={{ [$json.id, " + f"{IN}.channel, {IN}.message] }}}}",
   onError="continueRegularOutput", retryOnFail=True, alwaysOutputData=True)
connect("Upsert Lead", "Log Inbound")

if_true("AI Paused?", [640, 260], "={{ $('Upsert Lead').first().json.ai_paused }}", "paused")
connect("Log Inbound", "AI Paused?")

# --- a human has this lead: log it, tell the owner, send nothing ------------
code("Email: Paused Lead", [860, 460], LEAD_LABEL_JS + r"""
const inb = $('When Called').first().json;
const cfg = $('Resolve Studio').first().json;
return [{ json: {
  to: cfg.owner_alert_email || 'founders',
  subject: `${leadLabel(inb)} replied — the AI is paused for her`,
  heading: `${leadLabel(inb)} sent a new message. The AI did not answer, because you took this conversation over.`,
  lines: [`Her message: "${String(inb.message || '').slice(0, 500)}"`,
          `Reply to her in your ${inb.channel === 'instagram' ? 'Instagram DMs' : inb.channel}.`],
} }];
""")
connect("AI Paused?", "Email: Paused Lead", out=0)
send_email("Send: Paused Lead", [1080, 460])
connect("Email: Paused Lead", "Send: Paused Lead")

node("Return: Paused", "n8n-nodes-base.set", 3.4, [1300, 460], {
    "assignments": {"assignments": [
        {"id": "p1", "name": "reply", "value": "", "type": "string"},
        {"id": "p2", "name": "intent", "value": "paused", "type": "string"},
        {"id": "p3", "name": "send", "value": "={{ false }}", "type": "boolean"},
        {"id": "p4", "name": "lead_id", "value": "={{ $('Upsert Lead').first().json.id }}", "type": "number"},
        {"id": "p5", "name": "studio_id", "value": f"={{{{ {CFG}.studio_id }}}}", "type": "number"},
        {"id": "p6", "name": "channel", "value": f"={{{{ {IN}.channel }}}}", "type": "string"},
        {"id": "p7", "name": "external_id", "value": f"={{{{ {IN}.external_id }}}}", "type": "string"},
    ]}, "options": {}})
connect("Send: Paused Lead", "Return: Paused")

# ---------------------------------------------------------------------------
# 3. Studio data — read fresh on EVERY message, so an edit in the database
#    (Beekeeper, as studio_editor) is live on the very next reply.
# ---------------------------------------------------------------------------
pg("Load Studio Data", [860, 200], """-- Always exactly one row (scalar subqueries), even for a brand-new studio.
SELECT
  (SELECT json_agg(json_build_object('id', c.id, 'name', c.name, 'weekday', c.weekday,
                                     'time', to_char(c.start_local, 'HH24:MI'),
                                     'minutes', c.duration_minutes, 'level', c.level,
                                     'url', c.booking_url)
                   ORDER BY c.weekday, c.start_local)
     FROM studio_classes c WHERE c.studio_id = $1 AND c.active)                AS classes,
  (SELECT json_agg(json_build_object('id', o.id, 'kind', o.kind, 'name', o.name, 'price', o.price,
                                     'currency', o.currency, 'details', o.details, 'url', o.url,
                                     'for_new_clients', o.for_new_clients)
                   ORDER BY o.sort_order, o.id)
     FROM studio_offers o WHERE o.studio_id = $1 AND o.active)                  AS offers,
  -- The real conversation (staff replies included once auto-pause logs them),
  -- replacing n8n's separate chat memory. Newest 20, returned oldest-first.
  (SELECT json_agg(t ORDER BY t.created_at)
     FROM (SELECT direction, author, body, created_at FROM messages
            WHERE lead_id = $2 ORDER BY created_at DESC LIMIT 20) t)            AS thread,
  (SELECT count(*) FROM messages WHERE lead_id = $2 AND direction = 'outbound') AS outbound_count;""",
   replacement=f"={{{{ [{CFG}.studio_id, $('Upsert Lead').first().json.id] }}}}",
   alwaysOutputData=True, retryOnFail=True)
connect("AI Paused?", "Load Studio Data", out=1)

code("Build Context", [1080, 200], r"""
// Turns the studio's tables into (1) a numbered list of REAL upcoming classes,
// (2) the ONLY prices the AI may quote, (3) the prompt, (4) a readiness check.
const cfg  = $('Resolve Studio').first().json;
const inb  = $('When Called').first().json;
const data = $('Load Studio Data').first().json;
const v    = (typeof cfg.prompt_vars === 'string') ? JSON.parse(cfg.prompt_vars) : (cfg.prompt_vars || {});
const ZONE = cfg.timezone || 'UTC';
const now  = DateTime.now().setZone(ZONE);

const classes = Array.isArray(data.classes) ? data.classes : [];
const offers  = Array.isArray(data.offers)  ? data.offers  : [];
const thread  = Array.isArray(data.thread)  ? data.thread  : [];

// --- (1) next 7 days of real classes, as slot ids S1..Sn -----------------
// Weekly rows are LOCAL wall-clock times; Luxon applies the studio's zone, so a
// 6pm class stays 6pm across a clock change. Classes starting within 60 min are
// left out: nobody can buy a pass and get there in time.
const slots = [];
for (let d = 0; d < 7; d++) {
  const day = now.startOf('day').plus({ days: d });
  for (const c of classes) {
    if (Number(c.weekday) !== day.weekday) continue;
    const [h, m] = String(c.time).split(':').map(Number);
    const at = day.set({ hour: h, minute: m });
    if (at < now.plus({ minutes: 60 })) continue;
    slots.push({ id: `S${slots.length + 1}`, class_id: c.id, name: c.name, level: c.level || '',
                 minutes: c.minutes, url: c.url || null, iso: at.toISO(),
                 label: `${at.toFormat('ccc d LLL, h:mm a')} — ${c.name}` });
  }
}

// --- (2) the only prices the AI may quote ----------------------------------
const money = (o) => o.price == null ? 'price on request (ask the studio)'
  : `${o.currency === 'USD' || !o.currency ? '$' : o.currency + ' '}${Number(o.price) % 1 ? Number(o.price).toFixed(2) : Number(o.price).toFixed(0)}`;
const intro = offers.find((o) => o.kind === 'intro' && o.for_new_clients) || offers.find((o) => o.kind === 'intro') || null;

// --- (4) readiness: never improvise for a half-set-up studio --------------
const missing = [];
if (!classes.length) missing.push('no active classes in studio_classes');
if (!intro) missing.push('no intro offer in studio_offers');
if (!(intro && intro.url) && !cfg.booking_url) missing.push('no booking link (intro offer url or studios.booking_url)');
const ready = missing.length === 0;

// --- (3) the prompt ---------------------------------------------------------
const voice = (v.voice && typeof v.voice === 'object') ? v.voice : { register: v.voice || 'warm, casual and human' };
const list = (a) => (Array.isArray(a) ? a : []).filter(Boolean);
const faq = (v.faq && typeof v.faq === 'object') ? Object.entries(v.faq).map(([k, a]) => `- ${k.replace(/_/g, ' ')}: ${a}`) : [];
const owner = cfg.owner_name || 'the owner';
const studio = v.how_we_refer || v.studio_name || cfg.studio_name;

const systemMessage = [
`You are ${v.persona_name || 'the studio host'}, and you answer Instagram DMs for ${v.studio_name || cfg.studio_name}${v.type ? ', a ' + v.type : ''}${v.city ? ' in ' + v.city : ''}. ${owner} owns the studio.`,
``,
`YOUR VOICE: you write the way ${owner} texts a new client. It must read like a person typed it on her phone between classes, never like a company or an assistant.`,
`- ${owner}'s register: ${voice.register || 'warm, direct, short sentences, contractions'}.`,
Number(data.outbound_count || 0) === 0
  ? `- This is your FIRST reply to her${voice.greeting ? ', so open the way ' + owner + ' does: ' + voice.greeting + ' (use her first name only if you actually know it)' : ''}.`
  : `- You are mid-conversation: NO greeting, no "Hey", no "Hi". Just carry on, like the next text in a thread.`,
`- Write times the way people text: "5:30pm", "9am", "Sat 9:15am". Never "5:30 PM" or "17:30".`,
`- Emoji: ${voice.emoji || 'rarely, and only if she used one first'}.`,
`- Mirror her: if she writes one line, you write one or two. Match her energy; never be more excited than she is.`,
`- Answer what she actually asked, first. If you recommend a class, give ONE concrete reason tied to what SHE said (new to Pilates, after work, nervous, wants something gentle), not a generic one.`,
`- Respect what she asked for. If she names a class type (reformer, mat), a day or a time of day, your recommendation MUST match it. Only suggest something else if nothing on the list matches, and say so.`,
`- Be specific: "Monday 5:30, Reformer Flow" beats "a great evening option". A real detail from the facts below beats any adjective. Never call a class "a solid fit", "a good spot" or "the best starting point": say WHY it suits her.`,
`- Punctuation like texting: periods, commas, the odd question mark. NO em dashes or en dashes. No semicolons. Never two exclamation marks.`,
`- Vary how you start. Don't open with "Yes", "Yep", "Great question", "Absolutely", "So" or by repeating her question back.`,
`- Never sound like a template or a salesperson. Banned: "great question", "I'd be happy to", "feel free", "don't hesitate", "reach out", "circle back", "perfect for you", "option", "journey", "let me know if you have any other questions"${list(voice.never_say).length ? ', ' + list(voice.never_say).map((s) => '"' + s + '"').join(', ') : ''}.`,
list(voice.examples).length ? `How ${owner} actually writes. Match this STYLE (length, rhythm, warmth), but never copy their prices, times or class names; those only come from the lists below:\n${list(voice.examples).map((e) => '  > ' + (typeof e === 'object' && e ? ((e.lead ? 'Lead: ' + e.lead + '\n    ' + owner + ': ' : '') + (e.reply || '')) : e)).join('\n')}` : '',
``,
`STUDIO FACTS:`,
v.address ? `- Address: ${v.address}` : '',
v.beginner_policy ? `- Beginners: ${v.beginner_policy}` : '',
...list(v.prerequisites).map((p) => `- First visit: ${p}`),
...faq,
``,
`PRICES — the ONLY prices you may ever quote. If something is not on this list, say ${owner} will confirm rather than guessing:`,
...(offers.length ? offers.map((o) => `- ${o.name}: ${money(o)}${o.details ? ' (' + o.details + ')' : ''}${o.kind === 'intro' ? '  ← the starting point for anyone new' : ''}`) : ['- (none listed — do not quote any price)']),
``,
`UPCOMING CLASSES (next 7 days, ${ZONE}) — the ONLY classes you may recommend, each with its slot id:`,
...(slots.length ? slots.map((s) => `- ${s.id}: ${s.label}${s.level ? ' (' + s.level + ')' : ''}`) : ['- (none — do not recommend any time; say ' + owner + ' will help)']),
``,
`HOW BOOKING WORKS — you never book anything:`,
`- Recommend ONE specific class from the list that fits what she said, by day, time and name.`,
`- You do NOT know live availability; the booking page shows the live spots. NEVER say she is booked, in, confirmed, reserved or that a spot is held.`,
`- Use intent "send_booking_link" ONLY once she has already said yes to a class or asked how/where to book. Then the system adds the booking link to your message itself: NEVER type a link, URL or web address.`,
`- If you are suggesting a class, asking which one suits her, or offering to send the link, that is intent "continue" and nothing gets attached. When you suggest a class, end by asking whether that TIME works for her ("does Monday 5:30 work for you?"), not whether she wants a link. Once she says yes, the link follows.`,
v.booking_steps ? `- In that message, tell her the next step in one short line: ${v.booking_steps}` : '',
`- If she says she has booked, bought the pass or signed up, use intent "lead_booked" and keep your reply to a short, warm line. Do NOT write arrival times, what to bring or other first-visit tips: the system adds those itself.`,
``,
`HAND TO ${owner.toUpperCase()} (intent "escalate", do NOT answer it yourself) for: ${list(v.escalate_on).length ? list(v.escalate_on).join(', ').replace(/_/g, ' ') : 'medical questions, injuries, pregnancy, price negotiation, refunds, complaints'}.`,
``,
`STYLE: at most ONE question per message, and only if it moves her toward a class. Don't repeat anything already said in the conversation, and never summarise what she just told you.`,
`If she asks directly whether you are a person or a bot, say honestly that you are the studio's AI assistant and offer to get ${owner}.`,
``,
`Respond with ONLY this JSON — no prose, no code fences:`,
`{"reply": "<your message>", "intent": "continue" | "send_booking_link" | "lead_booked" | "escalate", "slot": "<slot id like S3, or null>", "returning_client": <true only if she says she has been before or has a membership>, "escalate_reason": "<short reason, or null>"}`,
].filter((l) => l !== '').join('\n');

const who = (m) => m.direction === 'inbound' ? 'Lead' : (m.author === 'human' ? 'Studio staff' : 'You');
// The newest row is normally this very message (just logged); show it once, as "latest".
const past = (thread.length && thread[thread.length - 1].direction === 'inbound'
              && String(thread[thread.length - 1].body) === String(inb.message)) ? thread.slice(0, -1) : thread;
const history = past.map((m) => `${who(m)}: ${String(m.body || '').replace(/\s+/g, ' ').slice(0, 600)}`);

const userMessage = [
`Current date and time at the studio: ${now.toFormat('cccc d LLLL yyyy, h:mm a')} (${ZONE}).`,
``,
history.length ? `Conversation so far:\n${history.join('\n')}` : 'This is the first message from this lead.',
``,
`Her latest message: ${inb.message}`,
].join('\n');

return [{ json: {
  systemMessage, userMessage, ready, missing,
  slots, intro: intro ? { name: intro.name, url: intro.url || null } : null,
  first_message: Number(data.outbound_count || 0) === 0,
} }];
""")
connect("Load Studio Data", "Build Context")

# ---------------------------------------------------------------------------
# 4. The model. No memory node any more: Build Context hands it the real
#    conversation from `messages`, one source of truth.
# ---------------------------------------------------------------------------
node("AI Agent", "@n8n/n8n-nodes-langchain.agent", 3.1, [1300, 200], {
    "promptType": "define",
    "text": "={{ $json.userMessage }}",
    "options": {"systemMessage": "={{ $json.systemMessage }}"},
}, retryOnFail=True, maxTries=3, waitBetweenTries=2000)
connect("Build Context", "AI Agent")

node("OpenAI Chat Model", "@n8n/n8n-nodes-langchain.lmChatOpenAi", 1.3, [1240, 400], {
    "model": {"__rl": True, "value": "gpt-5.4-mini", "mode": "list",
              "cachedResultName": "gpt-5.4-mini"},
    "builtInTools": {}, "options": {}},
     credentials={"openAiApi": {"id": "OPENAI_CRED_ID", "name": "OpenAI account"}})
connect("OpenAI Chat Model", "AI Agent", kind="ai_languageModel")

# ---------------------------------------------------------------------------
# 5. Decide — the CODE decides; the model only suggests.
# ---------------------------------------------------------------------------
code("Decide", [1520, 200], LEAD_LABEL_JS + r"""
const crypto = require('crypto');
const cfg = $('Resolve Studio').first().json;
const inb = $('When Called').first().json;
const ctx = $('Build Context').first().json;
const v   = (typeof cfg.prompt_vars === 'string') ? JSON.parse(cfg.prompt_vars) : (cfg.prompt_vars || {});
const owner = cfg.owner_name || 'the owner';

// --- parse the model's JSON (tolerate code fences / stray prose) ----------
const raw = String($json.output ?? $json.text ?? '');
let ai = null;
try { const m = raw.match(/\{[\s\S]*\}/); ai = JSON.parse(m ? m[0] : raw); } catch (e) { ai = null; }

const holding = v.escalation_line || `Let me get ${owner} to come back to you on that properly.`;
const INTENTS = ['continue', 'send_booking_link', 'lead_booked', 'escalate'];
let intent = ai && INTENTS.includes(ai.intent) ? ai.intent : 'continue';
let reason = ai && ai.escalate_reason ? String(ai.escalate_reason) : null;

// The model must never type a link — strip anything that looks like one.
const stripUrls = (s) => String(s || '').replace(/\bhttps?:\/\/\S+/gi, '').replace(/\bwww\.\S+/gi, '').replace(/[ \t]{2,}/g, ' ').trim();
let reply = stripUrls(ai ? ai.reply : '');
// Em/en dashes are the strongest "an AI wrote this" tell. The prompt forbids
// them; any that slip through become ordinary texting punctuation. Only the
// model's text: the owner's own escalation line and the disclosure are hers.
reply = reply
  .replace(/^\s*(yes|yep|yeah|ok|okay)\s*[—–]\s*/i, (m, w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase() + ', ')
  .replace(/\s*[—–]\s*/g, ', ')
  .replace(/,\s*([,.!?])/g, '$1')
  .trim();

// Unparseable or empty output: don't send the model's raw text to a lead.
if (!ai || !reply) { intent = 'escalate'; reason = reason || 'the AI produced no usable reply'; }

// --- the studio isn't fully set up: never improvise ------------------------
if (!ctx.ready) { intent = 'escalate'; reason = 'studio setup incomplete: ' + ctx.missing.join('; '); }

// --- validate the class it picked -----------------------------------------
// Safety net: if the model ASKS whether to send the link, it hasn't been agreed
// yet — attaching it anyway contradicts its own question (seen live 2026-10-25).
// Catches "want me to send the link?" AND "want me to send it over?" (the
// second slipped past the first version of this check in the voice lab).
if (intent === 'send_booking_link'
    && /\b(want|would you like|shall|should|can|do you want)\b[^?]{0,60}\b(send|share|drop|pass)\b[^?]*\?/i.test(reply)) {
  intent = 'continue';
}

const slot = (ai && ai.slot) ? (ctx.slots || []).find((s) => s.id === String(ai.slot).trim().toUpperCase()) || null : null;
const returning = !!(ai && ai.returning_client === true);

// --- choose the link (code, not the model) --------------------------------
let link = null, kind = null, linkProblem = false;
if (intent === 'send_booking_link') {
  if (!returning && ctx.intro && ctx.intro.url) { link = ctx.intro.url; kind = 'new_client'; }
  else if (returning && slot && slot.url)       { link = slot.url;       kind = 'class'; }
  else if (cfg.booking_url)                     { link = cfg.booking_url; kind = 'main'; }
  if (!link || cfg.links_ok === false) {
    // No working link: a human has to send it. Honest holding line + alert.
    intent = 'escalate'; linkProblem = true;
    link = null; kind = null;   // nothing is sent, so nothing is recorded or coded
    reason = cfg.links_ok === false ? 'booking link marked broken by the daily check' : 'no booking link configured';
    reply = `${reply}\n\n${owner} will send you the booking link shortly.`.trim();
  }
}

// --- compose the final message --------------------------------------------
let final;
if (intent === 'send_booking_link')                     final = `${reply}\n${link}`;
else if (intent === 'lead_booked' && v.first_visit_tips)
  // Once only: skip if the model already wrote tips despite being told not to.
  final = /grip socks|minutes early|waiver/i.test(reply) ? reply : `${reply}\n\n${v.first_visit_tips}`;
else if (intent === 'escalate')                         final = linkProblem ? reply : holding;   // never let the model answer what it must hand over
else                                                    final = reply;
if (ctx.first_message && v.ai_disclosure) final = `${final}\n\n${v.ai_disclosure}`;

// --- the owner email for this outcome (built here, sent once downstream) ---
const who = leadLabel(inb);
const where = inb.channel === 'instagram' ? 'your Instagram DMs' : inb.channel;
const theirMsg = `Her message: "${String(inb.message || '').slice(0, 500)}"`;
let owner_email = null;
if (intent === 'send_booking_link') owner_email = {
  subject: `${who} is heading to book${slot ? ' — ' + slot.label : ''}`,
  heading: `${who} wants to book, and was sent your booking link.`,
  lines: [slot ? `Recommended class: ${slot.label}` : 'No specific class was picked yet.',
          `Link sent: ${link}`, theirMsg,
          'She books herself on your booking page. Look out for a new intro purchase or booking.'] };
if (intent === 'lead_booked') owner_email = {
  subject: `${who} says she's booked`,
  heading: `${who} says she has booked or bought the pass.`,
  lines: [theirMsg, 'Please check she shows up in your booking system.'] };
if (intent === 'escalate') owner_email = {
  subject: `${who} needs you — ${reason || 'a question for you'}`,
  heading: `${who} asked something the AI handed to you.`,
  lines: [`Why: ${reason || 'not specified'}`, theirMsg,
          `The AI sent her a holding reply and has stepped back for this lead. Reply to her in ${where}.`] };
if (owner_email) owner_email.to = (reason && reason.startsWith('studio setup incomplete'))
  ? `${cfg.owner_alert_email || 'founders'}, founders` : (cfg.owner_alert_email || 'founders');
// Voice-lab conversations (channel 'test') must not flood real inboxes:
// their owner emails go to Resend's test sink, which accepts and discards.
if (owner_email && inb.channel === 'test') owner_email.to = 'delivered@resend.dev';

return [{ json: {
  intent, reason, final_reply: final, ai_reply: reply,
  slot_id: slot ? slot.id : null, class_id: slot ? slot.class_id : null, slot_label: slot ? slot.label : null,
  link, link_kind: kind, short_code: link ? crypto.randomBytes(6).toString('base64url') : null,
  returning_client: returning, owner_email,
} }];
""")
connect("AI Agent", "Decide")

node("Route by Intent", "n8n-nodes-base.switch", 3.4, [1740, 200], {
    "rules": {"values": [
        {"conditions": {"options": {"caseSensitive": True, "leftValue": "",
                                    "typeValidation": "strict", "version": 3},
                        "conditions": [{"id": f"r-{k}", "leftValue": "={{ $json.intent }}",
                                        "rightValue": k,
                                        "operator": {"type": "string", "operation": "equals"}}],
                        "combinator": "and"},
         "renameOutput": True, "outputKey": k}
        for k in ("send_booking_link", "lead_booked", "escalate")
    ]}, "options": {"fallbackOutput": "extra"}})
connect("Decide", "Route by Intent")

# --- send_booking_link: record it; the lead is now `link_sent` -------------
pg("Record Link Sent", [1960, 0], """WITH link AS (
  INSERT INTO booking_links (lead_id, studio_id, class_id, kind, target_url, short_code)
  VALUES ($1, $2, $3, $4, $5, $6)
  RETURNING id
)
UPDATE leads SET status = CASE WHEN status IN ('booked', 'escalated') THEN status ELSE 'link_sent' END,
                 updated_at = now()
 WHERE id = $1
RETURNING (SELECT id FROM link) AS booking_link_id;""",
   replacement=("={{ [$('Upsert Lead').first().json.id, " + f"{CFG}.studio_id, "
                f"{DEC}.class_id, {DEC}.link_kind, {DEC}.link, {DEC}.short_code] }}}}"),
   retryOnFail=True)
connect("Route by Intent", "Record Link Sent", out=0)

# --- lead_booked: she says she booked (self-reported; the owner confirms) ---
pg("Record Lead Booked", [1960, 160], """-- One statement (a parameterised query can't hold two): mark the latest
-- link as booked-by-the-lead, then the lead.
WITH latest AS (
  UPDATE booking_links SET lead_said_booked_at = COALESCE(lead_said_booked_at, now())
   WHERE id = (SELECT id FROM booking_links WHERE lead_id = $1 ORDER BY sent_at DESC LIMIT 1)
  RETURNING id
)
UPDATE leads SET status = 'booked', updated_at = now()
 WHERE id = $1
RETURNING (SELECT id FROM latest) AS booking_link_id;""",
   replacement="={{ [$('Upsert Lead').first().json.id] }}",
   retryOnFail=True, alwaysOutputData=True)
connect("Route by Intent", "Record Lead Booked", out=1)

# --- escalate: a human takes it; automation stops nudging this lead --------
pg("Insert Escalation", [1960, 320], """WITH e AS (
  INSERT INTO escalations (lead_id, reason, resolved, created_at)
  VALUES ($1, $2, false, now())
  RETURNING id
)
UPDATE leads SET status = 'escalated', updated_at = now()
 WHERE id = $1
RETURNING (SELECT id FROM e) AS escalation_id;""",
   replacement=("={{ [$('Upsert Lead').first().json.id, " + f"{DEC}.reason || 'unspecified'] }}}}"),
   onError="continueRegularOutput", retryOnFail=True, alwaysOutputData=True)
connect("Route by Intent", "Insert Escalation", out=2)

# All three branches share one owner email (built in Decide), sent once.
code("Owner Email", [2180, 160], "return [{ json: $('Decide').first().json.owner_email }];")
for src in ("Record Link Sent", "Record Lead Booked", "Insert Escalation"):
    connect(src, "Owner Email")
send_email("Send Owner Email", [2400, 160])
connect("Owner Email", "Send Owner Email")

# ---------------------------------------------------------------------------
# 6. Log + return the outbound contract (unchanged fields; adapters untouched)
# ---------------------------------------------------------------------------
pg("Log Outbound", [2620, 300], """INSERT INTO messages (lead_id, direction, channel, body, author, created_at)
VALUES ($1, 'outbound', $2, $3, 'ai', now())
RETURNING id;""",
   replacement=("={{ [$('Upsert Lead').first().json.id, " + f"{IN}.channel, {DEC}.final_reply] }}}}"),
   onError="continueRegularOutput", retryOnFail=True, alwaysOutputData=True)
connect("Send Owner Email", "Log Outbound")
connect("Route by Intent", "Log Outbound", out=3)   # plain conversation: no email

node("Return Result", "n8n-nodes-base.set", 3.4, [2840, 300], {
    "assignments": {"assignments": [
        {"id": "r1", "name": "reply", "type": "string", "value": f"={{{{ {DEC}.final_reply }}}}"},
        {"id": "r2", "name": "intent", "type": "string", "value": f"={{{{ {DEC}.intent }}}}"},
        {"id": "r3", "name": "send", "type": "boolean", "value": f"={{{{ !!{DEC}.final_reply }}}}"},
        {"id": "r4", "name": "lead_id", "type": "number", "value": "={{ $('Upsert Lead').first().json.id }}"},
        {"id": "r5", "name": "studio_id", "type": "number", "value": f"={{{{ {CFG}.studio_id }}}}"},
        {"id": "r6", "name": "channel", "type": "string", "value": f"={{{{ {IN}.channel }}}}"},
        {"id": "r7", "name": "external_id", "type": "string", "value": f"={{{{ {IN}.external_id }}}}"},
        # Kept for the adapters' contract. Nothing is booked by the system any more.
        {"id": "r8", "name": "booking_created", "type": "boolean", "value": "={{ false }}"},
        {"id": "r9", "name": "credential_ref", "type": "string", "value": f"={{{{ {CFG}.credential_ref }}}}"},
        {"id": "r10", "name": "link_sent", "type": "boolean",
         "value": f"={{{{ {DEC}.intent === 'send_booking_link' }}}}"},
        # Lets the Instagram adapter store IG's message id against this row,
        # so the echo of OUR reply can be told apart from a staff reply (step 6).
        {"id": "r11", "name": "outbound_message_id", "type": "number",
         "value": "={{ $('Log Outbound').first().json.id }}"},
    ]}, "options": {}})
connect("Log Outbound", "Return Result")

# ---------------------------------------------------------------------------
workflow = {
    # Pinned so pushes/imports UPDATE this workflow instead of creating a new one.
    "id": "fvHhAUDCbVN9ELb0",
    "name": "Lead Brain",
    "active": False,     # imported inactive, then published (deploy.sh / push.py keep it live)
    "versionId": "00000000-0000-4000-8000-00000000b001",
    "nodes": nodes,
    "connections": conns,
    "settings": {
        "executionOrder": "v1",
        "saveDataErrorExecution": "all",
        "saveDataSuccessExecution": "none",
        "saveManualExecutions": True,
        "saveExecutionProgress": True,
        "callerPolicy": "workflowsFromSameOwner",
        "errorWorkflow": ERROR_HANDLER_ID,
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
    names = [n["name"] for n in nodes]
    assert len(names) == len(set(names)), "duplicate node names"
    for src, kinds in conns.items():
        assert src in names, f"connection from unknown node {src}"
        for outs in kinds.values():
            for out in outs:
                for c in out:
                    assert c["node"] in names, f"connection to unknown node {c['node']}"
    print(f"Lead Brain: {len(nodes)} nodes")


if __name__ == "__main__":
    main()
