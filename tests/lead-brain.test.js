// Lead Brain "Decide" step: what the lead receives and what the owner is told.
// (Rebuilt 2026-10-30: the earlier 51-check suite lived in /tmp and was lost.)
const { run, ok, crypto } = require('./harness');
const F = 'lead-brain.json';
const ENV = { OWNER_LINK_SECRET: 's'.repeat(40) };
const cfg = { studio_id: 2, owner_name: 'Jenna', owner_alert_email: 'owner@studio.com', booking_url: 'https://studio.example/schedule', links_ok: true,
  prompt_vars: { ai_disclosure: "— Mia, Reform's AI assistant", escalation_line: 'That one I want Jenna to answer properly. She’ll come back to you today.',
                 first_visit_tips: 'Come 10 minutes early and bring grip socks.' } };
const slots = [{ id: 'S1', class_id: 11, label: 'Sat 31 Oct, 9:15 AM \u2014 Mat Basics', url: null }, { id: 'S2', class_id: 12, label: 'Fri 6:30pm Sculpt 45', url: 'https://studio.example/class/12' }];
const ctx = { ready: true, missing: [], slots, intro: { name: '2-week intro pass', url: 'https://studio.example/intro' }, first_message: false };
const inb = { channel: 'instagram', message: 'how do I book?', name: null };
const decide = async (ai, { c = cfg, x = ctx, up = { id: 42 } } = {}) =>
  (await run(F, 'Decide', [{ output: JSON.stringify(ai) }], { env: ENV, nodes: { 'Resolve Studio': [c], 'Build Context': [x], 'When Called': [inb], 'Upsert Lead': [up] } }))[0];

module.exports = async () => {
  let d = await decide({ reply: 'Tap the link, grab the intro pass.', intent: 'send_booking_link', slot: 'S1' });
  ok('new lead -> intro link, sent as OUR tracked link', d.link === 'https://studio.example/intro' && d.final_reply === `Tap the link, grab the intro pass.\nhttps://go.looplogicsystems.com/${d.short_code}`);
  ok('owner email: real link + Mark booked/Pause buttons, signed', d.owner_email.lines.join(' ').includes('https://studio.example/intro')
     && d.owner_email.buttons[0].url.endsWith(`a=booked&l=42&t=${crypto.createHmac('sha256', ENV.OWNER_LINK_SECRET).update('booked:42').digest('hex').slice(0, 32)}`));
  d = await decide({ reply: 'Tap the link, grab the intro pass.', intent: 'send_booking_link', slot: 'S1' }, { c: { ...cfg, links_ok: false } });
  ok('L12: link marked broken -> NO link, escalated', d.intent === 'escalate' && !d.link && !/https?:/.test(d.final_reply));
  ok('L12: the model\'s "tap the link" text is replaced by one honest line', d.final_reply === 'Perfect, Jenna will send you the booking link for Sat 31 Oct, 9:15am Mat Basics shortly.');
  ok('L12: owner + founders told why', /marked broken by the daily check/.test(d.reason));
  d = await decide({ reply: 'Want me to send you the link?', intent: 'send_booking_link', slot: 'S1' });
  ok('model ASKS "want me to send the link?" -> no link yet', d.intent === 'continue' && !d.link);
  d = await decide({ reply: 'Grab it at https://evil.example/x — easy!', intent: 'continue' });
  ok('model-typed URL stripped, em dash removed', !/evil/.test(d.final_reply) && !/—/.test(d.final_reply));
  d = await decide({ reply: 'Sure!', intent: 'escalate', escalate_reason: 'injury' });
  ok('escalation -> the owner\'s own holding line, not the model\'s text', d.final_reply === cfg.prompt_vars.escalation_line);
  d = await decide({ reply: 'Hi! Mat Basics is great.', intent: 'continue' }, { x: { ...ctx, first_message: true } });
  ok('first reply carries the AI disclosure', d.final_reply.endsWith("— Mia, Reform's AI assistant"));
  d = await decide({ reply: 'Mat Basics is great.', intent: 'continue' });
  ok('later replies do not', !/AI assistant/.test(d.final_reply));
  d = await decide({ reply: 'Yay, see you there!', intent: 'lead_booked' });
  ok('"booked!" -> first-visit tips added once', /grip socks/.test(d.final_reply) && d.owner_email.buttons[0].label === 'Confirm booked');
  d = await decide('not json at all');
  ok('unparseable model output -> escalate, never sent raw', d.intent === 'escalate' && d.final_reply === cfg.prompt_vars.escalation_line);
  d = await decide({ reply: 'x', intent: 'escalate', escalate_reason: 'injury' }, { up: { id: 42, username: 'jess.moves', name: 'Jess Carter' } });
  ok('owner email names "@jess.moves (Jess Carter)" + Open chat', /^@jess\.moves \(Jess Carter\) needs you/.test(d.owner_email.subject) && d.owner_email.buttons.some((b) => b.url === 'https://ig.me/m/jess.moves'));
};
