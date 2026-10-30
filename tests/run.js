// Runs every tests/*.test.js in order; exits non-zero if any check failed.
const fs = require('fs');
const path = require('path');
(async () => {
  for (const f of fs.readdirSync(__dirname).filter((x) => x.endsWith('.test.js')).sort()) {
    console.log(`\n== ${f}`);
    await require(path.join(__dirname, f))();
  }
  const { pass, fail } = require('./harness').summary();
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exitCode = fail ? 1 : 0;
})();
