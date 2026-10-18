// ── Sheet (as in Find My): Calendar | Saved | Nearby | Settings ───────────────
// Phone: at the bottom with the tab bar, at one of its tab's heights (detents): the
// Calendar is small (one week and the selected day) or medium (the month); the lists
// (Saved, Nearby, Settings) are medium or large (nearly the whole screen). Each kind
// keeps its own. It floats clear of the screen's edges, as iOS 26 sheets do. Drag the
// grabber, a tab's header or its content, or tap the grabber to switch. The map is
// padded by the sheet (up to medium) so the centre target sits in the middle of the
// map area left free.
// Desktop: a full-height panel on the left; no detents.
const tabbar  = document.getElementById('tabbar');
const grabber = document.getElementById('sheet-grabber');
const TABS = ['calendar', 'saved', 'nearby', 'settings'];
const _stored = (key, allowed, fallback) => {
  try { const v = localStorage.getItem(key); return allowed.includes(v) ? v : fallback; } catch (_) { return fallback; }
};
const _store = (key, v) => { try { localStorage.setItem(key, v); } catch (_) {} };
let _tab = _stored('bb_tab', [...TABS, 'me'], 'calendar').replace(/^me$/, 'settings');  // Settings was "Me"
// The detents of a tab, smallest first; each kind of tab remembers where it was left.
const _kind = t => t === 'calendar' ? 'calendar' : 'list';
const tabDetents = (t = _tab) => _kind(t) === 'calendar' ? ['small', 'medium'] : ['medium', 'large'];
const _detentOf = {
  calendar: _stored('bb_detent_calendar', ['small', 'medium'], 'small'),
  list: _stored('bb_detent_list', ['medium', 'large'], 'medium'),
};
let _detent = _detentOf[_kind(_tab)];
let _compact = false;  // placing a car or home: the sheet is small whatever the tab

// Sheet height (px) of each detent on this screen: small shows one week of the calendar
// and the selected day, medium half the screen (at least four weeks, at most 60% so the
// map stays in view), both whole weeks (calFit; before the calendar has been laid out,
// estimates); large reaches up to the map's controls.
const MEDIUM_MAX = 0.6;
const SHEET_PEEK = 200;  // px above the tab bar at small, until the calendar is measured
function detentHeights() {
  const H = document.getElementById('app-screen').clientHeight;
  const fit = typeof calFit === 'function' ? calFit() : null;
  const tb = getComputedStyle(tabbar);
  const small = fit ? fit.small : tabbar.offsetHeight + parseFloat(tb.marginTop) + parseFloat(tb.marginBottom) + SHEET_PEEK;
  const round = h => fit ? fit.round(h) : Math.round(h);
  const medium = Math.max(small, round(Math.min(H * MEDIUM_MAX, Math.max(H * 0.5, fit ? fit.weeks(4) : 0))));
  // Under the status bar (the map controls' top: the inset and the edge).
  const top = Math.max(document.getElementById('map-ctrl').offsetTop, 12);
  const large = Math.max(medium, H - top - sheetGap());
  return { small, medium, large };
}
// The height the sheet shows now.
const _shown = () => _compact ? 'small' : _detent;

function setDetent(d) {
  if (!tabDetents().includes(d)) return;
  if (d !== _detent) { _detent = _detentOf[_kind(_tab)] = d; _store('bb_detent_' + _kind(_tab), d); }
  applySheet();
}
// The current tab's smallest.
const lowerSheet = () => setDetent(tabDetents()[0]);
function setCompact(on) {
  _compact = on;
  applySheet();
}

// Its gap to the screen's edges (styles.css).
const sheetGap = () => parseFloat(getComputedStyle(sheet).getPropertyValue('--sheet-gap')) || 0;

function applySheet() {
  const desk = isDesktop(), ds = tabDetents();
  sheet.style.height = desk ? '' : detentHeights()[_shown()] + 'px';
  grabber.setAttribute('aria-label', _detent === ds[ds.length - 1] ? 'Shrink the sheet' : 'Enlarge the sheet');
  // The calendar shows the month view whenever it has the room: its medium.
  if (typeof calSetExpanded === 'function') calSetExpanded(desk || (!_compact && _detentOf.calendar !== 'small'));
  applyMapInsets();
}

