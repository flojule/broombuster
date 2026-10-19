// ── Calendar tab: one sweep calendar ──────────────────────────────────────────
// Layers: the street under the target (day tint), every car with a schedule (dot in
// the car's colour), every home's trash days (dot in the home colour) and the no-sweep
// holidays of those streets' cities. Days are {y, m, d}; arithmetic and weekday come
// from urgency.js.
const { addDays: _ymdAdd, dayKey: _ymdKey, weekday: _dow, dayLabel: _dayLabel, WEEKDAYS } = BroomUrgency;
const _WD1 = WEEKDAYS.map(w => w[0]);
const _MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
                 'August', 'September', 'October', 'November', 'December'];

// Region-local "today" for the map's tile region (browser tz before one loads).
function calNow() { return regionNow(_tilesRegion || _region); }

// key -> text for sweep days in [start, end] (holidays of the street's city excluded).
// `side` limits to one street side.
function sweepDayMap(sched, start, end, side = null) {
  const out = new Map();
  if (!sched) return out;
  for (const day of BroomUrgency.sweepDays(sched.schedule_even, sched.schedule_odd, start, end, schedSkip(sched))) {
    const items = side ? day.items.filter(it => it.side === side) : day.items;
    if (items.length) out.set(_ymdKey(day), BroomUrgency.dayText(items, sched.side_labels));
  }
  return out;
}
function _keyDay(k) { return { y: Math.floor(k / 10000), m: Math.floor(k / 100) % 100, d: k % 100 }; }

// /check-shaped schedule for the street under the target (tile entries -> [code, desc, time]).
function pointerSched(f) {
  const props = f?.properties;
  if (!props || !_tilesRegion) return null;
  const raw = tileEntries(props);
  const side = s => raw.filter(e => e.side === s).map(e => [e.code, e.desc || '', e.time || '']);
  return { schedule_even: side('even'), schedule_odd: side('odd'), region: _tilesRegion, city: props.city,
           side_labels: sideNames(f) };  // as the street card names them (js/signs.js)
}

// key -> "Trash, Compost" for a home's pickup days in [start, end].
function pickupDayMap(sched, start, end) {
  const out = new Map(), lo = _ymdKey(start), hi = _ymdKey(end);
  for (const [stream, dates] of Object.entries(sched?.pickups || {})) {
    for (const iso of dates) {
      const [y, m, d] = iso.split('-').map(Number), k = _ymdKey({ y, m, d });
      if (k >= lo && k <= hi) out.set(k, out.has(k) ? `${out.get(k)}, ${stream}` : stream);
    }
  }
  return out;
}

// key -> "Veterans Day (Oakland)": no-sweep holidays of the street's and the cars' cities.
function holidayDayMap(scheds) {
  const out = new Map();
  for (const s of scheds) {
    const skip = schedSkip(s);
    const name = s.city ? cityName(s.city) : '';
    for (const [k, h] of Object.entries(skip || {})) {
      const text = name ? `${h} (${name})` : h;
      if (!out.has(Number(k))) out.set(Number(k), text);
      else if (!out.get(Number(k)).includes(text)) out.set(Number(k), `${out.get(Number(k))}; ${text}`);
    }
  }
  return out;
}

// Layers for [start, end]: {ptr: Map, cars: [{car, color, days: Map}], homes: [{home,
// days: Map}], hol: Map}.
function calLayers(ptrSched, start, end) {
  const carScheds = cars.map(c => carSchedules[c.id]).filter(Boolean);
  return {
    ptr: sweepDayMap(ptrSched, start, end),
    cars: cars.flatMap(car => carSchedules[car.id]
      ? [{ car, color: carColor(car),
           days: sweepDayMap(carSchedules[car.id], start, end, carSchedules[car.id].car_side) }]
      : []),
    homes: homes.flatMap(home => homeSchedules[home.id]
      ? [{ home, days: pickupDayMap(homeSchedules[home.id], start, end) }] : []),
    hol: holidayDayMap([ptrSched, ...carScheds].filter(Boolean)),
  };
}

