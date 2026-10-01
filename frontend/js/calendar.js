// ── Sweep calendar: 14-day strip on the card, month grid in the detail window ──
// Days are {y, m, d}; arithmetic and weekday come from urgency.js.
const { addDays: _ymdAdd, dayKey: _ymdKey, weekday: _dow, WEEKDAYS } = BroomUrgency;
const _WD1 = WEEKDAYS.map(w => w[0]);
const _MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
                 'August', 'September', 'October', 'November', 'December'];

// key -> {cls, text} for sweep days in [start, end]. 'sw-car' = the car's side
// (or any side when unknown), 'sw-other' = only the opposite side.
function sweepDayMap(sched, start, end) {
  const out = new Map();
  const sw = sweepOf(sched);
  if (!sw) return out;
  const labels = sw.side_labels;
  for (const day of BroomUrgency.sweepDays(sw.schedule_even, sw.schedule_odd, start, end)) {
    const mine = !sw.car_side || day.items.some(it => it.side === sw.car_side);
    const sides = new Set(day.items.map(it => it.side));
    const text = day.items.map(it => {
      const t = it.time ? BroomUrgency.timeDisplay(it.time) : 'time n/a';
      return sides.size > 1 ? `${labels[it.side === 'even' ? 0 : 1]} ${t}` : t;
    });
    const where = sides.size > 1 ? '' : `${labels[sides.has('even') ? 0 : 1]} side `;
    out.set(_ymdKey(day), { cls: mine ? 'sw-car' : 'sw-other',
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
    cells += `<span class="${cls}" title="${esc(title)}"><i>${_WD1[_dow(p)]}</i>${p.d}</span>`;
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
  const legend = sweepOf(sched)?.car_side
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

// Subscribable .ics feed for the car's parked spot (side=auto: car's side).
function calendarFeedUrl(car, sched) {
  const q = new URLSearchParams({ lat: car.lat.toFixed(5), lon: car.lon.toFixed(5) });
  if (sched.region) q.set('region', sched.region);
  return `${location.origin}/calendar.ics?${q}`;
}

// ── Card schedule detail window (toggle, no arrow) ─────────────────────────────
// Month calendar + the street/ward detail + calendar-feed links, shown as a
// fixed panel. Clicking the same card's header again closes it.
let _cardDetailCarId = null;
let _cal = null;  // {ym: {y, m}, sel: dayKey | null} for the open window
function renderCardDetail() {
  const car = cars.find(c => c.id === _cardDetailCarId);
  const sched = car && carSchedules[car.id];
  if (!sched) { closeCardDetail(); return; }
  const url = calendarFeedUrl(car, sched);
  document.getElementById('card-detail-body').innerHTML =
      calendarHTML(sched, _cal.ym, _cal.sel)
    + `<div class="cal-info">${sweepOf(sched)?.detail_html || ''}</div>`
    + `<div class="cal-feed">📅 <a class="zd-link" href="${esc(url.replace(/^https?:/, 'webcal:'))}">Subscribe</a>`
    + ` · <a class="zd-link" href="#" data-cal-copy="${esc(url)}">Copy link</a>`
    + ` · <a class="zd-link" href="${esc(url)}" download="street-sweeping.ics">.ics</a>`
    + `<div class="cal-note">Feed follows this parked spot; resubscribe after moving.</div></div>`;
}
function openCardDetail(carId) {
  const sched = carSchedules[carId];
  if (!sched) return;
  const today = schedNow(sched);
  // Preselect the next sweep day within 60 days so the details line is useful.
  const next = sweepDayMap(sched, today, _ymdAdd(today, 60)).keys().next().value ?? null;
  _cardDetailCarId = carId;
  _cal = { ym: { y: today.y, m: today.m }, sel: next };
  if (next) _cal.ym = { y: Math.floor(next / 10000), m: Math.floor(next / 100) % 100 };
  renderCardDetail();
  document.getElementById('card-detail').style.display = 'block';
}
function closeCardDetail() {
  document.getElementById('card-detail').style.display = 'none';
  _cardDetailCarId = null;
  _cal = null;
}
function toggleCardDetail(carId) {
  if (_cardDetailCarId === carId) closeCardDetail();
  else openCardDetail(carId);
}
document.getElementById('card-detail-body').addEventListener('click', e => {
  const nav = e.target.closest('[data-cal-nav]');
  const day = e.target.closest('[data-cal-day]');
  const copy = e.target.closest('[data-cal-copy]');
  if (nav) {
    const d = new Date(Date.UTC(_cal.ym.y, _cal.ym.m - 1 + Number(nav.dataset.calNav), 1));
    _cal.ym = { y: d.getUTCFullYear(), m: d.getUTCMonth() + 1 };
  } else if (day) {
    _cal.sel = Number(day.dataset.calDay);
  } else if (copy) {
    e.preventDefault();
    navigator.clipboard?.writeText(copy.dataset.calCopy)
      .then(() => showToast('Calendar link copied'), () => showToast('Copy failed', true));
    return;
  } else {
    return;
  }
  renderCardDetail();
});
document.getElementById('btn-card-detail-close').addEventListener('click', closeCardDetail);

// Dismiss whatever transient map window is open — the click-away analogue of Esc.
function dismissMapWindows() {
  if (_gpsLocPin) hideGpsPinPopup();
  closeZoneDetail();
  closeCardDetail();
  clearCarSelection();
}
