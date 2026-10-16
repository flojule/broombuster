// ── Sheet (as in Find My): Calendar | Saved | Me ──────────────────────────────
// Phone: docked at the bottom with the tab bar, at one of three heights (detents):
// small (a tab's header and a peek of it), medium and large. Drag the grabber or a
// tab's header, or tap the grabber to step through them. The map is padded by the
// sheet so the centre target sits in the middle of the map area left free.
// Desktop: a full-height panel on the left; no detents.
const tabbar  = document.getElementById('tabbar');
const grabber = document.getElementById('sheet-grabber');
const TABS = ['calendar', 'saved', 'me'];
const DETENTS = ['small', 'medium', 'large'];
const SHEET_PEEK = 144;  // px above the tab bar at the small detent: grabber, header, one row
const _stored = (key, allowed, fallback) => {
  try { const v = localStorage.getItem(key); return allowed.includes(v) ? v : fallback; } catch (_) { return fallback; }
};
const _store = (key, v) => { try { localStorage.setItem(key, v); } catch (_) {} };
let _tab = _stored('bb_tab', TABS, 'calendar');
let _detent = _stored('bb_detent', DETENTS, 'small');

// Sheet height (px) of each detent on this screen.
function detentHeights() {
  const H = document.getElementById('app-screen').clientHeight;
  const small = tabbar.offsetHeight + SHEET_PEEK;
  const top = document.getElementById('map-ctrl').offsetTop;  // just below the status bar / notch
  return { small, medium: Math.max(small, Math.round(H * 0.5)), large: Math.max(small, H - top) };
}

function setDetent(d) {
  if (d !== _detent) { _detent = d; _store('bb_detent', d); }
  applySheet();
}
// At least `d` (never shrinks the sheet the user opened wider).
function raiseDetent(d) {
  if (DETENTS.indexOf(_detent) < DETENTS.indexOf(d)) setDetent(d);
}

function applySheet() {
  const desk = isDesktop();
  sheet.style.height = desk ? '' : detentHeights()[_detent] + 'px';
  grabber.setAttribute('aria-label', _detent === 'large' ? 'Shrink the sheet' : 'Enlarge the sheet');
  // The calendar shows the month view whenever it has the room: any detent above small.
  const expanded = desk || _detent !== 'small';
  if (typeof _cal !== 'undefined' && _cal.expanded !== expanded) {
    _cal.expanded = expanded;
    _cal.anchor = null;
    renderTargetInfo();
  }
  applyMapInsets();
}

// The map's padding for the sheet (up to the medium height: the large sheet covers the
// map anyway), so its centre, which the target marks, is the middle of what shows.
function mapPadding() {
  const desk = isDesktop(), h = detentHeights();
  return { top: 0, right: 0, left: desk ? sheet.offsetLeft + sheet.offsetWidth : 0,
           bottom: desk ? 0 : Math.min(h[_detent], h.medium) };
}
function applyMapInsets(animate = true) {
  const pad = mapPadding();
  const box = document.getElementById('map-container').style;
  box.setProperty('--pad-l', pad.left + 'px');
  box.setProperty('--pad-b', pad.bottom + 'px');
  if (!map) return;
  const cur = map.getPadding();
  if (cur.left !== pad.left || cur.bottom !== pad.bottom) map.easeTo({ padding: pad, duration: animate ? 400 : 0 });
}

