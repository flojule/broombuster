#!/usr/bin/env python3
"""Screenshot the app in every scenario and check its layout, so nobody re-writes this.

Scenarios = platform x theme x view. Each one is screenshotted and checked:

  * the app fills the visible height (or the whole screen in a home-screen app);
  * the centre target is not covered by the sheet, street signs or map buttons (the
    large sheet may cover the map: that is what it is for);
  * those three never overlap one another (the large sheet aside);
  * the street layer is drawn and every street has a colour (no blue flash);
  * no serious or critical WCAG 2.2 AA violation (axe-core);
  * no uncaught page errors.

Run (needs Playwright with WebKit and Chromium; it starts the server itself):

    uv tool run --from playwright python scripts/visual_scenarios.py
    uv tool run --from playwright python scripts/visual_scenarios.py --only iphone-safari --view month
    uv tool run --from playwright python scripts/visual_scenarios.py --url http://localhost:8000  # existing server
    uv tool run --from playwright python scripts/visual_scenarios.py --no-axe  # skip the accessibility check

Output goes to build/visual/ (PNGs, index.html contact sheet, report.txt). Exit code 1 on any FAIL.
Safari's own toolbars and the iOS status bar cannot be rendered by headless WebKit: Safari scenarios use
the visible height, and the home-screen app simulates the notch and home-indicator insets.

Add a platform, view or check below; nothing else needs touching.
"""

import argparse
import html
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parent.parent

# Two cars and a home, near Market St, San Francisco (data is committed in the repo).
PREFS = {"cars": [{"id": "c1", "name": "Civic", "lat": 37.7749, "lon": -122.4194},
                  {"id": "c2", "name": "Golf", "lat": 37.7793, "lon": -122.4148}],
         "homes": [{"id": "h1", "lat": 37.7765, "lon": -122.4172, "address": "1 Market St"}]}

# Simulated iOS insets for a home-screen app (headless WebKit has none): 62 pt status bar, 34 pt home bar.
APP_INSETS = ":root { --inset-t: 62px; --inset-b: 34px; }"
STANDALONE = "Object.defineProperty(navigator, 'standalone', { get: () => true });"

# axe-core (accessibility rules), downloaded once into the output directory. Only serious and
# critical WCAG 2.2 A/AA violations fail a scenario. The markup is the same everywhere, so it runs on
# one desktop and one phone platform (it takes ~20 s a page). A map pin that the pan has slid under
# the sheet or off the screen edge is not a target-size fault (as in Apple Maps): those are skipped.
AXE_PLATFORMS = ("desktop-webkit", "iphone-safari")
AXE_URL = "https://cdn.jsdelivr.net/npm/axe-core@4.10.2/axe.min.js"
AXE_RUN = """() => axe.run(document, {
  runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] },
  resultTypes: ['violations'],
}).then(r => r.violations.filter(v => v.impact === 'serious' || v.impact === 'critical')
                         .map(v => ({ ...v, nodes: v.nodes.filter(n => !(v.id === 'target-size' && n.html.startsWith('<button class="pin'))) }))
                         .filter(v => v.nodes.length)
                         .map(v => `${v.id}: ${v.nodes.slice(0, 3).map(n => n.target.join(' ')).join(', ')}`))"""

# name -> (browser, device profile or None, viewport override, standalone, expected app height)
# expected height: "viewport" = the visible height, or a number of CSS px (the whole screen).
PLATFORMS = {
    "desktop-webkit":   ("webkit",   None, {"width": 1280, "height": 800}, False, "viewport"),
    "desktop-chromium": ("chromium", None, {"width": 1280, "height": 800}, False, "viewport"),
    "desktop-small":    ("webkit",   None, {"width": 1024, "height": 640}, False, "viewport"),
    "iphone-safari":    ("webkit", "iPhone 17 Pro", {"width": 402, "height": 681}, False, "viewport"),
    "iphone-app":       ("webkit", "iPhone 17 Pro", {"width": 402, "height": 874}, True, 874),
    "iphone-landscape": ("webkit", "iPhone 17 Pro landscape", None, False, "viewport"),
    "ipad":             ("webkit", "iPad Pro 11", None, False, "viewport"),
    "android-chrome":   ("chromium", "Pixel 7", None, False, "viewport"),
}
THEMES = ("light", "dark")

