// ── Info card: street under the target + one sweep calendar ──────────────────
// Layers: the street under the target (solid red day) and every car with a
// schedule (dot in the car's colour). Days are {y, m, d}; arithmetic and weekday
// come from urgency.js.
const { addDays: _ymdAdd, dayKey: _ymdKey, weekday: _dow, WEEKDAYS } = BroomUrgency;
const _WD1 = WEEKDAYS.map(w => w[0]);
const _MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
                 'August', 'September', 'October', 'November', 'December'];

// Region-local "today" for the map's tile region (browser tz before one loads).
function calNow() {
  return BroomUrgency.nowForTimeZone(regions[_tilesRegion || regionSelect.value]?.tz);
}

// key -> text for sweep days in [start, end]. `side` limits to one street side.
function sweepDayMap(sched, start, end, side = null) {
  const out = new Map();
  if (!sched) return out;
  const labels = sched.side_labels;
  for (const day of BroomUrgency.sweepDays(sched.schedule_even, sched.schedule_odd, start, end)) {
    const items = side ? day.items.filter(it => it.side === side) : day.items;
    if (!items.length) continue;
    const sides = new Set(items.map(it => it.side));
    const text = items.map(it => {
      const t = it.time ? BroomUrgency.timeDisplay(it.time) : 'time n/a';
      return sides.size > 1 ? `${labels[it.side === 'even' ? 0 : 1]} ${t}` : t;
    });
    const where = sides.size > 1 ? '' : `${labels[sides.has('even') ? 0 : 1]} side `;
    out.set(_ymdKey(day), where + [...new Set(text)].join(', '));
  }
  return out;
}

