// Channel Adapter: Instagram (pilot step 6) + IG Token Refresh.
const { run, ok, enc, reply, TOKEN_KEY, crypto } = require('./harness');
const F = 'channel-adapter-instagram.json';
const ENV = { IG_TOKEN_KEY: TOKEN_KEY, OWNER_LINK_SECRET: 's'.repeat(40) };
const TOKEN = 'IGAAT' + 'z'.repeat(80);
module.exports = async () => {
  const body = { object: 'instagram', entry: [{ id: '1784BIZ', messaging: [
    { sender: { id: 'LEAD1' }, recipient: { id: '1784BIZ' }, timestamp: 1, message: { mid: 'm1', text: 'hi' } },
    { sender: { id: '1784BIZ' }, recipient: { id: 'LEAD1' }, timestamp: 2, message: { mid: 'm2', text: 'Hello from staff', is_echo: true } },
    { sender: { id: '1784BIZ' }, recipient: { id: 'LEAD1' }, timestamp: 3, message: { mid: 'm3', is_echo: true, attachments: [{ type: 'audio' }] } },
    { sender: { id: 'LEAD1' }, recipient: { id: '1784BIZ' }, timestamp: 4, message: { mid: 'm4', attachments: [{ type: 'image' }] } },
    { sender: { id: 'LEAD1' }, recipient: { id: '1784BIZ' }, timestamp: 5, message: { mid: 'm5', is_deleted: true } },
    { sender: { id: 'LEAD1' }, recipient: { id: '1784BIZ' }, timestamp: 6, read: { mid: 'm1' } } ] }] };
  let r = await run(F, 'Extract IG Events', [{ body }]);
  ok('lead text -> inbound', r[0].kind === 'inbound' && r[0].account_ref === '1784BIZ' && r[0].ig_sender === 'LEAD1');
  ok('staff text in the app -> echo', r[1].kind === 'echo' && r[1].lead_ref === 'LEAD1' && r[1].body === 'Hello from staff');
  ok('staff voice note -> echo "[voice note]"', r[2].kind === 'echo' && r[2].body === '[voice note]' && r[2].text === null);
  ok("lead photo, unsend, read receipt -> ignored", r.length === 3);

  let calls = [];
  const prof = async (o) => { calls.push(o); return reply(200, { username: 'jess.moves', name: 'Jess Carter' }); };
  const msg = { account_ref: '1784BIZ', ig_sender: 'LEAD1', ig_mid: 'm1', text: 'hi', received_at: 't', token_enc: enc(TOKEN), profile_due: true };
  r = await run(F, 'Look Up Profile', [msg], { env: ENV, http: prof });
  ok('new lead -> @username + name', r[0].username === 'jess.moves' && r[0].name === 'Jess Carter' && r[0].profile_checked);
  ok('profile call: Bearer, lead id in path, token not output', calls[0].headers.Authorization === `Bearer ${TOKEN}` && /\/LEAD1$/.test(calls[0].url) && !JSON.stringify(r).includes(TOKEN));
  calls = [];
  r = await run(F, 'Look Up Profile', [{ ...msg, profile_due: false }], { env: ENV, http: prof });
  ok('known lead -> no lookup', calls.length === 0 && r[0].username === null);
  r = await run(F, 'Look Up Profile', [msg], { env: ENV, http: async () => reply(400, { error: { message: 'no', code: 100 } }) });
  ok('lookup fails -> still goes to the brain', r.length === 1 && r[0].message === 'hi' && r[0].username === null);

  calls = [];
  const row = { lead_id: 7, external_id: 'LEAD1', reply: 'See you Saturday!', outbound_message_id: 99, account_ref: '1784BIZ', token_enc: enc(TOKEN), paused_by_now: null, username: 'jess.moves', studio_name: 'Reform' };
  const sendOk = async (o) => { calls.push(o); return reply(200, { message_id: 'mid.OURS' }); };
  r = await run(F, 'Send IG Reply', [row], { env: ENV, http: sendOk });
  ok('send: POST /me/messages, recipient + text', r[0].status === 'sent' && r[0].mid === 'mid.OURS' && /\/me\/messages$/.test(calls[0].url) && JSON.parse(calls[0].body).message.text === 'See you Saturday!');
  ok('send: token not in output', !JSON.stringify(r).includes(TOKEN));
  calls = [];
  r = await run(F, 'Send IG Reply', [{ ...row, paused_by_now: 'staff_reply' }], { env: ENV, http: sendOk });
  ok('staff replied while AI was writing -> not sent', r[0].status === 'skipped' && calls.length === 0);
  r = await run(F, 'Send IG Reply', [{ ...row, paused_by_now: 'escalation' }], { env: ENV, http: sendOk });
  ok("escalation's holding reply still sent", r[0].status === 'sent');
  calls = [];
  r = await run(F, 'Send IG Reply', [row], { env: ENV, http: async (o) => { calls.push(o); return reply(400, { error: { message: `bad token ${TOKEN}`, code: 190 } }); } });
  ok('revoked token -> failed, token_problem, scrubbed, tried once', r[0].status === 'failed' && r[0].token_problem && !r[0].error.includes(TOKEN) && calls.length === 1);

  r = await run(F, 'Email: Staff Took Over', [{ outcome: 'paused_now', lead_id: 7, username: 'jess.moves', owner_alert_email: 'owner@studio.com', staff_body: 'Hi Jess!' }], { env: ENV });
  const t = crypto.createHmac('sha256', ENV.OWNER_LINK_SECRET).update('resume:7').digest('hex').slice(0, 32);
  ok('takeover email: owner, signed Resume, Open chat', r[0].to === 'owner@studio.com' && /You replied to @jess\.moves/.test(r[0].subject) && r[0].buttons[0].url.endsWith(`a=resume&l=7&t=${t}`) && r[0].buttons[1].label === 'Open chat');

  calls = [];
  r = await run('ig-token-refresh.json', 'Refresh Tokens', [{ id: 1, account_ref: 'A', token_enc: enc(TOKEN), refresh_due: true }, { id: 2, token_enc: enc(TOKEN), refresh_due: false }, { success: true }],
    { env: ENV, http: async (o) => { calls.push(o); return reply(200, { access_token: 'IGAANEW' + 'q'.repeat(80), expires_in: 5183999 }); } });
  ok('refresh: due renewed, not-due skipped, empty row ignored, new token only encrypted', r.length === 2 && r[0].status === 'refreshed' && r[1].status === 'not_due' && calls.length === 1 && !JSON.stringify(r).includes('IGAANEW'));
  r = await run('ig-token-refresh.json', 'Anything Wrong?', [{ id: 1, status: 'refreshed', token_expires_at: new Date(Date.now() + 59 * 864e5).toISOString() }]);
  ok('refresh report: healthy -> no email', r.length === 0);
  r = await run('ig-token-refresh.json', 'Anything Wrong?', [{ id: 1, status: 'failed', token_error: 'HTTP 400', ig_username: 'talha08860', studio_name: 'Reform', token_expires_at: new Date(Date.now() + 5 * 864e5).toISOString() }]);
  ok('refresh report: failure -> one founder email', r.length === 1 && /@talha08860/.test(r[0].subject));
};
