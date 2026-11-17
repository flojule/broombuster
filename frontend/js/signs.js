// ── Street card: what applies on the street at the target ─────────────────────
// Top left: the street's name, then Sweeping: one row per side and set of days (the side
// lying higher on screen first), marked by a bar in that side's map line color, in the
// words of the posted sign (the calendar gives the dates). Under a hairline, the rules
// from /api/rules (permit area, time limit, meter, winter ban, fire zone, closure), each
// marked by its glyph in sign colors (a permit: its area's map color). Every row is at
// most two lines. The rules arrive once the
// map stops (one request per spot); the turned-on map layers' areas at the target (a
// permit area, a fire zone) show at once.
const signsEl = document.getElementById('signs');
let _signsKey = '';

// One side's tile entries as lines [{days, time}] (urgency.js formatScheduleSide: "Mon, Wed
// · 8AM–10AM", "Tue 2nd & 4th", Chicago's next dates), split at the time; one line per
// set of days, its times in order ("9–11AM, 12–2PM"). Shared with the car's page.
function sourceLines(entries, now) {
  const byDays = new Map();  // days (+ '|' when untimed: kept apart) -> times
  for (const l of BroomUrgency.formatScheduleSide(entries, now)) {
    const i = l.lastIndexOf(' · '), key = i < 0 ? `${l}|` : l.slice(0, i);
    if (!byDays.has(key)) byDays.set(key, []);
    if (i >= 0) byDays.get(key).push(l.slice(i + 3));
  }
  const start = t => BroomUrgency.timeWindow(t)?.[0] ?? 1440;
  return [...byDays].map(([key, ts]) => ({ days: key.replace(/\|$/, ''),
    time: tidyTimes(ts.sort((a, b) => start(a) - start(b)).join(', ')),
  }));
}

