# BroomBuster — Feature Plan (remaining work)

Shipped: the `DomainPlugin` abstraction (`/check` returns `domains[]`), a second
domain (trash days via ReCollect, home-subject), manifest-driven cities
(`data/manifests/*.yaml`), and the frontend split into `styles.css` +
`frontend/js/*.js`.

## Next domains

| Domain | Availability | Value | Notes |
|---|---|---|---|
| **Parking permit zones (RPP)** | Good in dense cities (SFMTA, Oakland, Berkeley) | High — answers "can I park here at all?" | **Next.** No urgency dimension; tests an "always-on label" plugin shape. |
| Alternate-side parking (NYC, Boston, DC) | Good in those cities | Very high there, ~0 in the Bay Area | When expanding east. |
| Snow-emergency routes | Chicago, Boston, Minneapolis | Niche; only during emergencies | When Chicago becomes core. |
| Leaf collection | Spotty (often PDFs) | Seasonal | Skip. |
| Holiday parking suspensions | Rare structured data | Medium | Skip — RSS, not GIS. |

A new domain is one module in `src/broombuster/domains/` plus one line in
`registry._REGISTRY`; a city opts in through its manifest.

## Frontend: classic scripts → ES modules

`frontend/js/*.js` are loaded as ordered classic `<script defer>` files that
share globals (state in `core.js`, functions called across files). Converting
them to native ES modules (no bundler) would make dependencies explicit:

- Each file exports what others use; `app.js` becomes the single
  `<script type="module">` entry; `urgency.js` keeps its `BroomUrgency` global
  for the Node parity harness (or the harness imports the module).
- Shared mutable state (`cars`, `carSchedules`, `homes`, …) moves to one
  `state.js` with a tiny subscribe/notify so panels re-render on change.
- `sw.js` `SHELL` lists the module files; bump `CACHE`.
- Convert leaf modules first (`urgency`, `core`, `auth`), then `map`/`markers`,
  then the panels; one commit per move so a regression is bisectable.

Verify in Safari (iPhone), Chrome and Firefox: auth, region select, place car,
GPS, schedule check, dark/light toggle, hover, card calendar, homes.
