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

// Car names by sweeping verdict; feeds the sheet handle.
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
// "Set here" commits at the map center. The sheet collapses so the target is
// unobstructed. Right-click or a long press is the alternative for an off-center spot.
// Places (or, with an id, moves) a car or home; the banner shows its icon.
function startPlacing(editId = null, kind = 'car') {
  placing = { kind, editId };
  placeBannerText.innerHTML = icon(editId ? 'pin' : kind)
    + `<span class="sr-only">${editId ? 'Move' : 'Place'} ${kind}: pan the map to the spot</span>`;
  setSheetCollapsed(true);
  if (!isDesktop()) collapseCal();  // phone: the card would cover the target
  document.body.classList.add('placing');
  placeBanner.classList.add('active');
  _crosshair.classList.add('placing');
  if (map) map.getCanvas().style.cursor = 'crosshair';
}

function stopPlacing() {
  placing = null;
  placeBanner.classList.remove('active');
  document.body.classList.remove('placing');
  _crosshair.classList.remove('placing');
  if (map) map.getCanvas().style.cursor = '';
  applySheet();
}

// Commit at the current map center (the point the crosshair marks).
function confirmPlacementAtCenter() {
  if (!map || !placing) return;
  const { lat, lng } = map.getCenter();
  const { kind, editId } = placing;
  stopPlacing();
  if (kind === 'home') { if (editId) moveHome(editId, lat, lng); else addHome(lat, lng); return; }
  const car = editId && cars.find(c => c.id === editId);
  if (car) { setSheetCollapsed(false); moveCar(car, lat, lng); }
  else openNameBox(lat, lng);
}

btnConfirmPlace.addEventListener('click', confirmPlacementAtCenter);
btnCancelPlace.addEventListener('click', () => { stopPlacing(); setSheetCollapsed(false); });

document.getElementById('btn-add-car').addEventListener('click', () => {
  closeNameBox();
  startPlacing(null);
});

// ── Context menu: "Add car / home here" (right-click, or a long press on touch) ──
function openCtxMenu(lngLat, x, y) {
  pendingLat = lngLat.lat; pendingLon = lngLat.lng;
  const mw = 170, mh = 100;
  ctxMenu.style.left = Math.min(x, window.innerWidth  - mw) + 'px';
  ctxMenu.style.top  = Math.min(y, window.innerHeight - mh) + 'px';
  ctxMenu.style.display = 'block';
}
function hideCtxMenu() { ctxMenu.style.display = 'none'; }

ctxAddCar.addEventListener('click', () => { hideCtxMenu(); openNameBox(pendingLat, pendingLon); });
ctxAddHome.addEventListener('click', () => { hideCtxMenu(); addHome(pendingLat, pendingLon); });

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
  input.type = 'text'; input.id = 'name-panel-input';
  input.placeholder = 'Car name'; input.maxLength = 32;
  input.setAttribute('aria-label', 'Car name');
  input.value = defaultCarName();
  const save = document.createElement('button');
  save.id = 'btn-np-save'; save.title = 'Save'; save.setAttribute('aria-label', 'Save');
  save.textContent = '✓';
  const close = document.createElement('button');
  close.className = 'popup-close inline-close';
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
  document.body.classList.add('naming');
  _namePopup.on('close', () => { _namePopup = null; _nameInput = null; document.body.classList.remove('naming'); });
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
  setSheetCollapsed(false);  // reveal the new card
  updateCarMarkers();
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
  return BroomUrgency.checkDaySweeping(entries, schedNow(sched));
}

// Verdict pill: icon + word (never colour alone).
function _statusHTML(u) {
  const ic = URGENCY[u].icon;
  return `<span class="ce-status" style="color:${URGENCY[u].color}">${ic ? icon(ic) + ' ' : ''}${esc(URGENCY[u].short)}</span>`;
}

// A schedule block: header and up to four lines; while loading, "Loading…", and after
// a failed load, the error with Retry.
function schedBlockHTML(id, header, lines) {
  if (lines) return `<div class="ce-sched-header">${esc(header)}</div>`
                  + lines.slice(0, 4).map(l => `<div class="ce-sched-item">${esc(l)}</div>`).join('');
  return loadFailed.has(id)
    ? '<div class="ce-error">Couldn’t load the schedule <button class="ce-btn ce-btn-retry">Retry</button></div>'
    : '<span class="ce-loading">Loading…</span>';
}

