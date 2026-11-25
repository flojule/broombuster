// ── Per-car schedule checks ───────────────────────────────────────────────────
// `loud`: tell the user about a slow server or an error (a check they asked for);
// otherwise a failure only shows on the card, with Retry.
async function checkCar(car, loud = false) {
  const warmupTimer = loud && setTimeout(() => showToast('Server warming up'), 5000);
  // Offline the card says so ("Offline · as of …"); a toast would only repeat the chip.
  const data = await loadSchedule(car.id, carSchedules, () => postCheck(car.lat, car.lon),
                                  loud ? msg => navigator.onLine && showToast(msg, true, { retry: () => checkCar(car, true) }) : null);
  clearTimeout(warmupTimer);
  if (data) { data._at = Date.now(); _saveSchedCache(); upgradeAddress(car, data); }
  updateCarMarkers();  // re-renders the cards (data, or the Retry state)
  if (await loadRules(car, 'car')) renderCarsPanel();
}

// /check answers without the Nominatim house number (it can take ~1 s); when
// it flags one as possible, fetch the full server address and re-render.
async function upgradeAddress(car, sched) {
  if (!sched?.address_pending) return;
  const q = new URLSearchParams({ lat: car.lat, lon: car.lon });
  if (sched.region) q.set('region', sched.region);
  try {
    const res = await fetch(`/api/address?${q}`);
    if (!res.ok) return;
    const { address } = await res.json();
    // Ignore a stale answer: the car was re-checked (moved) meanwhile.
    if (!address || carSchedules[car.id] !== sched) return;
    sched.address = address;
    sched.address_pending = false;
    renderCarsPanel();
  } catch (_) {}
}

// Cars by sweeping verdict; feeds the tab's badge.
function urgentCars() {
  const out = { today: [], tomorrow: [] };
  for (const car of cars) out[sweepUrgency(car)]?.push(car);
  return out;
}

// Each car's last /check answer, kept on this device with its time and position: offline
// (a parking garage) the car's page still has its schedule, "as of" then.
const SCHED_KEY = 'bb_sched';
function _saveSchedCache() {
  const out = {};
  for (const c of cars) {
    const s = carSchedules[c.id];
    if (s?._at) out[c.id] = { lat: c.lat, lon: c.lon, s };
  }
  _store(SCHED_KEY, JSON.stringify(out));
}
function loadSchedCache() {
  let saved = {};
  try { saved = JSON.parse(_read(SCHED_KEY)) || {}; } catch (_) {}
  for (const c of cars) {
    const e = saved[c.id];
    if (e && e.lat === c.lat && e.lon === c.lon && !carSchedules[c.id]) carSchedules[c.id] = e.s;
  }
}

// The user moved a car (GPS, map or address): save it, show it, and re-check its street.
// The old spot's schedule, side and rules no longer apply.
function moveCar(car, lat, lon) {
  car.lat = lat; car.lon = lon; touchCarLocation(car);
  delete car.side;
  delete carSchedules[car.id];
  forgetRules(car.id);
  savePrefs();
  updateCarMarkers();
  return checkCar(car, true);
}

// ── Placement (cars and homes) ────────────────────────────────────────────────
// Center-target flow: pan the map so the crosshair sits on the spot, then
// "Set here" commits at the map center. The sheet lowers to its tab bar so the map
// shows; it returns to its height after (or once a tab or a drag brings it up). A right-click sets an off-center spot (data.js).
// Places (or, with an id, moves) a car or home; the banner shows its icon.
// at: {lat, lon} to start from (a GPS fix, adjusted by hand before OK); else a new one
// starts where you are, when the app knows (people add a car once they've parked).
function startPlacing(editId = null, kind = 'car', at = null) {
  const p = placing = { kind, editId };
  // The street under the target follows as the map moves (js/signs.js _placeBannerStreet).
  placeBannerText.innerHTML = icon(editId ? 'pin' : kind)
    + `<span><span class="sr-only">${editId ? 'Set location of' : 'Place'} ${kind}: </span><span class="pb-street">Pan to spot</span></span>`;
  if (!isDesktop()) setCompact(true);
  document.body.classList.add('placing');
  placeBanner.classList.add('active');
  _crosshair.classList.add('placing');
  btnConfirmPlace.focus();
  syncHistory();
  if (!map) return;
  renderTargetInfo();
  map.getCanvas().style.cursor = 'crosshair';
  const goHere = (lat, lon) => map.jumpTo({ center: [lon, lat], zoom: Math.max(map.getZoom(), 17), padding: mapPadding() });
  if (at) { goHere(at.lat, at.lon); return; }
  if (editId) return;
  if (_here) { goHere(_here.lat, _here.lon); return; }
  // No dot yet: a fix only if location is already allowed (no prompt), and only while
  // this placement is on and the map hasn't been panned meanwhile.
  const c0 = map.getCenter();
  getPermittedGPS().then(fix => {
    if (fix && placing === p && map.getCenter().distanceTo(c0) < 1) goHere(fix.lat, fix.lon);
  });
}

