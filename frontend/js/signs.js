// ── Street card: what applies on the street at the target ─────────────────────
// Top left: the street's name, then one row per thing that applies there: its sweeping
// (in the source's own words, shortened; one row per side when the sides differ, the
// side lying higher on screen first, marked in that side's map line colour), then the
// parking rules from /api/rules (permit area, meter, winter ban, closure), each a short
// line with its mark. The rules arrive once the map stops (one request per spot); the
// turned-on map layers' areas at the target (a permit area, a fire zone) show at once.
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
function _rowHTML({ heading, when, color, mark, text, detail = '', said, sweep = false, ...rest }) {
  if (rest.divider) return '<hr class="sc-rule">';
  if (heading) return `<div class="sc-side" aria-hidden="true">${esc(heading)}${when ? _whenBadge(when) : ''}</div>`;  // each row says its side
  return `<div class="sc-row">${markHTML({ color, mark, sweep })}`
       + `<span class="sr-only">${esc(said || `${text} ${detail}`.trim())}</span>`
       + `<span class="sc-text" aria-hidden="true">${esc(text)}${detail ? ` <span class="sc-detail${sweep ? ' sc-time' : ''}">${esc(detail)}</span>` : ''}</span></div>`;
}

// A side's verdict: today, tomorrow, safe or none.
function _sideUrgency({ entries }, now, skip) {
  return entries.length ? BroomUrgency.checkDaySweeping(entries, now, skip) : 'none';
}

// A side's sweeping rows (one per time window): "S" once, in its map line colour; when
// the sides differ, under the side's heading ("NORTH SIDE"), which carries "Today" /
// "Tomorrow" when that side is swept then.
function _sweepRows(sec, now, skip) {
  const { lines, side, tag } = sec, u = _sideUrgency(sec, now, skip), soon = u === 'today' || u === 'tomorrow';
  const head = tag ? [{ heading: side, when: soon && u }] : [];
  const mark = { sweep: true, mark: _MARK.sweep.mark };
  if (!lines.length) return [...head, { ...mark, color: URGENCY.none.line, text: 'No sweeping', said: `${side}: no sweeping` }];
  return [...head, ...lines.map((l, i) => ({
    ...(i ? { sweep: true, mark: '' } : mark), color: i ? '' : URGENCY[u].line,
    text: l.days, detail: l.time, said: `${side}: ${!i && soon ? `swept ${u}; ` : ''}sweeping ${l.days} ${l.time}`.trim(),
  }))];
}

// Beside the street's name: "Today" / "Tomorrow" when the street (both sides alike, or a
// zone) is swept then. When the sides differ, each side's heading carries its own.
function _whenHTML(secs, now, skip) {
  if (secs.length !== 1) return '';
  const u = _sideUrgency(secs[0], now, skip);
  return u === 'today' || u === 'tomorrow' ? _whenBadge(u) : '';
}
function _whenBadge(u) {
  return `<b class="sc-when" style="background:var(--urg-${u}-bg)">${esc(URGENCY[u].short)}</b>`;
}

// A mark: a letter on a colour (dark on a light one: a city's pale permit colours).
function markHTML({ color, mark, sweep = false }) {
  const light = !sweep && _isLight(color);
  return `<i class="sc-mark${sweep ? ' sc-sweep' : ''}${light ? ' light' : ''}"${color ? ` style="background:${esc(color)}"` : ''}`
       + ` aria-hidden="true">${esc(mark)}</i>`;
}
function _isLight(color) {
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(color || '');
  return !!m && (0.299 * parseInt(m[1], 16) + 0.587 * parseInt(m[2], 16) + 0.114 * parseInt(m[3], 16)) > 165;
}

// The card's marks, also the Layers menu's legend (js/layers.js; a mark with a `layer`
// only where the region has it): sweeping, then the rules, in their map layer's colours
// (a permit area: its own colour on the map, LAYER_STYLE.key).
const CARD_MARKS = [
  { key: 'sweep',   mark: 'S', color: URGENCY.safe.line, name: 'Street sweeping' },
  { key: 'permit',  mark: 'P', color: LAYER_STYLE.parking.key, name: 'Permit area', layer: 'parking' },
  { key: 'limit',   mark: 'h', color: '#6d28d9', name: 'Time limit', layer: 'parking' },
  { key: 'meter',   mark: '$', color: LAYER_STYLE.meters.color, name: 'Meter', layer: 'meters' },
  { key: 'winter',  mark: '*', color: LAYER_STYLE.winter.color, name: 'Winter ban', layer: 'winter' },
  { key: 'fire',    mark: 'F', color: LAYER_STYLE.fire.color, name: 'Fire zone', layer: 'fire' },
  { key: 'ban',     mark: '!', color: LAYER_STYLE.closures.color, name: 'Closure or ban' },
];
const _MARK = Object.fromEntries(CARD_MARKS.map(m => [m.key, m]));

// /api/rules' rules as rows: the mark of their kind, and the rule in a few words (ruleText).
function _ruleRow(r) {
  const { text, detail } = ruleText(r);
  const kind = r.theme === 'meters' ? 'meter' : r.theme === 'winter' ? 'winter' : r.theme === 'closures' ? 'ban'
    : r.theme === 'fire' ? 'fire'
    : /^Permit (area|zone) /.test(text) ? 'permit' : /\b(h|min) limit$/.test(text) ? 'limit' : 'ban';
  const { mark } = _MARK[kind], color = kind === 'permit' && r.color || _MARK[kind].color;
  return { mark, color, text, detail, said: detail ? `${text}: ${detail}` : text };
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
  // The server's first (they carry the rule's words), then the map's areas it lacks.
  const seen = new Set();
  const rules = [...targetRules(live), ...overlayAreasAtTarget()]
    .filter(r => r.label && !seen.has(r.label) && seen.add(r.label)).slice(0, 4);
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
