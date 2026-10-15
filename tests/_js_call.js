// Calls BroomUrgency functions for tests/test_datetime.py.
// stdin: [[fn, [args...]], ...]  →  stdout: [result, ...]
// A `{"$date": iso}` argument becomes a JS Date.
const path = require('path');
require(path.join(__dirname, '..', 'frontend', 'js', 'urgency.js'));
const U = globalThis.BroomUrgency;

const revive = a => (a && typeof a === 'object' && '$date' in a) ? new Date(a.$date) : a;
const calls = JSON.parse(require('fs').readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(calls.map(([fn, args]) => {
  const r = U[fn](...args.map(revive));
  return r === undefined ? null : r;
})));
