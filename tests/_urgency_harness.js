// Node harness: load the browser urgency.js port and answer parity queries.
// Usage: node tests/_urgency_harness.js <cases.json>  → writes results JSON to stdout.
const path = require('path');
const fs = require('fs');
require(path.join(__dirname, '..', 'frontend', 'js', 'urgency.js'));
const U = globalThis.BroomUrgency;

function iso(d) {
  return d.y + '-' + String(d.m).padStart(2, '0') + '-' + String(d.d).padStart(2, '0');
}

const cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const out = cases.map(function (c) {
  if (c.kind === 'expand') {
    const ds = U.parseSweepingCode(c.code, c.now).map(iso);
    ds.sort();
    return { id: c.id, dates: ds };
  }
  if (c.kind === 'nextdates') {
    return { id: c.id, out: U.nextDatesDesc(c.code, c.now) };
  }
  if (c.kind === 'body') {
    return { id: c.id, out: U.sweepBody(c.desc, c.time) };
  }
  if (c.kind === 'side') {
    return { id: c.id, lines: U.formatScheduleSide(c.entries, c.now) };
  }
  return { id: c.id, urgency: U.urgencyForSched(c.sched, c.now) };
});
process.stdout.write(JSON.stringify(out));
