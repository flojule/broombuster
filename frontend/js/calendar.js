// ── Calendar: one sweep calendar + the street sign at the target ─────────────
// Layers: the street under the target (day tint) and every car with a schedule
// (dot in the car's colour). Days are {y, m, d}; arithmetic and weekday come from
// urgency.js.
const { addDays: _ymdAdd, dayKey: _ymdKey, weekday: _dow, dayLabel: _dayLabel, WEEKDAYS } = BroomUrgency;
const _WD1 = WEEKDAYS.map(w => w[0]);
const _MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
                 'August', 'September', 'October', 'November', 'December'];

// Region-local "today" for the map's tile region (browser tz before one loads).
function calNow() { return regionNow(_tilesRegion || _region); }

// key -> text for sweep days in [start, end]. `side` limits to one street side.
function sweepDayMap(sched, start, end, side = null) {
  const out = new Map();
  if (!sched) return out;
  for (const day of BroomUrgency.sweepDays(sched.schedule_even, sched.schedule_odd, start, end)) {
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
  let raw = [];
  try { raw = JSON.parse(props.sched || '[]') || []; } catch (_) {}
  const side = s => raw.filter(e => e.side === s).map(e => [e.code, e.desc || '', e.time || '']);
  return { schedule_even: side('even'), schedule_odd: side('odd'),
           side_labels: [props.side_even || 'Even', props.side_odd || 'Odd'] };
}

// Layers for [start, end]: {ptr: Map, cars: [{car, color, days: Map}]}.
function calLayers(ptrSched, start, end) {
  return {
    ptr: sweepDayMap(ptrSched, start, end),
    cars: cars.flatMap((car, i) => carSchedules[car.id]
      ? [{ car, color: carColor(i),
           days: sweepDayMap(carSchedules[car.id], start, end, carSchedules[car.id].car_side) }]
      : []),
  };
}

function _dotsHTML(layers, k) {
  const dots = layers.cars.filter(l => l.days.has(k))
    .map(l => `<span class="cal-dot" style="background:${l.color}"></span>`).join('');
  return `<span class="cal-dots">${dots}</span>`;
}

// Each car with sweeping that day: [{mark (HTML), text}]. The street is shown by
// the day tint and the sign, not repeated here.
function _dayLines(layers, k) {
  return layers.cars.filter(l => l.days.has(k)).map(l => ({
    mark: `<i class="cal-key cal-round" style="background:${l.color}"></i>`,
    text: `${l.car.name}: ${l.days.get(k)}`,
  }));
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

// Compact view: one row of days, scrolls sideways.
function sweepStripHTML(layers, range, today) {
  let cells = '';
  for (let i = 0; i < CAL_WEEKS * 7; i++) {
    const p = _ymdAdd(range.start, i), k = _ymdKey(p);
    const cls = ['cal-day', 'cal-strip', _streetCls(layers, k, today), k === _ymdKey(today) ? 'sw-today' : '',
                 p.m % 2 ? 'cal-alt' : ''].filter(Boolean).join(' ');
    cells += `<button class="${cls}" data-cal-day="${k}">`
           + `<i>${_WD1[_dow(p)]}</i>${p.d}${_dotsHTML(layers, k)}</button>`;
  }
  return `<div class="sw-strip" aria-label="Days">${cells}</div>`;
}

// Month view: weeks in a column, scrolls vertically; `sel` is the selected day key
// (details shown under the grid). The first of each month carries its name.
function calendarHTML(layers, range, sel, today) {
  const todayK = _ymdKey(today);
  let cells = '';
  for (let i = 0; i < CAL_WEEKS * 7; i++) {
    const p = _ymdAdd(range.start, i), k = _ymdKey(p);
    const cls = ['cal-day', _streetCls(layers, k, today), p.m % 2 ? 'cal-alt' : '',
                 k === todayK ? 'sw-today' : '', k === sel ? 'cal-sel' : ''].filter(Boolean).join(' ');
    cells += `<button class="${cls}" data-cal-day="${k}">`
           + (p.d === 1 ? `<i class="cal-mo">${_MONTHS[p.m - 1].slice(0, 3)}</i>` : '')
           + `${p.d}${_dotsHTML(layers, k)}</button>`;
  }
  const detail = sel
    ? `<b>${_dayLabel(_keyDay(sel))}</b>` + (_dayLines(layers, sel).map(l => `<div>${l.mark} ${esc(l.text)}</div>`).join('')
        || (layers.ptr.has(sel) ? `<div>Sweeping: ${esc(layers.ptr.get(sel))}</div>`
                                : '<div class="cal-muted">No sweeping</div>'))
    : '<span class="cal-muted">Tap a day for details</span>';
  return `<div class="cal-wds">${_WD1.map(w => `<span>${w}</span>`).join('')}</div>`
       + `<div class="cal-scroll"><div class="cal-grid">${cells}</div></div><div class="cal-detail">${detail}</div>`;
}

// ── Calendar card ────────────────────────────────────────────────────────────
// Compact: a row of days. Tap a day (or the header) for the month view, a column
// of weeks; the header (up chevron) or Esc closes it. Both scroll continuously
// (swipe, wheel, or ‹ › for a week / a month); Today scrolls back. Its size never
// depends on the target; only day marks change.
const infoCard  = document.getElementById('info-card');
const icHead    = document.getElementById('ic-head');
const icTitle   = document.getElementById('ic-title');
const icCal     = document.getElementById('ic-cal');
const sign      = document.getElementById('street-sign');
const signName  = document.getElementById('ss-name');
const signLines = document.getElementById('ss-lines');
// anchor: window index of the first visible day (strip) or week row (month view).
let _cal = { expanded: isDesktop(), sel: null, anchor: null };  // desktop opens the month view
let _calKey = '';       // last rendered state, to skip no-op re-renders
let _signKey = '';
let _ptrSched = null;   // street schedule under the target (null: none)
let _calRange0 = null;  // the window rendered last
const _schedIds = new WeakMap();
let _schedSeq = 0;

function _schedId(s) {
  if (!_schedIds.has(s)) _schedIds.set(s, ++_schedSeq);
  return _schedIds.get(s);
}

// Street sign for the street at the target: "No parking" + schedule lines as
// published + street name (hidden: no street; muted "clear" form: no sweeping).
function renderStreetSign(props, lines) {
  const key = props ? [props.street, props.render_type, lines.join('~')].join('|') : '';
  if (key === _signKey) return;
  _signKey = key;
  sign.hidden = !props;
  if (!props) return;
  sign.classList.toggle('clear', !lines.length);
  signName.textContent = (props.render_type === 'polygon' ? 'Zone: ' : '') + (props.street || '');
  signLines.innerHTML = lines.length ? lines.map(l => `<div>${esc(l)}</div>`).join('')
                                     : '<div>No sweeping</div>';
}

const _scroller = () => icCal.querySelector(_cal.expanded ? '.cal-scroll' : '.sw-strip');
// Distance between neighbouring days (strip) or weeks (month view), in px.
function _pitch(el) {
  const kids = _cal.expanded ? el.firstElementChild.children : el.children;
  return kids.length > 8 ? (_cal.expanded ? kids[7].offsetTop - kids[0].offsetTop : kids[1].offsetLeft - kids[0].offsetLeft) : 0;
}
function _scrollPos(el) { return _cal.expanded ? el.scrollTop : el.scrollLeft; }
function _scrollTo(el, idx, smooth) {
  const px = idx * _pitch(el);
  el.scrollTo(_cal.expanded ? { top: px, behavior: smooth ? 'smooth' : 'auto' }
                            : { left: px, behavior: smooth ? 'smooth' : 'auto' });
}

// Header title: the month in the middle of what is on screen.
function updateCalTitle() {
  const el = _scroller(), t = document.getElementById('ic-month');
  if (!el || !t || !_calRange0) return;
  const pitch = _pitch(el);
  if (!pitch) return;
  const pos = Math.round(_scrollPos(el) / pitch);
  _cal.anchor = _scrollPos(el) / pitch;
  const mid = _ymdAdd(_calRange0.start, _cal.expanded ? (pos + 2) * 7 + 3 : pos + 3);
  t.textContent = `${_MONTHS[mid.m - 1]} ${mid.y}`;
  t.dataset.y = mid.y; t.dataset.m = mid.m;
}

// f = center feature (undefined: look it up).
function renderInfoCard(f) {
  if (!infoCard) return;
  if (f === undefined) f = _centerFeature();
  const props = f?.properties || null;
  const lines = props ? tileSchedLines(props) : [];
  renderStreetSign(props, lines);
  const today = calNow(), todayK = _ymdKey(today);
  if (_cal.expanded && _cal.sel == null) _cal.sel = todayK;
  _ptrSched = props && lines.length ? pointerSched(f) : null;
  const carSig = cars.map((c, i) => `${c.id}:${c.name}:${i}:${carSchedules[c.id] ? _schedId(carSchedules[c.id]) : 0}`).join(',');
  const key = [_ptrSched ? props.sched : '', carSig, todayK, _cal.expanded, _cal.sel].join('|');
  if (key === _calKey) return;
  _calKey = key;

  infoCard.classList.toggle('expanded', _cal.expanded);
  document.body.classList.toggle('cal-open', _cal.expanded);
  icTitle.innerHTML = `<button class="cal-nav" data-cal-nav="-1" aria-label="${_cal.expanded ? 'Previous month' : 'Previous week'}">‹</button>`
    + `<b id="ic-month"></b>`
    + `<button class="cal-nav" data-cal-nav="1" aria-label="${_cal.expanded ? 'Next month' : 'Next week'}">›</button>`
    + `<button class="cal-today" data-cal-today>Today</button>`;
  icHead.setAttribute('aria-expanded', String(_cal.expanded));
  icHead.title = _cal.expanded ? 'Collapse calendar' : 'Expand calendar';
  const range = _calRange0 = _calRange(today);
  const layers = calLayers(_ptrSched, range.start, range.end);
  icCal.innerHTML = _cal.expanded ? calendarHTML(layers, range, _cal.sel, today)
                                  : sweepStripHTML(layers, range, today);
  const el = _scroller();
  if (_cal.anchor == null) _cal.anchor = _anchorFor(range, today);
  _scrollTo(el, _cal.anchor, false);
  updateCalTitle();
}

// Where a view starts when nothing is remembered: the strip at today, the month
// view at the week holding the 1st of the selected (else current) month.
function _anchorFor(range, today) {
  if (!_cal.expanded) return _dayIdx(range, today);
  const sel = _cal.sel ? _keyDay(_cal.sel) : today;
  return Math.max(0, Math.floor(_dayIdx(range, { y: sel.y, m: sel.m, d: 1 }) / 7));
}

// Earliest sweep day in the next 60 days (street or any car), else today.
function _nextSweepKey() {
  const today = calNow(), L = calLayers(_ptrSched, today, _ymdAdd(today, 60));
  const ks = [...L.ptr.keys(), ...L.cars.flatMap(l => [...l.days.keys()])];
  return ks.length ? Math.min(...ks) : _ymdKey(today);
}

function expandCal(sel) {
  if (sel == null) sel = _nextSweepKey();
  _cal.expanded = true;
  _cal.sel = sel;
  _cal.anchor = null;
  renderInfoCard();
}

function collapseCal() {
  if (!_cal.expanded) return;
  _cal.expanded = false;
  _cal.anchor = null;
  renderInfoCard();
}

// ‹ ›: a week in the strip, a month in the month view (smooth; the scroll event keeps the title current).
function calStep(dir) {
  const el = _scroller(), t = document.getElementById('ic-month');
  if (!el || !t) return;
  const pitch = _pitch(el);
  if (!_cal.expanded) { _scrollTo(el, Math.round(_scrollPos(el) / pitch) + 7 * dir, true); return; }
  const d = new Date(Date.UTC(Number(t.dataset.y), Number(t.dataset.m) - 1 + dir, 1));
  const first = { y: d.getUTCFullYear(), m: d.getUTCMonth() + 1, d: 1 };
  _scrollTo(el, Math.max(0, Math.floor(_dayIdx(_calRange0, first) / 7)), true);
}

// Today: back to today (the month view also selects it).
function calToday() {
  const today = calNow();
  if (_cal.expanded && _cal.sel !== _ymdKey(today)) { _cal.sel = _ymdKey(today); renderInfoCard(); }
  _scrollTo(_scroller(), _anchorFor(_calRange0, today), true);
}

infoCard.addEventListener('click', e => {
  const nav = e.target.closest('[data-cal-nav]');
  if (nav) { e.stopPropagation(); calStep(Number(nav.dataset.calNav)); return; }
  if (e.target.closest('[data-cal-today]')) { calToday(); return; }
  const day = e.target.closest('[data-cal-day]');
  if (!_cal.expanded) {
    expandCal(day ? Number(day.dataset.calDay) : undefined);
  } else if (day) {
    _cal.sel = Number(day.dataset.calDay);
    renderInfoCard();
  } else if (e.target.closest('#ic-head')) {
    collapseCal();
  }
});

// Scroll events do not bubble: catch them on the way down.
let _scrollRaf = 0;
infoCard.addEventListener('scroll', () => {
  if (_scrollRaf) return;
  _scrollRaf = requestAnimationFrame(() => { _scrollRaf = 0; updateCalTitle(); });
}, true);
// A wheel over the strip scrolls it sideways.
infoCard.addEventListener('wheel', e => {
  const strip = e.target.closest('.sw-strip');
  if (!strip || Math.abs(e.deltaX) >= Math.abs(e.deltaY)) return;
  e.preventDefault();
  strip.scrollLeft += e.deltaY;
}, { passive: false });
icHead.addEventListener('keydown', e => {
  if ((e.key !== 'Enter' && e.key !== ' ') || e.target !== icHead) return;  // not ‹ › / Today
  e.preventDefault();
  if (_cal.expanded) collapseCal(); else expandCal();
});

// Card height feeds the mobile sheet's max-height so the two never overlap.
new ResizeObserver(() => {
  document.documentElement.style.setProperty('--info-h', infoCard.offsetHeight + 'px');
}).observe(infoCard);

renderInfoCard();  // initial empty state

// Dismiss whatever transient map window is open — the click-away analogue of Esc.
function dismissMapWindows() {
  if (_gpsLocPin) hideGpsPinPopup();
  clearCarSelection();
}
