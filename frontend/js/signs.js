// ── Street sign: the sweeping rules of the street at the target, as one small sign ──
// Top left, in the source's own words (shortened): the data as published, a check on
// the schedule the calendar and cards derive from it. "No parking" and "Street sweeping"
// once; inside, one section per side of the street, laid out as the sides lie on screen
// (a street running across the screen has one side above the other) and outlined in the
// colour that side's line has on the map. One section when both sides read the same,
// and for a zone.
const signsEl = document.getElementById('signs');
let _signsKey = '';

// "8:00 AM - 11 :00 AM" / "8AM–10AM" -> "8–11AM" (one AM/PM when both share it, no ":00");
// other wordings are kept.
const _TIME_RE = /^\s*(\d{1,2})\s*(?::\s*(\d\d))?\s*([AP])\.?\s*M\.?\s*[-–·]\s*(\d{1,2})\s*(?::\s*(\d\d))?\s*([AP])\.?\s*M\.?\s*$/i;
function shortTime(t) {
  const m = _TIME_RE.exec(t);
  if (!m) return t;
  const hm = (h, min) => h + (min && min !== '00' ? ':' + min : '');
  const [a, b] = [m[3].toUpperCase() + 'M', m[6].toUpperCase() + 'M'];
  return a === b ? `${hm(m[1], m[2])}–${hm(m[4], m[5])}${b}` : `${hm(m[1], m[2])}${a}–${hm(m[4], m[5])}${b}`;
}

// One side's tile entries as published, shortened: [{days, time}], one per time window,
// with the days sharing it ("Mon–Wed, Fri"; weekly is the norm, so no "Every"). A dated
// code (Chicago) is a list of dates at the source: shown as the next run of them.
function sourceLines(entries, now) {
  const byTime = new Map();  // time -> [day wording]
  for (const e of entries) {
    const raw = e.time && !/^n\/?a$/i.test(e.time) ? e.time : '';
    let days = String(e.code).startsWith('DATES:') ? BroomUrgency.formatScheduleSide([e], now)[0]
             : e.desc || BroomUrgency.formatScheduleSide([e], now)[0];
    // SF's descriptions carry the time and an "(every)" of their own.
    days = (days || '').replace(/\s*\(every\)/i, '');
    if (raw) days = days.replace(`, ${raw}`, '');
    days = days.trim();
    if (!days) continue;
    const time = shortTime(raw);
    if (!byTime.has(time)) byTime.set(time, []);
    if (!byTime.get(time).includes(days)) byTime.get(time).push(days);
  }
  return [...byTime].map(([time, days]) => {
    const text = _joinDays(days);
    return { days: text, time: time && !text.includes(time) ? time : '' };
  });
}

// "Every Mon", "Every Tue", "Every Wed", "Every Fri" -> "Mon–Wed, Fri" (weekday order;
// 3+ days in a row as a range); other wordings are joined as they are, minus "Every".
const _WD = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
function _joinDays(days) {
  const bare = days.map(d => d.replace(/^Every /, ''));
  const ranks = bare.map(d => _WD.indexOf(d));
  if (days.length < 2 || ranks.includes(-1)) return bare.join(', ');
  ranks.sort((a, b) => a - b);
  const runs = [];
  for (let i = 0; i < ranks.length;) {
    let j = i;
    while (j + 1 < ranks.length && ranks[j + 1] === ranks[j] + 1) j++;
    runs.push(j - i >= 2 ? `${_WD[ranks[i]]}–${_WD[ranks[j]]}` : ranks.slice(i, j + 1).map(r => _WD[r]).join(', '));
    i = j + 1;
  }
  return runs.join(', ');
}

// A side's short tag: "N", "SW" for a compass name, else its name ("Even").
function _sideTag(label) {
  const l = String(label).toLowerCase();
  const tag = (l.match(/north|south/)?.[0]?.[0] || '') + (l.match(/east|west/)?.[0]?.[0] || '');
  return tag ? tag.toUpperCase() : label;
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

// One section: the side's tag (none for both sides or a zone), its lines, and the outline
// in its map colour (thicker today and tomorrow). Screen readers get the verdict in words.
function _sectionHTML({ lines, entries, side, tag }, now) {
  const u = entries.length ? BroomUrgency.checkDaySweeping(entries, now) : 'none';
  const body = lines.length
    ? lines.map(l => `<span class="sign-days">${esc(l.days)}</span>${l.time ? `<span class="sign-time">${esc(l.time)}</span>` : ''}`).join('')
    : '<span class="sign-days">No sweeping</span>';
  const said = `${side}: ${lines.length ? lines.map(l => `${l.days} ${l.time}`.trim()).join('; ') : 'no sweeping'}`
             + (u === 'today' || u === 'tomorrow' ? `; sweeping ${URGENCY[u].short.toLowerCase()}` : '');
  return `<div class="sign-sec${u === 'today' || u === 'tomorrow' ? ' soon' : ''}${lines.length ? '' : ' clear'}" style="--u:${URGENCY[u].line}">`
       + `<span class="sr-only">${esc(said)}</span><span aria-hidden="true">`
       + (tag ? `<span class="sign-tag">${esc(tag)}</span>` : '') + `${body}</span></div>`;
}

// f = the feature at the target (null: none, the sign hides).
function renderSigns(f) {
  const p = f?.properties;
  const now = p && _tilesRegion ? regionNow(_tilesRegion) : null;
  let secs = [], stacked = false;
  if (now) {
    const raw = tileEntries(p);
    const sides = ['even', 'odd'].map(s => {
      const entries = raw.filter(e => e.side === s);
      return { entries, lines: sourceLines(entries, now) };
    });
    const same = JSON.stringify(sides[0].lines) === JSON.stringify(sides[1].lines);
    if (p.render_type === 'polygon' || same) {
      secs = [{ ...sides[0], side: p.render_type === 'polygon' ? 'Zone' : 'Both sides', tag: '' }];
    } else {
      secs = sides.map((s, i) => {
        const label = [p.side_even || 'Even', p.side_odd || 'Odd'][i];
        return { ...s, side: `${label} side`, tag: _sideTag(label) };
      });
      const ev = _evenSideDirection(f);
      if (ev) {
        stacked = Math.abs(ev[1]) >= Math.abs(ev[0]);  // the sides lie above and below
        if (stacked ? ev[1] > 0 : ev[0] > 0) secs.reverse();
      }
    }
  }
  const street = p && p.street !== 'N/A' ? p.street : '';
  const any = secs.some(s => s.lines.length);
  const html = secs.length
    ? (street ? `<div id="signs-street">${esc(street)}</div>` : '')
      + `<div class="sign${stacked ? ' stacked' : ''}${any ? '' : ' clear'}">`
      + (any ? '<span class="sign-head">No parking</span>' : '')
      + `<div class="sign-secs">${secs.map(s => _sectionHTML(s, now)).join('')}</div>`
      + (any ? '<span class="sign-foot">Street sweeping</span>' : '') + '</div>'
    : '';
  if (html === _signsKey) return;
  _signsKey = html;
  signsEl.hidden = !html;
  signsEl.innerHTML = html;
}