// Card shell: header (icon, name, verdict) that toggles the card (click, Enter or
// Space), address, and the detail shown while the card is open.
function itemCard({ id, kind, color, name, urgency, addr, detail }) {
  const entry = document.createElement('div');
  entry.className = 'car-entry' + (kind === 'home' ? ' home-entry' : '');
  entry.dataset.id = id;
  if (urgency) entry.dataset.urgency = urgency;  // theme-aware tint via CSS
  entry.style.setProperty('--car-color', color);
  entry.innerHTML = `
    <div class="ce-header" role="button" tabindex="0" aria-expanded="false">
      <span class="ce-dot">${icon(kind)}</span>
      <span class="ce-name" contenteditable="false">${esc(name)}</span>
      ${urgency ? _statusHTML(urgency) : ''}
    </div>
    <div class="ce-addr" contenteditable="false">${esc(addr)}</div>
    <div class="ce-detail">${detail}</div>`;
  entry.addEventListener('click', e => {
    if (e.target.closest('button') || e.target.closest('[contenteditable="true"]')) return;
    toggleItem(id);
  });
  entry.querySelector('.ce-header').addEventListener('keydown', e => {
    if ((e.key === 'Enter' || e.key === ' ') && e.target.classList.contains('ce-header')) { e.preventDefault(); toggleItem(id); }
  });
  return entry;
}

// Don't wipe a field the user is typing in (rename, address) on a re-render.
function isEditingIn(panel) {
  const ae = document.activeElement;
  return panel.contains(ae) && (ae.isContentEditable || ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA');
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

// Status (and the new token, once) under a linked car's buttons.
function phoneLinkHTML(car) {
  if (!(car.id in phoneLinks)) return '';
  const lastUsed = phoneLinks[car.id];
  const status = lastUsed ? `Phone last reported ${_ago(lastUsed)}.` : 'Phone has not reported yet.';
  const token = _phoneLink?.carId === car.id ? _phoneLink.token : null;
  return `<div class="ce-phone">
    <div class="ce-token-note">${esc(status)}</div>
    ${token ? `
    <div class="ce-token-note">Copy this token into your phone automation now; it is shown only once.
      The automation posts {"lat", "lon"} to <b>${esc(location.origin)}/api/cars/location</b>
      with the header <b>Authorization: Bearer &lt;token&gt;</b>.</div>
    <input class="ce-token" readonly value="${esc(token)}" aria-label="Phone token">
    <button class="ce-btn ce-btn-copy">Copy token</button>` : ''}
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
  document.querySelector('.ce-token')?.scrollIntoView({ block: 'nearest' });  // it is below the card's buttons
}

function renderCarsPanel() {
  if (isEditingIn(carsPanel)) return;
  for (const el of [...carsPanel.querySelectorAll(':scope > .car-entry')]) el.remove();
  for (const car of cars) {
    const sched = carSchedules[car.id];
    const addrText = sched?.address || '';
    const linked = car.id in phoneLinks;
    const lines = sched && BroomUrgency.formatBothSides(
      sched.schedule_even, sched.schedule_odd, schedNow(sched), sched.car_side, sched.side_labels);
    const entry = itemCard({
      id: car.id, kind: 'car', color: carColor(car), name: car.name,
      urgency: sched && sweepUrgency(sched), addr: addrText,
      detail: `
        <div class="ce-sched">${schedBlockHTML(car.id, 'Sweeping:', lines && (lines.length ? lines : ['No sweeping']))}</div>
        <div class="ce-actions">
          <button class="ce-btn ce-btn-gps">${icon('locate')} GPS</button>
          <button class="ce-btn ce-btn-place">${icon('pin')} Set location</button>
          ${account ? `<button class="ce-btn ce-btn-phone">${linked ? 'Unlink phone' : 'Link phone'}</button>` : ''}
          <button class="ce-btn ce-remove ce-danger">${icon('trash')} Remove</button>
        </div>
        ${account ? phoneLinkHTML(car) : ''}`,
    });

    const nameEl = entry.querySelector('.ce-name');
    makeEditable(nameEl, () => car.name, v => { car.name = v; savePrefs(); updateCarMarkers(); });

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
    entry.querySelector('.ce-btn-place').addEventListener('click', () => startPlacing(car.id));

    const phoneBtn = entry.querySelector('.ce-btn-phone');
    if (phoneBtn && linked) confirmTap(phoneBtn, () => _togglePhoneLink(car));  // unlinking stops the automation
    else phoneBtn?.addEventListener('click', () => _togglePhoneLink(car));
    entry.querySelector('.ce-btn-copy')?.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(_phoneLink.token); showToast('Token copied'); }
      catch (_) { entry.querySelector('.ce-token').select(); }
    });

    confirmTap(entry.querySelector('.ce-remove'), () => {
      delete carSchedules[car.id];
      cars = cars.filter(c => c.id !== car.id);
      if (_selectedId === car.id) _selectedId = null;
      savePrefs();
      updateCarMarkers();  // drops its marker and re-renders the cards
    });

    carsPanel.appendChild(entry);
  }
  applySelection();
  renderInfoCard();
  updateSheetSummary();
}
