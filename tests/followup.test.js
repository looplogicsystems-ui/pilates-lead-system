// Follow-up Scheduler (pilot step 7): AI nudges, link nudge, hand-over.
const { run, ok, enc, reply, TOKEN_KEY } = require('./harness');
const F = 'followup-scheduler.json';
const ENV = { IG_TOKEN_KEY: TOKEN_KEY };
module.exports = async () => {
  const lead = { lead_id: 7, external_id: 'L1', channel: 'instagram', username: 'jess.moves', name: 'Jess', studio_name: 'Reform',
    prompt_vars: { persona_name: 'Mia', studio_name: 'Reform Collective', type: 'boutique reformer Pilates studio', city: 'Austin, TX' },
    owner_name: 'Jenna', kind: 'quiet', is_final: false, short_code: 'AbC12345', link_id: 5, token_enc: enc('TOKEN_' + 'x'.repeat(60)),
    thread: [{ direction: 'inbound', author: 'lead', body: 'any beginner classes saturday?' }, { direction: 'outbound', author: 'ai', body: 'Sat 9:15am Mat Basics is a good first one. Does that work?' }] };
  const compose = async (l) => (await run(F, 'Compose Prompt', [l], { perItem: true }))[0];
  let r = await compose(lead);
  ok('quiet nudge prompt: persona + real thread', /Mia, the AI assistant for Reform Collective/.test(r.systemMessage) && /Sat 9:15am Mat Basics/.test(r.userMessage) && /went quiet/.test(r.systemMessage));
  ok('final nudge prompt: the gentle last one', /LAST follow-up/.test((await compose({ ...lead, is_final: true })).systemMessage));
  const linkC = await compose({ ...lead, kind: 'link' });
  ok('link nudge prompt: system attaches the link', /attaches the same link/.test(linkC.systemMessage));
  r = (await run(F, 'Finish Nudge', [{ output: '"Did you get a chance to grab a spot — Saturday still good? https://evil.example/x"' }], { item: { 'Compose Prompt': linkC }, perItem: true }))[0];
  ok('finish: quotes/em dash/AI URL removed, OUR tracked link appended', r.text === 'Did you get a chance to grab a spot, Saturday still good?\nhttps://go.looplogicsystems.com/AbC12345' && !r.skip);
  ok('finish: prompt + thread not carried on', !('systemMessage' in r) && !('thread' in r));
  r = (await run(F, 'Finish Nudge', [{ error: 'OpenAI timeout' }], { item: { 'Compose Prompt': linkC }, perItem: true }))[0];
  ok('AI failed -> skipped (retried next run)', r.skip && r.skip_reason === 'AI error');
  r = (await run(F, 'Finish Nudge', [{ output: 'x'.repeat(450) }], { item: { 'Compose Prompt': linkC }, perItem: true }))[0];
  ok('rambling AI text -> skipped', r.skip === true);

  const calls = [];
  r = await run(F, 'Send IG Nudge', [{ message_id: 99, lead_id: 7, external_id: 'L1', text: 'Still keen?', token_enc: lead.token_enc, kind: 'quiet', channel: 'instagram' }, { success: true }],
    { env: ENV, http: async (o) => { calls.push(o); return reply(200, { message_id: 'mid.N1' }); } });
  ok('send: one DM, decrypted token, placeholder row ignored', r.length === 1 && r[0].status === 'sent' && r[0].mid === 'mid.N1' && calls.length === 1 && calls[0].headers.Authorization.startsWith('Bearer TOKEN_'));
  ok('send: token not in output', !JSON.stringify(r).includes('TOKEN_'));
  r = await run(F, 'Send IG Nudge', [{ message_id: 99, lead_id: 7, external_id: 'L1', text: 'x', token_enc: lead.token_enc, kind: 'quiet' }],
    { env: ENV, http: async () => reply(400, { error: { message: 'outside of allowed window', code: 10, error_subcode: 2534022 } }) });
  ok('refused by Instagram -> failed with reason', r[0].status === 'failed' && /code 10\/2534022/.test(r[0].error));
  r = await run(F, 'Slack Sent', [{}], { nodes: { 'Which Channel?': { branches: [[{ channel: 'instagram', message_id: 1 }], [{ channel: 'slack', message_id: 2 }]] } } });
  ok('slack branch reads output 1 of the split', r.length === 1 && r[0].message_id === 2);
  r = await run(F, 'Email: Over to You', [{}], { nodes: { 'Send or Hand Over?': { branches: [[{ action: 'send' }],
    [{ action: 'hand_over', kind: 'link', channel: 'instagram', username: 'jess.moves', name: 'Jess', owner_alert_email: 'owner@studio.com' }]] } } });
  ok('hand-over email: owner, 24h rule, Open chat', r.length === 1 && r[0].to === 'owner@studio.com' && /went quiet/.test(r[0].subject) && /never opened it/.test(r[0].heading) && /24 hours/.test(r[0].lines[0]) && r[0].buttons[0].label === 'Open chat');
};
