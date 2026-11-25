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
// The app opens on the calendar, small. The detents, smallest first.
let _tab = 'calendar';
const DETENTS = ['hidden', 'small', 'medium'];
let _detent = 'small';
let _compact = false;  // placing a car or home: the sheet is small whatever the tab

// Sheet height (px) of each detent on this screen: hidden the tab bar, small one week of
// the calendar and the selected day, medium half the screen (at least four weeks, at most
// 60% so the map stays in view), both whole weeks (calFit; before the calendar has been
// laid out, estimates).
const MEDIUM_MAX = 0.6;
const MEDIUM_FOUR_WEEKS = 0.72;
const SHEET_PEEK = 280;  // px at small, until the calendar is measured
const TABBAR_H = 56;     // px: hidden, the sheet is the tab bar's capsule (styles.css)
function detentHeights() {
  const H = document.getElementById('app-screen').clientHeight;
  const fit = typeof calFit === 'function' ? calFit() : null;
  const hidden = TABBAR_H;
  const small = fit ? fit.small : SHEET_PEEK;
  const round = h => fit ? fit.round(h) : Math.round(h);
  // Four weeks also when they fit in MEDIUM_FOUR_WEEKS of the screen (a 6.1" phone's 60% rounds to two).
  const four = fit ? fit.weeks(4) : 0;
  const medium = Math.max(small, round(Math.max(Math.min(H * MEDIUM_MAX, Math.max(H * 0.5, four)),
                                                four <= H * MEDIUM_FOUR_WEEKS ? four : 0)));
  return { hidden, small, medium };
}
// The height the sheet shows now.
const _shown = () => _compact ? 'small' : _detent;

