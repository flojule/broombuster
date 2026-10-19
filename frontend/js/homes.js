// ── Homes — trash pickup days ────────────────────────────────────────────────
// Oakland looks them up by address (ReCollect); elsewhere no lookup exists, so the
// home's page asks for its collection day and the server applies the city's holiday
// rule (/check-home trash_kind "weekly").
// [stream, next ISO date] per pickup stream, soonest first. Dates before the
// region's today are dropped: a response can outlive its day.
function _nextPickups(sched) {
  const today = BroomUrgency.isoDay(schedNow(sched));
  return Object.entries(sched?.pickups || {})
    .map(([stream, dates]) => [stream, dates.find(d => d >= today)])
    .filter(([, next]) => next)
    .sort((a, b) => a[1].localeCompare(b[1]));
}

function homeUrgency(sched) {
  const next = _nextPickups(sched)[0]?.[1];
  if (!next) return 'safe';
  const today = schedNow(sched);
  if (next === BroomUrgency.isoDay(today)) return 'today';
  return next === BroomUrgency.isoDay(BroomUrgency.addDays(today, 1)) ? 'tomorrow' : 'safe';
}

// Collection day picker (weekly cities): Mon–Sun, or not set.
function _collectionDayHTML(h) {
  const opts = ['<option value="">Not set</option>', ...BroomUrgency.WEEKDAYS.map((w, i) =>
    `<option value="${i}"${h.collection_day === i ? ' selected' : ''}>${esc(w)}</option>`)];
  return `<label class="ce-field">Collection day <select class="field ce-collection-day">${opts.join('')}</select></label>`;
}

function homeScheduleHTML(h, sched) {
  const id = h.id;
  const fmt = iso => { const [y, m, d] = iso.split('-').map(Number); return BroomUrgency.dayLabel({ y, m, d }); };
  const lines = sched && _nextPickups(sched).map(([stream, iso]) => `${stream}: ${fmt(iso)}`);
  if (sched?.trash_kind === 'weekly') {
    const note = h.collection_day == null ? 'Pick your collection day.'
      : sched.city === 'berkeley' ? 'On sweeping days, carts go on the parking strip.' : '';
    return schedBlockHTML(id, 'Trash day', lines?.length ? lines : [])
      .replace('<div class="sec">', `<div class="sec">${_collectionDayHTML(h)}`)
      + (note ? `<p class="note">${esc(note)}</p>` : '');
  }
  if (sched && !lines.length) return schedBlockHTML(id, 'Trash day', ['No collection info for this address.']);
  return schedBlockHTML(id, 'Trash day', lines);
}

// The home rows (after the cars); a home's settings are under its page's "…".
function renderHomePanel() {
  if (isEditingIn(itemList)) return;
  for (const el of [...itemList.querySelectorAll(':scope > .home-entry')]) el.remove();
  homes.forEach(h => {
    const sched = homeSchedules[h.id];
    const addrText = h.address ? abbreviate(h.address) : '';
    const entry = itemCard({
      id: h.id, kind: 'home', color: HOME_COLOR, name: itemName(h),
      urgency: _nextPickups(sched).length ? homeUrgency(sched) : null, addr: addrText,
      detail: homeScheduleHTML(h, sched) + rulesBlockHTML('Area', placeRules[h.id]),
    });
    entry.querySelector('.ce-collection-day')?.addEventListener('change', e => {
      const v = e.target.value;
      if (v === '') delete h.collection_day; else h.collection_day = Number(v);
      savePrefs();
      checkHome(h.id);
    });

    makeEditable(entry.querySelector('.ce-addr'), () => addrText, q => setHomeAddress(h.id, q));
    entry.querySelector('.ce-btn-retry')?.addEventListener('click', () => checkHome(h.id));
    itemList.appendChild(entry);
  });
  applySelection();
  updateSavedSummary();
  renderTargetInfo();  // the calendar shows the homes' pickups
}

// Add a home at a coordinate; the backend reverse-geocodes its address unless the
// user types one into the card.
function addHome(lat, lon) {
  const h = { id: newId(), lat, lon, address: '' };
  homes.push(h);
  homeChanged(h);
  openItem(h.id);
  offerInstall();
}

// Geocode a typed address → move that home's pin + refresh trash. Keeps the
// typed text as the address (ReCollect matches the user's address string best).
async function setHomeAddress(id, query) {
  const h = homes.find(x => x.id === id);
  if (!h) return;
  let hit;
  try { hit = await geocode(query, regionAt(h.lat, h.lon) || _region); }
  catch (_) { showToast('Could not look up the address', true); renderHomePanel(); return; }
  if (!hit) { showToast('Address not found', true); renderHomePanel(); return; }
  Object.assign(h, { lat: hit.lat, lon: hit.lon, address: query });
  homeChanged(h);
}

// Move a home to a map spot: its address is re-derived from the new location.
function moveHome(id, lat, lon) {
  const h = homes.find(x => x.id === id);
  if (!h) return;
  Object.assign(h, { lat, lon, address: '' });
  homeChanged(h);
  openItem(h.id);
}

// A home was added or moved: save, show, and look up its pickup days.
function homeChanged(h) {
  savePrefs();
  updateHomeMarkers();
  renderHomePanel();
  checkHome(h.id);
}

function removeHome(id) {
  homes = homes.filter(h => h.id !== id);
  delete homeSchedules[id];
  savePrefs();
  updateHomeMarkers();
  renderHomePanel();
}

function checkAllHomes() { for (const h of homes) checkHome(h.id); }

async function checkHome(id) {
  const h = homes.find(x => x.id === id);
  if (!h) return;
  // Region is derived server-side from the home coordinate — a home can sit
  // in a different region than the map's.
  const data = await loadSchedule(id, homeSchedules,
    () => postJSON('/api/check-home', { lat: h.lat, lon: h.lon, address: h.address,
                                        collection_day: h.collection_day ?? undefined }));
  // A home dropped on the map has no address; adopt the one the backend
  // reverse-geocoded so the card shows a real street address.
  if (data && !h.address && data.address) { h.address = data.address; savePrefs(); }
  renderHomePanel();
  if (await loadRules(h, 'home')) renderHomePanel();
}
