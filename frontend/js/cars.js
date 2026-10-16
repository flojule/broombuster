// ── Per-car schedule checks ───────────────────────────────────────────────────
async function checkCarWithRender(car) {
  const warmupTimer = setTimeout(() => showToast('Server warming up'), 5000);
  try {
    const res = await postCheck(car.lat, car.lon);
    clearTimeout(warmupTimer);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      showToast(`Error: ${err.detail}`, true); return;
    }
    const data = await res.json();
    carSchedules[car.id] = data;
    upgradeAddress(car, data);
    if (map) map.jumpTo({ center: [car.lon, car.lat], zoom: 16 });
    updateCarMarkers();  // re-renders the cards
  } catch (e) { clearTimeout(warmupTimer); showToast(`Network error: ${e.message}`, true); }
}

async function checkCarSilently(car) {
  try {
    const res = await postCheck(car.lat, car.lon);
    if (!res.ok) return;
    const data = await res.json();
    carSchedules[car.id] = data;
    upgradeAddress(car, data);
    updateSheetSummary();
    updateCarMarkers();
  } catch (_) {}
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

// Car names by sweeping verdict; feeds the sheet handle (textContent: no escaping).
function urgentCars() {
  const out = { today: [], tomorrow: [] };
  for (const [carId, s] of Object.entries(carSchedules)) {
    const car = cars.find(c => c.id === carId);
    const u = car && sweepUrgency(s);
    if (out[u]) out[u].push(car.name);
  }
  return out;
}

// ── Car placement ─────────────────────────────────────────────────────────────
// Center-target flow: pan the map so the crosshair sits on the spot, then
// "Place here" commits at the map center. The sheet collapses so the target is
// unobstructed. Right-click (desktop) is the alternative for an off-center spot.
function _beginPlacing() {
  setSheetCollapsed(true);
  if (!isDesktop()) collapseCal();  // phone: the card would cover the target
  document.body.classList.add('placing');
  placeBanner.classList.add('active');
  _crosshair.classList.add('placing');
  if (map) map.getCanvas().style.cursor = 'crosshair';
}

// Place (or, with an id, move) a car or home; the banner shows just the icon.
function startPlacing(editId = null) {
  placingCar    = true;
  placingHome   = false;
  placingEditId = editId;
  placeBannerText.innerHTML = icon(editId ? 'pin' : 'car');
  _beginPlacing();
}

function startPlacingHome(editId = null) {
  placingHome   = true;
  placingCar    = false;
  placingEditId = editId;
  placeBannerText.innerHTML = icon('home');
  _beginPlacing();
}

function stopPlacing() {
  placingCar    = false;
  placingHome   = false;
  placingEditId = null;
  placeBanner.classList.remove('active');
  document.body.classList.remove('placing');
  _crosshair.classList.remove('placing');
  if (map) map.getCanvas().style.cursor = '';
  applySheet();
}

// Commit at the current map center (the point the crosshair marks).
function confirmPlacementAtCenter() {
  if (!map) return;
  const c = map.getCenter();
  if (placingHome) {
    const editId = placingEditId;
    stopPlacing();
    if (editId) moveHome(editId, c.lat, c.lng); else addHome(c.lat, c.lng);
  }
  else if (placingCar) { commitPlacement(c.lat, c.lng); }
}

async function commitPlacement(lat, lon) {
  const editId = placingEditId;
  stopPlacing();

  if (editId) {
    const car = cars.find(c => c.id === editId);
    if (car) {
      car.lat = lat; car.lon = lon; touchCarLocation(car);
      savePrefs();
      setSheetCollapsed(false);
      updateCarMarkers();
      checkCarSilently(car);
    }
  } else {
    openNameBox(lat, lon);
  }
}

btnConfirmPlace.addEventListener('click', confirmPlacementAtCenter);
btnCancelPlace.addEventListener('click', () => { stopPlacing(); setSheetCollapsed(false); });

// "Add car" — enters center-target place mode.
document.getElementById('btn-add-car').addEventListener('click', () => {
  closeNameBox();
  startPlacing(null);
});

// ── Context menu ──────────────────────────────────────────────────────────────
function showCtxMenu(x, y) {
  const mw = 170, mh = 80;
  ctxMenu.style.left = Math.min(x, window.innerWidth  - mw) + 'px';
  ctxMenu.style.top  = Math.min(y, window.innerHeight - mh) + 'px';
  ctxMenu.style.display = 'block';
}
function hideCtxMenu() { ctxMenu.style.display = 'none'; }

ctxAddCar.addEventListener('click', () => {
  hideCtxMenu();
  openNameBox(pendingLat, pendingLon);
});

ctxAddHome.addEventListener('click', () => {
  hideCtxMenu();
  addHome(pendingLat, pendingLon);
});

// ── Name box (arrow popup at the pending location) ─────────────────────────────
// Anchored MapLibre popup whose tip points at the spot the car will sit, matching
// the ward/street detail box style. Drops a temp pin under the tip.
function openNameBox(lat, lon) {
  if (!map) return;
  pendingLat = lat; pendingLon = lon;
  addTempPin(lat, lon);
  closeNameBox(true);  // clear any prior box without wiping the pending coords

  const row = document.createElement('div');
  row.className = 'np-row';
  const input = document.createElement('input');
  input.type = 'text'; input.id = 'name-panel-input';
  input.placeholder = 'Car name'; input.maxLength = 32;
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

// keepPending=true tears down the popup/pin but leaves pendingLat/Lon set (used
// when re-opening or right before savePendingCar consumes them).
function closeNameBox(keepPending = false) {
  if (_namePopup) { _namePopup.remove(); _namePopup = null; }
  _nameInput = null;
  if (!keepPending) {
    removeTempPin();
    pendingLat = pendingLon = null;
  }
}

async function savePendingCar() {
  if (pendingLat === null) return;
  const name = (_nameInput?.value.trim()) || 'My car';
  const id = newId();
  cars.push({ id, name, lat: pendingLat, lon: pendingLon });
  touchCarLocation(cars[cars.length - 1]);
  removeTempPin();
  closeNameBox(true);
  if (_gpsLocPin) clearGpsPin();
  savePrefs();
  setSheetCollapsed(false);  // reveal the new card
  updateCarMarkers();
  checkCarSilently(cars[cars.length - 1]);
  pendingLat = pendingLon = null;
}

function addTempPin(lat, lon) { _tempPin = { lat, lon }; updateCarMarkers(); }
function removeTempPin()      { _tempPin = null;         updateCarMarkers(); }

function defaultCarName() {
  const used = new Set(cars.map(c => c.name));
  if (!used.has('Car')) return 'Car';
  let i = 1;
  while (used.has(`Car ${i}`)) i++;
  return `Car ${i}`;
}

// ── Cars panel ────────────────────────────────────────────────────────────────
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

// Verdict next to the card title, in the urgency colour.
// Verdict pill: icon + word (never colour alone).
function _statusHTML(u, label) {
  const ic = URGENCY[u].icon;
  return `<span class="ce-status" data-urgency="${esc(u)}" style="color:${URGENCY[u].color}">${ic ? icon(ic) + ' ' : ''}${esc(label)}</span>`;
}
function _itemsHTML(lines) {
  return lines.slice(0, 4).map(l => `<div class="ce-sched-item">${esc(l)}</div>`).join('');
}

// Both sides (car's first, labelled when they differ — same as the map hover).
function scheduleHTML(sched) {
  if (!sched) return '<span style="color:var(--muted)">Loading…</span>';
  const u = sweepUrgency(sched);
  const lines = BroomUrgency.formatBothSides(
    sched.schedule_even, sched.schedule_odd, schedNow(sched), sched.car_side, sched.side_labels);
  return '<div class="ce-sched-header">Sweeping:</div>'
       + _itemsHTML(lines.length ? lines : ['No sweeping']);
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
  if (linked && !confirm(`Unlink the phone from ${car.name}? Its automation will stop working.`)) return;
  if (!linked) await _flushPrefs();  // the server only issues tokens for cars it has saved
  const res = await fetch(`/api/cars/${encodeURIComponent(car.id)}/token`,
                          { method: linked ? 'DELETE' : 'POST' });
  if (!res.ok) { showToast(linked ? 'Could not unlink the phone' : 'Could not link a phone', true); return; }
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
  const ae = document.activeElement;
  if (carsPanel.contains(ae) && (ae.isContentEditable || ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA')) return;
  for (const el of [...carsPanel.querySelectorAll(':scope > .car-entry')]) el.remove();
  cars.forEach((car, i) => {
    const color   = carColor(i);
    const sched   = carSchedules[car.id];
    const urgency = sweepUrgency(sched);
    const addrText = sched?.address || '';

    const entry = document.createElement('div');
    entry.className = 'car-entry' + (car.id === _selectedCarId ? ' selected' : '');
    entry.dataset.id = car.id;
    // Urgency tint is theme-aware via [data-urgency] CSS (dark mode needs
    // different backgrounds), so set the attribute instead of a hardcoded hex.
    entry.dataset.urgency = urgency;
    entry.style.cssText = `--car-color:${color}`;
    // Click: expand (schedule + location buttons) and centre the target; click
    // the open card again to collapse it.
    entry.addEventListener('click', e => {
      if (e.target.closest('button') || e.target.closest('[contenteditable="true"]')) return;
      if (_selectedCarId === car.id) clearCarSelection(); else focusCar(car.id);
    });

    entry.innerHTML = `
      <div class="ce-header">
        <span class="ce-dot">${icon('car')}</span>
        <span class="ce-name" contenteditable="false">${esc(car.name)}</span>
        ${sched ? _statusHTML(urgency, URGENCY[urgency].short) : ''}
      </div>
      <div class="ce-addr" contenteditable="false">${esc(addrText)}</div>
      <div class="ce-detail">
      <div class="ce-sched">${scheduleHTML(sched)}</div>
      <div class="ce-actions">
        <button class="ce-btn ce-btn-gps">${icon('locate')} GPS</button>
        <button class="ce-btn ce-btn-place">${icon('pin')} Set location</button>
        ${account ? `<button class="ce-btn ce-btn-phone">${car.id in phoneLinks ? 'Unlink phone' : 'Link phone'}</button>` : ''}
        <button class="ce-btn ce-remove ce-danger">${icon('trash')} Remove</button>
      </div>
      ${account ? phoneLinkHTML(car) : ''}
      </div>`;

    // ── Name editing ──
    const nameEl = entry.querySelector('.ce-name');
    makeEditable(nameEl, () => car.name, v => { car.name = v; savePrefs(); });

    // ── Address editing ──
    const addrEl = entry.querySelector('.ce-addr');
    makeEditable(addrEl, () => addrText, async q => {
      addrEl.textContent = '…';
      try {
        const hit = await geocode(q);
        if (!hit) {
          addrEl.style.color = 'var(--red)';
          addrEl.textContent = 'Address not found';
          setTimeout(() => { addrEl.style.color = ''; addrEl.textContent = addrText; }, 2500);
          return;
        }
        car.lat = hit.lat; car.lon = hit.lon; touchCarLocation(car);
        savePrefs();
        if (map) map.jumpTo({ center: [car.lon, car.lat], zoom: 16 });
        updateCarMarkers();
        checkCarSilently(car);
      } catch (_) { addrEl.textContent = addrText; }
    });

    // ── GPS button ──
    entry.querySelector('.ce-btn-gps').addEventListener('click', e => {
      locateWith(e.currentTarget, async (lat, lon) => {
        car.lat = lat; car.lon = lon; touchCarLocation(car);
        savePrefs();
        if (map) map.jumpTo({ center: [lon, lat], zoom: 16 });
        await checkCarWithRender(car);
      });
    });

    // ── Set location button ──
    entry.querySelector('.ce-btn-place').addEventListener('click', () => startPlacing(car.id));

    // ── Phone link ──
    entry.querySelector('.ce-btn-phone')?.addEventListener('click', () => _togglePhoneLink(car));
    entry.querySelector('.ce-btn-copy')?.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(_phoneLink.token); showToast('Token copied'); }
      catch (_) { entry.querySelector('.ce-token').select(); }
    });

    // ── Remove button ──
    confirmTap(entry.querySelector('.ce-remove'), () => {
      delete carSchedules[car.id];
      cars = cars.filter(c => c.id !== car.id);
      if (_selectedCarId === car.id) _selectedCarId = null;
      savePrefs();
      updateCarMarkers();  // drops its marker and re-renders the cards
    });

    carsPanel.appendChild(entry);
  });
  renderInfoCard();
  updateSheetSummary();
}
