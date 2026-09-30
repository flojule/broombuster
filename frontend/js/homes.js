// ── Homes (residences) — trash/recycling day ───────────────────────────────────
function homeScheduleHTML(sched) {
  if (!sched) return '<span style="color:var(--muted)">Loading…</span>';
  const domains = sched.domains || [];
  if (!domains.length) {
    return '<div class="ce-sched-item" style="color:var(--muted)">'
         + 'No collection info for this address.</div>';
  }
  return domains.map(domainBlockHTML).join('');
}

function renderHomePanel() {
  if (!homePanel) return;
  // Don't wipe an address editor (contenteditable) mid-type on a re-render.
  const ae = document.activeElement;
  if (homePanel.contains(ae) && (ae.isContentEditable || ae.tagName === 'INPUT')) return;
  homePanel.innerHTML = '';

  // "Add home" button — always available (multiple homes allowed, like cars).
  // Tap-to-place mode; the map equivalent is the right-click "Set home here".
  const btn = document.createElement('button');
  btn.id = 'btn-add-home';
  btn.className = 'add-car-btn add-home-btn';
  btn.textContent = '🏠 Add home';
  btn.title = 'Add a home to see trash & recycling day';
  btn.addEventListener('click', startPlacingHome);
  homePanel.appendChild(btn);

  homes.forEach((h, i) => {
    const sched    = homeSchedules[h.id];
    const urgency  = panelUrgency(sched);
    const addrText = h.address ? abbreviate(h.address) : '';
    const label    = homes.length > 1 ? `Home ${i + 1}` : 'Home';

    const entry = document.createElement('div');
    entry.className = 'car-entry home-entry';
    entry.dataset.id = h.id;
    entry.dataset.urgency = urgency;
    entry.style.cssText = '--car-color:#16a34a;--urg-color:#16a34a';
    entry.innerHTML = `
      <div class="ce-header">
        <span class="ce-dot ce-dot-home">🏠</span>
        <span class="ce-name">${esc(label)}</span>
        <button class="ce-remove" title="Remove home">✕</button>
      </div>
      <div class="ce-addr" contenteditable="false" title="Double-click to edit this address">${esc(addrText)}</div>
      <div class="ce-sched">${homeScheduleHTML(sched)}</div>`;

    // ── Address editing → geocode + trash refresh ──
    const addrEl = entry.querySelector('.ce-addr');
    makeEditable(addrEl, () => addrText, q => setHomeAddress(h.id, q));

    entry.querySelector('.ce-remove').addEventListener('click', () => removeHome(h.id));

    homePanel.appendChild(entry);
  });
  updateSheetSummary();
}

// Add a home at a coordinate (map tap / right-click). Zone-based trash resolves
// straight from the coordinate; for address-based (ReCollect) cities the backend
// reverse-geocodes an address (or the user types one into the card afterward),
// since reverse geocoding lives in the backend, not the frontend.
async function addHome(lat, lon, address = '') {
  const h = { id: _newId(), lat, lon, address };
  homes.push(h);
  await savePrefs();
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
  await savePrefs();
  updateHomeMarkers();
  renderHomePanel();
  await checkHome(id);
}

async function removeHome(id) {
  homes = homes.filter(h => h.id !== id);
  delete homeSchedules[id];
  const m = _homeMarkers.get(id);
  if (m) { m.remove(); _homeMarkers.delete(id); }
  await savePrefs();
  renderHomePanel();
}

function updateHomeMarkers() {
  if (!map) return;
  for (const [id, marker] of _homeMarkers) {
    if (!homes.find(h => h.id === id)) { marker.remove(); _homeMarkers.delete(id); }
  }
  homes.forEach(h => {
    if (_homeMarkers.has(h.id)) { _homeMarkers.get(h.id).setLngLat([h.lon, h.lat]); return; }
    const el = document.createElement('div');
    el.className = 'home-marker';
    el.textContent = '🏠';
    el.title = 'Home';
    const marker = new maplibregl.Marker({ element: el, anchor: 'center' })
      .setLngLat([h.lon, h.lat]).addTo(map);
    _homeMarkers.set(h.id, marker);
  });
}

function checkAllHomes() { for (const h of homes) checkHome(h.id); }

async function checkHome(id) {
  const h = homes.find(x => x.id === id);
  if (!h) return;
  try {
    // Region is derived server-side from the home coordinate — a home can sit
    // in a different region than the map's currently selected one.
    const res = await apiFetch('/check-home', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lat: h.lat, lon: h.lon, address: h.address }),
    });
    if (res.ok) {
      const data = await res.json();
      homeSchedules[h.id] = data;
      // A home dropped by tap/right-click has no address; adopt the one the
      // backend reverse-geocoded so the card shows a real street address.
      if (!h.address && data.address) { h.address = data.address; await savePrefs(); }
      renderHomePanel();
    }
  } catch (_) {}
}
