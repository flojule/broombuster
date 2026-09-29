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
    return { id: c.id, dates: U.datesInRange(c.code, c.start, c.end).map(iso) };
  }
  if (c.kind === 'tables') {
    return { id: c.id, weekdays: U.WEEKDAY_CODES, noSweep: U.NO_SWEEP_CODES };
  }
  if (c.kind === 'both') {
    return { id: c.id, lines: U.formatBothSides(c.even, c.odd, c.now, c.carSide, c.labels) };
  }
  if (c.kind === 'days') {
    return { id: c.id, days: U.sweepDays(c.even, c.odd, c.start, c.end).map(function (d) {
      return [iso(d), d.items.map(function (it) { return [it.side, it.time]; })];
    }) };
  }
  if (c.kind === 'body') {
    return { id: c.id, out: U.sweepBody(c.desc, c.time) };
  }
  if (c.kind === 'side') {
    return { id: c.id, lines: U.formatScheduleSide(c.entries, c.now) };
  }
  return { id: c.id, urgency: U.checkDaySweeping(JSON.parse(c.sched), c.now) };
});
process.stdout.write(JSON.stringify(out));
