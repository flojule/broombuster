// ── Sweep calendar: 14-day strip on the card, month grid in the detail window ──
// Days are {y, m, d}; arithmetic and weekday come from urgency.js.
const { addDays: _ymdAdd, dayKey: _ymdKey, weekday: _dow, WEEKDAYS } = BroomUrgency;
const _WD1 = WEEKDAYS.map(w => w[0]);
const _MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
                 'August', 'September', 'October', 'November', 'December'];

// key -> {cls, text} for sweep days in [start, end]. 'sw-car' = the car's side
// (or any side when unknown), 'sw-other' = only the opposite side, 'sw-ptr' =
// the street under the map pointer (no car).
function sweepDayMap(sched, start, end) {
  const out = new Map();
  if (!sched) return out;
  const labels = sched.side_labels;
  for (const day of BroomUrgency.sweepDays(sched.schedule_even, sched.schedule_odd, start, end)) {
    const mine = !sched.car_side || day.items.some(it => it.side === sched.car_side);
    const sides = new Set(day.items.map(it => it.side));
    const text = day.items.map(it => {
      const t = it.time ? BroomUrgency.timeDisplay(it.time) : 'time n/a';
      return sides.size > 1 ? `${labels[it.side === 'even' ? 0 : 1]} ${t}` : t;
    });
    const where = sides.size > 1 ? '' : `${labels[sides.has('even') ? 0 : 1]} side `;
    out.set(_ymdKey(day), { cls: sched.pointer ? 'sw-ptr' : mine ? 'sw-car' : 'sw-other',
                            text: where + [...new Set(text)].join(', ') });
  }
  return out;
}

function _dayLabel(p) {
  return `${WEEKDAYS[_dow(p)]} ${_MONTHS[p.m - 1].slice(0, 3)} ${p.d}`;
}

function sweepStripHTML(sched) {
  const today = schedNow(sched);
  const days = sweepDayMap(sched, today, _ymdAdd(today, 13));
  let cells = '';
  for (let i = 0; i < 14; i++) {
    const p = _ymdAdd(today, i), info = days.get(_ymdKey(p));
    const cls = ['sw-cell', info?.cls, i === 0 ? 'sw-today' : ''].filter(Boolean).join(' ');
    const title = `${_dayLabel(p)}: ${info ? info.text : 'no sweeping'}`;
    cells += `<span class="${cls}" data-cal-day="${_ymdKey(p)}" title="${esc(title)}"><i>${_WD1[_dow(p)]}</i>${p.d}</span>`;
  }
  return `<div class="sw-strip" aria-label="Next 14 days">${cells}</div>`;
}

// Month grid; `sel` is the selected day key (details shown under the grid).
function calendarHTML(sched, ym, sel) {
  const first = { y: ym.y, m: ym.m, d: 1 };
  const start = _ymdAdd(first, -_dow(first));
  const inMonth = new Date(Date.UTC(ym.y, ym.m, 0)).getUTCDate();
  const n = Math.ceil((_dow(first) + inMonth) / 7) * 7;
  const days = sweepDayMap(sched, start, _ymdAdd(start, n - 1));
  const todayK = _ymdKey(schedNow(sched));
  let cells = _WD1.map(w => `<span class="cal-wd">${w}</span>`).join('');
  for (let i = 0; i < n; i++) {
    const p = _ymdAdd(start, i), k = _ymdKey(p), info = days.get(k);
    const cls = ['cal-day', info?.cls, p.m !== ym.m ? 'cal-out' : '',
                 k === todayK ? 'sw-today' : '', k === sel ? 'cal-sel' : ''].filter(Boolean).join(' ');
    cells += `<button class="${cls}" data-cal-day="${k}">${p.d}</button>`;
  }
  const selP = sel ? { y: Math.floor(sel / 10000), m: Math.floor(sel / 100) % 100, d: sel % 100 } : null;
  const selInfo = sel ? days.get(sel) : null;
  const detail = selP ? `${_dayLabel(selP)}: ${selInfo ? selInfo.text : 'no sweeping'}` : 'Tap a day for details';
  const legend = sched.pointer
    ? '<span class="cal-key sw-ptr"></span> sweeping at pointer'
    : sched.car_side
    ? '<span class="cal-key sw-car"></span> your side <span class="cal-key sw-other"></span> other side'
    : '<span class="cal-key sw-car"></span> sweeping';
  return `<div class="cal-head">`
       + `<button class="cal-nav" data-cal-nav="-1" aria-label="Previous month">‹</button>`
       + `<b>${_MONTHS[ym.m - 1]} ${ym.y}</b>`
       + `<button class="cal-nav" data-cal-nav="1" aria-label="Next month">›</button></div>`
       + `<div class="cal-grid">${cells}</div>`
       + `<div class="cal-detail">${esc(detail)}</div>`
       + `<div class="cal-legend">${legend}</div>`;
}

// ── Calendar tab (mobile + desktop) ─────────────────────────────────────────
// Source: the selected car's schedule, else the street under the map pointer
// (violet visuals). Compact 14-day strip; tap to expand to the month grid.
const calTab     = document.getElementById('cal-tab');
const calTitle   = document.getElementById('cal-tab-title');
const calBody    = document.getElementById('cal-tab-body');
let _calRetracted = localStorage.getItem('bb_cal_v2') === 'closed';  // default: open
let _cal = { expanded: false, ym: null, sel: null, srcKey: '' };
let _calSched = null;   // schedule currently shown (null when none)
let _calKey = '';       // last rendered state, to skip no-op re-renders
const _schedIds = new WeakMap();
let _schedSeq = 0;