function setDetent(d) {
  if (!DETENTS.includes(d)) return;
  _detent = d;
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

// animate: the phone sheet moves to its new height (a settle) rather than jumping.
function applySheet(animate = true) {
  const desk = isDesktop();
  if (desk) {
    _stopSettle();
    _setMorph(0);
    sheet.style.height = '';
  } else {
    const to = detentHeights()[_shown()];
    if (animate && sheet.style.height) _settle(to);
    else { _stopSettle(); _setMorph(_shown() === 'hidden' ? 1 : 0); sheet.style.height = to + 'px'; }
  }
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
  if (typeof fitSigns === 'function') fitSigns();  // the target moved: the street card may now reach it
}

// ── Dragging (phone) ──────────────────────────────────────────────────────────
// The sheet follows the finger; on release it settles on a detent: the next one in the
// flick's direction after a flick, else the one nearest where the finger's speed would
// carry it. Dragged by the grabber, the tab bar or a tab's header (pointer events),
// and from a tab's content as iOS sheets are (touch): up while it can grow, down once
// the content is scrolled to its top; the calendar's grid scrolls itself unless the
// sheet is small.
// While it moves (dragged or settling) the sheet is laid out once at its tallest (max)
// and slid down by the difference (a transform), its foot clipped and the tab bar slid
// back up: no layout per frame above small. The height is set once it stops.
const FLICK_V = 0.4;     // px/ms: a release at least this fast is a flick
const VEL_WINDOW = 100;  // ms of finger samples the release speed is taken over
let _drag = null, _dragged = false;
function _dragBegin(y, t) { _drag = { y0: y, samples: [{ y, t }], moved: false }; _dragged = false; }
function _dragShow(height, max) {
  const dy = max - height;
  sheet.style.transform = `translateY(${dy}px)`;
  sheet.style.clipPath = `inset(0 0 ${dy}px 0 round var(--r-top) var(--r-top) var(--sheet-r) var(--sheet-r))`;
  tabbar.style.transform = `translateY(${-dy}px)`;
}
// --m for a shown height: 0 at small and above, 1 at hidden.
const _morphAt = (h, height) => Math.min(1, Math.max(0, (h.small - height) / (h.small - h.hidden)));
// Lays the sheet out at `max` (transitions off) for a drag or a settle.
function _moveBegin(max) {
  sheet.classList.add('dragging');
  sheet.style.height = max + 'px';
}
// Back to a laid-out sheet at `height`.
function _moveEnd(height) {
  sheet.style.height = height + 'px';
  sheet.style.transform = sheet.style.clipPath = tabbar.style.transform = '';
  void sheet.offsetHeight;
  sheet.classList.remove('dragging');
}
// Follows the finger once it has moved 5 px; returns whether this is a drag.
function _dragTo(y, t) {
  if (!_drag) return false;
  if (!_drag.moved && Math.abs(y - _drag.y0) < 5) return false;
  if (!_drag.moved) {
    _drag.moved = true;
    _stopSettle();  // caught mid-settle: the drag starts where the sheet is
    _drag.h0 = sheet.offsetHeight;
    _drag.h = detentHeights();
    _drag.max = Math.max(_drag.h0, Math.round(_drag.h.medium * 1.04));
    _moveBegin(_drag.max);
  }
  const h = _drag.h, max = _drag.max;
  const height = _drag.height = Math.max(h.hidden, Math.min(max, _drag.h0 + _drag.y0 - y));
  _dragShow(height, max);
  _setMorph(_morphAt(h, height));
  const s = _drag.samples;
  s.push({ y, t });
  while (s.length > 2 && t - s[1].t > VEL_WINDOW) s.shift();
  return true;
}
// Release speed (px/ms, up is positive): the finger's travel over its last VEL_WINDOW ms;
// 0 when it was held still that long.
function _releaseV(now) {
  const s = _drag.samples.filter(p => now - p.t <= VEL_WINDOW);
  if (s.length < 2) return 0;
  const a = s[0], b = s[s.length - 1];
  return b.t > a.t ? (a.y - b.y) / (b.t - a.t) : 0;
}
function _dragEnd() {
  if (!_drag) return;
  _dragged = _drag.moved;
  if (_drag.moved) {
    const h = _drag.h, v = _releaseV(performance.now()), at = _drag.height;
    let d;
    if (Math.abs(v) >= FLICK_V) {  // a flick: the next detent its way
      const ahead = DETENTS.filter(k => v > 0 ? h[k] > at + 1 : h[k] < at - 1);
      d = ahead.length ? (v > 0 ? ahead[0] : ahead[ahead.length - 1]) : (v > 0 ? 'medium' : 'hidden');
    } else {
      const aim = at + v * 200;
      d = DETENTS.reduce((a, b) => Math.abs(h[b] - aim) < Math.abs(h[a] - aim) ? b : a);
    }
    _settleV = v;  // the settle starts at the finger's speed
    _settleFrom = { height: at, max: _drag.max };
    _drag = null;
    setDetent(d);
    _settleFrom = null;
    return;
  }
  _drag = null;
}

// ── Settling (phone): the sheet goes to a detent's height ──────────────────────
// A critically damped spring that starts at the finger's speed after a drag, so a flick
// carries on without a jolt (a change from code starts fast and eases out, as iOS sheets
// do); its rate rises with that speed, so it never overshoots the detent. Reduce Motion:
// no settle, the height is set at once.
const SETTLE_W = 0.018;  // 1/ms: the spring's rate at rest (within 1 px in ~400 ms)
const _reduceMotionSheet = window.matchMedia('(prefers-reduced-motion: reduce)');
let _settling = null;    // { raf, height } while settling
let _settleV = 0, _settleFrom = null;  // set by _dragEnd for the settle it starts (null: from code)
function _settle(to) {
  const h = detentHeights(), m1 = _shown() === 'hidden' ? 1 : 0;
  // From where the sheet shows now: the released drag, a settle under way, or at rest.
  const from = _settleFrom ? _settleFrom.height : _settling ? _settling.height : sheet.offsetHeight;
  const max0 = _settleFrom ? _settleFrom.max : _settling ? parseFloat(sheet.style.height) : from;
  if (_settling) { cancelAnimationFrame(_settling.raf); _settling = null; }
  if (Math.abs(to - from) < 1 || _reduceMotionSheet.matches) {
    _moveEnd(to);
    _setMorph(m1);
    _settled();
    return;
  }
  const max = Math.max(max0, from, to), A = to - from;
  const v0 = _settleFrom ? _settleV : SETTLE_W * A, w = A * v0 > 0 ? Math.max(SETTLE_W, v0 / A) : SETTLE_W, B = w * A - v0;
  _moveBegin(max);
  const st = _settling = { raf: 0, height: from };
  let t0 = null;
  const step = now => {
    if (t0 === null) t0 = now - 1000 / 60;  // the first frame shows one frame's motion
    const t = now - t0, e = Math.exp(-w * t);
    let x = to - (A + B * t) * e;
    const v = (w * (A + B * t) - B) * e;
    if ((Math.abs(to - x) < 0.5 && Math.abs(v) < 0.02) || t > 1000) {
      _settling = null;
      _moveEnd(to);
      _setMorph(m1);
      _settled();
      return;
    }
    st.height = x = Math.max(h.hidden, Math.min(max, x));  // a slow release the other way dips past its start
    _dragShow(x, max);
    _setMorph(_morphAt(h, x));
    st.raf = requestAnimationFrame(step);
  };
  _dragShow(from, max);
  st.raf = requestAnimationFrame(step);
}
// Stops a settle where it is, the sheet laid out at that height.
function _stopSettle() {
  if (!_settling) return;
  cancelAnimationFrame(_settling.raf);
  const at = Math.round(_settling.height);
  _settling = null;
  _moveEnd(at);
}
// Settled: the calendar measures itself at rest (js/calendar.js), the tops follow.
function _settled() {
  if (typeof measureCalChrome === 'function') measureCalChrome();
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
  for (const b of tabbar.querySelectorAll('[role="tab"]')) {
    const on = b.dataset.tab === name;
    b.setAttribute('aria-selected', String(on));
    b.tabIndex = on ? 0 : -1;
  }
  tabbar.style.setProperty('--i', TABS.indexOf(name));  // the selected pill slides to it (styles.css)
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
// Not while it moves: the sheet is then laid out at its tallest (_moveBegin).
new ResizeObserver(() => requestAnimationFrame(() => {
  if (_drag?.moved || _settling) return;
  document.documentElement.style.setProperty('--sheet-h', (isDesktop() ? 0 : sheet.offsetHeight + sheetGap()) + 'px');
})).observe(sheet);
// The detents follow the screen.
window.addEventListener('resize', () => applySheet(false));
showTab(_tab);
