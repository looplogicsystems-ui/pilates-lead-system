"""Shared Instagram pieces for the generators (step 6, 2026-10-28).

Imported by build_pilot.py (IG Connect), build_adapters.py (send, profile,
echoes) and build_ops.py (token refresh). Python constants plus JavaScript
snippets that are pasted into n8n Code nodes, so every workflow encrypts,
decrypts and calls Instagram the same way.

SECURITY MODEL
- A studio's Instagram token is stored in channel_accounts.access_token_enc,
  encrypted with AES-256-GCM. Key = SHA-256 of IG_TOKEN_KEY (the mini's .env,
  and Bitwarden). A database dump without that key holds no usable token.
- The token is decrypted ONLY inside the Code node that uses it, and that node
  never outputs it. Postgres nodes only ever see ciphertext, so n8n's saved
  executions (inputs and outputs of every node) never contain a usable token.
- Instagram API errors are re-thrown as short, scrubbed messages: the raw
  HTTP error would carry the request, including the Authorization header.

SANDBOX: n8n 2.34's Code-node task runner has NO URLSearchParams, URL, fetch,
structuredClone or AbortController (verified 2026-10-28). It does have
Buffer, setTimeout, TextEncoder, encodeURIComponent and require('crypto').
Use this.helpers.httpRequest for HTTP.
"""

# Our Meta app's Instagram app id. Public: it is part of every connect link.
IG_APP_ID = "1068683115593698"
# Must match, character for character, a "Valid OAuth Redirect URI" in the Meta
# app (Instagram > API setup with Instagram business login > Business login settings).
IG_REDIRECT_URI = "https://n8n.looplogicsystems.com/webhook/ig/connect"
IG_SCOPES = "instagram_business_basic,instagram_business_manage_messages"
GRAPH = "https://graph.instagram.com/v25.0"

# Encrypted token format (base64 of): 0x01 | 12-byte IV | 16-byte GCM tag | ciphertext
TOKEN_CRYPTO_JS = r"""
const crypto = require('crypto');
const tokenKey = () => {
  const k = String($env.IG_TOKEN_KEY || '');
  if (k.length < 32) throw new Error('IG_TOKEN_KEY is missing on the server');
  return crypto.createHash('sha256').update(k, 'utf8').digest();
};
const encryptToken = (plain) => {
  const iv = crypto.randomBytes(12);
  const c = crypto.createCipheriv('aes-256-gcm', tokenKey(), iv);
  const ct = Buffer.concat([c.update(String(plain), 'utf8'), c.final()]);
  return Buffer.concat([Buffer.from([1]), iv, c.getAuthTag(), ct]).toString('base64');
};
const decryptToken = (b64) => {
  const b = Buffer.from(String(b64 || ''), 'base64');
  if (b.length < 30 || b[0] !== 1) throw new Error('no usable Instagram token stored for this account');
  const d = crypto.createDecipheriv('aes-256-gcm', tokenKey(), b.subarray(1, 13));
  d.setAuthTag(b.subarray(13, 29));
  return Buffer.concat([d.update(b.subarray(29)), d.final()]).toString('utf8');
};
"""

# graphCall: one Instagram API request with scrubbed errors.
#   tries > 1 only for reads: a retried SEND could deliver the message twice.
#   form: object -> sent as application/x-www-form-urlencoded.
GRAPH_JS = r"""
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const scrub = (s) => String(s || '').replace(/[A-Za-z0-9_\-]{40,}/g, '[redacted]').slice(0, 300);
async function graphCall(ctx, { what, method = 'GET', url, token, qs, form, json, tries = 1 }) {
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  let body;
  // n8n's Code sandbox has no URLSearchParams / URL / fetch: encode by hand.
  if (form) { headers['Content-Type'] = 'application/x-www-form-urlencoded';
             body = Object.entries(form).map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v ?? ''))}`).join('&'); }
  else if (json) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(json); }
  for (let attempt = 1; ; attempt++) {
    let r;
    try {
      r = await ctx.helpers.httpRequest({ method, url, qs, headers, body, json: false,
        returnFullResponse: true, ignoreHttpStatusErrors: true, timeout: 20000 });
    } catch (e) {
      if (attempt < tries) { await sleep(700 * attempt); continue; }
      const err = new Error(`${what}: could not reach Instagram (${scrub(e.code || e.message)})`);
      err.kind = 'network'; throw err;
    }
    let data = r.body;
    if (Buffer.isBuffer(data)) data = data.toString('utf8');
    if (typeof data === 'string') { try { data = JSON.parse(data); } catch (e) { data = { raw: scrub(data) }; } }
    if (r.statusCode >= 500 && attempt < tries) { await sleep(700 * attempt); continue; }
    if (r.statusCode >= 400) {
      const m = (data && (data.error || data)) || {};
      const err = new Error(`${what}: HTTP ${r.statusCode}: ${scrub(m.message || m.error_message || m.raw || 'error')}` +
                            (m.code ? ` (code ${m.code}${m.error_subcode ? '/' + m.error_subcode : ''})` : ''));
      err.kind = 'http'; err.status = r.statusCode; err.metaCode = m.code; throw err;
    }
    return data;
  }
}
"""

# Lead label for owner emails, with the Instagram @username when we have it.
LEAD_NAME_JS = r"""
const leadLabel = (l) => {
  const u = l.username ? `@${l.username}` : null;
  if (u && l.name) return `${u} (${l.name})`;
  if (u) return u;
  if (l.name) return String(l.name);
  return l.channel === 'instagram' ? 'An Instagram lead' : `A ${l.channel} lead`;
};
// Opens the owner's DM thread with this lead in the Instagram app.
const chatButton = (l) => (l.channel === 'instagram' && l.username && /^[A-Za-z0-9._]{1,30}$/.test(l.username))
  ? { label: 'Open chat', url: `https://ig.me/m/${l.username}` } : null;
"""