function stopPlacing() {
  if (!placing) return;
  placing = null;
  placeBanner.classList.remove('active');
  document.body.classList.remove('placing');
  _crosshair.classList.remove('placing');
  if (map) map.getCanvas().style.cursor = '';
  setCompact(false);
  syncHistory();
}

// Commit at the current map center (the point the crosshair marks).
function confirmPlacementAtCenter() {
  if (!map || !placing) return;
  // Zoomed out the target covers a block: zoom in on it first.
  if (map.getZoom() < PLACE_ZOOM) { map.easeTo({ zoom: PLACE_ZOOM + 1 }); return; }
  // A second click of a double-click lands on the map once the banner is gone: no zoom.
  map.doubleClickZoom.disable();
  setTimeout(() => map.doubleClickZoom.enable(), 500);
  const { lat, lng } = map.getCenter();
  const { kind, editId } = placing;
  stopPlacing();
  if (kind === 'home') { if (editId) moveHome(editId, lat, lng); else addHome(lat, lng); return; }
  const car = editId && cars.find(c => c.id === editId);
  if (car) { moveCar(car, lat, lng); openItem(car.id); }
  else addCar(lat, lng);  // a default name: renamed on its page
}

btnConfirmPlace.addEventListener('click', confirmPlacementAtCenter);
btnCancelPlace.addEventListener('click', stopPlacing);

// ── "+": what to add (as the "+" on Find My's lists): a car or a home, placed with
// the center target. Anchored menus (this and an item's "…") share the code below.
const addMenu = document.getElementById('add-menu');
menuButton(btnAdd, addMenu);
addMenu.addEventListener('click', e => {
  const b = e.target.closest('[data-add]');
  if (!b) return;
  closeMenu();
  startPlacing(null, b.dataset.add);
});

// ── "…": the open item's settings (as the "…" on Apple Maps' place cards) ─────
// Rebuilt each time it opens, for the item open then.
const itemMenu = document.getElementById('item-menu');
const _menuRow = (act, label, ic, danger) =>
  `<button role="menuitem" class="ctx-item${danger ? ' danger' : ''}" data-act="${act}">${icon(ic)} ${esc(label)}</button>`;
menuButton(btnItemMore, itemMenu, () => {
  const item = selectedItem(), car = cars.includes(item);
  itemMenu.innerHTML = [
    _menuRow('place', 'Set location', 'pin'),
    _menuRow('address', 'Enter address', 'search'),
    car && _menuRow('rename', 'Rename', 'pencil'),
    '<hr>',
    _menuRow('remove', 'Remove', 'trash', true),
  ].filter(Boolean).join('');
});
itemMenu.addEventListener('click', async e => {
  const act = e.target.closest('[data-act]')?.dataset.act;
  const item = selectedItem();
  if (!act || !item) return;
  closeMenu();
  const car = cars.includes(item) ? item : null, name = itemName(item);
  const entry = carsPanel.querySelector(`.car-entry[data-id="${CSS.escape(item.id)}"]`);
  if (act === 'place') startPlacing(item.id, car ? 'car' : 'home');
  else if (act === 'address') entry?.querySelector('.ce-addr').startEdit();
  else if (act === 'rename') savedTitle.startEdit();
  else if (act === 'remove') {
    // Focus goes back to "…" on Cancel, to "+" on the list once it is gone.
    if (!await askConfirm(`Remove ${name}?`, car ? 'Its location is forgotten.' : 'Its trash days are no longer shown.', 'Remove')) {
      btnItemMore.focus();
      return;
    }
    const undo = car ? removeCar(car.id) : removeHome(item.id);
    (btnAdd.hidden ? savedSummary.querySelector('[data-parked]') : btnAdd)?.focus();  // nothing left: "Parked here"
    showToast(`${name} removed`, false, { ms: 6000, action: { label: 'Undo', run: () => {
      undo();
      carsPanel.querySelector(`.car-entry[data-id="${CSS.escape(item.id)}"] .ce-header`)?.focus();  // focus back on its row
    } } });
  }
});

