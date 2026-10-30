// Cloudflare Worker "go" — serves https://go.looplogicsystems.com/<code>
// (Workers & Pages -> go; custom domain go.looplogicsystems.com).
// Deployed by hand in the Cloudflare dashboard: this file is the record of
// what is live. Keep them in sync.
//
// Forwards to n8n's Link Redirect workflow, which counts the tap and 302s to
// the studio's booking page. The visitor's network (ASN + organisation) is
// passed on because Meta's link scanners pose as ordinary Chrome browsers and
// are only recognisable by coming from Meta's network (AS32934). Seen live
// 2026-10-30: without this, every link read "tapped" ~8 seconds after sending.
export default {
  async fetch(req) {
    const code = new URL(req.url).pathname.slice(1);
    if (!/^[A-Za-z0-9_-]{4,16}$/.test(code)) return new Response('Not found', { status: 404 });
    const cf = req.cf || {};
    return fetch(`https://n8n.looplogicsystems.com/webhook/go?c=${code}`, {
      headers: {
        'user-agent': req.headers.get('user-agent') || '',
        'x-client-asn': String(cf.asn || ''),
        'x-client-org': String(cf.asOrganization || ''),
      },
      redirect: 'manual',
    });
  },
};
