// ── Calendar tab: one sweep calendar ──────────────────────────────────────────
// Layers: the street under the target (day tint), every car with a schedule (dot in
// the car's color), every home's trash days (dot in the home color) and the no-sweep
// holidays of those streets' cities. Days are {y, m, d}; arithmetic and weekday come
// from urgency.js.
const { addDays: _ymdAdd, dayKey: _ymdKey, weekday: _dow, dayLabel: _dayLabel, WEEKDAYS } = BroomUrgency;
// The weekday each week row starts on, as urgency.js counts them (0 = Monday): the one
// place that decides it (the grid, its header letters, ‹ › and Home / End follow).
function calWeek0() { return 0; }
const _col = p => (_dow(p) - calWeek0() + 7) % 7;  // a day's column in its week row
const _MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
                 'August', 'September', 'October', 'November', 'December'];

// Region-local "today" for the map's tile region (browser tz before one loads).
function calNow() { return regionNow(_tilesRegion || _region); }

// key -> text for sweep days in [start, end] (holidays of the street's city excluded).
// `side` limits to one street side (a car's), which the text then leaves out.
function sweepDayMap(sched, start, end, side = null) {
  const out = new Map();
  if (!sched) return out;
  for (const day of BroomUrgency.sweepDays(sched.schedule_even, sched.schedule_odd, start, end, schedSkip(sched))) {
    const items = side ? day.items.filter(it => it.side === side) : day.items;
    if (items.length) out.set(_ymdKey(day), tidyTimes(BroomUrgency.dayText(items, sched.side_labels, !!side)));
  }
  return out;
}
function _keyDay(k) { return { y: Math.floor(k / 10000), m: Math.floor(k / 100) % 100, d: k % 100 }; }

// /check-shaped schedule for the street under the target (tile entries -> [code, desc, time]).
function pointerSched(f) {
  const props = f?.properties;
  if (!props || !_tilesRegion) return null;
  const { sides } = _tileSides(props, regionNow(_tilesRegion));  // parsed once (js/signs.js)
  return { schedule_even: sides[0].rows, schedule_odd: sides[1].rows, region: _tilesRegion, city: props.city,
           side_labels: sideNames(f) };  // as the street card names them (js/signs.js)
}

// key -> "Trash, Compost" for a home's pickup days in [start, end].
function pickupDayMap(sched, start, end) {
  const out = new Map(), lo = _ymdKey(start), hi = _ymdKey(end);
  for (const [stream, dates] of Object.entries(sched?.pickups || {})) {
    for (const iso of dates) {
      const k = _ymdKey(BroomUrgency.parseIso(iso));
      if (k >= lo && k <= hi) out.set(k, out.has(k) ? `${out.get(k)}, ${stream}` : stream);
    }
  }
  return out;
}

// key -> "Veterans Day (Oakland)": no-sweep holidays of the street's and the cars' cities;
// `daytime`: the days only daytime sweeping is off (night cleaning still runs).
function holidayDayMap(scheds, daytime = false) {
  const out = new Map();
  for (const s of scheds) {
    const skip = schedSkip(s);
    const name = s.city ? cityName(s.city) : '';
    for (const [k, h] of Object.entries((daytime ? skip?.daytime : skip) || {})) {
      const text = name ? `${h} (${name})` : h;
      if (!out.has(Number(k))) out.set(Number(k), text);
      else if (!out.get(Number(k)).includes(text)) out.set(Number(k), `${out.get(Number(k))}; ${text}`);
    }
  }
  return out;
}