function _dotsHTML(layers, k) {
  const dots = layers.cars.filter(l => l.days.has(k))
    .map(l => `<span class="cal-dot" style="background:${l.color}"></span>`)
    .concat(layers.homes.filter(l => l.days.has(k))
      .map(() => `<span class="cal-dot cal-dot-home" style="background:var(--home)"></span>`)).join('');
  return `<span class="cal-dots">${dots}</span>`;
}

// Each car with sweeping that day and each home with a pickup: [{mark (HTML), text}].
// The street is shown by the day tint and the signs, not repeated here.
function _dayLines(layers, k) {
  return layers.cars.filter(l => l.days.has(k)).map(l => ({
    mark: `<i class="cal-key cal-round" style="background:${l.color}"></i>`,
    text: `${l.car.name}: ${l.days.get(k)}`,
  })).concat(layers.homes.filter(l => l.days.has(k)).map(l => ({
    mark: `<i class="cal-key cal-square" style="background:var(--home)"></i>`,
    text: `${itemName(l.home)}: ${l.days.get(k)}`,
  })));
}

// What a screen reader says for a day: date, the street's sweeping, each car's.
function _dayAria(layers, k, p, today) {
  return [_dayLabel(p) + (k === _ymdKey(today) ? ', today' : ''),
          layers.ptr.has(k) ? `street sweeping ${layers.ptr.get(k)}` : '',
          layers.hol.has(k) ? `no sweeping: ${layers.hol.get(k)}` : '',
          ..._dayLines(layers, k).map(l => l.text)].filter(Boolean).join('; ');
}

// Street day tint: red today, orange tomorrow (as on the map), else light green.
function _streetCls(layers, k, today) {
  if (!layers.ptr.has(k)) return '';
  return 'sw-day' + (k === _ymdKey(today) ? ' sw-now' : k === _ymdKey(_ymdAdd(today, 1)) ? ' sw-next' : '');
}

// The calendar scrolls through one fixed window of days: a few weeks back, a
// year ahead. Day i of the window is `_rangeStart + i`; both views index into it.
const CAL_WEEKS = 70;
function _calRange(today) {
  const start = _ymdAdd(today, -56 - _dow(_ymdAdd(today, -56)));  // a Sunday
  return { start, end: _ymdAdd(start, CAL_WEEKS * 7 - 1) };
}
function _dayIdx(range, p) {
  return Math.round((Date.UTC(p.y, p.m - 1, p.d) - Date.UTC(range.start.y, range.start.m - 1, range.start.d)) / 864e5);
}

// The day details under the grid: a no-sweep holiday, each car with sweeping and each
// home with a pickup that day, else the street's sweeping.
function _detailHTML(layers, sel) {
  const hol = layers.hol.has(sel) ? `<div class="cal-hol-line">No sweeping: ${esc(layers.hol.get(sel))}</div>` : '';
  return `<b>${_dayLabel(_keyDay(sel))}</b>` + hol + (_dayLines(layers, sel).map(l => `<div>${l.mark} ${esc(l.text)}</div>`).join('')
    || (layers.ptr.has(sel) ? `<div>Sweeping: ${esc(layers.ptr.get(sel))}</div>` : hol ? '' : '<div class="cal-muted">No sweeping</div>'));
}

// Weeks in a column (scrolls vertically); `sel` is the selected day key. The first of
// each month carries its name.
function calendarHTML(layers, range, sel, today) {
  const todayK = _ymdKey(today);
  let cells = '';
  for (let i = 0; i < CAL_WEEKS * 7; i++) {
    const p = _ymdAdd(range.start, i), k = _ymdKey(p);
    const cls = ['cal-day', _streetCls(layers, k, today), layers.hol.has(k) ? 'cal-hol' : '', p.m % 2 ? 'cal-alt' : '',
                 k === todayK ? 'sw-today' : '', k === sel ? 'cal-sel' : ''].filter(Boolean).join(' ');
    cells += `<button class="${cls}" data-cal-day="${k}" aria-label="${esc(_dayAria(layers, k, p, today))}"`
           + ` aria-pressed="${k === sel}">`
           + (p.d === 1 ? `<i class="cal-mo">${_MONTHS[p.m - 1].slice(0, 3)}</i>` : '')
           + `${p.d}${_dotsHTML(layers, k)}</button>`;
  }
  return `<div class="cal-wds" aria-hidden="true">${_WD1.map(w => `<span>${w}</span>`).join('')}</div>`
       + `<div class="cal-scroll"><div class="cal-grid">${cells}</div></div>`
       + `<div class="cal-detail" aria-live="polite">${_detailHTML(layers, sel)}</div>`;
}