// Screen direction [dx, dy] in which the street at the target is drawn, at its point
// nearest the target; null for a zone.
function _streetDirection(f) {
  return nearestSegment(f, map.project(map.getCenter()))?.dir || null;
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

// A row: a mark (`bar` for sweeping, else a rule's glyph) in its color, the side when the
// sides differ, what applies, a muted detail and a "Today" / "Tomorrow" badge; `said`
// for screen readers. A side's next rows hide its name (`ghost`), keeping the text in
// line. `header`: a section's name; `divider`: the hairline above the rules.
function _rowHTML({ header, divider, side, ghost, when, color, mark, text, detail = '', said }) {
  if (header) return `<div class="sc-sec">${esc(header)}</div>`;
  if (divider) return '<hr class="sc-rule">';
  return `<div class="sc-row">${markHTML({ color, mark })}`
       + (side === undefined ? '' : `<span class="sc-side${ghost ? ' ghost' : ''}" aria-hidden="true">${esc(side)}</span>`)
       + `<span class="sr-only">${esc(said || `${text} ${detail}`.trim())}</span>`
       + `<span class="sc-text" aria-hidden="true">${esc(text)}${detail ? ` <span class="sc-detail">${_keepTimes(esc(detail))}</span>` : ''}</span>`
       + `${when ? _whenBadge(when) : ''}</div>`;
}
// Escaped text with each time range ("8am–9pm", "7 - 9AM") kept on one line.
const _TIME_RANGE = /\d{1,2}(?::\d\d)?\s*(?:[ap]\.?m\.?)?\s*[-–]\s*\d{1,2}(?::\d\d)?\s*[ap]\.?m\.?/gi;
const _keepTimes = html => html.replace(_TIME_RANGE, t => `<span class="sc-time">${t}</span>`);

// A side's verdict: today, tomorrow, safe or none.
function _sideUrgency({ entries }, now, skip) {
  return entries.length ? BroomUrgency.checkDaySweeping(entries, now, skip) : 'none';
}

// A side's sweeping rows (one per set of days, as the sign words it), each marked in the
// side's map line color; when the sides differ, the first row names the side ("North")
// and carries "Today" / "Tomorrow" when that side is swept then.
function _sweepRows(sec, now, skip) {
  const { lines, side, tag, entries } = sec, u = _sideUrgency(sec, now, skip), soon = u === 'today' || u === 'tomorrow';
  const color = URGENCY[lines.length ? u : u === 'check' ? 'check' : 'none'].line;
  const first = { mark: 'bar', color, ...(tag ? { side: tag, when: soon && u } : {}) };
  const more = { mark: 'bar', color, ...(tag ? { side: tag, ghost: true } : {}) };
  if (!lines.length) {  // none, or none left: Chicago's season is over, or its dates ran out
    const text = !entries.length ? 'No sweeping' : u === 'check' ? `No ${now.y} dates yet`
      : BroomUrgency.seasonOver(entries, now) ? 'Season over' : 'No sweeping';
    return [{ ...first, text, said: `${side}: ${text.toLowerCase()}` }];
  }
  return lines.map((l, i) => ({
    ...(i ? more : first),
    text: l.days, detail: l.time, said: `${side}: ${!i && soon ? `swept ${u}; ` : ''}sweeping ${l.days} ${l.time}`.trim(),
  }));
}

// Beside the street's name: "Today" / "Tomorrow" when the street (both sides alike, or a
// zone) is swept then. When the sides differ, each side's row carries its own.
function _whenHTML(secs, now, skip) {
  if (secs.length !== 1) return '';
  const u = _sideUrgency(secs[0], now, skip);
  return u === 'today' || u === 'tomorrow' ? _whenBadge(u) : '';
}
function _whenBadge(u) {
  return `<b class="sc-when" style="background:var(--urg-${u}-bg)">${esc(URGENCY[u].short)}</b>`;
}

// A mark: a bar (sweeping) in its color, or a rule's glyph (index.html i-<kind>).
function markHTML({ color, mark }) {
  if (mark === 'bar') return `<i class="sc-mark sc-bar" style="background:${esc(color)}" aria-hidden="true"></i>`;
  return `<i class="sc-mark sc-glyph sc-${mark}" style="--k:${esc(color)}" aria-hidden="true">${icon(mark)}</i>`;
}

// The rules' glyph colors, as street signs: green allows, red forbids, blue for snow,
// gray for meters (styles.css --rule-*, lighter in dark mode; the map layers match).
const _MARK = {
  permit: 'var(--rule-allow)',  // when the area has no color of its own
  limit:  'var(--rule-allow)',
  meter:  'var(--rule-meter)',
  winter: 'var(--rule-snow)',
  fire:   'var(--rule-ban)',
  ban:    'var(--rule-ban)',
};

// /api/rules' rules as rows: the mark of their kind, and the rule in a few words (ruleText).
function _ruleRow(r) {
  const { text, detail } = ruleText(r);
  const kind = r.theme === 'meters' ? 'meter' : r.theme === 'winter' ? 'winter' : r.theme === 'closures' ? 'ban'
    : r.theme === 'fire' ? 'fire'
    : /^Permit (area|zone) /.test(text) ? 'permit' : /\b(h|min) limit$/.test(text) ? 'limit' : 'ban';
  return { mark: kind, color: kind === 'permit' && r.color || _MARK[kind], text, detail, said: detail ? `${text}: ${detail}` : text };
}

// Rules at the target, by spot (~10 m): fetched once the map stops, zoomed in to street
// level (one request per stop; the server makes no address lookups for these), kept
// RULES_TTL_MS (closures come and go; core.js).
const RULES_ZOOM = 14;
const RULES_STREET_M = 25;  // the server's rules show within this distance of a street
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
  // The server's first (they carry the rule's words; a permit curb's area wins over the
  // area drawn around it), then the map's areas of the themes it lacks. The server's are
  // a street's: none with no street within RULES_STREET_M (a parking lane is 10-20 m off
  // the line the calendar reads; a block's middle is farther).
  const near = p || streetNear(RULES_STREET_M) !== false;
  const seen = new Set(), server = near ? targetRules(live) : [], themes = new Set(server.map(r => r.theme));
  const rules = [...server, ...overlayAreasAtTarget().filter(r => !themes.has(r.theme))]
    .filter(r => r.label && !seen.has(r.label) && seen.add(r.label)).slice(0, 4);
  const rows = [...(secs.length ? [{ header: 'Sweeping' }, ...secs.flatMap(s => _sweepRows(s, now, skip))] : []),
                ...(rules.length ? [...(secs.length ? [{ divider: true }] : []), ...rules.map(_ruleRow)] : [])];
  const street = p && p.street !== 'N/A' ? p.street : '';
  const html = rows.length
    ? `<div class="sc">${street || secs.length ? `<div class="sc-head"><span class="sc-street">${esc(street)}</span>${_whenHTML(secs, now, skip)}</div>` : ''}`
      + `${rows.map(_rowHTML).join('')}</div>`
    : '';
  if (html !== _signsKey) {
    _signsKey = html;
    signsEl.hidden = !html;
    signsEl.innerHTML = html;
  }
  // Screen readers hear the street and its first rows once the map stops (not while panning).
  const said = rows.length ? [street, ...rows.filter(r => r.said).slice(0, 3).map(r => r.said)].filter(Boolean).join('. ') : '';
  if (!live && signsLive.textContent !== said) signsLive.textContent = said;
}
const signsLive = document.getElementById('signs-live');