// Layers for [start, end]: {ptr: Map, cars: [{car, color, days: Map}], homes: [{home,
// days: Map}], hol: Map (street and cars), savedHol / savedHolDay: Map (cars only)}.
function calLayers(ptrSched, start, end) {
  const carScheds = cars.map(c => carSchedules[c.id]).filter(Boolean);
  return {
    ptr: sweepDayMap(ptrSched, start, end),
    cars: cars.flatMap(car => carSchedules[car.id]
      ? [{ car, color: carColor(car),
           days: sweepDayMap(carSchedules[car.id], start, end, carSide(car)) }]
      : []),
    homes: homes.flatMap(home => homeSchedules[home.id]
      ? [{ home, days: pickupDayMap(homeSchedules[home.id], start, end) }] : []),
    hol: holidayDayMap([ptrSched, ...carScheds].filter(Boolean)),
    savedHol: holidayDayMap(carScheds),
    savedHolDay: holidayDayMap(carScheds, true),
  };
}

function _dotsHTML(layers, k) {
  const dots = layers.cars.filter(l => l.days.has(k))
    .map(l => `<span class="cal-dot cal-round" style="background:${esc(l.color)}"></span>`)
    .concat(layers.homes.filter(l => l.days.has(k))
      .map(() => `<span class="cal-dot cal-square" style="background:var(--home)"></span>`)).join('');
  return `<span class="cal-dots">${dots}</span>`;
}

// What happens that day, each with its mark and saying what it is: each car with
// sweeping (a dot in its color; "Civic · 9–11AM", gray and "ended" once today's is over)
// and each home with a trash day (a square; "Home · trash day: trash, recycling"):
// [{mark, name, what, text, ended}].
function _dayLines(layers, k) {
  const line = (mark, name, what, ended = false) => ({ mark, name, what, text: `${name} · ${what}`, ended });
  return [
    ...layers.cars.filter(l => l.days.has(k)).map(l => line(
      `<i class="cal-key cal-round" style="background:${esc(l.color)}"></i>`,
      l.car.name, l.days.get(k) || 'Sweeping',  // Chicago's days have no times
      // Today in the car's own region, its sweep over (tonight's after midnight is tomorrow's).
      k === _ymdKey(schedNow(carSchedules[l.car.id]))
        && !BroomUrgency.sweptLaterToday(_sweepEntries(l.car), schedNow(carSchedules[l.car.id]), schedSkip(carSchedules[l.car.id])))),
    ...layers.homes.filter(l => l.days.has(k)).map(l => line(
      `<i class="cal-key cal-square" style="background:var(--home)"></i>`,
      itemName(l.home), `Trash day: ${l.days.get(k).toLowerCase()}`))];
}

// What a screen reader says for a day: date, the street's sweeping, a holiday, each car's.
function _dayAria(layers, k, p, today) {
  return [_dayLabel(p) + (k === _ymdKey(today) ? ', today' : ''),
          layers.ptr.has(k) ? `street sweeping ${layers.ptr.get(k)}` : '',
          layers.hol.has(k) ? `no sweeping: ${layers.hol.get(k)}` : '',
          layers.savedHolDay.has(k) ? `no daytime sweeping: ${layers.savedHolDay.get(k)}` : '',
          ..._dayLines(layers, k).map(l => l.text + (l.ended ? ', ended' : ''))].filter(Boolean).join('; ');
}

// Street days: red today while its window is to come or on (as the map says it, _ptrUrg),
// amber tomorrow (red when it starts tonight, after midnight: _ptrTonight), else a bar (styles.css).
function _streetCls(layers, k, today) {
  if (!layers.ptr.has(k)) return '';
  if (k === _ymdKey(today)) return 'sw-day' + (_ptrUrg === 'today' && !_ptrTonight ? ' sw-now' : '');
  return 'sw-day' + (k === _ymdKey(_ymdAdd(today, 1)) ? (_ptrTonight ? ' sw-now' : ' sw-next') : '');
}

// The calendar scrolls through one fixed window of days: a few weeks back, a
// year ahead. Day i of the window is `_rangeStart + i`; both views index into it.
// Only the weeks up to a few past those on screen are built (CAL_MORE more as the grid
// scrolls or steps toward them): a street change rebuilds a few dozen days, not 490.
const CAL_WEEKS = 70;
const CAL_MORE = 8;
function _calRange(today) {
  const back = _ymdAdd(today, -56);
  const start = _ymdAdd(back, -_col(back));  // the first day of a week row
  return { start, end: _ymdAdd(start, CAL_WEEKS * 7 - 1) };
}
function _dayIdx(range, p) {
  return BroomUrgency.diffDays(range.start, p);
}

