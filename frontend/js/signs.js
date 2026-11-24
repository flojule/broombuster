// ── Street card: what applies on the street at the target ─────────────────────
// Top left: the street's name (after a broom when it has sweeping), then one row per side
// and set of days (the side lying higher on screen first), in the words of the posted
// sign (the calendar gives the dates), marked by one bar down each side's rows in its map
// line color, each side's first row its verdict ("OK until Tue 9AM", "No parking now,
// until 11AM"). Under a hairline, the rules from /api/rules (permit area, time limit,
// meter, winter ban, fire zone, closure), each marked by its glyph in sign colors (a
// permit: its area's map color). Every row is at most two lines. The rules arrive once
// the map stops (one request per spot); the turned-on map layers' areas at the target (a
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
// sides differ, what applies and a muted detail; `said` for screen readers. A side's
// verdict row (`verdict`: {u, text}) puts it on a badge in its urgency's color when the
// side is swept today or tomorrow. A side's next rows (`cont`) run its bar on and hide
// its name (`ghost`), keeping the text in line. `divider`: the hairline above the rules.
function _rowHTML({ divider, cont, side, ghost, verdict, color, mark, text, detail = '', said }) {
  if (divider) return '<hr class="sc-rule">';
  const body = !verdict ? `${esc(text)}${detail ? ` <span class="sc-detail">${_keepTimes(esc(detail))}</span>` : ''}`
    : isSoon(verdict.u) ? `<b class="sc-when" style="background:var(--urg-${verdict.u}-bg)">${esc(text)}</b>`
    : `<span class="sc-ok">${esc(text)}</span>`;
  return `<div class="sc-row${cont ? ' sc-cont' : ''}${verdict ? ' sc-verdict' : ''}">${markHTML({ color, mark })}`
       + (side === undefined ? '' : `<span class="sc-side${ghost ? ' ghost' : ''}" aria-hidden="true">${esc(side)}</span>`)
       + `<span class="sr-only">${esc(said || `${text} ${detail}`.trim())}</span>`
       + `<span class="sc-text" aria-hidden="true">${body}</span></div>`;
}
// Escaped text with each time range ("8am–9pm", "7 - 9AM") kept on one line.
const _TIME_RANGE = /\d{1,2}(?::\d\d)?\s*(?:[ap]\.?m\.?)?\s*[-–]\s*\d{1,2}(?::\d\d)?\s*[ap]\.?m\.?/gi;
const _keepTimes = html => html.replace(_TIME_RANGE, t => `<span class="sc-time">${t}</span>`);

// A side's verdict: today, tomorrow, safe or none.
function _sideUrgency({ entries }, now, skip) {
  return entries.length ? BroomUrgency.checkDaySweeping(entries, now, skip) : 'none';
}

// A side's rows, each marked in the side's map line color: its verdict, then one per set
// of days (as the sign words it); the first names the side ("North") when the sides differ.
function _sweepRows(sec, now, skip) {
  const { lines, side, tag, entries } = sec, u = _sideUrgency(sec, now, skip);
  const color = `var(--line-${lines.length ? u : u === 'check' ? 'check' : 'none'})`;
  const first = { mark: 'bar', color, ...(tag ? { side: tag } : {}) };
  const more = { mark: 'bar', color, cont: true, ...(tag ? { side: tag, ghost: true } : {}) };
  if (!lines.length) {  // none, or none left: Chicago's season is over, or its dates ran out
    const text = !entries.length ? 'No sweeping' : u === 'check' ? noDatesText(entries, now)
      : BroomUrgency.seasonOver(entries, now) ? 'Season over' : 'No sweeping';
    return [{ ...first, text, said: `${side}: ${text.toLowerCase()}` }];
  }
  const v = u === 'check' ? null : sideVerdict(sec.rows, entries, now, skip, u);
  const rows = lines.map((l, i) => ({
    ...(i || v ? more : first), text: l.days, detail: l.time, said: `${side}: sweeping ${l.days} ${l.time}`.trim(),
  }));
  return v ? [{ ...first, verdict: v, text: v.text, said: `${side}: ${v.text}` }, ...rows] : rows;
}

// Can I park here, until when: {u, text} for a side (or both): "No parking now, until
// 11AM" while its window is on, "OK until 9AM" (later today), "OK until tomorrow 9AM",
// "OK until Thu 9AM"; null when nothing is known within 60 days. `rows`: [even, odd] as
// /check gives them ([code, desc, time]); `u`: its checkDaySweeping verdict.
function sideVerdict(rows, entries, now, skip, u) {
  const d = BroomUrgency.sweepDeadline(entries, now, skip);
  if (d?.state === 'running') return { u, text: `No parking now, until ${d.at}` };
  if (d?.state === 'today') return { u, text: 'Swept today: see the sign' };
  if (d && (d.state === 'by' || d.state === 'tomorrow')) {
    const later = d.state === 'by' && BroomUrgency.sweptLaterToday(entries, now, skip);
    const at = !d.at ? 'tomorrow' : d.at === 'midnight' || later ? d.at : `tomorrow ${d.at}`;
    return { u, text: `OK until ${at}` };
  }
  const w = clearUntilWords(rows[0], rows[1], now, skip, entries);
  return w && { u, text: w.long.replace(/^Clear until/, 'OK until') };
}

