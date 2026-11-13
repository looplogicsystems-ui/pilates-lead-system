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
  ok('owner email: the class is labelled as suggested, not booked', /is heading to book \(suggested: /.test(d.owner_email.subject)
     && /^Suggested in the chat: .+\. They may pick a different class on your booking page\.$/.test(d.owner_email.lines[0]) && !/Recommended/.test(JSON.stringify(d.owner_email)));
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
  // Fix C (2026-11-08): the "tips already said" check uses the studio's OWN tips
  const count = (t, re) => (t.match(re) || []).length;
  d = await decide({ reply: 'Yay! Bring grip socks and come 10 minutes early.', intent: 'lead_booked' });
  ok('model already wrote the tips -> not added again', count(d.final_reply, /grip socks/g) === 1);
  const park = { ...cfg, prompt_vars: { ...cfg.prompt_vars, first_visit_tips: 'Park on Aldrich St and wear something you can move in.' } };
  d = await decide({ reply: 'Booked! Bring grip socks.', intent: 'lead_booked' }, { c: park });
  ok('another studio: demo wording in the reply no longer blocks ITS tips', /Park on Aldrich/.test(d.final_reply));
  d = await decide({ reply: 'Amazing! Park on Aldrich and wear comfy layers.', intent: 'lead_booked' }, { c: park });
  ok('another studio: its tips already in the reply -> not added again', count(d.final_reply, /Aldrich/g) === 1);
  const demoTips = { ...cfg, prompt_vars: { ...cfg.prompt_vars, first_visit_tips: 'Come about 10 minutes early for a quick waiver and a walkthrough of the springs, and bring grip socks (or grab a pair at the front desk for $18).' } };
  d = await decide({ reply: 'See you at Reformer Flow tomorrow, come early!', intent: 'lead_booked' }, { c: demoTips });
  ok('a normal "see you tomorrow" reply still gets the tips', /walkthrough of the springs/.test(d.final_reply));
  d = await decide('not json at all');
  ok('unparseable model output -> escalate, never sent raw', d.intent === 'escalate' && d.final_reply === cfg.prompt_vars.escalation_line);
  d = await decide({ reply: 'x', intent: 'escalate', escalate_reason: 'injury' }, { up: { id: 42, username: 'jess.moves', name: 'Jess Carter' } });
  ok('owner email names "@jess.moves (Jess Carter)" + Open chat', /^@jess\.moves \(Jess Carter\) needs you/.test(d.owner_email.subject) && d.owner_email.buttons.some((b) => b.url === 'https://ig.me/m/jess.moves'));

  // --- "delete my data" (privacy page promise; migration 016) -----------------
  const wf = require('fs').readFileSync(require('path').join(__dirname, '..', 'workflows', F), 'utf8');
  const W = JSON.parse(wf);
  const ifNode = W.nodes.find((n) => n.name === 'Delete Request?');
  const expr = ifNode.parameters.conditions.conditions[0].leftValue.replace(/^=\{\{\s*/, '').replace(/\s*\}\}$/, '');
  const asks = (message) => new Function('$', 'return ' + expr)(() => ({ first: () => ({ json: { message } }) }));
  ok('"delete my data" variants trigger deletion',
     ['delete my data', 'Hi, please DELETE MY DATA thanks', 'can you erase my info?', 'remove all my details pls', 'delete my messages'].every(asks));
  ok('ordinary messages do not', !['do you delete classes when nobody books?', 'my data plan ran out lol', 'how do I book?', 'delete the booking'].some(asks));
  const to = (src, out) => ((W.connections[src] || {}).main[out] || []).map((c) => c.node);
  ok('wired before anything is stored: Studio Found? -> Delete Request? -> Forget Lead | Upsert Lead',
     to('Studio Found?', 0).join() === 'Delete Request?' && to('Delete Request?', 0).join() === 'Forget Lead' && to('Delete Request?', 1).join() === 'Upsert Lead');
  const mail = (await run(F, 'Email: Data Deleted', [{}], { nodes: { 'Forget Lead': [{ result: { found: true, messages: 7, booking_links: 1, escalations: 0, dead_letters: 0 } }],
    'Resolve Studio': [{ ...cfg, studio_name: 'Reform Collective' }], 'When Called': [{ ...inb, external_id: '17840000000000099', username: 'jess.moves', name: 'Jess Carter' }] } }))[0];
  ok('founders told it was done, with counts', mail.to === 'founders' && /7 messages/.test(mail.lines[0]));
  ok('...and the email names nobody', !/jess|17840000000000099/i.test(JSON.stringify(mail)));
  const ret = W.nodes.find((n) => n.name === 'Return: Data Deleted').parameters.assignments.assignments;
  ok('the lead gets a confirmation; lead_id 0 so the adapter can still send it', ret.find((a) => a.name === 'send').value === '={{ true }}' && ret.find((a) => a.name === 'lead_id').value === '={{ 0 }}');

  // --- voice slips from the 2026-11-11 real-DM tests ---------------------------
  // 1. "Hey! Yep, Tuesday 6pm…": the greeting put in front of a banned "Yep"
  d = await decide({ reply: 'Hey! Yep, Tuesday 6pm Reformer Foundations is on the schedule.', intent: 'continue' });
  ok('"Hey! Yep, Tuesday…" -> "Hey! Tuesday…"', d.final_reply === 'Hey! Tuesday 6pm Reformer Foundations is on the schedule.');
  d = await decide({ reply: 'Hey Sam! Yeah, we do mat on Saturdays.', intent: 'continue' });
  ok('"Hey Sam! Yeah, we do…" -> "Hey Sam! We do…"', d.final_reply === 'Hey Sam! We do mat on Saturdays.');
  d = await decide({ reply: 'Hey there, yes we have a 6pm on Thursday.', intent: 'continue' });
  ok('after "Hey there," the next word stays lowercase', d.final_reply === 'Hey there, we have a 6pm on Thursday.');
  d = await decide({ reply: 'Yep, Thursday 6pm we have Reformer Foundations.', intent: 'continue' });
  ok('a plain "Yep," (answering "still there?") is left alone', d.final_reply === 'Yep, Thursday 6pm we have Reformer Foundations.');
  d = await decide({ reply: 'Hey! Yesterday was packed, but Tuesday has room.', intent: 'continue' });
  ok('"Yesterday" is not "Yes"', d.final_reply === 'Hey! Yesterday was packed, but Tuesday has room.');
  d = await decide({ reply: 'Hey! Yep, Tue 6pm works. Tap the link, buy a single class, then pick it.', intent: 'send_booking_link', slot: 'S1', returning_client: true });
  ok('...also on the booking-link reply (returning client keeps the main link)', d.final_reply.startsWith('Hey! Tue 6pm works.') && d.link === cfg.booking_url);

  // 2. "the latest on the schedule is Tue 6pm" when Wed 7pm exists: the week at a glance
  const bcCode = W.nodes.find((n) => n.name === 'Build Context').parameters.jsCode;
  const glanceSrc = bcCode.slice(bcCode.indexOf('// <weekAtAGlance>'), bcCode.indexOf('// </weekAtAGlance>'));
  const weekAtAGlance = new Function(glanceSrc + '\nreturn weekAtAGlance;')();
  const dryRun = [{ weekday: 1, time: '07:00', name: 'All Levels Flow' }, { weekday: 2, time: '18:00', name: 'Reformer Foundations' },
    { weekday: 3, time: '19:00', name: 'All Levels Flow' }, { weekday: 4, time: '18:00', name: 'Reformer Foundations' },
    { weekday: 6, time: '09:00', name: 'Saturday Foundations' }];
  const g = weekAtAGlance(dryRun).join('\n');
  ok('glance: days with classes, and days with none', /Classes on: Mon, Tue, Wed, Thu, Sat\. NO classes at all on: Fri, Sun\./.test(g));
  ok('glance: latest start is Wed 7pm (not Tue 6pm), earliest Mon 7am', /Earliest start of the week: 7am \(Mon All Levels Flow\)\. Latest start of the week: 7pm \(Wed All Levels Flow\)\./.test(g));
  ok('glance: evening classes, in order', /Evening classes \(5pm or later\): Tue 6pm Reformer Foundations, Wed 7pm All Levels Flow, Thu 6pm Reformer Foundations\./.test(g));
  ok('glance: ties all named, times written the texting way', /Latest start of the week: 5:30pm \(Mon A, Wed B\)/.test(weekAtAGlance([{ weekday: 3, time: '17:30', name: 'B' }, { weekday: 1, time: '17:30', name: 'A' }, { weekday: 1, time: '09:15', name: 'C' }]).join(' ')));
  ok('glance: classes every day -> no "NO classes" line', !/NO classes/.test(weekAtAGlance([1, 2, 3, 4, 5, 6, 7].map((w) => ({ weekday: w, time: '12:00', name: 'X' }))).join(' ')));
  ok('glance: no timetable -> nothing', weekAtAGlance([]).length === 0 && weekAtAGlance(null).length === 0);

  // 3. The whole Build Context, with Luxon like n8n (skipped where Luxon isn't installed)
  let DateTime = null;
  for (const p of ['luxon', '/usr/local/lib/node_modules/n8n/node_modules/luxon']) { try { ({ DateTime } = require(p)); break; } catch (e) { /* try the next */ } }
  if (!DateTime) {
    console.log('SKIP Build Context prompt checks: Luxon not found. Run the tests inside the n8n image to include them (README → Tests).');
  } else {
    const studio = { studio_id: 4, studio_name: 'Dry Run Pilates', owner_name: 'Sam', timezone: 'America/Chicago', booking_url: 'https://studio.example/schedule',
      prompt_vars: { persona_name: 'Ava', studio_name: 'Dry Run Pilates', voice: { greeting: 'Hey [first name]!' },
                     booking_steps: 'Tap the link, buy the 2-week intro pass, then pick your class on the schedule.' } };
    const data = { classes: dryRun.map((c, i) => ({ id: i + 1, minutes: 50, level: '', url: null, ...c })),
      offers: [{ id: 1, kind: 'intro', name: '2-week intro pass', price: 49, currency: 'USD', url: 'https://studio.example/intro', for_new_clients: true },
               { id: 2, kind: 'drop_in', name: 'Single class', price: 30, currency: 'USD', url: null, for_new_clients: false }],
      thread: [], outbound_count: 0 };
    const b = (await run(F, 'Build Context', [{}], { DateTime,
      nodes: { 'Resolve Studio': [studio], 'When Called': [{ message: 'do you have a class at 10pm on Sunday?' }], 'Load Studio Data': [data] } }))[0];
    ok('Build Context runs: ready, real slots', b.ready === true && b.slots.length >= 4);
    ok('prompt has the week at a glance, with the real latest class', /WEEK AT A GLANCE/.test(b.systemMessage)
       && /Latest start of the week: 7pm \(Wed All Levels Flow\)/.test(b.systemMessage) && /NO classes at all on: Fri, Sun\./.test(b.systemMessage));
    ok('prompt: a day with no classes is said plainly', /there are no classes that day \(not "not this week"\)/.test(b.systemMessage));
    ok('prompt: returning clients never get the intro offer', /RETURNING clients .* never get the intro offer/.test(b.systemMessage) && /not even "if it still applies"/.test(b.systemMessage));
    ok('prompt: no "Yep" straight after the greeting either', /"Hey! Yep, …" still opens with Yep/.test(b.systemMessage));
    // 2026-11-14: confirm the class first, then send the link once
    ok('prompt: link only after a yes to ONE class the AI named', /only goes out once they have agreed to ONE specific class that YOU named/.test(b.systemMessage)
       && !/or asked how\/where to book/.test(b.systemMessage));
    ok('prompt: a straight "can I book?" gets the class named and a question, not the link', /don't send the link\. Name the ONE class and ask/.test(b.systemMessage));
    ok('prompt: the link is sent once per class', /Send the link ONCE per class/.test(b.systemMessage) && /it's the link in your earlier message/.test(b.systemMessage));
    ok('prompt: the link message names the class, no question; steps name the agreed class',
       /names the class they agreed to and has NO question in it/.test(b.systemMessage)
       && /with the agreed class named in it instead of "your class": Tap the link, buy the 2-week intro pass/.test(b.systemMessage));
    const none = (await run(F, 'Build Context', [{}], { DateTime,
      nodes: { 'Resolve Studio': [studio], 'When Called': [{ message: 'hi' }], 'Load Studio Data': [{ ...data, classes: [] }] } }))[0];
    ok('no timetable: no glance section, studio not ready (escalates)', !/WEEK AT A GLANCE/.test(none.systemMessage) && none.ready === false);
  }
};
