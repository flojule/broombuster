# BroomBuster — Feature Plan (remaining work)

The re-architecture plan that introduced the `DomainPlugin` abstraction
(Step 3) was completed: `/check` now returns a `domains[]` array, the
sweeping logic lives in
[`src/broombuster/domains/sweeping.py`](../src/broombuster/domains/sweeping.py),
and the registry is in
[`src/broombuster/domains/registry.py`](../src/broombuster/domains/registry.py).
Steps 1, 2, 2.5, 2.7 are also shipped (canonical address, slim repo,
`pyproject.toml`, single `src/broombuster/` package).

What remains: another domain to prove the abstraction (Step 4), a
maintainable frontend (Step 5), and manifest-driven city onboarding
(Step 6, optional).

---

## Step 4 — Add a second domain: trash days (Oakland first)

The `DomainPlugin` shape needs a second concrete implementation to
validate it isn't accidentally sweeping-shaped. Trash collection is the
right next domain because it has the same urgency UX (today / tomorrow /
safe), excellent open-data availability, and a polygon-zone shape that
exercises the `is_polygon` path in
[`src/broombuster/resolve.py`](../src/broombuster/resolve.py).

### Approach

New plugin
[`src/broombuster/domains/trash.py`](../src/broombuster/domains/trash.py).
Reuses `resolve.resolve_car_segment` (polygon path), and reuses
`analysis.parse_sweeping_code` if the day codes are compatible (Oakland's
trash schedule is weekly by zone — likely needs only a thin
`parse_trash_code` wrapper around the existing parser).

### Frontend changes

[`renderCarsPanel`](../frontend/index.html) currently hard-codes the
sweeping label and renders one card per car. After Step 4 it loops over
`data.domains[]` and renders one card per `(car, domain)` pair. Header
text comes from `domain.label`, urgency from `domain.urgency`, schedule
text from `domain.schedule_lines`. The total panel urgency for a car
becomes `max(d.urgency for d in domains)` with priority
`today > tomorrow > safe`.

### Files

- New: [`src/broombuster/domains/trash.py`](../src/broombuster/domains/trash.py)
- New: `data/oakland/TrashZones.fgb` (built via
  `scripts/build_oakland_trash.py`, registered in `data/sources.yaml`)
- Modified: [`src/broombuster/domains/registry.py`](../src/broombuster/domains/registry.py)
  (register `TrashPlugin` for `oakland`)
- Modified: [`src/broombuster/api/app.py`](../src/broombuster/api/app.py)
  (no API change — registry handles the new plugin automatically)
- Modified: [`frontend/index.html`](../frontend/index.html)
  (`renderCarsPanel` loop over `data.domains[]`)
- New tests: `tests/test_domain_trash.py` with synthetic Oakland trash
  zones; extended `tests/test_domain_registry.py` to cover two domains
  per car.

### Verification

- `/check` for an Oakland coordinate returns `domains[]` with both
  `sweeping` and `trash` entries.
- A car at a known Oakland address shows two cards in the panel, each
  with its own urgency colour.
- The MapLibre map shows the trash zone polygon (semi-transparent fill,
  different colour from sweeping) under the car's resolved zone.
- All existing tests pass unchanged — the sweeping behaviour cannot have
  regressed because the sweeping plugin is untouched.

### Next domains after trash

Already analysed in the original plan. In priority order:

| Domain | Availability | Value | Notes |
|---|---|---|---|
| **Parking permit zones (RPP)** | Good in dense cities (SFMTA, Oakland, Berkeley) | High in dense cities — answers "can I park here at all?" | **Step 4b — DO SECOND.** No urgency dimension; tests the "always-on label" plugin shape. |
| Alternate-side parking (NYC, Boston, DC) | Good in those cities | Very high there, ~0 in Bay Area | Skip until expanding east — sweeping already covers it in CA. |
| Snow-emergency routes | Good in Chicago, Boston, Minneapolis | Niche; only fires during emergencies | Skip until Chicago becomes core. |
| Leaf collection | Spotty (often PDFs only) | Seasonal | Skip. |
| Holiday parking suspensions | Rare structured data | Medium when available | Skip — RSS, not GIS. |

---

## Step 5 — Frontend split into ESM modules (no framework, no bundler)

[`frontend/index.html`](../frontend/index.html) is 1557 lines. Native
ESM is supported on every browser BroomBuster targets, the existing
service worker [`frontend/sw.js`](../frontend/sw.js) keeps working with
HTTP-cacheable `.js` files, and Caddy serves them correctly. **No build
step.**

### Why not Lit / Preact / Svelte

The user wants fast loading. Vanilla ESM has zero parse cost beyond the
project's own code. The frontend is mostly imperative DOM and splits
cleanly. If a framework is wanted later, ESM → Preact is a one-day port.

### Module split

```
frontend/
  index.html              ~250 lines (HTML + bootstrap only)
  styles.css              ~400 lines (extracted from the inline <style>)
  js/
    config.js             DEV_MODE, API base, region list
    api.js                apiFetch, /check client (with viewport cache + AbortController), /prefs
    auth.js               local JWT login/refresh flow
    state.js              cars[], carSchedules, activeCarId, savePrefs, tiny store
    location.js           IP & GPS, region selection
    geocode.js            forward geocode for address-edit input
    map.js                initMap, layers, snap chip, viewport cache + IndexedDB
    car-panel.js          renderCarsPanel, domainCardHTML, inline name edit
    car-placement.js      startPlacing, commitPlacement, name panel
    main.js               bootstrap
```

