// Calls BroomUrgency functions for tests/test_datetime.py.
// stdin: [[fn, [args...]], ...]  →  stdout: [result, ...]
// A `{"$date": iso}` argument becomes a JS Date; `{"$now": [tz, iso]}` that instant's
// nowForTimeZone clock.
const path = require('path');
require(path.join(__dirname, '..', 'frontend', 'js', 'urgency.js'));
const U = globalThis.BroomUrgency;

const revive = a => {
  if (a && typeof a === 'object' && '$date' in a) return new Date(a.$date);
  if (a && typeof a === 'object' && '$now' in a) return U.nowForTimeZone(a.$now[0], new Date(a.$now[1]));
  return a;
};
const calls = JSON.parse(require('fs').readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(calls.map(([fn, args]) => {
  const r = U[fn](...args.map(revive));
  return r === undefined ? null : r;
})));
