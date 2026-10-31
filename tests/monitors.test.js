// Monitors + error emails (pilot step 7).
const { run, ok, reply } = require('./harness');
module.exports = async () => {
  // --- Error Handler: founder email, throttled, never about Send Email itself
  const err = { workflow: { id: 'fvHhAUDCbVN9ELb0', name: 'Lead Brain' }, execution: { id: '123', lastNodeExecuted: 'AI Agent', error: { message: 'OpenAI 500' } } };
  const eh = (dl, e = err) => run('error-handler.json', 'Founder Email?', [dl], { nodes: { 'On Workflow Error': [e], 'Persist Dead Letter': [dl] } });
  let r = await eh({ dead_letter_id: 9, recent_same: 0 });
  ok('error -> founder email naming workflow, step, error, run link', r.length === 1 && /System error in "Lead Brain"/.test(r[0].subject) && /at "AI Agent"/.test(r[0].heading)
     && /OpenAI 500/.test(r[0].lines[0]) && r[0].buttons[0].url.endsWith('/workflow/fvHhAUDCbVN9ELb0/executions/123'));
  ok('2nd error from same workflow within 10 min -> no email (Slack only)', (await eh({ dead_letter_id: 10, recent_same: 1 })).length === 0);
  r = await eh({});
  ok('Postgres down (no dead letter) -> still emails, says it could not be saved', r.length === 1 && /could NOT be saved/.test(r[0].lines[1]));
  ok('Send Email failing -> never emails through itself (no loop)', (await eh({ dead_letter_id: 11, recent_same: 0 }, { ...err, workflow: { id: 'PQWBdER3x291uWeU', name: 'Send Email' } })).length === 0);

  // --- Unanswered DMs
  r = await run('monitor-unanswered.json', 'Build Alert', [{ success: true }]);
  ok('nothing unanswered -> no email', r.length === 0);
  r = await run('monitor-unanswered.json', 'Build Alert', [{ id: 1, lead_id: 7, body: 'hello?', minutes: 7, channel: 'instagram', username: 'jess.moves', name: 'Jess', studio_name: 'Reform' }]);
  ok('unanswered -> one founder email naming the lead', r.length === 1 && r[0].to === 'founders' && /@jess\.moves \(Jess\) has had NO reply for 7\+ minutes/.test(r[0].subject));

  // --- Booking link check
  const L = 'monitor-booking-links.json';
  const hits = {};
  const web = async (o) => {
    hits[o.url] = (hits[o.url] || 0) + 1;
    if (/ok\.example/.test(o.url)) return { statusCode: 200, body: '' };
    if (/gone\.example/.test(o.url)) return { statusCode: 404, body: '' };
    if (/bot\.example/.test(o.url)) return { statusCode: 403, body: '' };
    if (/flaky\.example/.test(o.url)) return hits[o.url] === 1 ? { statusCode: 503, body: '' } : { statusCode: 200, body: '' };
    throw Object.assign(new Error('getaddrinfo ENOTFOUND'), { code: 'ENOTFOUND' });
  };
  const studio = (links, extra = {}) => ({ studio_id: 2, studio_name: 'Reform', owner_alert_email: 'owner@studio.com', links_ok: true, link_problem: null, links, ...extra });
  r = await run(L, 'Check Links', [studio([{ what: 'main booking page', url: 'https://ok.example/' }, { what: 'offer "Intro"', url: 'https://ok.example/' }, { what: 'class', url: 'https://flaky.example/' }])], { http: web });
  ok('all fine (a flaky 503 passes on retry, same URL checked once) -> links_ok', r[0].links_ok && r[0].problem === '' && r[0].checked === 2);
  r = await run(L, 'Check Links', [studio([{ what: 'offer "Intro"', url: 'https://gone.example/intro' }])], { http: web });
  ok('404 -> BROKEN, links switched off', !r[0].links_ok && /^BROKEN offer "Intro": https:\/\/gone\.example\/intro -> HTTP 404/.test(r[0].problem));
  r = await run(L, 'Check Links', [studio([{ what: 'main booking page', url: 'https://nosuchsite.example/' }])], { http: web });
  ok('site unreachable -> BROKEN', !r[0].links_ok && /ENOTFOUND/.test(r[0].problem));
  r = await run(L, 'Check Links', [studio([{ what: 'main booking page', url: 'https://bot.example/' }])], { http: web });
  ok('403 bot-block -> warning only, links stay ON', r[0].links_ok && /^COULD NOT CHECK/.test(r[0].problem));
  ok('empty-query placeholder ignored', (await run(L, 'Check Links', [{ success: true }], { http: web })).length === 0);
  const em = (s) => run(L, 'Build Alerts', [{}], { nodes: { 'Check Links': s } });
  r = await em([{ studio_name: 'Reform', owner_alert_email: 'owner@studio.com', links_ok: false, old_links_ok: true, problem: 'BROKEN x', old_problem: '', broken: 1, blocked: 0, checked: 1 }]);
  ok('newly broken -> email owner + founders', r.length === 1 && r[0].to === 'owner@studio.com, founders' && /Broken booking link/.test(r[0].subject));
  r = await em([{ studio_name: 'Reform', links_ok: false, old_links_ok: false, problem: 'BROKEN x', old_problem: 'BROKEN x', broken: 1, blocked: 0 }]);
  ok('still broken, same problem -> no repeat email', r.length === 0);
  r = await em([{ studio_name: 'Reform', owner_alert_email: 'founders', links_ok: true, old_links_ok: false, problem: '', old_problem: 'BROKEN x', broken: 0, blocked: 0, checked: 3 }]);
  ok('fixed -> "working again" email', r.length === 1 && /working again/.test(r[0].subject) && r[0].to === 'founders');

  // --- Weekly review
  r = await run('weekly-review.json', 'Build Review', [{ studio_name: 'Reform', new_leads: 12, links_sent: 8, links_tapped: 6, said_booked: 3, owner_confirmed: 2,
    escalations: 1, nudges: 4, unanswered: 0, paused_now: '@jess.moves (staff_reply)', link_problem: null, open_dead_letters: 0, dead_letters_week: 1 }]);
  ok('weekly review: funnel with %, paused list, review checklist', r.length === 1 && /Links sent 8 → tapped 6 \(75%\) → said booked 3 → owner confirmed 2/.test(r[0].lines[0])
     && /@jess\.moves \(staff_reply\)/.test(r[0].lines[1]) && r[0].lines.some((l) => /5-minute review/.test(l)));
  ok('weekly review: privacy line, and a warning when the retention job didn't run',
     r[0].lines.some((l) => /^Privacy: 0 "delete my data" requests/.test(l) && /ran on 0 of 7 days: CHECK IT/.test(l)));
  r = await run('weekly-review.json', 'Build Review', [{ studio_name: 'Reform', new_leads: 0, links_sent: 0, links_tapped: 0, said_booked: 0, owner_confirmed: 0,
    escalations: 0, nudges: 0, unanswered: 0, paused_now: null, link_problem: null, open_dead_letters: 0, dead_letters_week: 0,
    deletion_requests_week: 1, retention_deleted_week: 3, retention_runs_week: 7 }]);
  ok('...and counts requests, retention deletions, no warning at 7/7', r[0].lines.some((l) => /1 "delete my data" requests carried out, 3 leads deleted/.test(l) && /ran on 7 of 7 days\.$/.test(l)));
};
