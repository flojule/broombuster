// ── Homes — trash pickup days (cities with ReCollect data) ──────────────────────
// [stream, next ISO date] per pickup stream, soonest first.
function _nextPickups(sched) {
  return Object.entries(sched?.pickups || {})
    .filter(([, dates]) => dates.length)
    .map(([stream, dates]) => [stream, dates[0]])
    .sort((a, b) => a[1].localeCompare(b[1]));
}

let _openHomeId = null;  // home card showing its schedule

function homeUrgency(sched) {
  const next = _nextPickups(sched)[0]?.[1];
  if (!next) return 'safe';
  const today = schedNow(sched);
  const key = p => `${p.y}-${String(p.m).padStart(2, '0')}-${String(p.d).padStart(2, '0')}`;
  if (next === key(today)) return 'today';
  return next === key(BroomUrgency.addDays(today, 1)) ? 'tomorrow' : 'safe';
}

function homeScheduleHTML(sched) {
  if (!sched) return '<span style="color:var(--muted)">Loading…</span>';
  const next = _nextPickups(sched);
  if (!next.length) {
    return '<div class="ce-sched-item" style="color:var(--muted)">'
         + 'No collection info for this address.</div>';
  }
  const fmt = iso => new Date(iso + 'T12:00:00Z').toLocaleDateString('en-US',
    { weekday: 'short', month: 'short', day: 'numeric', timeZone: 'UTC' });
  return '<div class="ce-sched-header">Trash day:</div>'
       + _itemsHTML(next.map(([stream, iso]) => `${stream}: ${fmt(iso)}`));
}

function renderHomePanel() {
  if (!homePanel) return;
  // Don't wipe an address editor (contenteditable) mid-type on a re-render.
  const ae = document.activeElement;
  if (homePanel.contains(ae) && (ae.isContentEditable || ae.tagName === 'INPUT')) return;
  homePanel.innerHTML = '';

  homes.forEach((h, i) => {
    const sched    = homeSchedules[h.id];
    const urgency  = homeUrgency(sched);
    const addrText = h.address ? abbreviate(h.address) : '';
    const label    = homes.length > 1 ? `Home ${i + 1}` : 'Home';

    const entry = document.createElement('div');
    entry.className = 'car-entry home-entry' + (h.id === _openHomeId ? ' selected' : '');
    entry.dataset.id = h.id;
    entry.dataset.urgency = urgency;
    entry.style.cssText = '--car-color:#16a34a';
    // Click toggles the schedule, as on a car card.
    entry.addEventListener('click', e => {
      if (e.target.closest('button') || e.target.closest('[contenteditable="true"]')) return;
      _openHomeId = _openHomeId === h.id ? null : h.id;
      entry.classList.toggle('selected', _openHomeId === h.id);
      for (const o of homePanel.children) if (o !== entry) o.classList.remove('selected');
    });
    entry.innerHTML = `
      <div class="ce-header">
        <span class="ce-dot ce-dot-home">🏠</span>
        <span class="ce-name">${esc(label)}</span>
        ${_nextPickups(sched).length ? _statusHTML(urgency, URGENCY[urgency].short) : ''}
        <button class="ce-remove" title="Remove home" aria-label="Remove home">${icon('close')}</button>
      </div>
      <div class="ce-addr" contenteditable="false" title="Double-click to edit this address">${esc(addrText)}</div>
      <div class="ce-detail">
        <div class="ce-sched">${homeScheduleHTML(sched)}</div>
        <div class="ce-actions">
          <button class="ce-btn ce-btn-addr">Set address</button>
          <button class="ce-btn ce-btn-place">${icon('pin')} Set location</button>
        </div>
      </div>`;

    // ── Address editing → geocode + trash refresh ──
    const addrEl = entry.querySelector('.ce-addr');
    makeEditable(addrEl, () => addrText, q => setHomeAddress(h.id, q));

    entry.querySelector('.ce-btn-addr').addEventListener('click', () => addrEl.startEdit());
    entry.querySelector('.ce-btn-place').addEventListener('click', () => startPlacingHome(h.id));
    entry.querySelector('.ce-remove').addEventListener('click', () => removeHome(h.id));

    homePanel.appendChild(entry);
  });
  updateSheetSummary();
}

// Tap-to-place mode; the map equivalent is the right-click "Add home here".
document.getElementById('btn-add-home').addEventListener('click', () => startPlacingHome());

// Add a home at a coordinate (map tap / right-click); the backend
// reverse-geocodes its address unless the user types one into the card.
async function addHome(lat, lon, address = '') {
  const h = { id: newId(), lat, lon, address };
  homes.push(h);
  savePrefs();
  setSheetCollapsed(false);  // reveal the new home card
  updateHomeMarkers();
  renderHomePanel();
  await checkHome(h.id);
  return h;
}

// Geocode a typed address → move that home's pin + refresh trash. Keeps the
// typed text as the address (ReCollect matches the user's address string best).
async function setHomeAddress(id, query) {
  const h = homes.find(x => x.id === id);
  if (!h) return;
  try {
    const hit = await geocode(query);
    if (!hit) { showToast('Address not found', true); renderHomePanel(); return; }
    h.lat = hit.lat; h.lon = hit.lon; h.address = query;
  } catch (_) { showToast('Could not look up address', true); renderHomePanel(); return; }
  savePrefs();
  updateHomeMarkers();
  renderHomePanel();
  await checkHome(id);
}

// Move a home to a map spot: its address is re-derived from the new location.
async function moveHome(id, lat, lon) {
  const h = homes.find(x => x.id === id);
  if (!h) return;
  h.lat = lat; h.lon = lon; h.address = '';
  savePrefs();
  setSheetCollapsed(false);
  updateHomeMarkers();
  renderHomePanel();
  await checkHome(id);
}

async function removeHome(id) {
  homes = homes.filter(h => h.id !== id);
  delete homeSchedules[id];
  const m = _homeMarkers.get(id);
  if (m) { m.remove(); _homeMarkers.delete(id); }
  savePrefs();
  renderHomePanel();
}

function updateHomeMarkers() {
  if (!map) return;
  for (const [id, marker] of _homeMarkers) {
    if (!homes.find(h => h.id === id)) { marker.remove(); _homeMarkers.delete(id); }
  }
  homes.forEach(h => {
    _homeMarkers.set(h.id, placeMarker(_homeMarkers.get(h.id), h, () => {
      const el = document.createElement('div');
      el.className = 'home-marker';
      el.textContent = '🏠';
      el.title = 'Home';
      return el;
    }));
  });
}

function checkAllHomes() { for (const h of homes) checkHome(h.id); }

async function checkHome(id) {
  const h = homes.find(x => x.id === id);
  if (!h) return;
  try {
    // Region is derived server-side from the home coordinate — a home can sit
    // in a different region than the map's currently selected one.
    const res = await postJSON('/api/check-home', { lat: h.lat, lon: h.lon, address: h.address });
    if (res.ok) {
      const data = await res.json();
      homeSchedules[h.id] = data;
      // A home dropped by tap/right-click has no address; adopt the one the
      // backend reverse-geocoded so the card shows a real street address.
      if (!h.address && data.address) { h.address = data.address; savePrefs(); }
      renderHomePanel();
    }
  } catch (_) {}
}
