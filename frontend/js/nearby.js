// ── Nearby tab: farmers markets, events, street events and council meetings this week
// near the centre target (/api/around), and links to the calendars without a feed.
// Fetched when the tab opens; "Here" refreshes it once the map has moved away.
const nearbyBody = document.getElementById('nearby-body');
const btnNearbyHere = document.getElementById('btn-nearby-here');
let _nearbyAt = null;   // {lat, lon} the list is for
let _nearbySeq = 0;
const NEARBY_KIND = { market: 'Market', event: 'Event', street: 'Street event', meeting: 'Meeting' };
const NEARBY_MOVED_KM = 1;

function _kmBetween(a, b) {
  const p = Math.PI / 180, s = Math.sin((b.lat - a.lat) * p / 2) ** 2
    + Math.cos(a.lat * p) * Math.cos(b.lat * p) * Math.sin((b.lon - a.lon) * p / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(s));
}

function _nearbyDay(iso, today) {
  if (iso === BroomUrgency.isoDay(today)) return 'Today';
  if (iso === BroomUrgency.isoDay(BroomUrgency.addDays(today, 1))) return 'Tomorrow';
  const [y, m, d] = iso.split('-').map(Number);
  return BroomUrgency.dayLabel({ y, m, d });
}

function _safeUrl(u) { return /^https?:\/\//.test(u || '') ? u : ''; }

function _nearbyRow(it) {
  const meta = [it.time, it.place, it.km != null ? `${(it.km / 1.609).toFixed(1)} mi` : ''].filter(Boolean).join(' · ');
  const url = _safeUrl(it.url);
  const title = url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(it.title)}</a>` : esc(it.title);
  return `<div class="nb-row"><span class="nb-kind nb-${esc(it.kind)}">${esc(NEARBY_KIND[it.kind] || it.kind)}</span>`
       + `<span class="nb-text"><span class="nb-title">${title}</span>`
       + (meta ? `<span class="nb-meta">${esc(meta)}</span>` : '') + '</span></div>';
}

function renderNearby(data) {
  const today = regionNow(data.region);
  const byDay = new Map();
  for (const it of data.items) {
    if (!byDay.has(it.date)) byDay.set(it.date, []);
    byDay.get(it.date).push(it);
  }
  const days = [...byDay].map(([iso, items]) =>
    `<div class="sec-label">${esc(_nearbyDay(iso, today))}</div><div class="sec">${items.map(_nearbyRow).join('')}</div>`).join('');
  const links = data.links.length
    ? '<div class="sec-label">More calendars</div><div class="sec">' + data.links.map(l => _safeUrl(l.url)
      ? `<a class="nb-link" href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.title)}</a>` : '').join('') + '</div>'
    : '';
  nearbyBody.innerHTML = (days || '<p class="note">Nothing this week.</p>') + links;
}

async function loadNearby() {
  if (!map) return;
  const c = map.getCenter(), at = { lat: c.lat, lon: c.lng }, n = ++_nearbySeq;
  _nearbyAt = at;
  btnNearbyHere.hidden = true;
  nearbyBody.innerHTML = '<p class="note">Loading…</p>';
  const q = new URLSearchParams({ lat: at.lat, lon: at.lon });
  const region = regionAt(at.lat, at.lon);
  if (region) q.set('region', region);
  try {
    const res = await fetch('/api/around?' + q);
    if (n !== _nearbySeq) return;
    if (!res.ok) throw new Error(res.statusText);
    renderNearby(await res.json());
  } catch (_) {
    if (n === _nearbySeq) nearbyBody.innerHTML = '<p class="note">Couldn’t load.</p>';
  }
}

// The tab opened: load for the map centre, or offer "Here" once the map has moved away.
function nearbyShown() {
  if (!map) { setTimeout(nearbyShown, 500); return; }
  if (!_nearbyAt) { loadNearby(); return; }
  _nearbyMoved();
}
function _nearbyMoved() {
  if (!_nearbyAt || !map) return;
  const c = map.getCenter();
  btnNearbyHere.hidden = _kmBetween(_nearbyAt, { lat: c.lat, lon: c.lng }) < NEARBY_MOVED_KM;
}
btnNearbyHere.addEventListener('click', loadNearby);
// While the tab is open, panning far enough offers "Here" (the map listener is added
// once the map exists).
(function waitForMap() {
  if (!map) { setTimeout(waitForMap, 500); return; }
  map.on('moveend', () => { if (_tab === 'nearby') _nearbyMoved(); });
})();
if (_tab === 'nearby') nearbyShown();
