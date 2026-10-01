// ── Per-car schedule checks ───────────────────────────────────────────────────
async function checkCarWithRender(car) {
  setStatus('idle', 'Checking…');
  const warmupTimer = setTimeout(() => setStatus('idle', '⏳ Server warming up, please wait…'), 5000);
  try {
    const res = await postCheck(car.lat, car.lon);
    clearTimeout(warmupTimer);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      setStatus('idle', `Error: ${err.detail}`); return;
    }
    const data = await res.json();
    carSchedules[car.id] = data;
    upgradeAddress(car, data);
    ensureTiles();
    if (map) map.jumpTo({ center: [car.lon, car.lat], zoom: 16 });
    updateCarMarkers();
    updateStatusFromSchedules();
    renderCarsPanel();
  } catch (e) { clearTimeout(warmupTimer); setStatus('idle', `Network error: ${e.message}`); }
}

async function checkCarSilently(car) {
  try {
    const res = await postCheck(car.lat, car.lon);
    if (!res.ok) return;
    const data = await res.json();
    carSchedules[car.id] = data;
    upgradeAddress(car, data);
    updateStatusFromSchedules();
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
    const res = await apiFetch(`/address?${q}`);
    if (!res.ok) return;
    const { address } = await res.json();
    // Ignore a stale answer: the car was re-checked (moved) meanwhile.
    if (!address || carSchedules[car.id] !== sched) return;
    sched.address = address;
    sched.address_pending = false;
    renderCarsPanel();
  } catch (_) {}
}

function updateStatusFromSchedules() {
  const today = new Date().toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
  const todayNames    = [];
  const tomorrowNames = [];
  for (const [carId, s] of Object.entries(carSchedules)) {
    const car = cars.find(c => c.id === carId);
    if (!car) continue;
    const u = sweepUrgency(s);
    if (u === 'today')    todayNames.push(esc(car.name));
    if (u === 'tomorrow') tomorrowNames.push(esc(car.name));
  }
  const dateSpan = `<span style="color:var(--muted);font-weight:400">${today}</span>&emsp;`;
  if (todayNames.length) {
    statusText.className = 'today';
    statusText.innerHTML = `${dateSpan}🚨 Move ${todayNames.join(', ')} today!`;
  } else if (tomorrowNames.length) {
    statusText.className = 'tomorrow';
    statusText.innerHTML = `${dateSpan}⚠️ Move ${tomorrowNames.join(', ')} tomorrow.`;
  } else {
    statusText.className = 'safe';
    statusText.innerHTML = `${dateSpan}✅ No sweeping today or tomorrow.`;
  }
}

// ── Car placement ─────────────────────────────────────────────────────────────
// Center-target flow: pan the map so the crosshair sits on the spot, then
// "Place here" commits at the map center. The sheet collapses so the target is
// unobstructed. Right-click (desktop) is the alternative for an off-center spot.
function _beginPlacing() {
  setSheetCollapsed(true);
  placeBanner.classList.add('active');
  _crosshair.classList.add('placing');
  if (map) map.getCanvas().style.cursor = 'crosshair';
}

function startPlacing(editCarId = null) {
  placingCar    = true;
  placingHome   = false;
  placingEditId = editCarId;
  placeBannerText.textContent = editCarId
    ? '📍 Aim the target, then Place'
    : '🚗 Aim the target, then Place';
  _beginPlacing();
}

function startPlacingHome() {
  placingHome   = true;
  placingCar    = false;
  placingEditId = null;
  placeBannerText.textContent = '🏠 Aim the target, then Place';
  _beginPlacing();
}

function stopPlacing() {
  placingCar    = false;
  placingHome   = false;
  placingEditId = null;
  placeBanner.classList.remove('active');
  _crosshair.classList.remove('placing');
  if (map) map.getCanvas().style.cursor = '';
}

// Commit at the current map center (the point the crosshair marks).
function confirmPlacementAtCenter() {
  if (!map) return;
  const c = map.getCenter();
  if (placingHome) { stopPlacing(); addHome(c.lat, c.lng); }
  else if (placingCar) { commitPlacement(c.lat, c.lng); }
}