// Returns the undo: the car back where it was in the list, with its schedule.
function removeCar(id) {
  const i = cars.findIndex(c => c.id === id), car = cars[i], sched = carSchedules[id];
  delete carSchedules[id];
  cars = cars.filter(c => c.id !== id);
  savePrefs();
  updateCarMarkers();  // drops its marker and re-renders the cards
  return () => {
    if (cars.some(c => c.id === id)) return;
    cars.splice(Math.min(i, cars.length), 0, car);
    if (sched) carSchedules[id] = sched;
    savePrefs();
    updateCarMarkers();
    if (!sched) checkCar(car);
  };
}

// ── Context menu: "Add car / home here" (right-click, or a long press on touch) ──
// At the finger (or the pointer), as the other menus are (ui.js).
function openCtxMenu(lngLat, x, y) {
  pendingLat = lngLat.lat; pendingLon = lngLat.lng;
  openMenu(ctxMenu, null, { x, y });
}

// Both go through placement from the spot pressed: adjusted, then Set here (F8).
ctxAddCar.addEventListener('click', () => { closeMenu(); startPlacing(null, 'car', { lat: pendingLat, lon: pendingLon }); });
ctxAddHome.addEventListener('click', () => { closeMenu(); startPlacing(null, 'home', { lat: pendingLat, lon: pendingLon }); });

// A new car at (lat, lon), named (else "Car", "Car 1", …: it can be renamed on its page);
// its page opens with the schedule as it loads.
function addCar(lat, lon, name = '') {
  const car = { id: newId(), name: name || defaultCarName(), lat, lon, color: nextCarColor() };
  touchCarLocation(car);
  cars.push(car);
  savePrefs();
  updateCarMarkers();
  openItem(car.id);
  checkCar(car);
  offerInstall();
}

function defaultCarName() {
  const used = new Set(cars.map(c => c.name));
  if (!used.has('Car')) return 'Car';
  let i = 1;
  while (used.has(`Car ${i}`)) i++;
  return `Car ${i}`;
}

// ── Cards (cars and homes share the shell) ────────────────────────────────────
// Region-local clock for a /check response (the car's region, not the selected one).
function schedNow(sched) { return regionNow(sched?.region || _region, undefined, sched?.tz); }

// The car's side of the street: the one its owner picked, else null, and then both sides
// count (a pin or a GPS fix lands either side of the centerline as easily; the server's
// guess, car_side, only orders the sides).
function carSide(car) {
  return carSchedules[car.id] && (car.side === 'even' || car.side === 'odd') ? car.side : null;
}
// The car's side's schedule entries (both sides' while it is not picked).
function _sweepEntries(car) {
  const sched = carSchedules[car.id], side = carSide(car);
  return BroomUrgency.entries(side === 'even' ? sched.schedule_even : side === 'odd' ? sched.schedule_odd
    : [...sched.schedule_even, ...sched.schedule_odd]);
}
// The car's verdict from the raw schedules against the live clock (the same rules as the
// map color, so cards roll over when a window closes): 'today', 'tomorrow', 'check' (no
// data there, or a day only the sign gives), 'safe'; null while its street loads.
function sweepUrgency(car) {
  const sched = carSchedules[car.id];
  if (!sched) return null;
  if (sched.covered === false) return 'check';
  return BroomUrgency.checkDaySweeping(_sweepEntries(car), schedNow(sched), schedSkip(sched));
}

