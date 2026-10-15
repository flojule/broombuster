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

function sweepStripHTML(layers, today) {
  let cells = '';
  for (let i = 0; i < 14; i++) {
    const p = _ymdAdd(today, i), k = _ymdKey(p);
    const cls = ['sw-cell', _streetCls(layers, k, today), i === 0 ? 'sw-today' : ''].filter(Boolean).join(' ');
    const title = `${_dayLabel(p)}: ${_dayLines(layers, k).map(l => l.text).join('; ') || (layers.ptr.has(k) ? `Sweeping: ${layers.ptr.get(k)}` : 'No sweeping')}`;
    cells += `<span class="${cls}" data-cal-day="${k}" title="${esc(title)}">`
           + `<i>${_WD1[_dow(p)]}</i>${p.d}${_dotsHTML(layers, k)}</span>`;
  }
  return `<div class="sw-strip" aria-label="Next 14 days">${cells}</div>`;
}

// Month grid; `sel` is the selected day key (details shown under the grid).
function calendarHTML(ptrSched, ym, sel, today) {
  const first = { y: ym.y, m: ym.m, d: 1 };
  const start = _ymdAdd(first, -_dow(first));
  const n = 42;  // six weeks always: the card keeps its shape across months
  const layers = calLayers(ptrSched, start, _ymdAdd(start, n - 1));
  const todayK = _ymdKey(today);
  let cells = _WD1.map(w => `<span class="cal-wd">${w}</span>`).join('');
  for (let i = 0; i < n; i++) {
    const p = _ymdAdd(start, i), k = _ymdKey(p);
    const cls = ['cal-day', _streetCls(layers, k, today), p.m !== ym.m ? 'cal-out' : '',
                 k === todayK ? 'sw-today' : '', k === sel ? 'cal-sel' : ''].filter(Boolean).join(' ');
    cells += `<button class="${cls}" data-cal-day="${k}">${p.d}${_dotsHTML(layers, k)}</button>`;
  }
  const detail = sel
    ? `<b>${_dayLabel(_keyDay(sel))}</b>` + (_dayLines(layers, sel).map(l => `<div>${l.mark} ${esc(l.text)}</div>`).join('')
        || (layers.ptr.has(sel) ? `<div>Sweeping: ${esc(layers.ptr.get(sel))}</div>`
                                : '<div class="cal-muted">No sweeping</div>'))
    : '<span class="cal-muted">Tap a day for details</span>';
  return `<div class="cal-grid">${cells}</div><div class="cal-detail">${detail}</div>`;
}

// ── Calendar card ────────────────────────────────────────────────────────────
// Compact: 14-day strip. Tap anywhere on the card to open the month view; tap
// the header (chevron) or press Esc to close it. Its size never depends on the
// target; only day marks change.
const infoCard  = document.getElementById('info-card');
const icHead    = document.getElementById('ic-head');
const icTitle   = document.getElementById('ic-title');
const icCal     = document.getElementById('ic-cal');
const sign      = document.getElementById('street-sign');
const signName  = document.getElementById('ss-name');
const signLines = document.getElementById('ss-lines');
let _cal = { expanded: isDesktop(), ym: null, sel: null };  // desktop opens the month view
let _calKey = '';       // last rendered state, to skip no-op re-renders
let _signKey = '';
let _ptrSched = null;   // street schedule under the target (null: none)
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

// f = center feature (undefined: look it up).
function renderInfoCard(f) {
  if (!infoCard) return;
  if (f === undefined) f = _centerFeature();
  const props = f?.properties || null;
  const lines = props ? tileSchedLines(props) : [];
  renderStreetSign(props, lines);
  const today = calNow(), todayK = _ymdKey(today);
  _cal.ym = _cal.ym || { y: today.y, m: today.m };
  if (_cal.expanded && _cal.sel == null) _cal.sel = todayK;
  _ptrSched = props && lines.length ? pointerSched(f) : null;
  const carSig = cars.map((c, i) => `${c.id}:${c.name}:${i}:${carSchedules[c.id] ? _schedId(carSchedules[c.id]) : 0}`).join(',');
  const key = [_ptrSched ? props.sched : '', carSig, todayK, _cal.expanded,
               _cal.ym.y * 100 + _cal.ym.m, _cal.sel].join('|');
  if (key === _calKey) return;
  _calKey = key;

  infoCard.classList.toggle('expanded', _cal.expanded);
  document.body.classList.toggle('cal-open', _cal.expanded);
  document.getElementById('ic-chevron').innerHTML = icon(_cal.expanded ? 'close' : 'chevron');
  // Month view: the header row carries the month nav (saves a row).
  icTitle.innerHTML = _cal.expanded
    ? `<button class="cal-nav" data-cal-nav="-1" aria-label="Previous month">‹</button>`
      + `<b>${_MONTHS[_cal.ym.m - 1]} ${_cal.ym.y}</b>`
      + `<button class="cal-nav" data-cal-nav="1" aria-label="Next month">›</button>`
    : `${icon('calendar')}&nbsp;Calendar`;
  icHead.setAttribute('aria-expanded', String(_cal.expanded));
  icHead.title = _cal.expanded ? 'Collapse calendar' : 'Expand calendar';
  icCal.innerHTML = _cal.expanded
    ? calendarHTML(_ptrSched, _cal.ym, _cal.sel, today)
    : sweepStripHTML(calLayers(_ptrSched, today, _ymdAdd(today, 13)), today);
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
  _cal.ym = { y: _keyDay(sel).y, m: _keyDay(sel).m };
  renderInfoCard();
}

function collapseCal() {
  if (!_cal.expanded) return;
  _cal.expanded = false;
  renderInfoCard();
}

infoCard.addEventListener('click', e => {
  if (!_cal.expanded) {
    const day = e.target.closest('[data-cal-day]');
    expandCal(day ? Number(day.dataset.calDay) : undefined);
    return;
  }
  const nav = e.target.closest('[data-cal-nav]');
  const day = e.target.closest('[data-cal-day]');
  if (nav) {
    const d = new Date(Date.UTC(_cal.ym.y, _cal.ym.m - 1 + Number(nav.dataset.calNav), 1));
    _cal.ym = { y: d.getUTCFullYear(), m: d.getUTCMonth() + 1 };
    renderInfoCard();
  } else if (day) {
    _cal.sel = Number(day.dataset.calDay);
    renderInfoCard();
  } else if (e.target.closest('#ic-head')) {
    collapseCal();
  }
});
icHead.addEventListener('keydown', e => {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  e.preventDefault();
  if (_cal.expanded) collapseCal(); else expandCal();
});

// Hover tooltips stay off while the pointer is on the card.
infoCard.addEventListener('mouseenter', () => { _hoverSuppressed = true;  customHoverEl.style.display = 'none'; });
infoCard.addEventListener('mouseleave', () => { _hoverSuppressed = false; });

// Card height feeds the mobile sheet's max-height so the two never overlap.
new ResizeObserver(() => {
  document.documentElement.style.setProperty('--info-h', infoCard.offsetHeight + 'px');
}).observe(infoCard);

renderInfoCard();  // initial empty state

// Dismiss whatever transient map window is open — the click-away analogue of Esc.
function dismissMapWindows() {
  if (_gpsLocPin) hideGpsPinPopup();
  closeZoneDetail();
  clearCarSelection();
}