// Nothing due: until when a street's sides (rows as /check gives them; [] for a side that
// does not count) stay clear, from the next sweep within 60 days ("Clear until Thu 9AM";
// a week or more away, with the date; a sweep at midnight is the night before's: "Clear
// until Sun midnight"); "Season over" past a city's last date; null when none. {short, long}.
// Shared with the car's page (js/cars.js clearUntilText).
function clearUntilWords(even, odd, now, skip, entries) {
  const todayK = BroomUrgency.dayKey(now);
  let day = BroomUrgency.sweepDays(even, odd, now, BroomUrgency.addDays(now, 60), skip)
    .find(d => BroomUrgency.dayKey(d) > todayK);
  if (!day) return BroomUrgency.seasonOver(entries, now) ? { short: 'Season over', long: 'Season over' } : null;
  const starts = day.items.map(it => BroomUrgency.timeWindow(it.time)?.[0]).filter(m => m != null);
  const at = starts.length ? ` ${BroomUrgency.clockText(Math.min(...starts))}` : '';
  if (at === ' midnight') day = BroomUrgency.addDays(day, -1);
  const far = BroomUrgency.diffDays(now, day) >= 7;
  const wd = far ? BroomUrgency.dayLabel(day) : BroomUrgency.WEEKDAYS[BroomUrgency.weekday(day)];
  const short = far ? BroomUrgency.dayLabel(day).replace(/^\S+ /, '') : wd;  // "Nov 22" a week or more away
  return { short: `Clear until ${short}`, long: `Clear until ${wd}${at}` };
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
    : r.theme === 'fire' ? 'fire' : r.theme === 'ev' ? 'ev' : r.theme === 'markets' ? 'market'
    : /^Permit (area|zone) /.test(text) ? 'permit' : /\b(h|min) limit$/.test(text) ? 'limit' : 'ban';
  return { mark: kind, color: (kind === 'permit' || kind === 'ev' || kind === 'market') && r.color || _MARK[kind], text, detail, said: detail ? `${text}: ${detail}` : text };
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

// A tile feature's sides, parsed and worded once per schedule and minute (the card is
// re-read every frame while the map moves; a street's segments share one schedule):
// {sides: [{entries, rows, lines}] (even, odd), same: both alike on screen}.
const _sidesMemo = new Map();
function _tileSides(p, now) {
  const key = `${_tilesRegion}|${p.city}|${p.sched}|${BroomUrgency.dayKey(now)}|${now.min}`;
  let hit = _sidesMemo.get(key);
  if (!hit) {
    const raw = tileEntries(p);
    const sides = ['even', 'odd'].map(s => {
      const entries = raw.filter(e => e.side === s);
      return { entries, rows: entries.map(e => [e.code, e.desc || '', e.time || '']), lines: sourceLines(entries, now) };
    });
    hit = { sides, same: JSON.stringify(sides[0].lines) === JSON.stringify(sides[1].lines), rows: new Map() };
    if (_sidesMemo.size >= 64) _sidesMemo.delete(_sidesMemo.keys().next().value);
    _sidesMemo.set(key, hit);
  }
  return hit;
}

// The street's sections ({entries, rows: [even, odd], lines, side, tag}) and their rows,
// in screen order: one section where the sides read alike (or a zone), else one per side,
// the side higher (or further left) on screen first.
function _streetRows(f, now, skip) {
  const p = f.properties, memo = _tileSides(p, now), { sides, same } = memo;
  let secs, order = '';
  if (p.render_type === 'polygon' || same) {
    // Alike on screen; the verdict still reads both sides' entries (codes can differ).
    secs = [{ ...sides[0], entries: [...sides[0].entries, ...sides[1].entries], rows: [sides[0].rows, sides[1].rows],
              side: p.render_type === 'polygon' ? 'Zone' : 'Both sides', tag: '' }];
  } else {
    const ev = _evenSideDirection(f), names = sideNames(f);
    secs = sides.map((s, i) => ({ ...s, rows: i ? [[], s.rows] : [s.rows, []], side: `${names[i]} side`, tag: names[i] }));
    if (ev && (Math.abs(ev[1]) >= Math.abs(ev[0]) ? ev[1] > 0 : ev[0] > 0)) secs.reverse();
    order = secs.map(s => s.tag).join('|');
  }
  if (!memo.rows.has(order)) memo.rows.set(order, secs.flatMap(s => _sweepRows(s, now, skip)));
  return { secs, rows: memo.rows.get(order) };
}

// The server's rules at the target: a street's only (none with no street within
// RULES_STREET_M: a parking lane is 10-20 m off the line the calendar reads; a block's
// middle is farther). While the map moves, only those already fetched for this spot
// (no lookup of a street nearby either).
function _serverRules(p, live) {
  if (live) {
    clearTimeout(_rulesTimer);
    const hit = map && map.getZoom() >= RULES_ZOOM && _rulesCache.get(_spotKey());
    if (!hit || !hit[0].length) return [];
  }
  return p || streetNear(RULES_STREET_M) !== false ? targetRules(live) : [];
}

// f = the feature at the target (null: none); live: the map is moving (no new lookups).
let _signsFeature = null;
function renderSigns(f, live = false) {
  _signsFeature = f;
  const p = f?.properties;
  const now = p && _tilesRegion ? regionNow(_tilesRegion) : null;
  const skip = p && _tilesRegion ? sweepSkip(_tilesRegion, p.city) : null;
  const { secs, rows: sweep } = now ? _streetRows(f, now, skip) : { secs: [], rows: [] };
  // A marker at the target first (a charger, a market), then the server's (they carry the
  // rule's words; a permit curb's area wins over the area drawn around it), then the map's
  // areas of the themes it lacks.
  const seen = new Set(), server = _serverRules(p, live), themes = new Set(server.map(r => r.theme));
  const rules = [...overlayMarkersAtTarget(), ...server, ...overlayAreasAtTarget().filter(r => !themes.has(r.theme))]
    .filter(r => r.label && !seen.has(r.label) && seen.add(r.label)).slice(0, 4);
  const rows = [...sweep, ...(rules.length ? [...(secs.length ? [{ divider: true }] : []), ...rules.map(_ruleRow)] : [])];
  const street = p && p.street !== 'N/A' ? p.street : '';
  const broom = secs.length ? markHTML({ mark: 'broom', color: 'var(--muted)' }) : '';
  const html = rows.length
    ? `<div class="sc">${street || secs.length ? `<div class="sc-head">${broom}<span class="sc-street">${esc(street)}</span></div>` : ''}`
      + `${rows.map(_rowHTML).join('')}</div>`
    : '';
  if (html !== _signsKey) {
    _signsKey = html;
    signsEl.hidden = !html;
    signsEl.innerHTML = html;
    signsEl.setAttribute('aria-label', street ? `${street}, at the target` : 'Street at the target');
    fitSigns();
  }
  if (placing && !live) _placeBannerStreet(f);  // read out once the map stops (role=status)
  // Screen readers hear the street and its first rows once the map stops (not while panning).
  const said = rows.length ? [street, ...rows.filter(r => r.said).slice(0, 3).map(r => r.said)].filter(Boolean).join('. ') : '';
  if (!live && signsLive.textContent !== said) signsLive.textContent = said;
}
const signsLive = document.getElementById('signs-live');

// Short screens: the card keeps its verdicts when it would reach the target (styles.css
// #signs.compact); the connection chip sits under it (--signs-h). Measured once per frame,
// in the next one (a new card, a resize and the sheet moving can all ask in one frame),
// so the card's layout is not forced while its rows are written.
let _fitRaf = 0;
function fitSigns() {
  if (!_fitRaf) _fitRaf = requestAnimationFrame(() => { _fitRaf = 0; _fitSigns(); });
}
function _fitSigns() {
  signsEl.classList.remove('compact');
  const box = signsEl.hidden ? null : signsEl.getBoundingClientRect();
  if (box && !isDesktop()) {
    const area = document.getElementById('map-container').getBoundingClientRect();
    const targetTop = area.top + (area.height - mapPadding().bottom) / 2 - 14;
    if (box.bottom > targetTop) signsEl.classList.add('compact');
  }
  const h = signsEl.hidden ? 0 : signsEl.offsetHeight + 6;
  document.documentElement.style.setProperty('--signs-h', h + 'px');
}
window.addEventListener('resize', fitSigns);

// Placing: the banner names the street the target is on ("On Valencia St"), or says there
// is none, so the car is never matched to a street silently.
function _placeBannerStreet(f) {
  const el = placeBannerText.querySelector('.pb-street');
  if (!el) return;
  const street = f?.properties?.street;
  const text = map && map.getZoom() < PLACE_ZOOM ? 'Zoom in to the spot'
    : f ? (street && street !== 'N/A' ? `On ${street}` : 'On a zone') : 'No street here';
  if (el.textContent !== text) el.textContent = text;
}