# View = how the page is driven before the screenshot (UI clicks and the page's own functions).
VIEWS = {
    # Calendar tab, small sheet: one week.
    "strip":  lambda pg: None,
    # Mid-drag between small and medium: the weeks below unfold.
    "unfold": lambda pg: pg.evaluate("sheet.classList.add('dragging'); sheet.style.height = (detentHeights().small + 120) + 'px'"),
    # Tapping a day: the month view, sheet at medium.
    "month":  lambda pg: pg.click("#ic-cal .cal-sel"),
    "saved":  lambda pg: (pg.click("#tb-saved"), pg.evaluate("setDetent('medium')")),
    # A car's page, opened from the Saved list.
    "item":   lambda pg: (pg.click("#tb-saved"), pg.evaluate("setDetent('medium')"),
                          pg.click('.car-entry[data-id="c1"] .ce-header')),
    # Its "…" menu, and removing it (the confirmation).
    "more":   lambda pg: (pg.evaluate("openItem('c1')"), pg.wait_for_timeout(600), pg.click("#btn-item-more")),
    "remove": lambda pg: (pg.evaluate("openItem('c1')"), pg.wait_for_timeout(600), pg.click("#btn-item-more"),
                          pg.click('#item-menu [data-act="remove"]')),
    # A home's page, opened from its pin.
    "home":   lambda pg: pg.evaluate("openItem('h1')"),
    "add":    lambda pg: (pg.click("#tb-saved"), pg.click("#btn-add")),
    "me":     lambda pg: (pg.click("#tb-me"), pg.evaluate("setDetent('medium')")),
    "large":  lambda pg: pg.evaluate("setDetent('large')"),
    "place":  lambda pg: pg.evaluate("startPlacing()"),
    # You-are-here dot with a 40 m accuracy halo, near the first car.
    "here":   lambda pg: pg.evaluate("_setHere(37.7752, -122.4189, 40)"),
    # The "Add here" menu (right-click / long press), up and left of the centre (clear of the car there).
    "menu":   lambda pg: pg.evaluate("(() => { const r = mapDiv.getBoundingClientRect(), x = r.width / 2 - 150, y = r.height / 2 - 110;"
                                     " openCtxMenu(map.unproject([x, y]), r.left + x, r.top + y); })()"),
    # A car whose schedule failed to load: its page offers Retry.
    "failed": lambda pg: pg.evaluate("delete carSchedules.c1; loadFailed.add('c1'); openItem('c1'); renderCarsPanel()"),
}


MEASURE = """() => {
  const rect = el => {
    if (!el) return null;
    const cs = getComputedStyle(el);
    if (el.hidden || cs.display === 'none' || cs.visibility === 'hidden') return null;
    const r = el.getBoundingClientRect();
    return r.width && r.height ? [r.left, r.top, r.right, r.bottom] : null;
  };
  const feats = map && map.getLayer('zones-line') ? map.queryRenderedFeatures({ layers: ['zones-line'] }) : [];
  let unset = 0;
  for (const f of feats) {
    if (!map.getFeatureState({ source: TILES_SOURCE, sourceLayer: TILES_SRC_LAYER, id: f.id }).urgency) unset++;
  }
  const ch = rect(document.getElementById('center-crosshair'));
  const c = map.project(map.getCenter()), mc = document.getElementById('map').getBoundingClientRect();
  return { app: rect(document.getElementById('app-screen')), inner: [innerWidth, innerHeight],
           center: ch && [(ch[0] + ch[2]) / 2, (ch[1] + ch[3]) / 2], mapCenter: [mc.left + c.x, mc.top + c.y],
           large: !isDesktop() && _detent === 'large',
           boxes: { sheet: rect(document.getElementById('sheet')), signs: rect(document.getElementById('signs')),
                    buttons: rect(document.getElementById('map-ctrl')) },
           crosshair: ch, streets: feats.length, unset };
}"""
READY = "map && map.getLayer('zones-line') && map.queryRenderedFeatures({layers:['zones-line']}).length > 0"
SETTLED = "map.loaded() && map.areTilesLoaded() && !_urgencyTimer"