// Why the car's verdict is 'check', in words: {short, long}.
function _checkWords(car) {
  const sched = carSchedules[car.id], now = schedNow(sched);
  if (sched.covered === false) {
    const n = sched.nearby_street;
    return { short: 'No data', long: n ? `No sweeping data here (nearest street: ${n.name}, ${n.m} m). Check the signs.`
                                     : 'No sweeping data here. Check the signs.' };
  }
  const expired = _sweepEntries(car).some(e => BroomUrgency.unknownReason(e.code, now) === 'expired');
  return { short: 'Check sign', long: expired ? `${noDatesText(_sweepEntries(car), now)}. Check the signs.` : 'Sweeping day: check the sign' };
}

// When to move, in words (urgency.js sweepDeadline): `short` for a Saved row, `long`
// after "Move" (the car's page) or "Move <name>" (the Saved summary); null: nothing due.
// A sweep starting at midnight is tonight's deadline, not tomorrow's.
function deadlineText(car) {
  const sched = carSchedules[car.id];
  const d = sched && sched.covered !== false && BroomUrgency.sweepDeadline(_sweepEntries(car), schedNow(sched), schedSkip(sched));
  if (!d) return null;
  if (d.state === 'running') return { short: 'Sweeping now', long: `now, sweeping until ${d.at}` };
  if (d.state === 'by') return { short: `Move by ${d.at}`, long: `by ${d.at}` };
  if (d.state === 'today') return { short: 'Today', long: 'today' };
  if (d.at === 'midnight') return { short: 'Tonight', long: 'by midnight tonight' };
  return { short: d.at ? `Tomorrow ${d.at}` : 'Tomorrow', long: d.at ? `tomorrow by ${d.at}` : 'tomorrow' };
}

// Nothing due: until when the car's side stays clear ("Clear until Thu 9AM", "Season
// over"; js/signs.js clearUntilWords); null when none.
function clearUntilText(car) {
  const sched = carSchedules[car.id];
  if (!sched || sched.covered === false) return null;
  const side = carSide(car);
  return clearUntilWords(side === 'odd' ? [] : sched.schedule_even, side === 'even' ? [] : sched.schedule_odd,
                         schedNow(sched), schedSkip(sched), _sweepEntries(car));
}

// The verdict's words for the car's row (short) and page (long).
function carWords(car) {
  const u = sweepUrgency(car);
  if (u === 'check') return _checkWords(car);
  const dl = deadlineText(car), w = dl ? { short: dl.short, long: `Move ${dl.long}` } : clearUntilText(car);
  return w && _otherSideUnknown(car) ? { short: w.short, long: `${w.long} · other side: check the sign` } : w;
}
// Side not picked, and one side's day is only on the sign: the verdict is the other side's.
function _otherSideUnknown(car) {
  const sched = carSchedules[car.id], now = schedNow(sched);
  if (carSide(car) || !sched || sched.covered === false) return false;
  return [sched.schedule_even, sched.schedule_odd].some(r => BroomUrgency.entries(r).some(e => BroomUrgency.unknownReason(e.code, now)));
}

// Verdict: icon + words (never color alone): the short form shown, the long one read by
// screen readers (U1). `words`: {short, long}, else the verdict's name.
function _statusHTML(u, words) {
  const ic = URGENCY[u].icon, short = words?.short || URGENCY[u].short, long = words?.long || short;
  return `<span class="ce-status" style="color:${URGENCY[u].color}">${ic ? icon(ic) + ' ' : ''}`
       + `<span aria-hidden="true">${esc(short)}</span><span class="sr-only">${esc(long)}</span></span>`;
}

// When a cached schedule was fetched, on the region clock: "2:10PM", "Nov 14 2:10PM".
function _asOf(sched) {
  const at = regionNow(sched.region, new Date(sched._at), sched.tz), now = schedNow(sched);
  const day = BroomUrgency.dayKey(at) === BroomUrgency.dayKey(now) ? '' : BroomUrgency.dayLabel(at).replace(/^\S+ /, '') + ' ';
  return day + BroomUrgency.clockText(at.min);
}