function _dayLabel(p) {
  return `${WEEKDAYS[_dow(p)]} ${_MONTHS[p.m - 1].slice(0, 3)} ${p.d}`;
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

// Street first, then each car with sweeping that day: [{mark (HTML), text}].
function _dayLines(layers, k) {
  const out = [];
  if (layers.ptr.has(k)) out.push({ mark: '📍', text: layers.ptr.get(k) });
  for (const l of layers.cars) {
    if (l.days.has(k)) out.push({ mark: `<i class="cal-key cal-round" style="background:${l.color}"></i>`,
                                  text: `${l.car.name}: ${l.days.get(k)}` });
  }
  return out;
}

function sweepStripHTML(layers, today) {
  let cells = '';
  for (let i = 0; i < 14; i++) {
    const p = _ymdAdd(today, i), k = _ymdKey(p);
    const cls = ['sw-cell', layers.ptr.has(k) ? 'sw-day' : '', i === 0 ? 'sw-today' : ''].filter(Boolean).join(' ');
    const title = `${_dayLabel(p)}: ${_dayLines(layers, k).map(l => l.text).join('; ') || 'no sweeping'}`;
    cells += `<span class="${cls}" data-cal-day="${k}" title="${esc(title)}">`
           + `<i>${_WD1[_dow(p)]}</i>${p.d}${_dotsHTML(layers, k)}</span>`;
  }
  return `<div class="sw-strip" aria-label="Next 14 days">${cells}</div>`;
}

// Month grid; `sel` is the selected day key (details shown under the grid).
function calendarHTML(ptrSched, ym, sel, today) {
  const first = { y: ym.y, m: ym.m, d: 1 };
  const start = _ymdAdd(first, -_dow(first));
  const inMonth = new Date(Date.UTC(ym.y, ym.m, 0)).getUTCDate();
  const n = Math.ceil((_dow(first) + inMonth) / 7) * 7;
  const layers = calLayers(ptrSched, start, _ymdAdd(start, n - 1));
  const todayK = _ymdKey(today);
  let cells = _WD1.map(w => `<span class="cal-wd">${w}</span>`).join('');
  for (let i = 0; i < n; i++) {
    const p = _ymdAdd(start, i), k = _ymdKey(p);
    const cls = ['cal-day', layers.ptr.has(k) ? 'sw-day' : '', p.m !== ym.m ? 'cal-out' : '',
                 k === todayK ? 'sw-today' : '', k === sel ? 'cal-sel' : ''].filter(Boolean).join(' ');
    cells += `<button class="${cls}" data-cal-day="${k}">${p.d}${_dotsHTML(layers, k)}</button>`;
  }
  const detail = sel
    ? `<b>${_dayLabel(_keyDay(sel))}</b>` + (_dayLines(layers, sel).map(l => `<div>${l.mark} ${esc(l.text)}</div>`).join('')
        || '<div class="cal-muted">No sweeping</div>')
    : '<span class="cal-muted">Tap a day for details</span>';
  const legend = (ptrSched ? '<span><i class="cal-key sw-day"></i>street at target</span>' : '')
    + layers.cars.map(l => `<span><i class="cal-key" style="background:${l.color}"></i>${esc(l.car.name)}</span>`).join('');
  return `<div class="cal-head">`
       + `<button class="cal-nav" data-cal-nav="-1" aria-label="Previous month">‹</button>`
       + `<b>${_MONTHS[ym.m - 1]} ${ym.y}</b>`
       + `<button class="cal-nav" data-cal-nav="1" aria-label="Next month">›</button></div>`
       + `<div class="cal-grid">${cells}</div>`
       + `<div class="cal-detail">${detail}</div>`
       + `<div class="cal-legend">${legend}</div>`;
}

// ── Info card ────────────────────────────────────────────────────────────────
// Compact: street name, schedule lines, 14-day strip. Tap anywhere on the card
// to open the month view; tap the header (chevron) or press Esc to close it.
const infoCard  = document.getElementById('info-card');
const icHead    = document.getElementById('ic-head');
const icTitle   = document.getElementById('ic-title');
const icLines   = document.getElementById('ic-lines');
const icCal     = document.getElementById('ic-cal');
let _cal = { expanded: false, ym: null, sel: null, srcKey: '' };
let _calKey = '';       // last rendered state, to skip no-op re-renders
let _ptrSched = null;   // street schedule under the target (null: none)
const _schedIds = new WeakMap();
let _schedSeq = 0;

function _schedId(s) {
  if (!_schedIds.has(s)) _schedIds.set(s, ++_schedSeq);
  return _schedIds.get(s);
}

// f = center feature (undefined: look it up).
function renderInfoCard(f) {
  if (!infoCard) return;
  if (f === undefined) f = _centerFeature();
  const props = f?.properties || null;
  const street = props ? (props.street || '') : '';
  const srcKey = props ? 'ptr:' + street : '';
  const today = calNow(), todayK = _ymdKey(today);
  if (srcKey !== _cal.srcKey) {                     // new street: back to today's month
    _cal.srcKey = srcKey;
    _cal.sel = null;
    _cal.ym = { y: today.y, m: today.m };
  }
  _cal.ym = _cal.ym || { y: today.y, m: today.m };
  const lines = props ? tileSchedLines(props) : [];
  _ptrSched = props && lines.length ? pointerSched(f) : null;
  const carSig = cars.map((c, i) => `${c.id}:${c.name}:${i}:${carSchedules[c.id] ? _schedId(carSchedules[c.id]) : 0}`).join(',');
  const key = [srcKey, props?.sched, lines.join('~'), carSig, todayK, _cal.expanded,
               _cal.ym.y * 100 + _cal.ym.m, _cal.sel].join('|');
  if (key === _calKey) return;
  _calKey = key;

  infoCard.classList.toggle('expanded', _cal.expanded);
  icHead.setAttribute('aria-expanded', String(_cal.expanded));
  icHead.title = _cal.expanded ? 'Collapse calendar' : 'Expand calendar';
  icTitle.textContent = !props ? '📍 No street at target'
    : (props.render_type === 'polygon' ? '📍 Zone: ' : '📍 ') + street;
  icLines.hidden = _cal.expanded;
  icLines.innerHTML = !props ? '<div class="sc-muted">Pan the target onto a street.</div>'
    : lines.length ? lines.map(l => `<div class="sc-line">${esc(l)}</div>`).join('')
    : '<div class="sc-muted">No sweeping scheduled.</div>';
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