Each module has named exports. `state.js` exports a small `createStore`
(~20 lines) so `renderCarsPanel` re-runs when `cars` or `carSchedules`
change.

### Migration order

Modules with no DOM coupling first (`config`, `api`, `auth`, `state`),
then `location` and `geocode`, then `map` and the panel modules,
finally `main`. Each move is its own commit so any regression is
bisectable.

### Files

- New: [`frontend/styles.css`](../frontend/styles.css), `frontend/js/*.js`
- Modified: [`frontend/index.html`](../frontend/index.html) (drop to a
  bootstrap shell, `<script type="module" src="js/main.js">`)
- Modified: [`frontend/sw.js`](../frontend/sw.js) (cache the new JS files
  in `SHELL`)
- New: `tests/test_frontend_modules.py` — static checks that each
  module file exists and exports the expected names; no cross-module
  global references.

### Verification

- The page loads correctly in Safari (iPhone), Chrome, Firefox.
- DevTools → Network: each `js/*.js` file is fetched independently and
  cached by the SW after the first visit.
- Every existing flow works: auth, region select, place car, GPS,
  schedule check, dark/light toggle, map pan/zoom, hover tooltip,
  multi-car panel.
- Lighthouse score is at least as good as the monolithic version
  (loading is faster on cold load because parse + execute is parallel
  per module; warm cache is faster because module hashes are stable).

---

## Step 6 (optional) — Manifest-driven city onboarding

Adding a city today requires: a `CITIES` entry in
[`src/broombuster/cities.py`](../src/broombuster/cities.py), usually a
`_normalise_<schema>()` function in
[`src/broombuster/data_loader.py`](../src/broombuster/data_loader.py),
and sometimes a PDF-parser script in `scripts/`.

### Target

A YAML manifest per city, with custom Python only for genuinely novel
schemas.

```yaml
# data/manifests/oakland.yaml
city_key: oakland
display_name: Oakland, CA
center: { lat: 37.8044, lon: -122.2712 }
bbox: [37.69, -122.38, 37.90, -122.11]
timezone: America/Los_Angeles
geometry_mode: line
source:
  kind: local
  path: data/oakland/StreetSweeping.fgb
domains:
  - id: sweeping
    schema: oakland          # one of the SCHEMA_PROFILES below
    columns:
      street_name: { from: ["NAME","TYPE"], transform: upper_strip_join }
      day_even:    { from: "DAY_EVEN" }
      day_odd:     { from: "DAY_ODD" }
      ...
```

`data_loader.py` becomes a generic dispatcher:

```python
SCHEMA_PROFILES = {
    "oakland":       from_oakland_columns,        # current _normalise_oakland
    "sf_socrata":    from_sf_socrata_columns,     # current _normalise_sf
    "chicago_zones": from_chicago_zone_columns,   # current _normalise_chicago
    "berkeley_pdf":  from_berkeley_pdf_columns,
    "alameda_pdf":   from_alameda_pdf_columns,
    "prebuilt":      identity_normaliser,
}
```

### Adding a new city becomes

1. Drop a YAML manifest in `data/manifests/`.
2. New schema profile only if the source format is genuinely novel.
3. Run `scripts/rebuild_city_data.py <city>` to produce the FGB.
4. Done — no Python changes required.

### Files

- New: [`src/broombuster/manifest.py`](../src/broombuster/manifest.py)
  (loads YAML, validates, hands off to the dispatcher)
- New: `data/manifests/<city>.yaml` for each existing city
- Modified: [`src/broombuster/cities.py`](../src/broombuster/cities.py)
  (becomes a thin loader that reads manifests at startup)
- Modified: [`src/broombuster/data_loader.py`](../src/broombuster/data_loader.py)
  (existing `_normalise_*` functions become entries in `SCHEMA_PROFILES`)
- Modified: `scripts/rebuild_city_data.py` (uses the manifest to find
  the source URL and pick the right schema profile)

### Verification

- Every existing city loads identically through the manifest path.
  `tests/test_manifest_byte_identical.py` (new) asserts
  `load_city_data("oakland")` produces an FGB that is byte-identical
  to the pre-migration version.
- Adding a synthetic small city (e.g. Piedmont) requires zero new
  Python — just a manifest pointing at a local FGB.

### When to skip

If Step 4 has already shown the scaling concern is satisfied — the
existing dispatch is good enough for ~10 cities; this matters more at
30+. Mark this step as deferred unless the project actually starts
adding cities at that pace.

---

## Order of execution

1. **Step 4 (Trash for Oakland)** — validates the `DomainPlugin`
   contract. Forcing-function for any abstraction issues.
2. **Step 4b (Permit zones for SF / Oakland / Berkeley)** — proves the
   "always-on label, no urgency" plugin shape works.
3. **Step 5 (Frontend ESM split)** — prerequisite for further frontend
   features (per-domain layer toggles, multi-domain hover composition,
   etc.). Best done after Step 4 so the new module structure is shaped
   by real multi-domain requirements rather than theoretical ones.
4. **Step 6 (Manifest-driven cities)** — only if/when the project
   starts adding cities at a rate where the current dispatch becomes
   tedious.

Performance work (server bbox cache, PMTiles, IndexedDB tuning) is
tracked separately in [`performance_plan.md`](performance_plan.md).
