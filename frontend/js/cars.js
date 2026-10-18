// ── Per-car schedule checks ───────────────────────────────────────────────────
// `loud`: tell the user about a slow server or an error (a check they asked for);
// otherwise a failure only shows on the card, with Retry.
async function checkCar(car, loud = false) {
  const warmupTimer = loud && setTimeout(() => showToast('Server warming up'), 5000);
  const data = await loadSchedule(car.id, carSchedules, () => postCheck(car.lat, car.lon),
                                  loud ? msg => showToast(`Error: ${msg}`, true) : null);
  clearTimeout(warmupTimer);
  if (data) upgradeAddress(car, data);
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

// Car names by sweeping verdict; feeds the Saved summary and the tab's badge.
function urgentCars() {
  const out = { today: [], tomorrow: [] };
  for (const car of cars) out[sweepUrgency(carSchedules[car.id])]?.push(car.name);
  return out;
}

// The user moved a car (GPS, map or address): save it, show it, and re-check its street.
function moveCar(car, lat, lon) {
  car.lat = lat; car.lon = lon; touchCarLocation(car);
  savePrefs();
  updateCarMarkers();
  return checkCar(car, true);
}

// ── Placement (cars and homes) ────────────────────────────────────────────────
// Center-target flow: pan the map so the crosshair sits on the spot, then
// "Set here" commits at the map center. The sheet shrinks so most of the map shows;
// it returns to its height after. Right-click or a long press is the alternative for
// an off-center spot. Places (or, with an id, moves) a car or home; the banner shows its icon.
// A new one starts where you are, when the app knows (people add a car once they've parked).
function startPlacing(editId = null, kind = 'car') {
  closeNameBox();
  const p = placing = { kind, editId };
  placeBannerText.innerHTML = icon(editId ? 'pin' : kind)
    + `<span><span class="sr-only">${editId ? 'Move' : 'Place'} ${kind}: </span>Pan to the spot</span>`;
  if (!isDesktop()) setCompact(true);
  document.body.classList.add('placing');
  placeBanner.classList.add('active');
  _crosshair.classList.add('placing');
  if (!map) return;
  map.getCanvas().style.cursor = 'crosshair';
  if (editId) return;
  const goHere = (lat, lon) => map.jumpTo({ center: [lon, lat], zoom: Math.max(map.getZoom(), 17), padding: mapPadding() });
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
}

// Commit at the current map center (the point the crosshair marks).
function confirmPlacementAtCenter() {
  if (!map || !placing) return;
  const { lat, lng } = map.getCenter();
  const { kind, editId } = placing;
  stopPlacing();
  if (kind === 'home') { if (editId) moveHome(editId, lat, lng); else addHome(lat, lng); return; }
  const car = editId && cars.find(c => c.id === editId);
  if (car) { moveCar(car, lat, lng); openItem(car.id); }
  else openNameBox(lat, lng);
}

btnConfirmPlace.addEventListener('click', confirmPlacementAtCenter);
btnCancelPlace.addEventListener('click', stopPlacing);

// ── "+": what to add (as the "+" on Find My's lists): a car or a home, placed with
// the centre target. Anchored menus (this and an item's "…") share the code below.
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
    _menuRow('place', car ? 'Set location' : 'Move', 'pin'),
    _menuRow('address', 'Enter address', 'search'),
    car && _menuRow('rename', 'Rename', 'pencil'),
    car && account && _menuRow('phone', item.id in phoneLinks ? 'Unlink phone' : 'Link phone', 'phone'),
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
  else if (act === 'phone') {
    if (!(item.id in phoneLinks)
        || await askConfirm(`Unlink the phone from ${name}?`, 'Its automation will stop updating where the car is.', 'Unlink')) {
      _togglePhoneLink(car);
    }
  } else if (act === 'remove') {
    // Focus goes back to "…" on Cancel, to "+" on the list once it is gone.
    if (!await askConfirm(`Remove ${name}?`, car ? 'Its location is forgotten.' : 'Its trash days are no longer shown.', 'Remove')) {
      btnItemMore.focus();
      return;
    }
    if (car) removeCar(car.id); else removeHome(item.id);
    btnAdd.focus();
  }
});

function removeCar(id) {
  delete carSchedules[id];
  cars = cars.filter(c => c.id !== id);
  savePrefs();
  updateCarMarkers();  // drops its marker and re-renders the cards
}