def check(m, expect_h):
    """Problems found in one scenario's measurements (empty list: pass)."""
    bad = []
    top, bottom = m["app"][1], m["app"][3]
    want = m["inner"][1] if expect_h == "viewport" else expect_h
    if abs((bottom - top) - want) > 1 or top != 0:
        bad.append(f"app is {bottom - top:.0f} px tall at y={top:.0f}, expected {want}")
    if not m["streets"]:
        bad.append("no streets drawn")
    elif m["unset"]:
        bad.append(f"{m['unset']} of {m['streets']} streets have no colour")
    boxes = {k: v for k, v in m["boxes"].items() if v and not (k == "sheet" and m["large"])}
    if m["crosshair"] is None:
        return [*bad, "centre target not visible"]
    cx, cy = m["center"]
    if max(abs(cx - m["mapCenter"][0]), abs(cy - m["mapCenter"][1])) > 2:
        bad.append(f"centre target at {cx:.0f},{cy:.0f} but the map centre is at {m['mapCenter'][0]:.0f},{m['mapCenter'][1]:.0f}")
    for k, (x0, y0, x1, y1) in boxes.items():
        if x0 - 12 <= cx <= x1 + 12 and y0 - 12 <= cy <= y1 + 12:
            bad.append(f"{k} covers the centre target")
    names = list(boxes)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            (al, at, ar, ab), (bl, bt, br, bb) = boxes[a], boxes[b]
            if min(ar, br) - max(al, bl) > 1 and min(ab, bb) - max(at, bt) > 1:
                bad.append(f"{a} overlaps {b}")
    for k, (x0, _y0, x1, y1) in boxes.items():
        if x0 < -1 or x1 > m["inner"][0] + 1 or y1 > m["app"][3] + 1:
            bad.append(f"{k} runs off the screen")
    return bad


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server():
    port = free_port()
    proc = subprocess.Popen(
        ["uv", "run", "python", "-m", "uvicorn", "broombuster.api.app:app", "--port", str(port)],
        cwd=REPO, env={**os.environ, "BB_RATE_LIMIT": "0"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(120):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
            return proc, f"http://127.0.0.1:{port}"
        except OSError:
            time.sleep(0.5)
    proc.kill()
    sys.exit("server did not start")


def axe_source(out):
    path = out / "axe.min.js"
    if not path.exists():
        urllib.request.urlretrieve(AXE_URL, path)
    return path.read_text()


def run_one(pw, browsers, base, out, platform, theme, view, axe):
    engine, device, viewport, standalone, expect_h = PLATFORMS[platform]
    kw = dict(pw.devices[device]) if device else {"device_scale_factor": 1}
    if viewport:
        kw["viewport"] = viewport
        if standalone:
            kw["screen"] = viewport
    if device:
        kw["device_scale_factor"] = 2
    if engine not in browsers:
        browsers[engine] = getattr(pw, engine).launch()
    ctx = browsers[engine].new_context(**kw)
    seed = (f"if (!sessionStorage.s) {{ sessionStorage.s = 1; localStorage.setItem('bb_prefs', {json.dumps(json.dumps(PREFS))});"
            f" localStorage.setItem('bb_tab', 'calendar'); localStorage.setItem('bb_detent', 'small');"
            f" localStorage.setItem('bb_theme', '{theme}'); }}")
    ctx.add_init_script(seed + (STANDALONE if standalone else ""))
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(base)
    if standalone:
        pg.evaluate("css => { const s = document.createElement('style'); s.textContent = css; document.head.append(s); }", APP_INSETS)
    pg.wait_for_function(READY, timeout=60000)
    VIEWS[view](pg)
    pg.wait_for_function(SETTLED, timeout=60000)
    pg.wait_for_timeout(1200)
    for _ in range(3):  # headless WebKit can leave a stale frame until the next repaint
        pg.evaluate("map.triggerRepaint()")
        pg.wait_for_timeout(300)
    name = f"{platform}-{theme}-{view}"
    pg.screenshot(path=str(out / f"{name}.png"))
    m = pg.evaluate(MEASURE)
    bad = check(m, expect_h)
    if axe and platform in AXE_PLATFORMS:
        pg.evaluate(axe)  # devtools evaluation: not subject to the page's CSP
        bad += [f"a11y {v}" for v in pg.evaluate(AXE_RUN)]
    ctx.close()
    return name, bad + [f"page error: {e}" for e in errors]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", choices=PLATFORMS, help="platforms (default: all)")
    ap.add_argument("--theme", nargs="*", choices=THEMES, help="themes (default: both)")
    ap.add_argument("--view", nargs="*", choices=VIEWS, help="views (default: all)")
    ap.add_argument("--url", help="use this running server instead of starting one")
    ap.add_argument("--out", default=str(REPO / "build" / "visual"), help="output directory")
    ap.add_argument("--no-axe", action="store_true", help="skip the axe-core accessibility check")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.png"):
        old.unlink()
    axe = None if a.no_axe else axe_source(out)
    proc, base = (None, a.url.rstrip("/")) if a.url else start_server()
    results = []
    try:
        with sync_playwright() as pw:
            browsers = {}
            for platform in a.only or PLATFORMS:
                for theme in a.theme or THEMES:
                    for view in a.view or VIEWS:
                        try:
                            name, bad = run_one(pw, browsers, base, out, platform, theme, view, axe)
                        except Exception as e:  # a scenario that cannot run is a failure, not a crash
                            name, bad = f"{platform}-{theme}-{view}", [f"could not run: {str(e).splitlines()[0]}"]
                        results.append((name, bad))
                        print(("FAIL " if bad else "ok   ") + name + ("  " + "; ".join(bad) if bad else ""), flush=True)
            for b in browsers.values():
                b.close()
    finally:
        if proc:
            proc.terminate()
    lines = [f"{'FAIL' if bad else 'ok  '} {n}  {'; '.join(bad)}" for n, bad in results]
    (out / "report.txt").write_text("\n".join(lines) + "\n")
    cells = "".join(
        f'<figure class="{"bad" if bad else "ok"}"><img src="{n}.png" loading="lazy"><figcaption>{html.escape(n)}'
        f'{"<br>" + html.escape("; ".join(bad)) if bad else ""}</figcaption></figure>' for n, bad in results)
    (out / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>BroomBuster scenarios</title><style>"
        "body{font:13px system-ui;margin:16px;background:#f3f4f1}main{display:flex;flex-wrap:wrap;gap:14px}"
        "figure{margin:0;width:260px}img{width:100%;border:3px solid #15803d;border-radius:6px}"
        ".bad img{border-color:#b91c1c}.bad figcaption{color:#b91c1c}</style><main>" + cells + "</main>")
    failed = sum(1 for _, bad in results if bad)
    print(f"\n{len(results) - failed}/{len(results)} scenarios pass; screenshots in {out}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