// ── Calendar tab ─────────────────────────────────────────────────────────────
// One grid of weeks, never rebuilt by the sheet: the small sheet shows one week of it
// (the selected day's) and that day's details, and as the sheet grows, during the drag
// too, the weeks below unfold into the month view (as Apple Calendar's week and month
// views). The sheet's heights fit whole weeks (calFit). A tap on a day selects it.
// Small: ‹ ›, a sideways swipe or the wheel step a week (the selection with it). Month:
// the grid scrolls; ‹ › step a month. Today goes back. The header's title is the month
// on screen.
const tabCal = document.getElementById('tab-calendar');
const icCal  = document.getElementById('ic-cal');
// anchor: the week row at the top of the grid (fractional while scrolled between rows).
let _cal = { expanded: null, sel: null, anchor: null, pending: false };
let _calKey = '';       // last rendered state, to skip no-op re-renders
let _ptrSched = null;   // street schedule under the target (null: none)
let _calRange0 = null;  // the window rendered last
let _calLayers0 = null; // and its layers (for the day details)
const _schedIds = new WeakMap();
let _schedSeq = 0;

function _schedId(s) {
  if (!_schedIds.has(s)) _schedIds.set(s, ++_schedSeq);
  return _schedIds.get(s);
}

const _scroller = () => icCal.querySelector('.cal-scroll');
// Distance between neighbouring week rows, in px.
function _pitch(el) {
  const kids = el.firstElementChild.children;
  return kids.length > 8 ? kids[7].offsetTop - kids[0].offsetTop : 0;
}
function _scrollTo(el, row, smooth) {
  el.scrollTo({ top: row * _pitch(el), behavior: smooth ? 'smooth' : 'auto' });
}
const _weekOf = (range, k) => Math.floor(_dayIdx(range, _keyDay(k)) / 7);

const CAL_ROW = 43;  // px: a 40 px row + the 3 px gap (styles.css .cal-grid)
// Desktop: the panel is as tall as six weeks of the grid (any month fits, as in Apple's and
// Google's month views; the grid still scrolls on), fewer on a short screen; every tab
// takes that height. On a phone the sheet's heights fit whole weeks instead (calFit).
const DESK_WEEKS = 6;
let _deskChrome = 0;  // the panel besides the grid, measured with the calendar shown
// Week rows the month view shows: the panel's (desktop) or the medium sheet's (phone), from
// their heights rather than the grid's, which may not be laid out yet.
function _calRows() {
  if (isDesktop()) return Math.round((deskPanelHeight() - _deskChrome + 3) / CAL_ROW) || DESK_WEEKS;
  const fit = calFit();
  return fit ? Math.floor((detentHeights().medium - fit.small) / CAL_ROW) + 1 : DESK_WEEKS;
}
function deskPanelHeight() {
  const el = _scroller();
  if (!el) return 0;
  if (!_deskChrome) {
    // Measured with the calendar in place of the open tab, within this frame (never painted).
    const open = [...sheet.querySelectorAll('.tab')].find(t => !t.hidden), h = sheet.style.height;
    if (open !== tabCal) { open.hidden = true; tabCal.hidden = false; }
    sheet.style.height = '';
    _deskChrome = sheet.offsetHeight - el.offsetHeight;
    sheet.style.height = h;
    if (open !== tabCal) { open.hidden = false; tabCal.hidden = true; }
  }
  const room = document.getElementById('app-screen').clientHeight - 2 * parseFloat(getComputedStyle(sheet).top);
  const rows = Math.max(2, Math.min(DESK_WEEKS, Math.floor((room - _deskChrome + 3) / CAL_ROW)));
  return _deskChrome + rows * CAL_ROW - 3;
}
// The screen changed (or the text size): measure again.
window.addEventListener('resize', () => { _deskChrome = 0; applySheet(); });