// ── Context menu: "Add car / home here" (right-click, or a long press on touch) ──
// At the finger (or the pointer), as the other menus are (ui.js).
function openCtxMenu(lngLat, x, y) {
  pendingLat = lngLat.lat; pendingLon = lngLat.lng;
  openMenu(ctxMenu, null, { x, y });
}

ctxAddCar.addEventListener('click', () => { closeMenu(); openNameBox(pendingLat, pendingLon); });
ctxAddHome.addEventListener('click', () => { closeMenu(); addHome(pendingLat, pendingLon); });

// ── Name box (arrow popup at the pending location) ─────────────────────────────
// Anchored MapLibre popup whose tip points at the spot the car will sit. Drops a
// temp pin under the tip.
function openNameBox(lat, lon) {
  if (!map) return;
  closeNameBox();
  pendingLat = lat; pendingLon = lon;
  _tempPin = { lat, lon }; updateTempPin();

  const row = document.createElement('div');
  row.className = 'np-row';
  const input = document.createElement('input');
  input.type = 'text'; input.className = 'field';
  input.placeholder = 'Car name'; input.maxLength = 32;
  input.setAttribute('aria-label', 'Car name');
  input.value = defaultCarName();
  const save = document.createElement('button');
  save.className = 'btn btn-sm'; save.title = 'Save'; save.setAttribute('aria-label', 'Save');
  save.innerHTML = icon('check');
  const close = document.createElement('button');
  close.className = 'round-btn';
  close.title = 'Close (Esc)'; close.setAttribute('aria-label', 'Close');
  close.innerHTML = icon('close');
  row.append(input, save, close);

  save.addEventListener('click', savePendingCar);
  close.addEventListener('click', () => closeNameBox());
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter')  { e.preventDefault(); savePendingCar(); }
    if (e.key === 'Escape') { e.preventDefault(); closeNameBox(); }
  });

  _nameInput = input;
  _namePopup = new maplibregl.Popup({
    closeButton: false, closeOnClick: false, anchor: 'bottom', offset: 22,
    className: 'name-popup',
  }).setLngLat([lon, lat]).setDOMContent(row).addTo(map);
  _namePopup.on('close', () => { _namePopup = null; _nameInput = null; });
  setTimeout(() => { input.focus(); input.select(); }, 30);
}

// Close the name box and drop its pending spot and temp pin.
function closeNameBox() {
  _namePopup?.remove();
  _namePopup = null; _nameInput = null;
  _tempPin = null; updateTempPin();
  pendingLat = pendingLon = null;
}

