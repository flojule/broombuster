// ── Street signs: one small no-parking sign per side of the street at the target ──
// Top left, in the source's own words: the data as published, a check on the
// schedule the calendar and cards derive from it. One sign per side, laid out as the
// sides lie on screen (a street running across the screen has one side above the
// other); one sign when both sides read the same, and for a zone. Each label names
// its side, with a swatch of the colour that side's line has on the map.
const signsEl = document.getElementById('signs');
let _signsKey = '';

// One side's tile entries as published: the source's words for the days, those
// sharing a time window on one line ("Every Mon, Wed · 8AM–10AM"). A dated code
// (Chicago) is a list of dates at the source: shown as the next run of them.
function sourceLines(entries, now) {
  const byTime = new Map();  // time -> [day wording]
  for (const e of entries) {
    const time = e.time && e.time !== 'N/A' ? e.time : '';
    let days = String(e.code).startsWith('DATES:') ? BroomUrgency.formatScheduleSide([e], now)[0]
             : e.desc || BroomUrgency.formatScheduleSide([e], now)[0];
    // SF's descriptions carry the time and an "(every)" of their own.
    days = (days || '').replace(/\s*\(every\)/i, '');
    if (time) days = days.replace(`, ${time}`, '');
    days = days.trim();
    if (!days) continue;
    if (!byTime.has(time)) byTime.set(time, []);
    if (!byTime.get(time).includes(days)) byTime.get(time).push(days);
  }
  return [...byTime].map(([time, days]) => {
    const text = _joinDays(days);
    return time && !text.includes(time) ? `${text} · ${time}` : text;
  });
}

// "Every Mon", "Every Tue", "Every Wed", "Every Fri" -> "Every Mon–Wed, Fri" (weekday
// order; 3+ days in a row as a range); other wordings are joined as they are.
const _WD = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
function _joinDays(days) {
  const ranks = days.map(d => _WD.indexOf(d.replace(/^Every /, '')));
  if (days.length < 2 || !days.every(d => d.startsWith('Every ')) || ranks.includes(-1)) return days.join(', ');
  ranks.sort((a, b) => a - b);
  const runs = [];
  for (let i = 0; i < ranks.length;) {
    let j = i;
    while (j + 1 < ranks.length && ranks[j + 1] === ranks[j] + 1) j++;
    runs.push(j - i >= 2 ? `${_WD[ranks[i]]}–${_WD[ranks[j]]}` : ranks.slice(i, j + 1).map(r => _WD[r]).join(', '));
    i = j + 1;
  }
  return 'Every ' + runs.join(', ');
}

// Screen direction [dx, dy] in which the street at the target is drawn, at its point
// nearest the target; null for a zone.
function _streetDirection(f) {
  const g = f.geometry;
  const parts = g.type === 'LineString' ? [g.coordinates] : g.type === 'MultiLineString' ? g.coordinates : [];
  const c = map.project(map.getCenter());
  let best = null, bestD = Infinity;
  for (const part of parts) {
    const pts = part.map(p => map.project(p));
    for (let i = 0; i + 1 < pts.length; i++) {
      const a = pts[i], b = pts[i + 1], dx = b.x - a.x, dy = b.y - a.y;
      const t = Math.max(0, Math.min(1, ((c.x - a.x) * dx + (c.y - a.y) * dy) / (dx * dx + dy * dy || 1)));
      const d = Math.hypot(a.x + t * dx - c.x, a.y + t * dy - c.y);
      if (d < bestD) { bestD = d; best = [dx, dy]; }
    }
  }
  return best;
}

// Compass side label ("North", "Southwest", ...) -> screen direction (the map never rotates).
function _compassDirection(label) {
  const l = String(label).toLowerCase();
  const v = [(l.includes('east') ? 1 : 0) - (l.includes('west') ? 1 : 0),
             (l.includes('south') ? 1 : 0) - (l.includes('north') ? 1 : 0)];
  return v[0] || v[1] ? v : null;
}

// Where the even side lies on screen (a direction), from the drawing direction and
// which side the even addresses are on, else from the side's compass name; null if unknown.
function _evenSideDirection(f) {
  const p = f.properties;
  if ('even_left' in p) {
    const d = _streetDirection(f);
    if (d) {
      const left = [d[1], -d[0]];  // left of travel, on a screen whose y points down
      return p.even_left ? left : [-left[0], -left[1]];
    }
  }
  return p.side_even ? _compassDirection(p.side_even) : null;
}

function _signHTML({ lines, entries, label }, now) {
  const u = entries.length ? BroomUrgency.checkDaySweeping(entries, now) : 'none';
  const when = u === 'today' || u === 'tomorrow' ? `<b>${URGENCY[u].short}</b>` : '';
  const plate = lines.length
    ? `<span class="sign-head">No parking</span><span class="sign-lines">${lines.map(esc).join('<br>')}</span>`
      + '<span class="sign-foot">Street sweeping</span>'
    : '<span class="sign-lines">No sweeping</span>';
  return `<div class="sign${lines.length ? '' : ' clear'}"><div class="sign-plate">${plate}</div>`
       + `<div class="sign-label"><i class="side-key" style="background:${URGENCY[u].line}"></i><span>${esc(label)}${when}</span></div></div>`;
}

// f = the feature at the target (null: none, the signs hide).
function renderSigns(f) {
  const p = f?.properties;
  const now = p && _tilesRegion ? regionNow(_tilesRegion) : null;
  let signs = [], stacked = false;
  if (now) {
    const raw = tileEntries(p);
    const sides = ['even', 'odd'].map(s => {
      const entries = raw.filter(e => e.side === s);
      return { entries, lines: sourceLines(entries, now) };
    });
    if (p.render_type === 'polygon' || sides[0].lines.join('\n') === sides[1].lines.join('\n')) {
      signs = [{ ...sides[0], label: p.render_type === 'polygon' ? 'Zone' : 'Both sides' }];
    } else {
      signs = sides.map((s, i) => ({ ...s, label: `${[p.side_even || 'Even', p.side_odd || 'Odd'][i]} side` }));
      const ev = _evenSideDirection(f);
      if (ev) {
        stacked = Math.abs(ev[1]) >= Math.abs(ev[0]);  // the sides lie above and below
        if (stacked ? ev[1] > 0 : ev[0] > 0) signs.reverse();
      }
    }
  }
  const street = p && p.street !== 'N/A' ? p.street : '';
  const html = signs.length
    ? (street ? `<div id="signs-street">${esc(street)}</div>` : '')
      + `<div class="signs-row${stacked ? ' stacked' : ''}">${signs.map(s => _signHTML(s, now)).join('')}</div>`
    : '';
  if (html === _signsKey) return;
  _signsKey = html;
  signsEl.hidden = !html;
  signsEl.innerHTML = html;
}