// Phone: sheet heights that show whole weeks and the day details: {small: one week, round(h): the nearest
// height at or under h showing whole weeks and the day details}; null until the
// calendar has been laid out on screen (measured, so it follows fonts and insets).
let _calChrome = null;  // {base: the sheet besides the grid and details, detail}
function calFit() {
  if (!_calChrome) return null;
  const { base, detail } = _calChrome, weeks = n => base + detail + n * CAL_ROW - 3;
  return { small: weeks(1), weeks,
           round: h => weeks(Math.max(1, Math.floor((h - base - detail + 3) / CAL_ROW))) };
}
// Measured once the sheet has settled (mid-animation sizes round differently).
function measureCalChrome() {
  const el = _scroller(), det = icCal.querySelector('.cal-detail');
  if (isDesktop() || tabCal.hidden || !el || !det || sheet.classList.contains('dragging')) return;
  if (sheet.style.height && Math.abs(sheet.offsetHeight - parseFloat(sheet.style.height)) > 1) return;
  const detail = det.offsetHeight + parseFloat(getComputedStyle(det).marginTop);
  const base = sheet.offsetHeight - icCal.clientHeight + (icCal.scrollHeight - el.offsetHeight - detail);
  if (_calChrome && Math.abs(_calChrome.base - base) < 2 && Math.abs(_calChrome.detail - detail) < 2) return;
  _calChrome = { base, detail };
  applySheet();
}

// The small sheet's week (two while scrolling between them) is marked .cal-on: once the
// sheet has settled small (.cal-folded), the other weeks are hidden, so they take no
// taps or keyboard focus behind the header and tab bar. The weeks a drag up can bring
// into view (up to the medium sheet's) are .cal-near: they unfold under the finger.
let _weekOn = [], _weekNear = [];
function _markWeek(el, pitch) {
  const kids = el.firstElementChild.children, a = Math.floor(el.scrollTop / pitch + .02), b = Math.ceil(el.scrollTop / pitch - .02);
  const fit = calFit();  // before it is measured, a month's worth
  const below = _cal.expanded ? 0 : fit ? Math.ceil((detentHeights().medium - fit.small) / CAL_ROW) + 1 : 5;
  const row = (r0, r1) => { const out = []; for (let i = r0 * 7; i < (r1 + 1) * 7 && i < kids.length; i++) out.push(kids[i]); return out; };
  const mark = (cls, was, now) => {
    for (const k of was) if (!now.includes(k)) k.classList.remove(cls);
    for (const k of now) k.classList.add(cls);
    return now;
  };
  _weekOn = mark('cal-on', _weekOn, row(a, b));
  _weekNear = mark('cal-near', _weekNear, below ? row(b + 1, b + below) : []);
}

// Header title: the month in the middle of what is on screen.
function updateCalTitle() {
  const el = _scroller(), t = document.getElementById('ic-month');
  if (!el || !t || !_calRange0) return;
  const pitch = _pitch(el);
  if (!pitch) return;
  const pos = Math.round(el.scrollTop / pitch);
  _cal.anchor = el.scrollTop / pitch;
  _markWeek(el, pitch);
  const midRow = Math.max(0, Math.floor((el.clientHeight / pitch - 1) / 2));
  const mid = _ymdAdd(_calRange0.start, (pos + midRow) * 7 + 3);
  const key = `${mid.y}-${mid.m}-${t.clientWidth}`;
  if (t.dataset.key === key) return;
  t.dataset.key = key; t.dataset.y = mid.y; t.dataset.m = mid.m;
  // The longest form that fits ("September 2026", "Sep 2026", then without this year's).
  const M = _MONTHS[mid.m - 1], forms = [`${M} ${mid.y}`, `${M.slice(0, 3)} ${mid.y}`];
  if (mid.y === calNow().y) forms.push(M, M.slice(0, 3));
  for (const f of forms) { t.textContent = f; if (t.scrollWidth <= t.clientWidth) break; }
}