function savePendingCar() {
  if (pendingLat === null) return;
  const car = { id: newId(), name: _nameInput?.value.trim() || 'My car',
                lat: pendingLat, lon: pendingLon, color: nextCarColor() };
  touchCarLocation(car);
  cars.push(car);
  closeNameBox();
  savePrefs();
  updateCarMarkers();
  openItem(car.id);  // its page: the schedule as it loads
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
function schedNow(sched) { return regionNow(sched?.region || _region); }

// Sweeping urgency from the raw schedules against the live clock — the same
// verdict as the map colour, so cards roll over when a window closes.
function sweepUrgency(sched) {
  if (!sched) return 'safe';
  const entries = [...sched.schedule_even, ...sched.schedule_odd]
    .map(e => ({ code: e[0], time: e[2] || '' }));
  return BroomUrgency.checkDaySweeping(entries, schedNow(sched), schedSkip(sched));
}

// Verdict pill: icon + word (never colour alone).
function _statusHTML(u) {
  const ic = URGENCY[u].icon;
  return `<span class="ce-status" style="color:${URGENCY[u].color}">${ic ? icon(ic) + ' ' : ''}${esc(URGENCY[u].short)}</span>`;
}

// An item page's section (as iOS grouped lists): a label over a rounded box holding up
// to four lines; while loading, "Loading…", and after a failed load, the error with Retry.
function schedBlockHTML(id, header, lines) {
  const body = lines ? lines.slice(0, 4).map(l => `<div class="ce-sched-item">${esc(l)}</div>`).join('')
    : loadFailed.has(id) ? '<div class="ce-error">Couldn’t load the schedule <button class="btn btn-tinted ce-btn-retry">Retry</button></div>'
    : '<div class="ce-loading">Loading…</div>';
  return `<div class="sec-label">${esc(header)}</div><div class="sec">${body}</div>`;
}

// A row of the Saved list (icon, name over address, verdict) that opens the item's page
// (click, Enter or Space); `detail` shows on that page only.
function itemCard({ id, kind, color, name, urgency, addr, detail }) {
  const entry = document.createElement('div');
  entry.className = 'car-entry' + (kind === 'home' ? ' home-entry' : '');
  entry.dataset.id = id;
  entry.style.setProperty('--car-color', color);
  entry.innerHTML = `
    <div class="ce-header" role="button" tabindex="0">
      <span class="ce-dot">${icon(kind)}</span>
      <span class="ce-text">
        <span class="ce-name">${esc(name)}</span>
        <span class="ce-addr" contenteditable="false">${esc(addr)}</span>
      </span>
      ${urgency ? _statusHTML(urgency) : ''}
    </div>
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
  if (c.busy) out = '<div class="ce-loading">Checking…</div>';
  else if (c.error) out = `<div class="ce-error">${esc(c.error)}</div>`;
  else if (c.result) {
    const rows = [...c.result.towed.map(t => `Towed ${t.date} to ${t.to}${t.phone ? ` (${t.phone})` : ''}`),
                  ...c.result.relocated.map(r => `Moved ${r.date.replace('T', ' ')} to ${r.to}${r.reason ? ` (${r.reason})` : ''}`)];
    out = rows.length ? rows.map(r => `<div class="ce-rule warn">${esc(r)}</div>`).join('')
      : '<div>No tow or relocation in the last 90 days.</div>';
  }
  return `<div class="sec-label">Towed or moved?</div><div class="sec">
    <label class="ce-field">Licence plate <input class="field ce-plate" maxlength="10" autocapitalize="characters"
      autocomplete="off" spellcheck="false" value="${esc(car.plate || '')}" aria-label="Licence plate"></label>
    <label class="ce-field">State <input class="field ce-plate-state" maxlength="2" autocapitalize="characters"
      value="${esc(car.plate_state || 'IL')}" aria-label="Plate state"></label>
    <button class="btn btn-tinted ce-btn-towed">Check the city’s records</button>${out}</div>`;
}
async function checkVehicle(car) {
  if (!car.plate) { showToast('Enter the licence plate first', true); return; }
  vehicleChecks[car.id] = { busy: true };
  renderCarsPanel();
  const q = new URLSearchParams({ plate: car.plate, state: car.plate_state || 'IL' });
  try {
    const res = await fetch('/api/vehicle?' + q);
    vehicleChecks[car.id] = res.ok ? { result: await res.json() }
      : { error: (await res.json().catch(() => ({}))).detail || 'The lookup failed.' };
  } catch (_) { vehicleChecks[car.id] = { error: 'The lookup failed.' }; }
  renderCarsPanel();
}

// ── Phone link: a token that lets a phone automation report where the car parked ──
let _phoneLink = null;  // { carId, token } while a freshly created token is on screen

function _ago(epochS) {
  const m = Math.max(0, Math.round((Date.now() / 1000 - epochS) / 60));
  if (m < 2) return 'just now';
  if (m < 90) return `${m} min ago`;
  const h = Math.round(m / 60);
  return h < 36 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
}

// A linked car's page: the phone's status (and the new token, once), as a section.
function phoneLinkHTML(car) {
  if (!(car.id in phoneLinks)) return '';
  const lastUsed = phoneLinks[car.id];
  const status = lastUsed ? `Last reported ${_ago(lastUsed)}.` : 'Has not reported yet.';
  const token = _phoneLink?.carId === car.id ? _phoneLink.token : null;
  return `<div class="sec-label">Phone</div><div class="sec">
    <div class="note">${esc(status)}</div>
    ${token ? `
    <div class="note">Copy this token into your phone automation now; it is shown only once.
      The automation posts {"lat", "lon"} to <b>${esc(location.origin)}/api/cars/location</b>
      with the header <b>Authorization: Bearer &lt;token&gt;</b>.</div>
    <input class="field ce-token" readonly value="${esc(token)}" aria-label="Phone token">
    <button class="btn btn-tinted ce-btn-copy">Copy token</button>` : ''}
  </div>`;
}

async function _togglePhoneLink(car) {
  const linked = car.id in phoneLinks;
  if (!linked) await _flushPrefs();  // the server only issues tokens for cars it has saved
  const res = await fetch(`/api/cars/${encodeURIComponent(car.id)}/token`,
                          { method: linked ? 'DELETE' : 'POST' }).catch(() => null);
  if (!res?.ok) { showToast(linked ? 'Could not unlink the phone' : 'Could not link a phone', true); return; }
  if (linked) {
    delete phoneLinks[car.id];
    _phoneLink = null;
  } else {
    phoneLinks[car.id] = null;
    _phoneLink = { carId: car.id, token: (await res.json()).token };
  }
  renderCarsPanel();
  document.querySelector('.ce-token')?.scrollIntoView({ block: 'nearest' });  // it is below the schedule
}

// The car rows (before the homes) and, on a car's page, its one visible action: Parked
// here, the one used every time (as Find My keeps Play Sound out); the rest is under "…".
function renderCarsPanel() {
  if (isEditingIn(carsPanel) || savedTitle.isContentEditable) return;
  for (const el of [...itemList.querySelectorAll(':scope > .car-entry:not(.home-entry)')]) el.remove();
  const firstHome = itemList.querySelector('.home-entry');
  for (const car of cars) {
    const sched = carSchedules[car.id];
    const addrText = sched?.address || '';
    const lines = sched && BroomUrgency.formatBothSides(
      sched.schedule_even, sched.schedule_odd, schedNow(sched), sched.car_side, sched.side_labels);
    const entry = itemCard({
      id: car.id, kind: 'car', color: carColor(car), name: car.name,
      urgency: sched && sweepUrgency(sched), addr: addrText,
      detail: `
        <button class="btn btn-tinted btn-wide ce-btn-gps">${icon('locate')} Parked here</button>
        ${schedBlockHTML(car.id, 'Sweeping', lines && (lines.length ? lines : ['No sweeping']))}
        ${rulesBlockHTML('Also here', placeRules[car.id])}
        ${vehicleHTML(car)}
        ${account ? phoneLinkHTML(car) : ''}`,
    });

    const addrEl = entry.querySelector('.ce-addr');
    makeEditable(addrEl, () => addrText, async q => {
      addrEl.textContent = '…';
      let hit;
      try { hit = await geocode(q, regionAt(car.lat, car.lon) || _region); }
      catch (_) { showToast('Could not look up the address', true); addrEl.textContent = addrText; return; }
      if (!hit) { showToast('Address not found', true); addrEl.textContent = addrText; return; }
      if (map) map.jumpTo({ center: [hit.lon, hit.lat], zoom: 16 });
      moveCar(car, hit.lat, hit.lon);
    });

    entry.querySelector('.ce-btn-retry')?.addEventListener('click', () => checkCar(car, true));
    entry.querySelector('.ce-btn-gps').addEventListener('click', e => {
      locateWith(e.currentTarget, (lat, lon) => {
        if (map) map.jumpTo({ center: [lon, lat], zoom: 16 });
        return moveCar(car, lat, lon);
      });
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
    entry.querySelector('.ce-btn-copy')?.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(_phoneLink.token); showToast('Token copied'); }
      catch (_) { entry.querySelector('.ce-token').select(); }
    });

    itemList.insertBefore(entry, firstHome);
  }
  applySelection();
  renderTargetInfo();
  updateSavedSummary();
}

// Weather and air alerts where cars and homes are (Red Flag, snow, Spare the Air, …):
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
      return res.ok ? (await res.json()).alerts : [];
    } catch (_) { return []; }
  }));
  const seen = new Set();
  placeAlerts = got.flat().filter(a => !seen.has(a.title) && seen.add(a.title));
  updateSavedSummary();
}
function _alertsHTML() {
  return placeAlerts.map(a => {
    const until = a.until ? new Date(a.until) : null;
    const when = until && !isNaN(until) ? ` until ${until.toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' })}` : '';
    return `<div class="alert-line"><span class="urg-tomorrow">${icon('alert')}</span> <span><b>${esc(a.title)}</b>${esc(when)}`
         + (a.detail ? `<br><small>${esc(a.detail)}</small>` : '') + '</span></div>';
  }).join('');
}

// Saved tab's first line: the most urgent car verdict, else all clear; with nothing
// saved, how to start. A red dot on the tab while a car must move today. Alerts in
// effect where cars and homes are follow it.
function updateSavedSummary() {
  const { today, tomorrow } = urgentCars();
  savedSummary.innerHTML = today.length ? `<span class="urg-today">${icon('alert')}</span> Move ${esc(today.join(', '))} today`
    : tomorrow.length ? `<span class="urg-tomorrow">${icon('alert')}</span> Move ${esc(tomorrow.join(', '))} tomorrow`
    : cars.length ? `<span class="urg-safe">${icon('check')}</span> All clear`
    : homes.length ? '' : '<span class="note">Add your car with + to see when to move it, or your home for trash day.</span>';
  savedSummary.insertAdjacentHTML('beforeend', _alertsHTML());
  tabbar.querySelector('.tab-badge').hidden = !today.length;
}
