// ── Sheet (as in Find My): Calendar | Saved | Me ──────────────────────────────
// Phone: at the bottom with the tab bar, at one of three heights (detents): small (a
// tab's header and a peek of it), medium and large. Below large it floats clear of the
// screen's edges, as iOS 26 sheets do; it docks edge to edge on the way up to large. Drag the grabber, a tab's
// header or its content, or tap the grabber to step through them. The map is padded by the
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

// Sheet height (px) of each detent on this screen: small shows one week of the calendar,
// medium (half the screen, at least four weeks) and large whole weeks (calFit; before
// the calendar has been laid out, estimates).
function detentHeights() {
  const H = document.getElementById('app-screen').clientHeight;
  const top = document.getElementById('map-ctrl').offsetTop;  // just below the status bar / notch
  const fit = typeof calFit === 'function' ? calFit() : null;
  const tb = getComputedStyle(tabbar);
  const small = fit ? fit.small : tabbar.offsetHeight + parseFloat(tb.marginTop) + parseFloat(tb.marginBottom) + SHEET_PEEK;
  const round = h => fit ? fit.round(h) : Math.round(h);
  const large = Math.max(small, round(H - top));
  // Medium: half the screen, and at least four weeks of the month.
  const medium = Math.max(small, Math.min(large, Math.max(round(H * 0.5), fit ? fit.weeks(4) : 0)));
  return { small, medium, large };
}

function setDetent(d) {
  if (d !== _detent) { _detent = d; _store('bb_detent', d); }
  applySheet();
}
// At least `d` (never shrinks the sheet the user opened wider).
function raiseDetent(d) {
  if (DETENTS.indexOf(_detent) < DETENTS.indexOf(d)) setDetent(d);
}

// How docked the sheet is: 0 floating (small, medium) .. 1 edge to edge (large); its
// gap to the screen's edges is --sheet-gap times 1 - k (styles.css).
function _setDock(k) { sheet.style.setProperty('--k', k); }
const sheetGap = () => parseFloat(getComputedStyle(sheet).getPropertyValue('--sheet-gap')) || 0;
// Room the sheet takes from the bottom of the screen at the detent (0 on desktop).
function sheetFootprint(d = _detent) {
  return isDesktop() ? 0 : detentHeights()[d] + (d === 'large' ? 0 : sheetGap());
}

function applySheet() {
  const desk = isDesktop();
  sheet.style.height = desk ? '' : detentHeights()[_detent] + 'px';
  _setDock(!desk && _detent === 'large' ? 1 : 0);
  document.body.classList.toggle('sheet-large', !desk && _detent === 'large');
  grabber.setAttribute('aria-label', _detent === 'large' ? 'Shrink the sheet' : 'Enlarge the sheet');
  // The calendar shows the month view whenever it has the room: any detent above small.
  if (typeof calSetExpanded === 'function') calSetExpanded(desk || _detent !== 'small');
  applyMapInsets();
}

// The map's padding for the sheet (up to the medium height: the large sheet covers the
// map anyway), so its centre, which the target marks, is the middle of what shows.
function mapPadding() {
  const desk = isDesktop();
  return { top: 0, right: 0, left: desk ? sheet.offsetLeft + sheet.offsetWidth : 0,
           bottom: desk ? 0 : sheetFootprint(_detent === 'large' ? 'medium' : _detent) };
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
  const h = detentHeights(), height = Math.max(h.small * 0.85, Math.min(h.large, _drag.h0 + _drag.y0 - y));
  sheet.style.height = height + 'px';
  // Docks gradually between medium and large, so it widens under the finger.
  _setDock(Math.max(0, Math.min(1, (height - h.medium) / (h.large - h.medium || 1))));
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
    setDetent(DETENTS.reduce((a, b) => Math.abs(h[b] - aim) < Math.abs(h[a] - aim) ? b : a));
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
// Tap (or Enter / Space) on the grabber: the next detent up, from large back to small.
grabber.addEventListener('click', () => {
  if (_dragged) { _dragged = false; return; }
  setDetent(DETENTS[(DETENTS.indexOf(_detent) + 1) % DETENTS.length]);
});

// Content: decided on the first move; a sheet drag then keeps the content from scrolling.
function _contentMovesSheet(body, dy) {
  if (body.id === 'ic-cal' && _detent !== 'small') return false;
  return dy < 0 ? _detent !== 'large' : body.scrollTop <= 0;
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
  if (name === 'me' && typeof renderAccountSection === 'function') renderAccountSection();
  if (name === 'calendar' && typeof calShown === 'function') calShown();
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

// The sheet's top lifts the place banner and toasts above it (in a frame, so one
// observer's update can't re-trigger another within the same frame).
new ResizeObserver(() => requestAnimationFrame(() => {
  const gap = _detent === 'large' ? 0 : sheetGap();
  document.documentElement.style.setProperty('--sheet-h', (isDesktop() ? 0 : sheet.offsetHeight + gap) + 'px');
})).observe(sheet);
// The detents follow the screen and the tab bar's height (its width changes as the
// sheet docks, under the finger: that must not reset a drag).
window.addEventListener('resize', () => applySheet());
let _tabbarH = 0;
new ResizeObserver(() => requestAnimationFrame(() => {
  if (tabbar.offsetHeight === _tabbarH) return;
  _tabbarH = tabbar.offsetHeight;
  if (!_drag?.moved) applySheet();
})).observe(tabbar);
showTab(_tab);
applySheet();
