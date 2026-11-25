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
let _compact = false;  // placing a car or home: the sheet is lowered to the tab bar

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
const _shown = () => _compact ? 'hidden' : _detent;

// A height the user picks (a drag, a tab) also lifts the lowered placing sheet.
function setDetent(d) {
  if (!DETENTS.includes(d)) return;
  _detent = d;
  _compact = false;
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
// Past its tallest or below the tab bar it gives with resistance (rubber band).
// While it moves (dragged or settling) the sheet is laid out once, open (styles.css
// #sheet.dragging) and at its tallest (max): the shown height, and the closing in on
// the tab bar's capsule below small, are drawn by transforms and clip paths only, so no
// frame takes a layout. The height and --m's layout are set once it stops.
const FLICK_V = 0.4;     // px/ms: a release at least this fast is a flick
const VEL_WINDOW = 100;  // ms of finger samples the release speed is taken over
const RB_C = 0.55, RB_L = 120;  // rubber band: give per px pulled, and the most it gives (px)
const _rubber = d => RB_C * d / (1 + RB_C * d / RB_L);
let _drag = null, _dragged = false;
function _dragBegin(y, t) { _drag = { y0: y, samples: [{ y, t }], moved: false }; _dragged = false; }
// --m for a shown height: 0 at small and above, 1 at hidden.
const _morphAt = (h, height) => Math.min(1, Math.max(0, (h.small - height) / (h.small - h.hidden)));
// The open sheet's gap, the hidden one, the tab bar's inset and the screen's corner
// radius, in px (styles.css #sheet), read as a move starts.
let _geo = null;
function _readGeo() {
  const open = sheetGap(false), hid = sheetGap(true);
  return { open, hid, tbIn: parseFloat(getComputedStyle(sheet).getPropertyValue('--tb-in')), screenR: hid + 28 };
}
// The place banner and toasts ride on the sheet's top while it moves; --sheet-h places them at rest.
const placeBannerEl = document.getElementById('place-banner'), toastsEl = document.getElementById('toast-container');
let _sheetH = 0;  // px: --sheet-h as last set
function _setSheetH(px) {
  _sheetH = px;
  document.documentElement.style.setProperty('--sheet-h', px + 'px');
}
// Draws the sheet at `height` (below the tab bar: its capsule slid down) while laid out at
// `max`: what styles.css draws from --m (gaps, radii, the tab bar's size), as clip paths.
function _show(height, max, h) {
  const g = _geo, m = _morphAt(h, height);
  const r = (g.hid - g.open) * m;                                  // the sheet's gap, past the open one
  const gb = g.open + g.tbIn + (g.hid - g.open - g.tbIn) * m;      // the tab bar's gap to the screen
  const c = gb - g.open - g.tbIn;                                  // the tab bar, inside its open box
  const shown = Math.max(height, h.hidden), sink = h.hidden - Math.min(height, h.hidden), dy = max - shown;
  const rs = g.screenR - g.open - r, rt = Math.min(32, rs);
  sheet.style.transform = `translateY(${dy - r + sink}px)`;
  sheet.style.clipPath = `inset(0 ${r}px ${dy}px ${r}px round ${rt}px ${rt}px ${rs}px ${rs}px)`;
  tabbar.style.transform = `translateY(${r - dy}px)`;
  tabbar.style.clipPath = c > 0.01 ? `inset(${c}px round ${g.screenR - gb}px)` : '';
  _setMorph(m);
  const lift = _sheetH - (g.open + r + shown - sink);
  toastsEl.style.transform = `translateY(${lift}px)`;
  placeBannerEl.style.setProperty('--lift', lift + 'px');
}
// Lays the sheet out open at `max` (transitions off) for a drag or a settle.
function _moveBegin(max) {
  _geo = _readGeo();
  sheet.classList.add('dragging');
  sheet.style.height = max + 'px';
}
// Back to a laid-out sheet at `height`.
function _moveEnd(height) {
  sheet.style.height = height + 'px';
  sheet.style.transform = sheet.style.clipPath = tabbar.style.transform = tabbar.style.clipPath = toastsEl.style.transform = '';
  placeBannerEl.style.removeProperty('--lift');
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
    _drag.top = Math.max(_drag.h0, _drag.h.medium);
    _drag.max = Math.ceil(_drag.top + RB_L);
    _moveBegin(_drag.max);
  }
  const h = _drag.h, raw = _drag.h0 + _drag.y0 - y;
  const height = _drag.height = raw > _drag.top ? _drag.top + _rubber(raw - _drag.top)
    : raw < h.hidden ? h.hidden - _rubber(h.hidden - raw) : raw;
  _show(height, _drag.max, h);
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
  // The banner and toasts go straight to where the sheet is heading (the observer below
  // waits while it moves).
  _setSheetH(to + sheetGap(m1 === 1));
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
    st.height = x = Math.min(max, x);  // a slow release the other way dips past its start
    _show(x, max, h);
    st.raf = requestAnimationFrame(step);
  };
  _show(from, max, h);
  st.raf = requestAnimationFrame(step);
}
// Stops a settle where it is, the sheet laid out at that height.
function _stopSettle() {
  if (!_settling) return;
  cancelAnimationFrame(_settling.raf);
  const at = Math.max(TABBAR_H, Math.round(_settling.height));
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
// The month view's grid scrolls itself; its weekday row is a handle, as the header is.
// Content scrolled back to its top and pulled on down hands over to the sheet, where the
// browser still lets the touch be taken (a cancelable move).
function _contentMovesSheet(c, dy) {
  if (c.body.id === 'ic-cal' && _detent === 'medium' && !c.wds) return false;
  return dy < 0 ? _detent !== 'medium' : c.body.scrollTop <= 0;
}
let _content = null;  // { body, x0, y0, wds: began on the weekday row, sheet: null (undecided) | bool }
for (const body of document.querySelectorAll('.tab-body')) {
  body.addEventListener('touchstart', e => {
    _content = null;
    if (isDesktop() || e.touches.length !== 1 || _drag || e.target.closest('[contenteditable="true"], input')) return;
    _content = { body, x0: e.touches[0].clientX, y0: e.touches[0].clientY, wds: !!e.target.closest('.cal-wds'), sheet: null };
  }, { passive: true });
  body.addEventListener('touchmove', e => {
    const c = _content;
    if (!c || c.body !== body) return;
    const p = e.touches[0], dx = p.clientX - c.x0, dy = p.clientY - c.y0;
    if (c.sheet === false) {
      if (!c.scrolls || !e.cancelable || body.scrollTop > 0 || p.clientY <= c.yPrev) { c.yPrev = p.clientY; return; }
      c.sheet = true;  // scrolled back to the top and still pulling down
      _dragBegin(p.clientY, e.timeStamp);
    }
    if (c.sheet === null) {
      if (Math.hypot(dx, dy) < 6) return;
      const vertical = Math.abs(dy) > Math.abs(dx);
      c.sheet = vertical && _contentMovesSheet(c, dy);
      c.scrolls = vertical && !c.sheet && body.id !== 'ic-cal';
      c.yPrev = p.clientY;
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
// A new tab's content fades in, unless the sheet is opening from its tab bar (it fades then).
function showTab(name) {
  if (name !== 'saved' && typeof clearSelection === 'function') clearSelection();
  if (name !== _tab && !sheet.classList.contains('retracted')) restartAnim(document.getElementById('tab-' + name), 'tab-in');
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
// A tab opens a hidden sheet (small). Tapping the open tab goes to its start, as iOS
// tab bars do: Saved back to its list, the calendar to today, and to the top.
function _tabHome(name) {
  if (name === 'saved' && typeof _selectedId !== 'undefined' && _selectedId) { clearSelection(); return; }
  if (name === 'calendar' && typeof calToday === 'function') { calToday(); return; }
  document.querySelector(`#tab-${name} .tab-body`)?.scrollTo({ top: 0, behavior: _reduceMotionSheet.matches ? 'auto' : 'smooth' });
}
tabbar.addEventListener('click', e => {
  const b = e.target.closest('[data-tab]');
  if (!b || _dragged) { _dragged = false; return; }
  const again = b.dataset.tab === _tab && _shown() !== 'hidden';
  if (_detent === 'hidden') setDetent('small');
  else if (_compact) setDetent(_detent);  // placing: the sheet comes back up as it was
  if (again) _tabHome(_tab);
  else showTab(b.dataset.tab);
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
// Not while it moves: the sheet is then laid out at its tallest (_moveBegin); a settle
// sets its end value at the start.
new ResizeObserver(() => requestAnimationFrame(() => {
  if (_drag?.moved || _settling) return;
  _setSheetH(isDesktop() ? 0 : sheet.offsetHeight + sheetGap());
})).observe(sheet);
// The detents follow the screen.
window.addEventListener('resize', () => applySheet(false));
showTab(_tab);
