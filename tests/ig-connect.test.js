// IG Connect (pilot step 6): link checks, token exchange, encryption, pages.
const { run, ok, dec, reply, TOKEN_KEY, crypto } = require('./harness');
const F = 'ig-connect.json';
const ENV = { OWNER_LINK_SECRET: 'x'.repeat(40), IG_TOKEN_KEY: TOKEN_KEY, IG_APP_SECRET_INSTAGRAM: 'appsecret' };
const state = (o, secret = ENV.OWNER_LINK_SECRET) => {
  const p64 = Buffer.from(JSON.stringify(o)).toString('base64url');
  return p64 + '.' + crypto.createHmac('sha256', secret).update('ig-connect|' + p64).digest('hex').slice(0, 32);
};
module.exports = async () => {
  const good = { s: 2, e: Math.floor(Date.now() / 1000) + 3600, n: 'abcdefghijklmnopqrstuv' };
  const check = (q) => run(F, 'Check Link', [{ query: q }], { env: ENV });
  let r = (await check({ code: 'AQB123#_', state: state(good) }))[0];
  ok('valid link -> go, studio 2, "#_" stripped', r.route === 'go' && r.studio_id === 2 && r.code === 'AQB123');
  const forged = Buffer.from(JSON.stringify({ ...good, s: 3 })).toString('base64url') + '.' + state(good).split('.')[1];
  ok('studio id edited -> refused', (await check({ code: 'A', state: forged }))[0].route === 'stop');
  ok('signed with another secret -> refused', (await check({ code: 'A', state: state(good, 'y'.repeat(40)) }))[0].route === 'stop');
  r = (await check({ code: 'A', state: state({ ...good, e: 1000 }) }))[0];
  ok('expired -> "expired" page', r.route === 'stop' && /expired/.test(r.html));
  r = (await check({ error: 'access_denied', state: state(good) }))[0];
  ok('Cancel on Instagram -> "Not connected"', r.route === 'stop' && /Not connected/.test(r.html));

  const calls = [];
  const meta = (o = {}) => async (req) => {
    calls.push(req);
    if (req.url.includes('oauth/access_token')) return o.short || reply(200, { data: [{ access_token: 'SHORT_' + 'a'.repeat(60), user_id: 9, permissions: 'instagram_business_basic,instagram_business_manage_messages' }] });
    if (req.url.endsWith('/access_token')) return reply(200, { access_token: 'LONG_' + 'b'.repeat(60), expires_in: 5183944 });
    if (req.url.endsWith('/me')) return o.me || reply(200, { user_id: '17841414365226685', username: 'talha08860', name: 'Talha', account_type: 'BUSINESS' });
    if (req.url.endsWith('/subscribed_apps')) return reply(200, { success: true });
  };
  const st = { studio_id: 2, studio_name: 'Reform Collective', nonce: good.n, code: 'AQB123', link_used: false };
  r = (await run(F, 'Connect Account', [st], { env: ENV, http: meta() }))[0];
  ok('connect: account id + username from /me', r.ok && r.account_ref === '17841414365226685' && r.username === 'talha08860');
  ok('connect: output has NO usable token', !JSON.stringify(r).includes('LONG_') && !JSON.stringify(r).includes('SHORT_'));
  ok('ciphertext decrypts to the long-lived token', dec(r.token_enc).startsWith('LONG_'));
  ok('code exchange: form-encoded by hand (sandbox has no URLSearchParams)', calls[0].headers['Content-Type'] === 'application/x-www-form-urlencoded'
     && /client_id=1068683115593698/.test(calls[0].body) && /code=AQB123/.test(calls[0].body) && /redirect_uri=https%3A%2F%2Fn8n\.looplogicsystems\.com%2Fwebhook%2Fig%2Fconnect/.test(calls[0].body));
  ok('/me + subscribe: Bearer header, token not in URL', calls[2].headers.Authorization.startsWith('Bearer LONG_') && !String(calls[2].url).includes('LONG_'));
  ok('subscribes to "messages"', calls[3].qs.subscribed_fields === 'messages' && r.subscribed === true);
  r = (await run(F, 'Connect Account', [st], { env: ENV, http: meta({ short: reply(200, { data: [{ access_token: 'T', permissions: 'instagram_business_basic' }] }) }) }))[0];
  ok('messaging permission unticked -> no_message_permission', !r.ok && r.outcome === 'no_message_permission');
  r = (await run(F, 'Connect Account', [st], { env: ENV, http: meta({ me: reply(400, { error: { message: 'bad LONG_' + 'b'.repeat(60), code: 190 } }) }) }))[0];
  ok('Meta error -> error, token scrubbed', !r.ok && r.outcome === 'error' && !JSON.stringify(r).includes('LONG_') && /code 190/.test(r.error));
  r = (await run(F, 'Connect Account', [{ ...st, link_used: true }], { env: ENV, http: meta() }))[0];
  ok('used link -> refused before calling Instagram', !r.ok && r.outcome === 'link_used');
  const cn = { ok: true, studio_id: 2, studio_name: 'Reform Collective', username: 'talha08860', name: 'Talha', account_ref: '1', account_type: 'BUSINESS', expires_in: 5183944, subscribed: true };
  r = (await run(F, 'Result', [{ outcome: 'connected' }], { nodes: { 'Connect Account': [cn] } }))[0];
  ok('"Connected ✓" page + founder email', /Connected/.test(r.html) && /Instagram connected: @talha08860/.test(r.email.subject));
  r = (await run(F, 'Result', [{ outcome: 'other_studio' }], { nodes: { 'Connect Account': [cn] } }))[0];
  ok('other studio -> refused page + FAILED email', /Already connected elsewhere/.test(r.html) && /FAILED \(other_studio\)/.test(r.email.subject));
};