// The map's padding for the sheet and its gap, so its centre, which the target marks, is
// the middle of what shows.
function mapPadding() {
  const desk = isDesktop(), h = !desk && detentHeights();
  return { top: 0, right: 0, left: desk ? sheet.offsetLeft + sheet.offsetWidth : 0,
           bottom: desk ? 0 : Math.min(h[_shown()], h.medium) + sheetGap() };
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
// The sheet follows the finger; on release it settles on the tab's detent nearest where
// the flick would carry it. Dragged by the grabber or a tab's header (pointer events),
// and from a tab's content as iOS sheets are (touch): up while it can grow, down once
// the content is scrolled to its top; the calendar's grid scrolls itself unless the
// sheet is small.
let _drag = null, _dragged = false;
function _dragBegin(y, t) { _drag = { y0: y, h0: sheet.offsetHeight, y, t, v: 0, moved: false }; }
// Follows the finger once it has moved 5 px; returns whether this is a drag.
function _dragTo(y, t) {
  if (!_drag) return false;
  if (!_drag.moved && Math.abs(y - _drag.y0) < 5) return false;
  if (!_drag.moved) { _drag.moved = true; sheet.classList.add('dragging'); }
  const h = detentHeights(), ds = tabDetents();
  sheet.style.height = Math.max(h[ds[0]] * 0.85, Math.min(h[ds[1]] * 1.04, _drag.h0 + _drag.y0 - y)) + 'px';
  const dt = t - _drag.t;
  if (dt > 0) _drag.v = (_drag.y - y) / dt;  // px per ms, up is positive
  _drag.y = y; _drag.t = t;
  return true;
}
function _dragEnd() {
  if (!_drag) return;
  _dragged = _drag.moved;
  if (_drag.moved) {
    sheet.classList.remove('dragging');
    const h = detentHeights(), aim = sheet.offsetHeight + _drag.v * 200;
    setDetent(tabDetents().reduce((a, b) => Math.abs(h[b] - aim) < Math.abs(h[a] - aim) ? b : a));
  }
  _drag = null;
}

// Grabber and headers. The finger is captured only once it moves, so a tap stays a click.
for (const el of [grabber, ...document.querySelectorAll('.tab-head')]) {
  el.addEventListener('pointerdown', e => {
    if (isDesktop() || e.button > 0) return;
    if (e.target.closest('button:not(#sheet-grabber), [contenteditable="true"]')) return;
    _dragBegin(e.clientY, e.timeStamp);
  });
  el.addEventListener('pointermove', e => {
    const was = _drag?.moved;
    if (_dragTo(e.clientY, e.timeStamp) && !was) el.setPointerCapture(e.pointerId);
  });
  el.addEventListener('pointerup', _dragEnd);
  el.addEventListener('pointercancel', _dragEnd);
}
// Tap (or Enter / Space) on the grabber: the other detent.
grabber.addEventListener('click', () => {
  if (_dragged) { _dragged = false; return; }
  const ds = tabDetents();
  setDetent(ds[(ds.indexOf(_detent) + 1) % ds.length]);
});

// Content: decided on the first move; a sheet drag then keeps the content from scrolling.
function _contentMovesSheet(body, dy) {
  if (body.id === 'ic-cal' && _detent !== 'small') return false;
  return dy < 0 ? _detent !== tabDetents().at(-1) : body.scrollTop <= 0;
}
let _content = null;  // { body, x0, y0, sheet: null (undecided) | bool }
for (const body of document.querySelectorAll('.tab-body')) {
  body.addEventListener('touchstart', e => {
    _content = null;
    if (isDesktop() || e.touches.length !== 1 || _drag || e.target.closest('[contenteditable="true"], input')) return;
    _content = { body, x0: e.touches[0].clientX, y0: e.touches[0].clientY, sheet: null };
  }, { passive: true });
  body.addEventListener('touchmove', e => {
    const c = _content;
    if (!c || c.body !== body || c.sheet === false) return;
    const p = e.touches[0], dx = p.clientX - c.x0, dy = p.clientY - c.y0;
    if (c.sheet === null) {
      if (Math.hypot(dx, dy) < 6) return;
      c.sheet = Math.abs(dy) > Math.abs(dx) && _contentMovesSheet(body, dy);
      if (!c.sheet) return;
      _dragBegin(c.y0, e.timeStamp);
    }
    e.preventDefault();
    _dragTo(p.clientY, e.timeStamp);
  }, { passive: false });
  const end = () => { if (_content?.sheet) _dragEnd(); _content = null; };
  body.addEventListener('touchend', end);
  body.addEventListener('touchcancel', end);
}

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
  tabbar.style.setProperty('--i', TABS.indexOf(name));  // the selected pill slides to it
  for (const t of TABS) document.getElementById('tab-' + t).hidden = t !== name;
  _detent = _detentOf[_kind(name)];
  applySheet();
  if (name === 'settings' && typeof renderAccountSection === 'function') renderAccountSection();
  if (name === 'calendar' && typeof calShown === 'function') calShown();
  if (name === 'nearby' && typeof nearbyShown === 'function') nearbyShown();
}
// Tapping the open tab again, with the sheet at its smallest, opens it.
tabbar.addEventListener('click', e => {
  const b = e.target.closest('[data-tab]');
  if (!b) return;
  if (b.dataset.tab === _tab && _detent === tabDetents()[0]) setDetent(tabDetents()[1]);
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

// The sheet's top lifts the place banner and toasts above it (in a frame, so one
// observer's update can't re-trigger another within the same frame).
new ResizeObserver(() => requestAnimationFrame(() => {
  document.documentElement.style.setProperty('--sheet-h', (isDesktop() ? 0 : sheet.offsetHeight + sheetGap()) + 'px');
})).observe(sheet);
// The detents follow the screen and the tab bar's height (not mid-drag).
window.addEventListener('resize', () => applySheet());
let _tabbarH = 0;
new ResizeObserver(() => requestAnimationFrame(() => {
  if (tabbar.offsetHeight === _tabbarH) return;
  _tabbarH = tabbar.offsetHeight;
  if (!_drag?.moved) applySheet();
})).observe(tabbar);
showTab(_tab);