// Everything that reads the street at the target: the calendar's street days, the
// signs and the glow on that street. f = the feature there (undefined: look it up).
// live (the map moving): the sign and glow only; rebuilding the calendar's grid costs
// far more, so it waits until the map stops (moveend).
function renderTargetInfo(f, live = false) {
  if (f === undefined) f = _centerFeature();
  const props = f?.properties || null;
  renderSigns(f, live);
  setActiveFeature(f ? f.id : null);
  if (live) return;
  const lines = props ? tileSchedLines(props) : [];
  const today = calNow(), todayK = _ymdKey(today);
  if (_cal.sel == null) _cal.sel = todayK;
  _ptrSched = props && lines.length ? pointerSched(f) : null;
  const carSig = cars.map((c, i) => `${c.id}:${c.name}:${i}:${carSchedules[c.id] ? _schedId(carSchedules[c.id]) : 0}`).join(',');
  const homeSig = homes.map(h => `${h.id}:${itemName(h)}:${homeSchedules[h.id] ? _schedId(homeSchedules[h.id]) : 0}`).join(',');
  const key = [_ptrSched ? `${props.city}:${props.sched}:${_ptrSched.side_labels}` : '', carSig, homeSig, todayK].join('|');
  if (key === _calKey) return;
  _calKey = key;

  const range = _calRange0 = _calRange(today);
  const layers = _calLayers0 = calLayers(_ptrSched, range.start, range.end);
  icCal.innerHTML = calendarHTML(layers, range, _cal.sel, today);
  if (_cal.anchor == null) _cal.anchor = _anchorFor(range, today);
  _restoreScroll();
  updateCalTitle();
  measureCalChrome();
}
// Back to the remembered week row; a hidden tab has no layout to scroll, so it waits
// until the tab shows again (calShown).
function _restoreScroll() {
  const el = _scroller();
  _cal.pending = !_pitch(el);
  if (!_cal.pending) _scrollTo(el, _cal.anchor, false);
}

// Where the grid starts when nothing is remembered: the selected week (small sheet),
// the week holding the 1st of the selected month (month view), lower if that would
// leave the selected week below what fits (a short screen).
function _anchorFor(range, today) {
  const sel = _cal.sel ? _keyDay(_cal.sel) : today, week = _weekOf(range, _ymdKey(sel));
  if (!_cal.expanded) return week;
  return Math.max(0, Math.floor(_dayIdx(range, { y: sel.y, m: sel.m, d: 1 }) / 7), week - _calRows() + 1);
}

// The sheet grew past small (expanded) or came back to it. Nothing is rebuilt: folding
// to one week scrolls the selected week to the top as the sheet shrinks.
let _foldTimer = 0;
function calSetExpanded(on) {
  if (_cal.expanded === on) return;
  const first = _cal.expanded === null;
  _cal.expanded = on;
  icCal.classList.toggle('cal-small', !on);
  // Hide the other weeks only once the sheet has folded (they fold away with it).
  clearTimeout(_foldTimer);
  if (on) icCal.classList.remove('cal-folded');
  else _foldTimer = setTimeout(() => icCal.classList.toggle('cal-folded', !_cal.expanded), first ? 0 : 450);
  for (const b of tabCal.querySelectorAll('[data-cal-nav]')) {
    b.setAttribute('aria-label', `${b.dataset.calNav < 0 ? 'Previous' : 'Next'} ${on ? 'month' : 'week'}`);
  }
  const el = _scroller();
  if (!on && !first && el && _calRange0 && _cal.sel) _scrollTo(el, _weekOf(_calRange0, _cal.sel), true);
}

// Select a day in place: its ring and the details under the grid.
function selectCalDay(k) {
  _cal.sel = k;
  for (const b of icCal.querySelectorAll('.cal-sel')) { b.classList.remove('cal-sel'); b.setAttribute('aria-pressed', 'false'); }
  const b = icCal.querySelector(`[data-cal-day="${k}"]`);
  b?.classList.add('cal-sel');
  b?.setAttribute('aria-pressed', 'true');
  const det = icCal.querySelector('.cal-detail');
  if (det && _calLayers0) det.innerHTML = _detailHTML(_calLayers0, k);
}