// The day details under the grid: the date, then one row per saved car and home with
// something that day and per no-sweep holiday of the cars' cities.
// A no-sweep holiday (the grid strikes its date through) says so, marked as the grid
// marks it. Nothing saved that day: the street at the target's sweeping (the grid's bar);
// nothing saved at all: the way to add a car.
function _detailHTML(layers, sel) {
  const row = (mark, label, right, muted = false) =>
    `<div class="cal-row">${mark}<span class="cal-rl">${label}</span><span class="cal-rr${muted ? ' muted' : ''}">${right}</span></div>`;
  const ended = '<span class="tag muted">Ended</span>';
  const lines = _dayLines(layers, sel);
  const rows = [
    ...lines.map(l => row(l.mark, esc(l.name), esc(l.what) + (l.ended ? ` ${ended}` : ''), l.ended)),
    ...[[layers.hol, 'No sweeping', '<s class="cal-key cal-key-hol" aria-hidden="true">1</s>'], [layers.savedHolDay, 'No daytime sweeping', '']]
      .filter(([m]) => m.has(sel)).map(([m, what, mark]) => row(mark, what, esc(m.get(sel)), true)),
  ];
  if (!rows.length && layers.ptr.has(sel)) {
    rows.push(row('<i class="cal-key cal-key-bar" aria-hidden="true"></i>', 'Street at the target', esc(layers.ptr.get(sel)), true));
  }
  const empty = lines.length ? ''
    : cars.length || homes.length ? '<div class="cal-empty muted">Nothing for your cars or homes.</div>'
    : '<div class="cal-empty"><span class="muted">Save your car to see its days.</span>'
      + `<button class="btn btn-sm btn-tinted" data-add="car">${icon('car')} Add car</button></div>`;
  return `<div class="cal-hero"><b class="cal-verdict">${esc(_dayLabel(_keyDay(sel)))}</b></div>`
       + (rows.length ? `<div class="cal-rows">${rows.join('')}</div>` : '') + empty;
}

