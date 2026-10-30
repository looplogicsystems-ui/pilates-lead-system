// Test harness for the JavaScript inside n8n Code nodes.
//
// Reads the GENERATED workflow files (workflows/*.json), so it tests exactly
// what gets pushed. Mimics n8n 2.34's Code-node sandbox: URLSearchParams, URL,
// fetch, structuredClone and AbortController do NOT exist there (a connect
// flow once failed live on that while plain-Node tests passed), so they are
// hidden here too. `this.helpers.httpRequest` is faked per test.
//
//   node tests/run.js            # every *.test.js
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const ROOT = path.join(__dirname, '..');
const SANDBOX_MISSING = ['URLSearchParams', 'URL', 'fetch', 'structuredClone', 'AbortController'];
const AsyncFn = Object.getPrototypeOf(async function () {}).constructor;
const cache = {};

function codeOf(file, node) {
  const w = cache[file] || (cache[file] = JSON.parse(fs.readFileSync(path.join(ROOT, 'workflows', file), 'utf8')));
  const n = w.nodes.find((x) => x.name === node);
  if (!n) throw new Error(`no node "${node}" in ${file}`);
  return n.parameters.jsCode;
}

// nodes: { 'Node Name': [items...] }  or  { 'Node Name': { branches: [[...], [...]] } }
// item:  { 'Node Name': json }  for $('Node').item in per-item mode
async function run(file, node, items, { nodes = {}, item = {}, http, env = {}, perItem = false } = {}) {
  const $input = { first: () => ({ json: items[0] }), all: () => items.map((j) => ({ json: j })), item: { json: items[0] } };
  const $ = (n) => {
    const v = nodes[n];
    const branch = (b) => (v && v.branches ? (v.branches[b] || []) : (b ? [] : (v || [])));
    return { first: () => ({ json: branch(0)[0] }), all: (b = 0) => branch(b).map((j) => ({ json: j })), item: { json: item[n] } };
  };
  const ctx = { helpers: { httpRequest: http || (async () => { throw new Error('unexpected HTTP call in test'); }) } };
  const f = new AsyncFn('$input', '$', '$env', '$json', 'require', ...SANDBOX_MISSING, codeOf(file, node));
  const out = await f.call(ctx, $input, $, env, items[0], require);
  if (Array.isArray(out)) return out.map((i) => i.json);
  return out && out.json ? [out.json] : [];
}

let pass = 0, fail = 0;
const ok = (name, cond) => { cond ? pass++ : fail++; console.log(`${cond ? 'PASS' : 'FAIL'} ${name}`); };
const summary = () => ({ pass, fail });

const TOKEN_KEY = 'k'.repeat(64);
const enc = (plain, key = TOKEN_KEY) => {
  const iv = crypto.randomBytes(12);
  const c = crypto.createCipheriv('aes-256-gcm', crypto.createHash('sha256').update(key).digest(), iv);
  const ct = Buffer.concat([c.update(plain), c.final()]);
  return Buffer.concat([Buffer.from([1]), iv, c.getAuthTag(), ct]).toString('base64');
};
const dec = (b64, key = TOKEN_KEY) => {
  const b = Buffer.from(b64, 'base64');
  const d = crypto.createDecipheriv('aes-256-gcm', crypto.createHash('sha256').update(key).digest(), b.subarray(1, 13));
  d.setAuthTag(b.subarray(13, 29));
  return Buffer.concat([d.update(b.subarray(29)), d.final()]).toString();
};
const reply = (status, body) => ({ statusCode: status, body: JSON.stringify(body) });

module.exports = { run, ok, summary, enc, dec, reply, TOKEN_KEY, crypto };