// ‹ ›: a week on the small sheet (the selected day moves with it, so its details stay
// in view), a month in the month view (smooth; the scroll event keeps the title current).
function calStep(dir) {
  const el = _scroller(), t = document.getElementById('ic-month');
  if (!el || !t || !_calRange0) return;
  if (!_cal.expanded) {
    const last = CAL_WEEKS * 7 - 1, i = _dayIdx(_calRange0, _keyDay(_cal.sel)) + 7 * dir;
    if (i < 0 || i > last) return;
    const k = _ymdKey(_ymdAdd(_calRange0.start, i));
    selectCalDay(k);
    _scrollTo(el, _weekOf(_calRange0, k), true);
    return;
  }
  const d = new Date(Date.UTC(Number(t.dataset.y), Number(t.dataset.m) - 1 + dir, 1));
  const first = { y: d.getUTCFullYear(), m: d.getUTCMonth() + 1, d: 1 };
  _scrollTo(el, Math.max(0, Math.floor(_dayIdx(_calRange0, first) / 7)), true);
}

// Today: selects today and scrolls back to it.
function calToday() {
  const today = calNow();
  selectCalDay(_ymdKey(today));
  _scrollTo(_scroller(), _anchorFor(_calRange0, today), true);
}

tabCal.addEventListener('click', e => {
  const nav = e.target.closest('[data-cal-nav]');
  if (nav) { calStep(Number(nav.dataset.calNav)); return; }
  if (e.target.closest('[data-cal-today]')) { calToday(); return; }
  const day = e.target.closest('[data-cal-day]');
  if (!day) return;
  selectCalDay(Number(day.dataset.calDay));
});

// Scroll events do not bubble: catch them on the way down.
let _scrollRaf = 0;
tabCal.addEventListener('scroll', () => {
  if (_scrollRaf) return;
  _scrollRaf = requestAnimationFrame(() => { _scrollRaf = 0; updateCalTitle(); });
}, true);

// Small sheet: a sideways swipe or the wheel steps a week (one step per gesture); a
// vertical drag moves the sheet (sheet.js).
let _swipe = null, _wheelAt = 0;
icCal.addEventListener('touchstart', e => {
  _swipe = !_cal.expanded && e.touches.length === 1 ? { x: e.touches[0].clientX, y: e.touches[0].clientY } : null;
}, { passive: true });
icCal.addEventListener('touchend', e => {
  if (!_swipe) return;
  const t = e.changedTouches[0], dx = t.clientX - _swipe.x, dy = t.clientY - _swipe.y;
  _swipe = null;
  if (Math.abs(dx) > 40 && Math.abs(dx) > 1.5 * Math.abs(dy)) calStep(dx < 0 ? 1 : -1);
}, { passive: true });
icCal.addEventListener('wheel', e => {
  if (_cal.expanded) return;
  e.preventDefault();
  const d = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
  if (Math.abs(d) < 4 || e.timeStamp - _wheelAt < 350) return;
  _wheelAt = e.timeStamp;
  calStep(d > 0 ? 1 : -1);
}, { passive: false });

// The sheet resizing (a drag, a detent, the screen) changes how many weeks show.
new ResizeObserver(() => requestAnimationFrame(() => { updateCalTitle(); measureCalChrome(); })).observe(icCal);
// Measured mid-animation is skipped; once the sheet settles, measure.
sheet.addEventListener('transitionend', e => { if (e.target === sheet && e.propertyName === 'height') measureCalChrome(); });
// The tab shows again (sheet.js showTab): a re-render while it was hidden left the grid at its top.
function calShown() {
  if (_cal.pending) _restoreScroll();
  updateCalTitle();
  measureCalChrome();
}

calSetExpanded(isDesktop() || _detentOf.calendar !== 'small');
renderTargetInfo();  // initial empty state
if (isDesktop()) applySheet();  // the panel's height, now that the grid is there