// Week rows [w0, w1) of the window as day buttons; `sel` is the selected day key. The
// first of each month carries its name.
function _cellsHTML(layers, range, sel, today, w0, w1) {
  const todayK = _ymdKey(today);
  let cells = '';
  for (let i = w0 * 7; i < w1 * 7; i++) {
    const p = _ymdAdd(range.start, i), k = _ymdKey(p);
    const cls = ['cal-day', `cal-m${p.m}`, String(p.m) === icCal.dataset.m ? '' : 'cal-out', _streetCls(layers, k, today), layers.hol.has(k) ? 'cal-hol' : '',
                 k === todayK ? 'sw-today' : k < todayK ? 'cal-past' : '', k === sel ? 'cal-sel' : ''].filter(Boolean).join(' ');
    cells += `<button class="${cls}" data-cal-day="${k}" aria-label="${esc(_dayAria(layers, k, p, today))}"`
           + ` aria-pressed="${k === sel}" tabindex="${k === sel ? 0 : -1}">`
           + (p.d === 1 ? `<i class="cal-mo">${_MONTHS[p.m - 1].slice(0, 3)}</i>` : '')
           + `<span class="cal-n">${p.d}</span>${_dotsHTML(layers, k)}<i class="cal-bar"></i></button>`;
  }
  return cells;
}
// Weeks in a column (scrolls vertically): the window's first `weeks` rows.
function calendarHTML(layers, range, sel, today, weeks) {
  const wds = WEEKDAYS.map((_, i) => WEEKDAYS[(i + calWeek0()) % 7][0]);
  return `<div class="cal-wds" aria-hidden="true">${wds.map(w => `<span>${w}</span>`).join('')}</div>`
       + `<div class="cal-scroll"><div class="cal-grid">${_cellsHTML(layers, range, sel, today, 0, weeks)}</div></div>`
       + `<div class="cal-detail" tabindex="0" role="region" aria-label="Day details" aria-live="polite">${_detailHTML(layers, sel)}</div>`;
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
// anchor: the week row at the top of the grid (fractional while scrolled between rows);
// today: the day key the grid was last rendered for.
let _cal = { expanded: null, sel: null, anchor: null, pending: false, today: null };
let _calKey = '';       // last rendered state, to skip no-op re-renders
let _ptrSched = null;   // street schedule under the target (null: none)
let _ptrUrg = null;     // its verdict now (urgency.js checkDaySweeping)
let _ptrTonight = false; // 'today' only for tomorrow's sweep after midnight (today's is over)
let _calRange0 = null;  // the window rendered last
let _calWeeks = 0;      // its week rows built so far (CAL_MORE past those in view)
let _calLayers0 = null; // and their layers (for the day details)
const _schedIds = new WeakMap();
let _schedSeq = 0;

function _schedId(s) {
  if (!_schedIds.has(s)) _schedIds.set(s, ++_schedSeq);
  return _schedIds.get(s);
}

const _scroller = () => icCal.querySelector('.cal-scroll');
// Distance between neighboring week rows, in px: fixed (styles.css .cal-grid), so reading
// it never forces a layout.
const CAL_ROW = 43;  // px: a 40 px row + the 3 px gap (styles.css .cal-grid)
const _pitch = () => CAL_ROW;
const _reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
function _scrollTo(el, row, smooth) {
  _calGrow(Math.ceil(row) + 6 + CAL_MORE);  // the rows it lands on (a month), and a few past them
  el.scrollTo({ top: row * _pitch(), behavior: smooth && !_reduceMotion.matches ? 'smooth' : 'auto' });
}
// Build the week rows up to `upto` (the window's end at most): the layers over the longer
// span, the new rows' days appended (the rows above stay put, and so does the scroll).
function _calGrow(upto) {
  const n = Math.min(CAL_WEEKS, upto), grid = icCal.querySelector('.cal-grid');
  if (!_calRange0 || !grid || n <= _calWeeks) return;
  _calLayers0 = calLayers(_ptrSched, _calRange0.start, _ymdAdd(_calRange0.start, n * 7 - 1));
  grid.insertAdjacentHTML('beforeend', _cellsHTML(_calLayers0, _calRange0, _cal.sel, calNow(), _calWeeks, n));
  _calWeeks = n;
}
const _weekOf = (range, k) => Math.floor(_dayIdx(range, _keyDay(k)) / 7);
// Desktop: the grid shows one month, six week rows from the week holding the 1st.
const _monthRow = (range, k) => _weekOf(range, Math.floor(k / 100) * 100 + 1);

// On a phone the sheet's heights fit whole weeks (calFit); on a desktop the grid shows one
// month (styles.css) and ‹ › or the wheel step a month.

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
  if (isDesktop() || !el || !det || sheet.classList.contains('dragging') || sheet.classList.contains('retracted')) return;
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
function _markWeek(el, pitch, top) {
  const kids = el.firstElementChild.children, a = Math.floor(top / pitch + .02), b = Math.ceil(top / pitch - .02);
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

// Header title: on the small sheet, the selected day's month (the details under the week
// are that day's); in the month view, the month of the top week on screen (of its middle
// day, so a week belongs to the month holding most of it; ‹ › scroll to such a week,
// calStep). Desktop: of the third week, as the top one holds the 1st.
function updateCalTitle() {
  const el = _scroller(), t = document.getElementById('ic-month');
  if (!el || !t || !_calRange0) return;
  const pitch = _pitch(), top = el.scrollTop, pos = Math.round(top / pitch);
  _cal.anchor = top / pitch;
  // Scrolled near the last rows built: build more before they come into view.
  if (pos + 6 + CAL_MORE / 2 > _calWeeks) _calGrow(pos + 6 + CAL_MORE);
  _markWeek(el, pitch, top);
  const mid = !_cal.expanded && _cal.sel != null ? _keyDay(_cal.sel)
    : _ymdAdd(_calRange0.start, (pos + (isDesktop() ? 2 : 0)) * 7 + 3);
  const key = `${mid.y}-${mid.m}-${t.clientWidth}`;
  if (t.dataset.key === key) return;
  t.dataset.key = key; t.dataset.y = mid.y; t.dataset.m = mid.m;
  icCal.dataset.m = mid.m;  // days of the other months are muted (.cal-out, styles.css)
  for (const d of icCal.querySelectorAll('.cal-day')) d.classList.toggle('cal-out', !d.classList.contains(`cal-m${mid.m}`));
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
  _ptrSched = lines.length ? pointerSched(f) : null;
  const ptrEntries = _ptrSched ? BroomUrgency.entries([..._ptrSched.schedule_even, ..._ptrSched.schedule_odd]) : [];
  _ptrUrg = _ptrSched ? BroomUrgency.checkDaySweeping(ptrEntries, today, schedSkip(_ptrSched)) : null;
  _ptrTonight = _ptrUrg === 'today' && !BroomUrgency.sweptLaterToday(ptrEntries, today, schedSkip(_ptrSched));
  const carSig = cars.map((c, i) => `${c.id}:${c.name}:${i}:${carSide(c) || ''}:${sweepUrgency(c)}:${carSchedules[c.id] ? _schedId(carSchedules[c.id]) : 0}`).join(',');
  const homeSig = homes.map(h => `${h.id}:${itemName(h)}:${homeSchedules[h.id] ? _schedId(homeSchedules[h.id]) : 0}`).join(',');
  const key = [_ptrSched ? `${props.city}:${props.sched}:${_ptrSched.side_labels}:${_ptrUrg}:${_ptrTonight}` : '',
               carSig, homeSig, todayK].join('|');
  if (key === _calKey) return;
  _calKey = key;

  const range = _calRange(today);
  // A new day (the app left open overnight): the selection moves from yesterday to today,
  // and the week rows on screen stay on their dates as the window moves.
  if (_calRange0 && _cal.today != null && _cal.today !== todayK) {
    if (_cal.sel === _cal.today) _cal.sel = todayK;
    if (_cal.anchor != null) _cal.anchor = Math.max(0, _cal.anchor + _dayIdx(range, _calRange0.start) / 7);
  }
  _cal.today = todayK;
  _calRange0 = range;
  if (_cal.anchor == null) _cal.anchor = _anchorFor(range, today);
  // The rows from the remembered one (a month's worth), the selected day's, and a few more.
  _calWeeks = Math.min(CAL_WEEKS, Math.max(Math.ceil(_cal.anchor) + 6, _weekOf(range, _cal.sel) + 1) + CAL_MORE);
  const layers = _calLayers0 = calLayers(_ptrSched, range.start, _ymdAdd(range.start, _calWeeks * 7 - 1));
  const hadFocus = icCal.contains(document.activeElement);
  icCal.innerHTML = calendarHTML(layers, range, _cal.sel, today, _calWeeks);
  _restoreScroll();
  updateCalTitle();  // marks the week on screen: a folded sheet's other weeks take no focus
  if (hadFocus) icCal.querySelector('.cal-sel')?.focus({ preventScroll: true });  // the grid's one Tab stop
  measureCalChrome();
}
// Back to the remembered week row; a hidden tab has no layout to scroll, so it waits
// until the tab shows again (calShown).
function _restoreScroll() {
  const el = _scroller();
  _cal.pending = !el.clientHeight;
  if (!_cal.pending) _scrollTo(el, _cal.anchor, false);
}

// Where the grid starts when nothing is remembered: the selected week on a phone (the
// weeks ahead matter, not those past; ‹ › still step whole months), its month on a desktop.
function _anchorFor(range, today) {
  const k = _ymdKey(_cal.sel ? _keyDay(_cal.sel) : today);
  return isDesktop() ? _monthRow(range, k) : _weekOf(range, k);
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

// Select a day in place: its ring, the one Tab stop of the grid, the details under it and
// (the small sheet) the month in the header.
function selectCalDay(k) {
  _cal.sel = k;
  if (_calRange0) _calGrow(_weekOf(_calRange0, k) + 1 + CAL_MORE);
  for (const b of icCal.querySelectorAll('.cal-sel')) { b.classList.remove('cal-sel'); b.setAttribute('aria-pressed', 'false'); b.tabIndex = -1; }
  const b = icCal.querySelector(`[data-cal-day="${k}"]`);
  b?.classList.add('cal-sel');
  b?.setAttribute('aria-pressed', 'true');
  if (b) b.tabIndex = 0;
  const det = icCal.querySelector('.cal-detail');
  if (det && _calLayers0) det.innerHTML = _detailHTML(_calLayers0, k);
  if (!_cal.expanded) updateCalTitle();
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
  _scrollTo(el, _monthTop({ y: d.getUTCFullYear(), m: d.getUTCMonth() + 1, d: 1 }), true);
}
// The month view's top row for a month (`first`: its 1st): its first week; on a phone the
// next when the 1st falls past the week's middle (that week is the last month's).
function _monthTop(first) {
  return Math.max(0, Math.floor(_dayIdx(_calRange0, first) / 7) + (!isDesktop() && _col(first) > 3 ? 1 : 0));
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
  if (e.target.closest('[data-add]')) { startPlacing(null, 'car'); return; }
  const day = e.target.closest('[data-cal-day]');
  if (!day) return;
  const k = Number(day.dataset.calDay);
  selectCalDay(k);
  // The month view: a day of the month before or after (muted) brings its month into
  // view, so the header names the selected day's month.
  if (_cal.expanded && day.classList.contains('cal-out')) _scrollTo(_scroller(), _monthTop({ ..._keyDay(k), d: 1 }), true);
});

// The grid is one Tab stop (the selected day): the arrow keys move a day or a week,
// Home / End to the week's start / end, and the week in view follows.
icCal.addEventListener('keydown', e => {
  const day = e.target.closest?.('[data-cal-day]');
  const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[e.key];
  if (!day || !_calRange0 || (step === undefined && e.key !== 'Home' && e.key !== 'End')) return;
  e.preventDefault();
  const i = _dayIdx(_calRange0, _keyDay(Number(day.dataset.calDay)));
  const j = step !== undefined ? i + step : e.key === 'Home' ? i - i % 7 : i - i % 7 + 6;
  if (j < 0 || j >= CAL_WEEKS * 7) return;
  const k = _ymdKey(_ymdAdd(_calRange0.start, j));
  selectCalDay(k);
  const b = icCal.querySelector(`[data-cal-day="${k}"]`);
  b?.focus({ preventScroll: true });
  const el = _scroller(), row = _weekOf(_calRange0, k), top = el.scrollTop / _pitch();
  if (row < Math.floor(top) || row > Math.floor(top) + Math.max(0, Math.round(el.clientHeight / _pitch()) - 1)) {
    _scrollTo(el, isDesktop() ? _monthRow(_calRange0, k) : row, true);
  }
});

// Scroll events do not bubble: catch them on the way down.
let _scrollRaf = 0;
tabCal.addEventListener('scroll', () => {
  if (_scrollRaf) return;
  _scrollRaf = requestAnimationFrame(() => { _scrollRaf = 0; updateCalTitle(); });
}, true);

// Small sheet: a sideways swipe or the wheel steps a week (one step per gesture); a
// vertical drag moves the sheet (sheet.js). Desktop: the wheel steps a month.
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
  if (_cal.expanded && !isDesktop()) return;
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

calSetExpanded(isDesktop() || _detent === 'medium');
renderTargetInfo();  // initial empty state
