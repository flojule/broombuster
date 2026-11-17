// ── Sheet (as in Find My): Calendar | Saved | Settings ────────────────────────
// Phone: at the bottom with the tab bar, at one of three heights (detents) shared by all
// tabs: hidden (the tab bar only), small (one week of the calendar and the selected day)
// or medium (the month); a tab's content scrolls in it. It floats clear of the screen's
// edges, as iOS 26 sheets do. Drag the grabber, the tab bar, a tab's header or its
// content, or tap the grabber to switch; a tab opens a hidden sheet. The map is padded
// by the sheet so the center target sits in the middle of the map area left free.
// Desktop: a panel on the left, the calendar's height for every tab (styles.css); no detents.
const tabbar  = document.getElementById('tabbar');
const grabber = document.getElementById('sheet-grabber');
const TABS = ['calendar', 'saved', 'settings'];
let _tab = _stored('bb_tab', [...TABS, 'me'], 'calendar').replace(/^me$/, 'settings');  // Settings was "Me"
// The detents, smallest first; the sheet remembers where it was left.
const DETENTS = ['hidden', 'small', 'medium'];
let _detent = _stored('bb_detent', DETENTS, 'small');
let _compact = false;  // placing a car or home: the sheet is small whatever the tab

// Sheet height (px) of each detent on this screen: hidden the tab bar, small one week of
// the calendar and the selected day, medium half the screen (at least four weeks, at most
// 60% so the map stays in view), both whole weeks (calFit; before the calendar has been
// laid out, estimates).
const MEDIUM_MAX = 0.6;
const SHEET_PEEK = 280;  // px at small, until the calendar is measured
const TABBAR_H = 56;     // px: hidden, the sheet is the tab bar's capsule (styles.css)
function detentHeights() {
  const H = document.getElementById('app-screen').clientHeight;
  const fit = typeof calFit === 'function' ? calFit() : null;
  const hidden = TABBAR_H;
  const small = fit ? fit.small : SHEET_PEEK;
  const round = h => fit ? fit.round(h) : Math.round(h);
  const medium = Math.max(small, round(Math.min(H * MEDIUM_MAX, Math.max(H * 0.5, fit ? fit.weeks(4) : 0))));
  return { hidden, small, medium };
}
// The height the sheet shows now.
const _shown = () => _compact ? 'small' : _detent;

function setDetent(d) {
  if (!DETENTS.includes(d)) return;
  if (d !== _detent) { _detent = d; _store('bb_detent', d); }
  applySheet();
}
const lowerSheet = () => setDetent('hidden');
function setCompact(on) {
  _compact = on;
  applySheet();
}

// Its gap to the screen's edges, open or hidden (styles.css #sheet-probe).
const sheetProbe = document.getElementById('sheet-probe');
const sheetGap = (hidden = _shown() === 'hidden') => {
  const r = sheetProbe.getBoundingClientRect();
  return hidden ? r.height : r.width;
};
// --m: 0 open, 1 hidden, in between while dragged; styles.css draws the sheet and the tab
// bar from it, concentric with the screen's corners at every step.
function _setMorph(m) {
  sheet.style.setProperty('--m', m);
  sheet.classList.toggle('retracted', m > 0.99);
}

function applySheet() {
  const desk = isDesktop();
  _setMorph(!desk && _shown() === 'hidden' ? 1 : 0);
  sheet.style.height = desk ? '' : detentHeights()[_shown()] + 'px';
  grabber.setAttribute('aria-label', _detent === 'medium' ? 'Shrink the sheet' : 'Enlarge the sheet');
  // The calendar shows the month view whenever it has the room: medium.
  if (typeof calSetExpanded === 'function') calSetExpanded(desk || (!_compact && _detent === 'medium'));
  applyMapInsets();
}

// The map's padding for the sheet and its gap, so its center, which the target marks, is
// the middle of what shows.
function mapPadding() {
  const desk = isDesktop(), h = !desk && detentHeights();
  return { top: 0, right: 0, left: desk ? sheet.offsetLeft + sheet.offsetWidth : 0,
           bottom: desk ? 0 : Math.min(h[_shown()], h.medium) + sheetGap(!desk && _shown() === 'hidden') };
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
// The sheet follows the finger; on release it settles on the detent nearest where the
// flick would carry it. Dragged by the grabber, the tab bar or a tab's header (pointer events),
// and from a tab's content as iOS sheets are (touch): up while it can grow, down once
// the content is scrolled to its top; the calendar's grid scrolls itself unless the
// sheet is small.
let _drag = null, _dragged = false;
function _dragBegin(y, t) { _drag = { y0: y, h0: sheet.offsetHeight, y, t, v: 0, moved: false }; _dragged = false; }
// Follows the finger once it has moved 5 px; returns whether this is a drag.
function _dragTo(y, t) {
  if (!_drag) return false;
  if (!_drag.moved && Math.abs(y - _drag.y0) < 5) return false;
  if (!_drag.moved) { _drag.moved = true; sheet.classList.add('dragging'); }
  const h = detentHeights(), height = Math.max(h.hidden, Math.min(h.medium * 1.04, _drag.h0 + _drag.y0 - y));
  sheet.style.height = height + 'px';
  _setMorph(Math.min(1, Math.max(0, (h.small - height) / (h.small - h.hidden))));
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

// Grabber, tab bar and headers. The finger is captured only once it moves, so a tap stays a click.
for (const el of [grabber, tabbar, ...document.querySelectorAll('.tab-head')]) {
  el.addEventListener('pointerdown', e => {
    if (isDesktop() || e.button > 0) return;
    if (e.target.closest('button:not(#sheet-grabber, [role="tab"]), [contenteditable="true"]')) return;
    _dragBegin(e.clientY, e.timeStamp);
  });
  el.addEventListener('pointermove', e => {
    const was = _drag?.moved;
    if (_dragTo(e.clientY, e.timeStamp) && !was) el.setPointerCapture(e.pointerId);
  });
  el.addEventListener('pointerup', _dragEnd);
  el.addEventListener('pointercancel', _dragEnd);
}
// Tap (or Enter / Space) on the grabber: small and medium in turn.
grabber.addEventListener('click', () => {
  if (_dragged) { _dragged = false; return; }
  setDetent(_detent === 'medium' ? 'small' : 'medium');
});

// Content: decided on the first move; a sheet drag then keeps the content from scrolling.
function _contentMovesSheet(body, dy) {
  if (body.id === 'ic-cal' && _detent === 'medium') return false;
  return dy < 0 ? _detent !== 'medium' : body.scrollTop <= 0;
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
  if (name === 'calendar' && typeof calShown === 'function') calShown();
  applySheet();
  if (name === 'settings' && typeof renderAccountSection === 'function') renderAccountSection();
}
// A tab opens a hidden sheet (small); tapping the open tab again at small opens the month.
tabbar.addEventListener('click', e => {
  const b = e.target.closest('[data-tab]');
  if (!b || _dragged) { _dragged = false; return; }
  if (_detent === 'hidden') setDetent('small');
  else if (b.dataset.tab === _tab && _detent === 'small') setDetent('medium');
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
// The detents follow the screen.
window.addEventListener('resize', () => applySheet());
showTab(_tab);