// An item page's section (as iOS grouped lists): a label over a rounded box holding up
// to six lines (text, or {text, cls, tag}); while loading, "Loading…", and after a failed
// load, the error with Retry (with lines kept from before: "Offline · as of 2:10PM").
// `top`: HTML above the lines (a picker).
function schedBlockHTML(id, header, lines, top = '', asOf = null) {
  const retry = '<button class="btn btn-tinted ce-btn-retry">Retry</button>';
  const body = lines ? lines.slice(0, 6).map(l => typeof l === 'string' ? { text: l } : l)
    .map(l => `<div class="ce-sched-item ${l.cls || ''}">${esc(l.text)}`
      + (l.tag ? ` ${tagHTML(l.tag)}` : '') + '</div>').join('')
      + (loadFailed.has(id) && asOf ? `<div class="ce-error">${navigator.onLine ? 'Couldn’t update' : 'Offline'} · as of ${esc(asOf)} ${retry}</div>` : '')
    : loadFailed.has(id) ? `<div class="ce-error">Couldn’t load the schedule ${retry}</div>`
    : '<div class="muted">Loading…</div>';
  return `${header ? `<div class="sec-label">${esc(header)}</div>` : ''}<div class="sec">${top}${body}</div>`;
}

// An address whose city is one of its region's (the item at `id`): the city in its own
// span, which a Saved row hides (styles.css .ce-city); the item's page shows it all.
function _addrHTML(addr, id) {
  const item = cars.find(c => c.id === id) || homes.find(h => h.id === id);
  const region = item && regionAt(item.lat, item.lon);
  const city = Object.values(regions[region]?.cities || {}).find(n => addr.endsWith(`, ${n}`));
  if (!city) return esc(addr);
  const at = addr.length - city.length - 2;
  return `${esc(addr.slice(0, at))}<span class="ce-city">${esc(addr.slice(at))}</span>`;
}

// A row of the Saved list (icon, name over address, verdict) that opens the item's page
// (click, Enter or Space); `detail` shows on that page only.
function itemCard({ id, kind, color, name, urgency, words, addr, detail, retry = false }) {
  const entry = document.createElement('div');
  entry.className = 'car-entry' + (kind === 'home' ? ' home-entry' : '');
  entry.dataset.id = id;
  if (urgency) entry.dataset.urg = urgency;
  entry.style.setProperty('--car-color', color);
  entry.innerHTML = `
    <div class="ce-header" role="button" tabindex="0">
      <span class="ce-dot">${icon(kind)}</span>
      <span class="ce-text">
        <span class="ce-name">${esc(name)}</span>
        <span class="ce-addr" contenteditable="false">${_addrHTML(addr, id)}</span>
      </span>
      ${urgency ? _statusHTML(urgency, words) : ''}
    </div>
    ${retry ? '<button class="btn btn-sm btn-tinted ce-btn-retry ce-row-retry">Retry</button>' : ''}
    <div class="ce-detail">${detail}</div>`;
  entry.addEventListener('click', e => {
    if (_selectedId === id || e.target.closest('button') || e.target.closest('[contenteditable="true"]')) return;
    focusItem(id);
  });
  entry.querySelector('.ce-header').addEventListener('keydown', e => {
    if ((e.key === 'Enter' || e.key === ' ') && e.target.classList.contains('ce-header')) { e.preventDefault(); focusItem(id); }
  });
  return entry;
}

