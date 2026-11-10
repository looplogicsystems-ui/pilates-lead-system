// Leads and studio owners can be any gender: nothing the system sends, shows
// or tells the AI may assume "she". Scans every generated workflow, so a new
// email line, button or prompt with she/her fails here before it ships.
// (A studio's own lines, like its escalation line, live in the database.)
const fs = require('fs');
const path = require('path');
const { ok } = require('./harness');
const WORD = /\b(she|her|hers|herself)\b|she['’]s/i;
module.exports = async () => {
  const dir = path.join(__dirname, '..', 'workflows');
  for (const f of fs.readdirSync(dir).filter((x) => x.endsWith('.json')).sort()) {
    const hits = [];
    const walk = (v, where) => {
      if (typeof v === 'string') {
        v.split('\n').forEach((line) => { if (WORD.test(line)) hits.push(`${where}: ${line.trim().slice(0, 120)}`); });
      } else if (v && typeof v === 'object') {
        for (const [k, x] of Object.entries(v)) walk(x, where);
      }
    };
    for (const n of JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8')).nodes || []) walk(n.parameters, n.name);
    ok(`${f}: no she/her wording` + (hits.length ? `\n      ${hits.join('\n      ')}` : ''), hits.length === 0);
  }
};
