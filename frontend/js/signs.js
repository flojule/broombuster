// ── Street card: what applies on the street at the target ─────────────────────
// Top left: the street's name, then one row per thing that applies there: its sweeping
// (in the source's own words, shortened; one row per side when the sides differ, the
// side lying higher on screen first, marked in that side's map line colour), then the
// parking rules from /api/rules (permit area, meter, winter ban, closure), each a short
// line with its mark. The rules arrive once the map stops (one request per spot).
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

// A screen direction as a compass point ("North", "Southwest"): the map never rotates.
const _COMPASS = ['East', 'Southeast', 'South', 'Southwest', 'West', 'Northwest', 'North', 'Northeast'];
function _compassName([dx, dy]) {
  return _COMPASS[(Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) + 8) % 8];
}

// [even, odd] side names of the street at the target by compass: the data's ("North"),
// else where each side lies on the map (even/odd data), else "Even" / "Odd". Shared
// with the calendar's day details.
function sideNames(f) {
  const p = f.properties;
  if (p.side_even) return [p.side_even, p.side_odd || 'Odd'];
  const ev = _evenSideDirection(f);
  return ev ? [_compassName(ev), _compassName([-ev[0], -ev[1]])] : ['Even', 'Odd'];
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

// A row: a mark (its colour, a letter), what applies, and a detail; `said` for screen readers.
// A row without a colour continues the one above (another time window of that side).
function _rowHTML({ heading, color, mark, text, detail = '', soon = false, said, sweep = false, ...rest }) {
  if (rest.divider) return '<hr class="sc-rule">';
  if (heading) return `<div class="sc-side" aria-hidden="true">${esc(heading)}</div>`;  // each row says its side
  return `<div class="sc-row${soon ? ' soon' : ''}"><i class="sc-mark${sweep ? ' sc-sweep' : ''}"${color ? ` style="background:${esc(color)}"` : ''} aria-hidden="true">${esc(mark)}</i>`
       + `<span class="sr-only">${esc(said || `${text} ${detail}`.trim())}</span>`
       + `<span class="sc-text" aria-hidden="true">${esc(text)}${detail ? ` <span class="sc-detail${sweep ? ' sc-time' : ''}">${esc(detail)}</span>` : ''}</span></div>`;
}

// A side's verdict: today, tomorrow, safe or none.
function _sideUrgency({ entries }, now, skip) {
  return entries.length ? BroomUrgency.checkDaySweeping(entries, now, skip) : 'none';
}

// A side's sweeping rows (one per time window): "S" once, in its map line colour; when
// the sides differ, under the side's heading ("NORTH SIDE").
function _sweepRows(sec, now, skip) {
  const { lines, side, tag } = sec, u = _sideUrgency(sec, now, skip);
  const head = tag ? [{ heading: side }] : [];
  const mark = { sweep: true, mark: _MARK.sweep.mark };
  if (!lines.length) return [...head, { ...mark, color: URGENCY.none.line, text: 'No sweeping', said: `${side}: no sweeping` }];
  return [...head, ...lines.map((l, i) => ({
    ...(i ? { sweep: true, mark: '' } : mark), color: i ? '' : URGENCY[u].line, soon: u === 'today' || u === 'tomorrow',
    text: l.days, detail: l.time, said: `${side}: sweeping ${l.days} ${l.time}`.trim(),
  }))];
}

// Beside the street's name: "Today" / "Tomorrow" when a side is swept then (the
// sooner one; with its tag when the sides differ).
function _whenHTML(secs, now, skip) {
  const soon = secs.map(s => ({ s, u: _sideUrgency(s, now, skip) })).filter(x => x.u === 'today' || x.u === 'tomorrow');
  if (!soon.length) return '';
  const { s, u } = soon.find(x => x.u === 'today') || soon[0];
  const said = secs.length > 1 && soon.length < secs.length ? `${s.side}: ` : '';
  return `<b class="sc-when" style="background:var(--urg-${u}-bg)"><span class="sr-only">${esc(said)}</span>${esc(URGENCY[u].short)}</b>`;
}

// The card's marks, also the Layers menu's legend (js/layers.js): sweeping, then the rules.
const CARD_MARKS = [
  { key: 'sweep',   mark: 'S', color: URGENCY.safe.line, name: 'Street sweeping' },
  { key: 'permit',  mark: 'P', color: '#7c3aed', name: 'Permit area' },
  { key: 'limit',   mark: 'h', color: '#6d28d9', name: 'Time limit' },
  { key: 'meter',   mark: '$', color: '#0f766e', name: 'Meter' },
  { key: 'winter',  mark: '*', color: '#0369a1', name: 'Winter ban' },
  { key: 'ban',     mark: '!', color: '#b91c1c', name: 'Closure, tow-away or ban' },
];
const _MARK = Object.fromEntries(CARD_MARKS.map(m => [m.key, m]));

// /api/rules' rules as rows: the label, and the first clause of the rule when it says
// something the label does not (hours, a limit, a rate).
function _ruleRow(r) {
  const rule = String(r.rule || '');
  let text = r.label, detail = rule.split(/;|\. /)[0].replace(/\.$/, '').trim();
  const kind = r.theme === 'meters' ? 'meter' : r.theme === 'winter' ? 'winter' : r.theme === 'closures' ? 'ban'
    : /^Permit (area|zone) /.test(text) ? 'permit' : /\bh limit$/.test(text) ? 'limit' : 'ban';
  if (kind === 'meter') {
    text = 'Metered';
    detail = detail.replace(/^Metered:\s*/i, '');
    if (!/\d/.test(detail)) detail = '';  // "pay at the meter": nothing to add
  }
  // "No oversized vehicles" + "No oversized vehicles, M–Su 12am–6am": the hours only.
  if (detail.toLowerCase().startsWith(text.toLowerCase())) detail = detail.slice(text.length).replace(/^[\s,:–-]+/, '');
  if (/exempt from the posted|follows? the (posted|signs)/i.test(detail)) detail = '';
  const { mark, color } = _MARK[kind];
  return { mark, color, text, detail, said: rule.startsWith(text) ? rule : `${text}${rule ? ': ' + rule : ''}` };
}

// Rules at the target, by spot (~10 m): fetched once the map stops, zoomed in to street
// level (one request per stop; the server makes no address lookups for these), kept
// RULES_TTL_MS (closures come and go; core.js).
const RULES_ZOOM = 14;
const _rulesCache = new Map();  // "lat,lon" -> [rules ([] while loading), fetched at]
let _rulesTimer = 0;
function _spotKey() {
  const c = map.getCenter();
  return `${c.lat.toFixed(4)},${c.lng.toFixed(4)}`;
}
function targetRules(live) {
  if (!map || map.getZoom() < RULES_ZOOM) return [];
  const key = _spotKey(), hit = _rulesCache.get(key);
  if (hit && Date.now() - hit[1] < RULES_TTL_MS) return hit[0];
  clearTimeout(_rulesTimer);
  if (live) return [];
  _rulesTimer = setTimeout(async () => {
    _rulesCache.delete(key);  // re-inserted last: the oldest go first
    _rulesCache.set(key, [[], Date.now()]);
    if (_rulesCache.size > 200) _rulesCache.delete(_rulesCache.keys().next().value);
    const [lat, lon] = key.split(',');
    const q = new URLSearchParams({ lat, lon, kind: 'car', geocode: 'false' });
    if (_tilesRegion) q.set('region', _tilesRegion);
    try {
      const res = await fetch('/api/rules?' + q);
      if (!res.ok) throw new Error(res.statusText);
      _rulesCache.set(key, [(await res.json()).rules || [], Date.now()]);
    } catch (_) { _rulesCache.delete(key); return; }
    if (_spotKey() === key) renderSigns(_signsFeature, false);
  }, 250);
  return hit ? hit[0] : [];
}

// f = the feature at the target (null: none); live: the map is moving (no new lookups).
let _signsFeature = null;
function renderSigns(f, live = false) {
  _signsFeature = f;
  const p = f?.properties;
  const now = p && _tilesRegion ? regionNow(_tilesRegion) : null;
  const skip = p && _tilesRegion ? sweepSkip(_tilesRegion, p.city) : null;
  let secs = [];
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
      // The side higher (or further left) on screen first.
      const ev = _evenSideDirection(f), names = sideNames(f);
      secs = sides.map((s, i) => ({ ...s, side: `${names[i]} side`, tag: names[i] }));
      if (ev && (Math.abs(ev[1]) >= Math.abs(ev[0]) ? ev[1] > 0 : ev[0] > 0)) secs.reverse();
    }
  }
  const seen = new Set();
  const rules = targetRules(live).filter(r => !seen.has(r.label) && seen.add(r.label)).slice(0, 4);
  // The rules apply to the whole block: a rule line sets them apart from a side's heading.
  const rows = [...secs.flatMap(s => _sweepRows(s, now, skip)),
                ...(secs.length && rules.length ? [{ divider: true }] : []), ...rules.map(_ruleRow)];
  const street = p && p.street !== 'N/A' ? p.street : '';
  const html = rows.length
    ? `<div class="sc">${street || secs.length ? `<div class="sc-head"><span class="sc-street">${esc(street)}</span>${_whenHTML(secs, now, skip)}</div>` : ''}`
      + `${rows.map(_rowHTML).join('')}</div>`
    : '';
  if (html === _signsKey) return;
  _signsKey = html;
  signsEl.hidden = !html;
  signsEl.innerHTML = html;
}