// ── Dragging (phone) ──────────────────────────────────────────────────────────
// The sheet follows the finger; on release it settles on the detent nearest where
// the flick would carry it.
let _drag = null, _dragged = false;
function _dragStart(e) {
  if (isDesktop() || e.button > 0) return;
  if (e.target.closest('button:not(#sheet-grabber), [contenteditable="true"]')) return;
  _drag = { y0: e.clientY, h0: sheet.offsetHeight, y: e.clientY, t: e.timeStamp, v: 0, moved: false };
}
// The finger is captured only once it moves, so a tap (or a double-tap on the title) stays a click.
function _dragMove(e) {
  if (!_drag) return;
  if (!_drag.moved && Math.abs(e.clientY - _drag.y0) < 5) return;
  if (!_drag.moved) {
    _drag.moved = true;
    sheet.classList.add('dragging');
    e.currentTarget.setPointerCapture(e.pointerId);
  }
  const h = detentHeights();
  sheet.style.height = Math.max(h.small * 0.85, Math.min(h.large, _drag.h0 + _drag.y0 - e.clientY)) + 'px';
  const dt = e.timeStamp - _drag.t;
  if (dt > 0) _drag.v = (_drag.y - e.clientY) / dt;  // px per ms, up is positive
  _drag.y = e.clientY; _drag.t = e.timeStamp;
}
function _dragEnd() {
  if (!_drag) return;
  _dragged = _drag.moved;
  if (_drag.moved) {
    sheet.classList.remove('dragging');
    const h = detentHeights(), aim = sheet.offsetHeight + _drag.v * 200;
    setDetent(DETENTS.reduce((a, b) => Math.abs(h[b] - aim) < Math.abs(h[a] - aim) ? b : a));
  }
  _drag = null;
}
for (const el of [grabber, ...document.querySelectorAll('.tab-head')]) {
  el.addEventListener('pointerdown', _dragStart);
  el.addEventListener('pointermove', _dragMove);
  el.addEventListener('pointerup', _dragEnd);
  el.addEventListener('pointercancel', _dragEnd);
}
// Tap (or Enter / Space) on the grabber: the next detent up, from large back to small.
grabber.addEventListener('click', () => {
  if (_dragged) { _dragged = false; return; }
  setDetent(DETENTS[(DETENTS.indexOf(_detent) + 1) % DETENTS.length]);
});

// ── Tabs ──────────────────────────────────────────────────────────────────────
// Leaving Saved closes an open item (as Find My does).
function showTab(name) {
  if (name !== 'saved' && typeof clearSelection === 'function') clearSelection();
  _tab = name;
  _store('bb_tab', name);
  for (const b of tabbar.querySelectorAll('[role="tab"]')) {
    const on = b.dataset.tab === name;
    b.setAttribute('aria-selected', String(on));
    b.tabIndex = on ? 0 : -1;
  }
  for (const t of TABS) document.getElementById('tab-' + t).hidden = t !== name;
  if (name === 'me' && typeof renderAccountSection === 'function') renderAccountSection();
}
// Tapping the open tab again, with the sheet small, opens it to medium.
tabbar.addEventListener('click', e => {
  const b = e.target.closest('[data-tab]');
  if (!b) return;
  if (b.dataset.tab === _tab && _detent === 'small') setDetent('medium');
  showTab(b.dataset.tab);
});
// Arrow keys move between tabs (the ARIA tabs pattern).
tabbar.addEventListener('keydown', e => {
  const step = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
  if (!step) return;
  e.preventDefault();
  const next = TABS[(TABS.indexOf(_tab) + step + TABS.length) % TABS.length];
  showTab(next);
  document.getElementById('tb-' + next).focus();
});

// A drag that starts on the sheet stays with it, even once the finger leaves it: the
// map listens for touch moves on the whole window.
sheet.addEventListener('touchmove', e => e.stopPropagation(), { passive: true });

// The sheet's height lifts the place banner and toasts above it (in a frame, so one
// observer's update can't re-trigger another within the same frame).
new ResizeObserver(() => requestAnimationFrame(() => {
  document.documentElement.style.setProperty('--sheet-h', (isDesktop() ? 0 : sheet.offsetHeight) + 'px');
})).observe(sheet);
// The detents follow the screen and the tab bar (which grows by the home-indicator inset).
window.addEventListener('resize', () => applySheet());
new ResizeObserver(() => requestAnimationFrame(() => applySheet())).observe(tabbar);
showTab(_tab);
applySheet();