async function commitPlacement(lat, lon) {
  const editId = placingEditId;
  stopPlacing();

  if (editId) {
    const car = cars.find(c => c.id === editId);
    if (car) {
      car.lat = lat; car.lon = lon;
      await savePrefs();
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

// "🚗 Add car" — enters center-target place mode.
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
  input.placeholder = 'Car name…'; input.maxLength = 32;
  input.value = defaultCarName();
  const save = document.createElement('button');
  save.id = 'btn-np-save'; save.title = 'Save'; save.setAttribute('aria-label', 'Save');
  save.textContent = '✓';
  const close = document.createElement('button');
  close.className = 'popup-close inline-close';
  close.title = 'Close (Esc)'; close.setAttribute('aria-label', 'Close');
  close.textContent = '✕';
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
  removeTempPin();
  closeNameBox(true);
  if (_gpsLocPin) hideGpsPinPopup();
  await savePrefs();
  activeCarId = id;
  setNearestRegion(pendingLat, pendingLon);
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
// The sweeping domain's extras (raw schedules, car side, labels, detail_html)
// from a /check response, or null.
function sweepOf(sched) {
  return (sched?.domains || []).find(d => d.id === 'sweeping')?.extras || null;
}

// Region-local clock for a /check response (the car's region, not the selected one).
function schedNow(sched) {
  return BroomUrgency.nowForTimeZone(REGION_TZ[sched?.region || regionSelect.value] || 'UTC');
}

// A domain's urgency. Sweeping is recomputed from the raw schedules — the same
// verdict as the map colour — so cards roll over when a window closes or a day
// starts; other domains use the server's verdict.
function domainUrgency(sched, d) {
  if (d.id !== 'sweeping') return d.urgency || 'safe';
  const entries = [...d.extras.schedule_even, ...d.extras.schedule_odd]
    .map(e => ({ code: e[0], time: e[2] || '' }));
  return BroomUrgency.checkDaySweeping(entries, schedNow(sched));
}

// Live sweeping urgency alone (the "move your car" banner).
function sweepUrgency(sched) {
  const sw = sweepOf(sched);
  return sw ? domainUrgency(sched, { id: 'sweeping', extras: sw }) : 'safe';
}

// Worst-case urgency across all domains (today > tomorrow > safe). Drives the
// card tint/dot so a trash-today still flags a car whose sweeping is clear.
function panelUrgency(sched) {
  let best = 'safe';
  for (const d of (sched?.domains || [])) {
    const u = domainUrgency(sched, d);
    if (URGENCY_RANK[u] > URGENCY_RANK[best]) best = u;
  }
  return best;
}

function _urgencyLine(u, label) {
  return `<div class="ce-sched-urgency" style="color:${URGENCY[u].color}">${esc(label)}</div>`;
}
function _itemsHTML(lines) {
  return lines.slice(0, 4).map(l => `<div class="ce-sched-item">${esc(l)}</div>`).join('');
}

// Street-sweeping card block: both sides (car's first, labelled when they
// differ — same as the map hover); the header opens the full-year detail window.
function sweepBlockHTML(sched, sw) {
  const u = domainUrgency(sched, { id: 'sweeping', extras: sw });
  const lines = BroomUrgency.formatBothSides(
    sw.schedule_even, sw.schedule_odd, schedNow(sched), sw.car_side, sw.side_labels);
  const hasDetail = !!sw.detail_html;
  const chevron = hasDetail ? ' <span class="ce-sched-chevron">▸</span>' : '';
  return _urgencyLine(u, URGENCY[u].car)
       + `<div class="ce-sched-header${hasDetail ? ' clickable' : ''}">Street sweeping schedule:${chevron}</div>`
       + _itemsHTML(lines.length ? lines : ['No sweeping scheduled'])
       + (hasDetail ? sweepStripHTML(sched) : '');
}

// Card block for any other domain (trash, …): server-formatted schedule_lines
// under the domain label, with its own urgency line.
function domainBlockHTML(d) {
  const u = d.urgency || 'safe';
  return _urgencyLine(u, URGENCY[u].short)
       + `<div class="ce-sched-header">${esc(d.label)}:</div>`
       + _itemsHTML(d.schedule_lines?.length ? d.schedule_lines : ['No schedule']);
}

function scheduleHTML(sched) {
  if (!sched) return '<span style="color:var(--muted)">Loading…</span>';
  const html = (sched.domains || [])
    .map(d => d.id === 'sweeping' ? sweepBlockHTML(sched, d.extras) : domainBlockHTML(d))
    .join('');
  return html || '<span style="color:var(--muted)">No schedule</span>';
}

function renderCarsPanel() {
  const ae = document.activeElement;
  if (carsPanel.contains(ae) && (ae.isContentEditable || ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA')) return;
  for (const el of [...carsPanel.querySelectorAll('.car-entry')]) el.remove();
  cars.forEach((car, i) => {
    const color   = carColor(i);
    const sched   = carSchedules[car.id];
    const urgency = panelUrgency(sched);
    const addrText = sched?.address || '';

    const entry = document.createElement('div');
    entry.className = 'car-entry' + (car.id === _selectedCarId ? ' selected' : '');
    entry.dataset.id = car.id;
    // Urgency tint is theme-aware via [data-urgency] CSS (dark mode needs
    // different backgrounds), so set the attribute instead of a hardcoded hex.
    entry.dataset.urgency = urgency;
    entry.style.cssText = `--car-color:${color};--urg-color:${URGENCY[urgency].color}`;
    entry.addEventListener('click', e => {
      if (e.target.closest('button') || e.target.closest('[contenteditable="true"]')
          || e.target.closest('.ce-sched-header.clickable') || e.target.closest('.sw-strip')) return;
      setSelectedCar(car.id);
    });

    entry.innerHTML = `
      <div class="ce-header">
        <span class="ce-dot"></span>
        <span class="ce-name" contenteditable="false" data-id="${esc(car.id)}" title="Double-click to edit">${esc(car.name)}</span>
        <button class="ce-remove" data-id="${esc(car.id)}" title="Remove">✕</button>
      </div>
      <div class="ce-addr" contenteditable="false" data-id="${esc(car.id)}" title="Double-click to edit">${esc(addrText)}</div>
      <div class="ce-sched">${scheduleHTML(sched)}</div>
      <div class="ce-actions">
        <button class="ce-btn ce-btn-gps" data-id="${esc(car.id)}">📍 GPS</button>
        <button class="ce-btn ce-btn-place" data-id="${esc(car.id)}">📌 Set location</button>
      </div>`;

    // ── Schedule header → toggle full-year detail window ──
    if (sweepOf(sched)?.detail_html) {
      for (const el of entry.querySelectorAll('.ce-sched-header.clickable, .sw-strip')) {
        el.addEventListener('click', e => { e.stopPropagation(); toggleCardDetail(car.id); });
      }
    }

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
          addrEl.textContent = '⚠️ Address not found';
          setTimeout(() => { addrEl.style.color = ''; addrEl.textContent = addrText; }, 2500);
          return;
        }
        car.lat = hit.lat; car.lon = hit.lon;
        await savePrefs();
        setNearestRegion(car.lat, car.lon);
        updateCarMarkers();
        checkCarSilently(car);
      } catch (_) { addrEl.textContent = addrText; }
    });

    // ── GPS button ──
    entry.querySelector('.ce-btn-gps').addEventListener('click', () => {
      const btn = entry.querySelector('.ce-btn-gps');
      btn.disabled = true; btn.textContent = '…';
      getGPS(async (lat, lon) => {
        car.lat = lat; car.lon = lon;
        await savePrefs();
        btn.disabled = false; btn.textContent = '📍 GPS';
        setNearestRegion(lat, lon); setLocationKnown('gps', lat, lon);
        if (map) map.jumpTo({ center: [lon, lat], zoom: 16 });
        await checkCarWithRender(car);
      }, msg => { btn.disabled = false; btn.textContent = '📍 GPS'; showToast(`GPS: ${msg}`, true); });
    });

    // ── Set location button ──
    entry.querySelector('.ce-btn-place').addEventListener('click', () => startPlacing(car.id));

    // ── Remove button ──
    entry.querySelector('.ce-remove').addEventListener('click', async () => {
      const marker = _carMarkers.get(car.id);
      if (marker) { marker.remove(); _carMarkers.delete(car.id); }
      delete carSchedules[car.id];
      cars = cars.filter(c => c.id !== car.id);
      if (activeCarId === car.id) activeCarId = cars[0]?.id ?? null;
      if (_selectedCarId === car.id) _selectedCarId = null;
      if (_cardDetailCarId === car.id) closeCardDetail();
      await savePrefs();
      updateCarMarkers();
      renderCarsPanel();
      if (!cars.length) setStatus('idle', 'Add a car to check street sweeping.');
      else updateStatusFromSchedules();
    });

    carsPanel.appendChild(entry);
  });
  if (_cardDetailCarId) renderCardDetail();
  updateSheetSummary();
}
