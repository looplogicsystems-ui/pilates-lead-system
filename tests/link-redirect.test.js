// Link Redirect: which visits count as a real tap (pilot step 5, fixed step 7).
const { run, ok } = require('./harness');
const visit = async (ua, headers = {}, c = 'BFU7DaPW') =>
  (await run('link-redirect.json', 'Classify', [{ query: { c }, headers: { 'user-agent': ua, ...headers } }]))[0];
const ANDROID = 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Mobile Safari/537.36';
const IG_INAPP = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Instagram 345.0.0.39.94';
module.exports = async () => {
  ok("Meta's named preview robot -> not counted", (await visit('facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)')).crawler);
  ok("Meta scanner posing as Android Chrome, from Meta's network (AS32934) -> not counted",
     (await visit(ANDROID, { 'x-client-asn': '32934', 'x-client-org': 'Facebook, Inc.' })).crawler);
  ok('same scanner, network named only -> not counted', (await visit(ANDROID, { 'x-client-org': 'Meta Platforms, Inc.' })).crawler);
  ok('real lead tapping in Instagram, on her phone network -> COUNTED', !(await visit(IG_INAPP, { 'x-client-asn': '21928', 'x-client-org': 'T-Mobile USA, Inc.' })).crawler);
  ok('real Android Chrome tap from an ISP -> COUNTED', !(await visit(ANDROID, { 'x-client-asn': '7922', 'x-client-org': 'Comcast Cable' })).crawler);
  ok('no user agent -> not counted', (await visit('')).crawler);
  const v = await visit(ANDROID, { 'x-client-asn': '32934', 'x-client-org': 'Facebook, Inc.' });
  ok('network recorded for the evidence log', v.asn === '32934' && v.network === 'Facebook, Inc.');
  ok('bad code characters rejected', (await visit(ANDROID, {}, "x';drop")).code === '');
};