// /check-shaped schedule for the pointer feature (tile entries -> [code, desc, time]).
function pointerSched(f) {
  const props = f?.properties;
  if (!props || !_tilesRegion) return null;
  let raw = [];
  try { raw = JSON.parse(props.sched || '[]') || []; } catch (_) {}
  const side = s => raw.filter(e => e.side === s).map(e => [e.code, e.desc || '', e.time || '']);
  return { pointer: true, region: _tilesRegion, car_side: null,
           schedule_even: side('even'), schedule_odd: side('odd'),
           side_labels: [props.side_even || 'Even', props.side_odd || 'Odd'] };
}

// {id, title, sched, sig} for the calendar, or null. f = center feature (undefined: look it up).
function calSource(f) {
  const car = cars.find(c => c.id === _selectedCarId);
  const cs = car && carSchedules[car.id];
  if (cs) {
    if (!_schedIds.has(cs)) _schedIds.set(cs, ++_schedSeq);
    return { id: 'car:' + car.id, title: '🚗 ' + car.name, sched: cs, sig: _schedIds.get(cs) };
  }
  if (f === undefined) f = _centerFeature();
  const ps = pointerSched(f);
  if (!ps) return null;
  const p = f.properties, street = p.street || '';
  return { id: 'ptr:' + street, title: (p.render_type === 'polygon' ? '📍 Zone: ' : '📍 ') + street,
           sched: ps, sig: p.sched };
}

function _nextSweepKey(sched) {
  const today = schedNow(sched);
  return sweepDayMap(sched, today, _ymdAdd(today, 60)).keys().next().value ?? null;
}
function _ymOf(k) { return { y: Math.floor(k / 10000), m: Math.floor(k / 100) % 100 }; }

function hasSweepLines(sched) {
  return BroomUrgency.formatBothSides(sched.schedule_even, sched.schedule_odd, schedNow(sched),
                                      sched.car_side, sched.side_labels).length > 0;
}

function renderCalTab(f) {
  if (!calTab) return;
  const src = calSource(f);
  const sched = src && hasSweepLines(src.sched) ? src.sched : null;
  const todayK = src ? _ymdKey(schedNow(src.sched)) : 0;
  if ((src?.id || '') !== _cal.srcKey) {            // new source: back to today's month
    _cal.srcKey = src?.id || '';
    _cal.sel = null;
    _cal.ym = src ? { y: Math.floor(todayK / 10000), m: Math.floor(todayK / 100) % 100 } : null;
  }
  const key = [src?.id, src?.sig, !!sched, todayK, _calRetracted, _cal.expanded,
               _cal.ym && _cal.ym.y * 100 + _cal.ym.m, _cal.sel].join('|');
  if (key === _calKey) return;
  _calKey = key;
  _calSched = sched;

  calTab.classList.toggle('retracted', _calRetracted);
  calTitle.textContent = src ? src.title : '📍 Pan the target onto a street';
  if (_calRetracted) return;
  if (!src) { calBody.innerHTML = '<div class="cal-empty">Sweep dates appear here for the street under the target.</div>'; return; }
  if (!sched) { calBody.innerHTML = '<div class="cal-empty">No sweeping scheduled.</div>'; return; }
  calBody.innerHTML = _cal.expanded
    ? calendarHTML(sched, _cal.ym, _cal.sel)
        + '<button class="cal-compact" data-cal-compact>▴ compact view</button>'
    : sweepStripHTML(sched) + '<div class="cal-hint" data-cal-expand>Tap for month view</div>';
}

function expandCal(sel) {
  if (!_calSched) return;
  if (sel == null) sel = _nextSweepKey(_calSched);   // preselect the next sweep day
  _cal.expanded = true;
  _cal.sel = sel;
  if (sel != null) _cal.ym = _ymOf(sel);
  renderCalTab();
}

function setCalRetracted(v) {
  _calRetracted = !!v;
  localStorage.setItem('bb_cal_v2', _calRetracted ? 'closed' : 'open');
  renderCalTab();
}

// Car card strip/header: select the car and show its month calendar. On mobile
// the sheet drops so the calendar tab is visible.
function showCarCalendar(carId) {
  _calRetracted = false;
  localStorage.setItem('bb_cal_v2', 'open');
  setSelectedCar(carId);
  expandCal();
  if (window.matchMedia('(max-width: 767px)').matches) setSheetCollapsed(true);
}

document.getElementById('cal-tab-handle').addEventListener('click', () => setCalRetracted(!_calRetracted));
document.getElementById('cal-tab-handle').addEventListener('keydown', e => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setCalRetracted(!_calRetracted); }
});
calBody.addEventListener('click', e => {
  const nav = e.target.closest('[data-cal-nav]');
  const day = e.target.closest('[data-cal-day]');
  if (nav && _cal.ym) {
    const d = new Date(Date.UTC(_cal.ym.y, _cal.ym.m - 1 + Number(nav.dataset.calNav), 1));
    _cal.ym = { y: d.getUTCFullYear(), m: d.getUTCMonth() + 1 };
    renderCalTab();
  } else if (day) {
    if (_cal.expanded) { _cal.sel = Number(day.dataset.calDay); renderCalTab(); }
    else expandCal(Number(day.dataset.calDay));
  } else if (e.target.closest('[data-cal-compact]')) {
    _cal.expanded = false; renderCalTab();
  } else if (e.target.closest('[data-cal-expand]')) {
    expandCal();
  }
});

renderCalTab();  // initial empty-state text

// Dismiss whatever transient map window is open — the click-away analogue of Esc.
function dismissMapWindows() {
  if (_gpsLocPin) hideGpsPinPopup();
  closeZoneDetail();
  clearCarSelection();
}