// Don't wipe a field the user is typing in (rename, address) on a re-render.
function isEditingIn(panel) {
  const ae = document.activeElement;
  return panel.contains(ae) && (ae.isContentEditable || ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA');
}

// ── Chicago: was the car towed or moved (relocated for sweeping, construction, …)? ──
// The plate is saved with the car (synced when signed in) and sent only when asked.
const vehicleChecks = {};  // car id -> {busy} | {result} | {error}
function vehicleHTML(car) {
  if (regionAt(car.lat, car.lon) !== 'chicago') return '';
  const c = vehicleChecks[car.id] || {};
  let out = '';
  if (c.busy) out = '<div class="muted">Checking…</div>';
  else if (c.error) out = `<div class="ce-error">${esc(c.error)}</div>`;
  else if (c.result) {
    const rows = [...c.result.towed.map(t => `Towed ${_when(t.date)} to ${t.to}${t.phone ? ` (${t.phone})` : ''}`),
                  ...c.result.relocated.map(r => `Moved ${_when(r.date)} to ${r.to}${r.reason ? ` (${r.reason})` : ''}`)];
    out = rows.length ? rows.map(r => `<div class="ce-rule warn">${esc(r)}</div>`).join('')
      : '<div>No tow or relocation in the last 90 days.</div>';
  }
  return `<div class="sec-label">Towed or moved?</div><div class="sec">
    <label class="ce-field">License plate <input class="field ce-plate" maxlength="10" autocapitalize="characters"
      autocomplete="off" spellcheck="false" value="${esc(car.plate || '')}" aria-label="License plate"></label>
    <label class="ce-field">State <input class="field ce-plate-state" maxlength="2" autocapitalize="characters"
      value="${esc(car.plate_state || 'IL')}" aria-label="Plate state"></label>
    <button class="btn btn-tinted ce-btn-towed">Check the city’s records</button>${out}</div>`;
}
// "2026-11-10T14:23" (the city's local time) -> "Tue Nov 10, 2:23PM"; other text as is.
function _when(iso) {
  const p = BroomUrgency.parseIso(iso);
  if (!p) return iso || '';
  const day = BroomUrgency.dayLabel(p);
  return p.min != null ? `${day}, ${BroomUrgency.clockText(p.min)}` : day;
}
async function checkVehicle(car) {
  if (!car.plate) { showToast('Enter the license plate first', true); return; }
  vehicleChecks[car.id] = { busy: true };
  renderCarsPanel();
  const q = new URLSearchParams({ plate: car.plate, state: car.plate_state || 'IL' });
  try {
    const res = await fetch('/api/vehicle?' + q);
    vehicleChecks[car.id] = res.ok ? { result: await res.json() }
      : { error: (await res.json().catch(() => ({}))).detail || 'Couldn’t check.' };
  } catch (_) { vehicleChecks[car.id] = { error: 'Couldn’t check.' }; }
  renderCarsPanel();
}

// A side's sweeping as lines in the map card's words ("Tue, Thu 6–8AM").
function _sideLines(entries, now) {
  const ls = sourceLines(entries, now).map(l => `${l.days} ${l.time}`.trim());
  return ls.length ? ls : ['No sweeping'];
}

// A car's sweeping lines where both sides are alike (or a zone); [] where they differ
// (the side choices carry each side's lines).
function carSchedLines(car) {
  const sched = carSchedules[car.id], now = schedNow(sched);
  const text = [sched.schedule_even, sched.schedule_odd].map(r => _sideLines(r, now));
  return text[0].join('\n') === text[1].join('\n') ? text[0] : [];
}

// Where the sides' schedules differ: one choice per side with its sweeping, the car's side
// checked. A side not checked is muted, and tagged Today / Tomorrow when swept then; until
// a side is checked, both count.
function sidePickHTML(car) {
  const sched = carSchedules[car.id];
  if (!sched || sched.covered === false || carSchedLines(car).length) return '';
  const now = schedNow(sched), skip = schedSkip(sched), side = carSide(car);
  const labels = sched.side_labels || ['Even', 'Odd'];
  return '<div class="ce-side" role="radiogroup" aria-label="Your side of the street">'
    + ['even', 'odd'].map((s, i) => {
      const raw = i ? sched.schedule_odd : sched.schedule_even, mine = side === s;
      const u = BroomUrgency.checkDaySweeping(BroomUrgency.entries(raw), now, skip);
      const tag = !mine && isSoon(u) ? u : null;
      return `<button type="button" class="ce-pick${side && !mine && !tag ? ' muted' : ''}" role="radio" data-side="${s}" aria-checked="${mine}">`
        + `<i class="ce-radio" aria-hidden="true"></i><span><b>${esc(labels[i])} side</b>`
        + _sideLines(raw, now).map(l => `<span>${esc(l)}</span>`).join('') + '</span>'
        + (tag ? tagHTML(tag) : '') + '</button>';
    }).join('') + '</div>';
}

// The city's sweeping data is over a year old: say when it is from (the signs win).
function dataAgeHTML(sched) {
  const ym = sched && regions[sched.region]?.updated?.[sched.city];
  const m = /^(\d{4})-(\d{2})/.exec(ym || ''), now = schedNow(sched);
  if (!m || (now.y - +m[1]) * 12 + now.m - +m[2] < 12) return '';
  return `<p class="note">Data: ${BroomUrgency.dayLabel({ y: +m[1], m: +m[2], d: 1 }).split(' ')[1]} ${m[1]}</p>`;
}

// A file for the calendar app with the car's next sweep and its reminders (the evening
// before, an hour before): the reminder without an account. Signed in with a calendar
// link, the feed has it already.
function remindHTML(car) {
  const sched = carSchedules[car.id];
  const cl = clearUntilText(car);
  if (!sched || sched.covered === false || calendarOn || !deadlineText(car) && !(cl && cl.short !== 'Season over')) return '';
  const q = new URLSearchParams({ lat: car.lat, lon: car.lon, name: car.name, id: car.id });
  if (carSide(car)) q.set('side', carSide(car));
  return `<a class="btn btn-tinted ce-btn-remind" href="/api/next-sweep.ics?${esc(q)}">${icon('calendar')} Add to calendar</a>`;
}

// The car rows (before the homes) and, on a car's page, its actions under the verdict:
// Update location (placement starts at the device's position, adjusted by hand, then OK)
// and Add to calendar; the rest is under "…". Until its side is picked (where the sides differ), the
// side choice comes first, in view with the verdict.
function renderCarsPanel() {
  if (isEditingIn(carsPanel) || savedTitle.isContentEditable) return;
  for (const el of [...itemList.querySelectorAll(':scope > .car-entry:not(.home-entry)')]) el.remove();
  const firstHome = itemList.querySelector('.home-entry');
  for (const car of cars) {
    const sched = carSchedules[car.id];
    const addrText = sched?.address || '';
    const asOf = sched?._at ? _asOf(sched) : null, sidePick = sidePickHTML(car), pickFirst = sidePick && !carSide(car);
    const actions = `<div class="ce-actions"><button class="btn ce-btn-gps">${icon('locate')} Update location</button>${remindHTML(car)}</div>`;
    const sweeping = sched?.covered === false ? '' : schedBlockHTML(car.id, pickFirst ? 'Pick side' : 'Sweeping',
                                                                     sched && carSchedLines(car), sidePick, asOf);
    const entry = itemCard({
      id: car.id, kind: 'car', color: carColor(car), name: car.name,
      // Never checked and the check failed: "Couldn't check", with Retry on the row.
      urgency: sweepUrgency(car) || (loadFailed.has(car.id) ? 'check' : null),
      words: sched ? carWords(car) : loadFailed.has(car.id) ? { short: 'Couldn’t check', long: 'Couldn’t check the street' } : null,
      addr: addrText, retry: !sched && loadFailed.has(car.id),
      detail: `
        ${pickFirst ? sweeping + actions : actions + sweeping}${dataAgeHTML(sched)}
        ${rulesBlockHTML('Other rules', placeRules[car.id])}
        ${vehicleHTML(car)}`,
    });

    const addrEl = entry.querySelector('.ce-addr');
    makeEditable(addrEl, () => addrText, async q => {
      addrEl.textContent = '…';
      let hit;
      try { hit = await geocode(q, regionAt(car.lat, car.lon) || _region); }
      catch (_) { showToast('Couldn’t find the address', true); addrEl.textContent = addrText; return; }
      if (!hit) { showToast('Address not found', true); addrEl.textContent = addrText; return; }
      if (map) map.jumpTo({ center: [hit.lon, hit.lat], zoom: 16 });
      moveCar(car, hit.lat, hit.lon);
    });

    for (const b of entry.querySelectorAll('.ce-btn-retry')) b.addEventListener('click', () => checkCar(car, true));
    entry.querySelector('.ce-btn-gps').addEventListener('click', e => {
      locateWith(e.currentTarget, (lat, lon) => startPlacing(car.id, 'car', { lat, lon }));
    });
    const sideGroup = entry.querySelector('.ce-side');
    if (sideGroup) radioTabs(sideGroup);
    sideGroup?.addEventListener('click', e => {
      const b = e.target.closest('[data-side]');
      if (!b) return;
      car.side = b.dataset.side;
      savePrefs();
      renderCarsPanel();
      carsPanel.querySelector(`.car-entry[data-id="${CSS.escape(car.id)}"] [data-side="${car.side}"]`)?.focus();
    });
    const plateIn = entry.querySelector('.ce-plate'), stateIn = entry.querySelector('.ce-plate-state');
    plateIn?.addEventListener('change', () => {
      car.plate = plateIn.value.toUpperCase().replace(/[^A-Z0-9 -]/g, '').trim() || undefined;
      savePrefs();
    });
    stateIn?.addEventListener('change', () => {
      const v = stateIn.value.toUpperCase().replace(/[^A-Z]/g, '');
      car.plate_state = v.length === 2 ? v : undefined;
      savePrefs();
    });
    entry.querySelector('.ce-btn-towed')?.addEventListener('click', () => checkVehicle(car));

    itemList.insertBefore(entry, firstHome);
  }
  applySelection();
  renderTargetInfo();
  updateSavedSummary();
}

// Weather alerts where cars and homes are that change where to park (Red Flag, snow, …):
// one fetch per region that has a saved item, refreshed every 10 minutes (app.js).
let placeAlerts = [];
async function loadAlerts() {
  const byRegion = new Map();
  for (const it of [...cars, ...homes]) {
    const r = regionAt(it.lat, it.lon);
    if (r && !byRegion.has(r)) byRegion.set(r, it);
  }
  const got = await Promise.all([...byRegion].map(async ([region, it]) => {
    try {
      const res = await fetch('/api/alerts?' + new URLSearchParams({ lat: it.lat, lon: it.lon, region }));
      return res.ok ? (await res.json()).alerts.map(a => ({ ...a, region })) : [];
    } catch (_) { return []; }
  }));
  const seen = new Set();
  placeAlerts = got.flat().filter(a => !seen.has(a.title) && seen.add(a.title));
  updateSavedSummary();
}
// "until 6PM" today, else "until Tue 6PM", on the alert's region clock; an end at 00:00
// is the midnight ending the day before ("until Mon midnight").
function _alertUntil(a) {
  const until = a.until ? new Date(a.until) : null;
  if (!until || isNaN(until)) return '';
  const end = regionNow(a.region, until), day = end.min === 0 ? BroomUrgency.addDays(end, -1) : end;
  const today = BroomUrgency.dayKey(day) === BroomUrgency.dayKey(regionNow(a.region));
  return ` until ${today ? '' : BroomUrgency.WEEKDAYS[BroomUrgency.weekday(day)] + ' '}${BroomUrgency.clockText(end.min)}`;
}
function _alertsHTML() {
  return placeAlerts.map(a => {
    const when = _alertUntil(a);
    return `<div class="alert-line"><span class="urg-tomorrow">${icon('alert')}</span> <span><b>${esc(a.title)}</b>${esc(when)}`
         + (a.detail ? `<br><small>${esc(a.detail)}</small>` : '') + '</span></div>';
  }).join('');
}

// Above the Saved list: with nothing saved, the two ways to start; then the alerts in
// effect where cars and homes are (each row says its own verdict). The tab's dot: red
// while a car must move today, amber tomorrow.
function updateSavedSummary() {
  const { today, tomorrow } = urgentCars();
  const due = today.length ? ['today', today] : tomorrow.length ? ['tomorrow', tomorrow] : null;
  savedSummary.innerHTML = (cars.length || homes.length ? ''
    : `<div class="empty-saved"><button class="btn btn-wide" data-parked>${icon('locate')} Parked here</button>
         <div class="empty-btns"><button class="btn btn-plain" data-add="car">${icon('car')} Add car</button>
         <button class="btn btn-plain" data-add="home">${icon('home')} Add home</button></div></div>`)
    + _alertsHTML();
  const badge = tabbar.querySelector('.tab-badge');
  badge.hidden = !due;
  badge.classList.toggle('soon', !!due && due[0] === 'tomorrow');
  // A "!" on the dot (not color alone); the words for screen readers.
  badge.innerHTML = due ? `<b aria-hidden="true">!</b><span class="sr-only">: ${esc(due[1].map(car => `move ${car.name} ${deadlineText(car)?.long || due[0]}`).join(', '))}</span>` : '';
}
// Nothing saved yet: "Parked here" starts placing a car at the device's position (adjusted
// by hand, then OK); the other buttons start placing a car or a home.
savedSummary.addEventListener('click', e => {
  const parked = e.target.closest('[data-parked]');
  if (parked) {
    locateWith(parked, (lat, lon) => startPlacing(null, 'car', { lat, lon }));
    return;
  }
  const b = e.target.closest('[data-add]');
  if (b) startPlacing(null, b.dataset.add);
});
