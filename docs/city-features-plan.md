# City features: sources and plan

Research and plan only; nothing here is implemented. Sources were checked on **2026-10-17**
(endpoints called where possible; "verified" means a live call returned data that day).
The cities covered are the ones in `data/`: Berkeley, Oakland, Alameda, San Francisco, Chicago.

## 1. Summary and ranking

| # | Feature | Cities with good data | Value | Effort | Phase |
|---|---|---|---|---|---|
| 1 | **Holiday-aware sweeping** (no red "today" on a city holiday) | all five | High | S | 1 |
| 2 | **Trash days in the Calendar**, with holiday shifts, for every city | Oakland (live); Berkeley, Alameda, Chicago (rule + day the user picks); SF (day the user picks) | High | S–M | 1 |
| 3 | **Residential permit parking (RPP)**: map layer, plus "this car is in RPP area X" on the car's card | Berkeley (live 2026), SF, Chicago, Oakland (2019) | High | M | 2 |
| 4 | **Calendar subscription (.ics)** for each signed-in user: sweeping, trash and holidays, with alarms. It stands in for notifications on iPhone | all | High | M | 2 |
| 5 | **Street closures and event no-parking** on a car's block | SF (daily, excellent), Chicago, Oakland (irregular), 511 work zones | High in SF, Medium elsewhere | M | 3 |
| 6 | **Chicago: towed or relocated car lookup**, winter overnight ban, 2-inch snow ban | Chicago | High (Chicago) | S–M | 3 |
| 7 | **Meters and paid parking** (layer, then hours/rates on the car's card) | SF (per-space schedules), Oakland (locations; rates by hand), Berkeley (meters and lots) | Medium | M–L | 4 |
| 8 | **EV charging** layer | all, via NREL AFDC | Medium | S | 4 |
| 9 | **Council meetings** | Oakland, Alameda (Legistar API); Berkeley, SF, Chicago by scraping | Low–Medium | S | 5 |
| 10 | **"Around" tab**: farmers markets, library and park events, street fairs | Oakland and Chicago libraries, SF Our415, Chicago Park District; markets entered by hand | Medium | M–L | 5 |
| 11 | **Hazards and alerts**: evacuation zones, fire zones, Red Flag / Spare the Air, NWS alerts | Berkeley, Oakland (ArcGIS); regional APIs | Medium | M | 6 |
| 12 | 311, crime, trees, paving, schools | many | Low for this app | – | not planned |

What drives the order: BroomBuster answers "can my car stay here, and when must it move?". Features 1–6
answer that question better. The first two need no new data layer, and #1 fixes a real bug: today
`urgency.js` has no notion of holidays, so on Oakland's 14 no-sweep holidays in 2026 the app tells
people to move their car when they don't need to. Features 7–8 help with parking choices.
Features 9–11 make the app a city companion, which is wider than its purpose, so they come last.

Things found along the way, outside the scope of this plan:

- **SF's portal moved** from `data.sfgov.org` to `data.sf.gov`. The old host answers with a 301, which
  `requests` follows, but `data/manifests/san_francisco.yaml` should be updated.
- **Berkeley publishes its sweeping routes on ArcGIS** (`Street_Sweeping_Map_WFL1`, layer 2: 47 routes
  with `DAY`, `TIME_OCCUR`, `DAYofMONTH`, `STREET_SIDE`, edited 2023-10; also
  `gis.cityofberkeley.info/.../Public/Portal_CommSvcs/MapServer/6,7`). These could replace the
  2022 PDFs and the OSM-placed build.
- **Chicago publishes "Delayed Street Sweeping Zones"** (`fuz6-n5nj`), which lists sweeping postponements.
- **Oakland's sweeping layer** is also on ArcGIS (`StreetSweeping`, 23,862 segments, edited 2021).
  `data.oaklandca.gov` no longer lists a current sweeping dataset in its Socrata catalog.

## 2. How the app models data today (constraints for the plan)

- **Static, committed data.** Each city has a manifest (`data/manifests/<city>.yaml`), a raw input and a
  normaliser (`schemas.SCHEMA_PROFILES`), and builds into one FlatGeobuf. `scripts/rebuild_city_data.py`
  fetches and normalises. `scripts/build_pmtiles.py` merges each region into a single
  `frontend/tiles/<region>.pmtiles` with one source-layer, `zones`, whose features carry
  `render_type`, `street`, `city` and raw `sched` codes. The client colours them against the clock,
  so tiles depend on data, not on the date.
- **Server-side point lookups.** `/api/check` resolves a car to a street segment in the region
  GeoDataFrame (`api/state.py`, `resolve.py`) and returns that street's schedules.
- **Live third-party data via a cached proxy.** `trash.py` calls ReCollect behind a TTL cache that
  keeps failures briefly and caps memory, and degrades to "no data". `/api/check-home` serves the
  result for each home. Only Oakland has a `trash:` block (`{area: OaklandCA, service_id: 608}`). The
  ReCollect address lookup was verified working today.
- **Frontend.** Classic scripts sharing globals, with no build step and a strict CSP:
  `connect-src 'self' https://tiles.openfreemap.org`. Every third-party fetch must therefore go
  through `/api/`. The sheet has Calendar, Saved and Me tabs. The Calendar shows the street under the
  centre dot and each car's sweeping days. **Home trash days appear only on the Saved card, not in the
  Calendar.**
- **No notifications.** The only push-like path is the iOS Shortcut, which posts a car's position to
  `/api/cars/location` and shows the returned `summary`.
- **Deployment.** A Raspberry Pi auto-deploys `main`. `/health` is the only health check. Settings
  (and any API keys) come from `config.py`.

## 3. Source inventory

Legend: **Fresh** = when the data last changed (as of 2026-10-17). **Rel** = reliability (H/M/L).
**Effort** = ingest effort (S: one fetch plus a normaliser; M: a new pipeline piece or cache module;
L: scraping, hand curation, or fragile sources). **Fit**: *static* = goes into an FGB/PMTiles
build; *live* = a cached server proxy like `trash.py`; *hand* = a YAML table kept in the repo.

### 3.1 Berkeley

Portals: Socrata `data.cityofberkeley.info` (63 assets; the city says data can be "download[ed],
reuse[d], and redistribute[d] … without restrictions"). ArcGIS Online org `services1.arcgis.com/IYiCpZoSIq9lAxi8`
(472 services). ArcGIS Server `gis.cityofberkeley.info/arcgis/rest/services`. The ArcGIS items carry no
licence text of their own; assume the city's open-data terms apply, and confirm with the city.

| Topic | Source / URL | Owner | Format | Coverage | Fresh | Rel | Effort | Fit |
|---|---|---|---|---|---|---|---|---|
| Trash days | None published. [Residential Waste Services](https://berkeleyca.gov/city-services/trash-recycling/residential-waste-services) says "call (510) 981-7270". | City Zero Waste (trash, compost); Ecology Center (recycling) | Phone | – | – | – | – | **Holiday rule** (hand): no pickup on Thanksgiving, Christmas or New Year's Day; the rest of that week shifts one day later. No other holidays apply. |
| Trash days, inferred | 311 cases `p88g-6gs2`, "Missed Trash Pickup – Residential" (277 cases), with address and date | City | Socrata | addresses that filed reports | daily | L | M | Not recommended: sparse and noisy. At most, a hint ("neighbours report Tuesday"). |
| Trash days, official | Public Records Act request for the refuse route GIS | City | ? | citywide | – | – | S once received | Worth one request: the routes certainly exist internally. |
| RPP areas and address eligibility | `Public_Online_RPP_Map_WFL1`: layer 10 = 26 RPP area polygons (`Area`); layer 1 = 65,153 address points (`RPP_Area`, `Residential_Eligibility`, `Merchant_Eligibility`, `Conditions`) | City Transportation | ArcGIS FeatureServer (GeoJSON query) | citywide | **2026-08-19** | H | S | static. **Do not ingest layer 2**: it holds parcel owner names and mailing addresses. |
| RPP areas (older) | `RPP_Enforced_Areas` (32 polygons, `RPP_Permit`, `Occupancy`; 2023-04); `gis.cityofberkeley.info/.../Public/Portal_Transportation/MapServer/6,7` | City | ArcGIS | citywide | 2023 | M | S | Fallback |
| RPP rules (hours, 2 h limit, exemptions) | City RPP web pages and BMC ch. 14.72 | City | HTML | per area | as amended | H | S | hand table keyed by area letter |
| Meters | `gis.cityofberkeley.info/.../Public/Portal_CommSvcs/MapServer/3` (points: `METERTYPE`, `GOBERKELEY`, `LOCDESC`) | City | ArcGIS MapServer | citywide | unknown | M | S | static (locations only) |
| Meter rates and hours | [Parking meters page](https://berkeleyca.gov/city-services/parking/parking-meters): goBerkeley demand pricing, $0.50–$8/h cap, changed 2–3×/yr; "Paid Public Parking – Hourly Rates" ArcGIS Experience (2026-04, web map `09b52ae8a7d04cd9b62157ff7d999e93`) | City | HTML; ArcGIS web map (its layer still needs inspecting) | Downtown, Telegraph, Elmwood, Fourth St, Solano, etc. | 2026-04 | M | M | static, if the web map's layer holds rates by block; else hand |
| Public lots and garages | `Portal_Transportation/MapServer/3` (`Price`, `Rate_info`, `Time_limit`, `EV_char`, `Free_hours`, `Capacity`) | City | ArcGIS | city-owned lots | unknown | M | S | static |
| Fire-zone parking | `Map_of_No_Parking_or_Stopping_Signs_Near_Narrow_Roads_WFL1`; `Map_of_No_Parking_Signs__Without_Tow_Away__in_FZ_2_and_3_WFL1` | City Fire | ArcGIS | hills | unknown | M | S | static. Hills streets with no-parking/no-stopping signs. |
| Fire hazard and evacuation | `CAL_FIRE_and_Berkeley_FHSZ_2025` (9,752 polygons, 2026-01); `cob_evacuation_zones` (Genasys zones with `zone_status`, 2026-05) | City / CAL FIRE / Genasys | ArcGIS | citywide | 2026 | H | S | static zones; `zone_status` live |
| Street projects | `FF_Street_Sidewalk_Projects` (55 lines, `Proposed_FY`; 2026-08); `Paving_5yr_Plan` (2020, stale) | City Public Works | ArcGIS | planned work | 2026-08 | M | S | Low value: it gives fiscal years, not dates |
| 311 | `p88g-6gs2` "311 Cases" (`case_request`, address, lat/lon) | City | Socrata | citywide | daily | H | S | Not planned |
| Council meetings | [berkeleyca.gov/council-agendas](https://berkeleyca.gov/your-government/city-council/city-council-agendas); 2026 meeting-schedule PDF. Not on Legistar (verified: no `berkeley` client). | City Clerk | HTML / PDF | Council and commissions | per meeting | M | M (scrape) | live (scrape, daily) |
| City events | [berkeleyca.gov/community-recreation/events](https://berkeleyca.gov/community-recreation/events) (Drupal; no feed found) | City | HTML | city-run events | ongoing | M | M (scrape) | live |
| Library events | Berkeley Public Library on Communico (`berkeleypubliclibrary.libnet.info`), which has RSS/iCal exports | BPL | RSS / iCal | 5 branches | daily | M | S–M | live |
| Farmers markets | Ecology Center [Berkeley Farmers' Markets](https://ecologycenter.org/fm/): three weekly markets (Tue, Thu, Sat) | Ecology Center | HTML | 3 sites | changes rarely | H | S | hand |
| Community calendar (unofficial) | Berkeleyside listings: `https://www.berkeleyside.org/wp-json/wp/v2/newspack_lst_event` (verified; editorial listings) | Cityside (non-profit newsroom) | WordPress REST | Berkeley | ongoing | M | S | live. **Copyright: link out only, or ask Cityside for permission.** |
| Visitor events (unofficial) | [visitberkeley.com/events](https://www.visitberkeley.com/events/) | Visit Berkeley | HTML | arts, tours | ongoing | M | M | Skip, or link only |

### 3.2 Oakland

Portals: Socrata `data.oaklandca.gov` (400 assets). ArcGIS Online org `services.arcgis.com/9tC74aDHuml0x5Yz`
(1,371 services, many of them staff working copies). `gismaps.oaklandca.gov`. Licence: the portal's
terms of use apply (no explicit open licence was found on the ArcGIS items); confirm before
redistributing tiles.

| Topic | Source / URL | Owner | Format | Coverage | Fresh | Rel | Effort | Fit |
|---|---|---|---|---|---|---|---|---|
| Trash, recycling and compost days | ReCollect (`api.recollect.net`, area `OaklandCA`, service 608), the widget behind [oaklandrecycles.com](https://www.oaklandrecycles.com/). **Already integrated** (`trash.py`); verified today. | City; haulers WM (trash, compost) and CWS (recycling) | Unofficial JSON | every residential address | live, includes holiday shifts | M (undocumented) | done | live |
| Bulky pickup | Same ReCollect service (event flags) | City / WM | JSON | addresses | live | M | S | live: show the `bulky` stream if present |
| RPP areas | Socrata `su5x-2u99` / map `6wms-4i8c` "Residential Parking Permit Zones"; ArcGIS `RPP_Boundaries_1218` (17 polygons, `Zone`), `RPPCurbs0819` (842 curb lines, `RPP_ZONE`) | OakDOT | Socrata / ArcGIS | 17 areas | **2019-09** | M (stale; areas change slowly) | S | static. Prefer the curb lines: they say which side of the street. |
| RPP rules | [Apply/Renew RPP](https://www.oaklandca.gov/Public-Safety-Streets/Parking-Vehicles/Parking-Permits/Apply-Renew-a-Residential-Parking-Permit): permits per address by area (F: 1; M: no limit; N, Q: 2; others: 3); enforcement resumed in 2021 | OakDOT | HTML | per area | as amended | H | S | hand |
| Meters | ArcGIS `Oakland_Parking_Meters` (8,107 points, `AREA`, `ZONE_`, `METER_TYPE`; **2025-09**); Socrata `wnjv-tz7z` (2024-06); `MultispaceMeters` | OakDOT | ArcGIS / Socrata | citywide | 2025-09 | H | S | static |
| Meter rates and hours | [Find and Pay for Parking](https://www.oaklandca.gov/Public-Safety-Streets/Parking-Vehicles/Find-and-Pay-for-Parking): most $3/h, range $1–$4; Mon–Sat 8–6; Lake Merritt runs later and on Sunday afternoons; meters are free on Dec 25 and Jan 1 | OakDOT | HTML | by area | as changed | M | S | hand table keyed by meter `AREA`/`ZONE_` |
| Curb colours (red, yellow, white, green, blue) | ArcGIS `ParkingRestrictions_Citywide` (35,174 segments, surveyed 2005, edited 2020), `Red_Curb_Citywide`, `Blue_Curb_Citywide` (2022); Socrata `cnpw-bvzz` (blue zones) | OakDOT | ArcGIS | citywide | 2005–2022 | L–M | S | static; label as "surveyed 2005" |
| No-parking signs | ArcGIS `No_Parking_Signs` (27,954 points, `SIGNTYPE*`; 2021) | OakDOT | ArcGIS | citywide | 2021 | M | M (sign codes need decoding) | static, later |
| Downtown curb inventory | Socrata `87ce-u2wf`, `3wwm-2td4`; Jack London `q8h9-buxg` (2019–20) | OakDOT | Socrata | Downtown, Jack London | 2019–20 | M | M | Later |
| Street closures for events | ArcGIS `Latest_Street_Closures_Nov24` (32 lines: `Event_type`, `Organization`, `Start_date`, `End_date`, `Recurring_*`; edited 2025-10). Examples: "Town Nights", "Fright Night on Washington Street". | OakDOT special events | ArcGIS | permitted closures | **irregular** (last edit 2025-10) | L–M | S | live (daily poll). Also a source of street-fair events. |
| Construction and closures | `Construction_Management__Project` (25, 2025-01), `StreetClosures` (2023), `FlexStreetClosure` (2021), `PSL_ActiveConstruction` (2020) | City | ArcGIS | partial | stale | L | S | Not planned (too stale) |
| Holiday-season restrictions | `HolidayRestrictedStreets` (948 segments, `HJWCODE`; 2024-07) | City | ArcGIS | arterials | 2024 | ? | ? | Purpose unclear: ask OakDOT before using |
| EV charging | ArcGIS `EV_Locations` (285 points, edited **2026-10-04**, few attributes) | City | ArcGIS | city sites | 2026 | L | S | Prefer NREL AFDC (§3.6) |
| Sweeping citations | Socrata `58em-y96b` "Citation Data Portal" (2026-08); ArcGIS `Street_Sweeping_Citations_2021` | City Finance / OakDOT | Socrata | citywide | 2026-08 | H | M | Optional: "enforcement is heavy on this block" hint |
| Evacuation and fire | `Zonehaven_OKL` (247 Genasys zones, 2024-12); `Fire_Severity_Zone_LRA`; `Evacuation_Routes_*` (draft); `AC_Evacuation_Zones` | City / Genasys | ArcGIS | citywide | 2024 | M | S | static |
| School zones | `15_MPH_Zone_Schools`, `School_w_CrossingGuard_Current` | OakDOT | ArcGIS | citywide | unknown | M | S | Not planned |
| 311 | Socrata `quth-gb8e` "OAK 311" | City | Socrata | citywide | daily | H | S | Not planned |
| Crime | Socrata `ppgh-7dqv` "CrimeWatch Data" (last 90 days) | OPD | Socrata | citywide | daily | M | S | Not planned (see §7) |
| Council meetings | Legistar Web API `https://webapi.legistar.com/v1/oakland/events` (verified; meetings through 2026-10-29) | City Clerk | JSON (no key) | Council and committees | live | H | S | live |
| Library events | BiblioCommons gateway `https://gateway.bibliocommons.com/v2/libraries/oaklandlibrary/events` (verified; 2,855 events) | Oakland Public Library | Unofficial JSON | 18 branches | live | M (undocumented) | S | live |
| City events | oaklandca.gov/events (Cloudflare blocks scripts with 403) | City | HTML | – | – | – | L | Skip |
| Farmers markets | Grand Lake (Sat), Temescal (Sun), Old Oakland (Fri), Jack London Square (Sun), Montclair (Sun), Fruitvale and others; operators include Urban Village and PCFMA | Market operators | HTML | ~6–8 markets | seasonal | H | S | hand |
| Community calendar (unofficial) | Oaklandside listings (same Newspack endpoint as Berkeleyside; empty today); Visit Oakland (403 to scripts); Funcheap RSS (`sf.funcheap.com/feed/`, has East Bay categories) | Cityside / Visit Oakland / Funcheap | WordPress REST / RSS | – | – | L–M | S | **Link out only** (copyright) |

### 3.3 Alameda

The city has no Socrata or ArcGIS Hub open-data portal: only the
[Citywide Viewer](https://alamedaca.gov/BUSINESS/Geographic-Information-System-GIS/City-of-Alameda-Citywide-Viewer).
Its service directory could be probed for layers, which this research did not do. County data:
`data.acgov.org` and [acgov geospatial](https://acgov.org/government/geospatial.htm). The city's
site returns 403 to scripts.

| Topic | Source | Owner | Format | Fresh | Rel | Effort | Fit |
|---|---|---|---|---|---|---|---|
| Trash days | Alameda County Industries (ACI Alameda), 510-483-1400. No public lookup found. | ACI under city franchise | Phone | – | – | – | Day picked by the user, plus a **holiday rule** (hand). At its other franchises, ACI shifts pickups one day later for the rest of a Thanksgiving, Christmas or New Year's week; confirm this for Alameda. |
| Council meetings | Legistar `https://webapi.legistar.com/v1/alameda/events` (verified) | City Clerk | JSON | live | H | S | live |
| Sweeping holidays, meters | City pages (Public Works, Parking) | City | HTML | – | M | S | hand (rules not yet found; verify) |
| RPP, meters, closures | Citywide Viewer layers: unknown | City | ArcGIS | ? | ? | M | Probe later |
| Library events | Alameda Free Library (site returns 403 to scripts) | Library | ? | – | – | – | Skip for now |
| Farmers markets | Webster St and South Shore markets | operators | HTML | seasonal | H | S | hand |

### 3.4 San Francisco

Portal: DataSF, now `data.sf.gov` (Socrata). DataSF publishes under the **ODC Public Domain Dedication
and Licence (PDDL)**, the most permissive licence among these sources.

| Topic | Dataset | Format | Coverage | Fresh | Rel | Effort | Fit |
|---|---|---|---|---|---|---|---|
| Parking regulations (time limits, hours, RPP area) | `hi6h-neyh` "Parking regulations (except non-metered color curb)": `regulation`, `days`, `hrs_begin`/`hrs_end`, `hrlimit`, `rpparea1`, `exceptions` | Socrata GeoJSON | citywide blockfaces | loaded 2026-06 (some rows edited 2017) | H | S | static. **Best single parking-rules source in the set.** |
| RPP eligibility | `i886-hxz9` "RPP Eligibility Parcels" | Socrata | parcels | 2024-01 | M (known inconsistencies before 2014) | S | static (optional; `hi6h-neyh` already has the area) |
| Meters | `8vzz-qzz9` "Parking Meters" (2026-09); `27b3-yjjx` "Metered Street Blocks" | Socrata | citywide | 2026-09 | H | S | static |
| Meter schedules and rates | `qq7v-hds4` "Meter Policies" (per space: `dayofweek`, `starttime`, `endtime`, `scheduletype`; updated daily); `itv4-r6g6` special-event/evening areas | Socrata | per space | daily | H | M (large join) | static, rebuilt weekly |
| Tow-away zones | `wnhf-gu86` "Regularly scheduled tow-away zone" | Socrata | arterials | 2023-10 | M | S | static |
| Temporary street closures and events | `8x25-yybr` "Temporary Street Closures" (`case_name`, `type` such as Special Event, `status`, `start_dt`/`end_dt`, `veh_imp`, `cnn`, `street`/`from_st`/`to_st`; loaded **today**) | Socrata | citywide | daily | H | S | live (hourly poll); also a street-fair calendar |
| Temporary no-parking permits | `43dv-5hf4` "Street Space Permit Report for SFMTA" (daily) | Socrata | citywide | daily | M (contents not yet inspected) | M | live (verify that it holds temporary tow-away signs) |
| Blue curb | `g69s-9jxr` | Socrata | citywide | 2026-09 | H | S | static |
| Trash days | Recology (three companies). No public data or API. | – | – | – | – | – | Day picked by the user |
| Events | `8i3s-ih2a` "Our415 Events and Activities" (Rec & Park and others, with lat/lon; daily) | Socrata | citywide | daily | H | S | live |
| Council (Board of Supervisors) | Legistar `sfgov`. The Web API answers with a configuration error, so scrape `sfgov.legistar.com`. | HTML | – | – | M | M | Later |
| Library | SFPL on BiblioCommons (gateway pattern as Oakland; not tested) | JSON | – | – | M | S | live |
| Trees | `qrwx-q4gg` "Street Tree Removal Notifications" (daily) | Socrata | citywide | daily | H | S | Not planned |
| 311 | `vw6y-z8j6` (daily) | Socrata | citywide | daily | H | S | Not planned |
| Tsunami zone | `7p2k-c3z6` (2021) | Socrata | coast | 2021 | H | S | static (hazards phase) |

### 3.5 Chicago

Portal: `data.cityofchicago.org` (Socrata). The portal's terms of use are permissive, with a
disclaimer. Sweeping zones `2r7q-emq3` are still the 2026 dataset; nothing to change.

| Topic | Dataset | Format | Fresh | Rel | Effort | Fit |
|---|---|---|---|---|---|---|
| Permit parking zones | `u9xt-hiju` "Parking Permit Zones": zone, odd/even side, address range, street | Socrata (rows, no geometry) | **2026-10-08** | H | M (match to street segments, like the sweeping address ranges) | static |
| Winter overnight ban (Dec 1–Apr 1, 3–7 am) | `mcad-r2g5` | Socrata | 2021 (rule unchanged) | H | S | static + hand rule |
| 2-inch snow route ban | `i6k4-giaj` | Socrata | 2021 | H | S | static. The ban only applies after 2 inches of snow, so pair it with a live trigger (NWS snowfall or a city announcement). |
| Towed vehicles | `ygr5-vcbg` "Towed Vehicles" (`plate`, `state`, `towed_to_address`, phone; daily) | Socrata | daily | H | S | live: look up a saved car's plate on demand |
| Relocated vehicles | `5k2z-suxx` "Relocated Vehicles" (`plate`, `relocated_from_*`, `relocated_to_*`, `relocated_reason`; daily) | Socrata | daily | H | S | live: "your car was moved to X". Relocation for sweeping happens. |
| Sweeping delays | `fuz6-n5nj` "Delayed Street Sweeping Zones" | Socrata | 2026-04 | M | S | live (sweeping, side finding) |
| Garbage day | Streets & Sanitation, by ward/section. No dataset. The "City Services by Address" dashboard shows it. | Web dashboard | – | M | L (scrape) | Day picked by the user |
| Blue cart recycling | 2025 zone PDFs (A/B weeks; holiday pickups move to the next day); the old open dataset is deprecated | PDF | 2025 | M | M (digitise) | hand / static |
| Special events and block parties | `xgse-8eg7` "Special Events" (2026-01); `pk66-w54g` Park District outdoor event permits (2026-10) | Socrata | 2026 | M | S | live |
| Library events | `vsdy-d8k7` "Chicago Public Library Events" (daily) | Socrata | daily | H | S | live |
| Park District activities | `tn7v-6rnw` (daily, lat/lon) | Socrata | daily | H | S | live |
| Meters | Parking meters run under a private concession (ParkChicago / Chicago Parking Meters LLC); no open data found | – | – | – | – | Skip |
| Council | City Clerk eLMS (`chicityclerkelms.chicago.gov`, which has a public API; not tested) | JSON | – | M | S | Later |
| Farmers markets | `x5xx-pszi` etc. (2015, stale) | Socrata | 2015 | L | – | hand |
| 311 | `v6vf-nfxy` (daily) | Socrata | daily | H | S | Not planned |

### 3.6 Regional and cross-city

| Source | URL | Owner | Format / access | Licence / terms | Coverage | Fresh | Rel | Effort | Use |
|---|---|---|---|---|---|---|---|---|---|
| City holiday lists | e.g. [Oakland street sweeping](https://www.oaklandca.gov/topics/street-sweeping), which lists 2026 **and** 2027 no-sweep holidays; SFMTA holiday enforcement page; Chicago | Cities | HTML | public | each city | yearly | H | S | hand YAML per city (§5.1) |
| Legistar Web API | `webapi.legistar.com/v1/{client}/events` | Granicus | JSON, no key | public | Oakland, Alameda (SF misconfigured) | live | H | S | council |
| 511 SF Bay | [511.org/open-data/traffic](https://511.org/open-data/traffic): traffic events; **WZDx work zones on local streets** for participating jurisdictions | MTC | JSON, free token | free with token; **60 requests/hour** by default; meant to be cached server-side | 9 Bay Area counties | live | H | M | closures (Bay Area) |
| NWS alerts | `api.weather.gov/alerts/active?point=…` (verified) | NOAA | GeoJSON, no key (User-Agent required) | public domain | US | live | H | S | Red Flag, heat, snow (Chicago) |
| BAAQMD Spare the Air / wood-burn ban | [baaqmd.gov/alertstatus](https://www.baaqmd.gov/alertstatus) (HTML only; no API found) | Air District | HTML | public | Bay Area | daily | H | M (scrape) | alerts (low priority) |
| AirNow | airnowapi.org | EPA | JSON, free key | public | US | hourly | H | S | AQI (low priority) |
| EV charging | NREL (now NLR) AFDC `developer.nlr.gov/api/alt-fuel-stations/v1` | NREL / DOE | JSON, free key, ~1,000 requests/hour | US government data, public | US | daily | H | S | EV layer (all five cities) |
| EV charging (alternative) | Open Charge Map | community | JSON | CC BY-SA 4.0 | global | varies | M | S | fallback |
| OpenStreetMap (Overpass) | already used for Berkeley street placement | OSM | Overpass | **ODbL** (attribution; share-alike for derived databases) | global | live | M | S | **Not useful for curb parking**: in Berkeley's bounding box 11 ways carry `parking:both` and 0 carry restrictions; Oakland has 109 and 0. Chargers: 18 in Berkeley, 73 in Oakland (AFDC has more). Markets: 7 and 40 `amenity=marketplace` (can seed the hand list). |
| Genasys Protect (ex-Zonehaven) zones | via city ArcGIS layers (Berkeley, Oakland) | Genasys / cities | ArcGIS | city layers | Berkeley, Oakland | 2024–26 | H | S | hazards |
| CAL FIRE FHSZ, CGS seismic hazard zones | CAL FIRE / CGS ArcGIS | State | ArcGIS | public | CA | 2025 | H | S | hazards |
| USGS earthquakes | earthquake.usgs.gov GeoJSON feeds | USGS | GeoJSON | public domain | global | live | H | S | Not planned |
| Transit (GTFS / GTFS-RT) | 511 (all Bay Area operators), CTA | MTC / CTA | GTFS | open with token | regions | live | H | M | Not planned (other apps do this better) |
| USDA Local Food Directories | usdalocalfoodportal.com API | USDA | JSON, key | public | US | self-reported | M | S | seed for farmers markets |
| Eventbrite | public event search removed in 2020; only per-organiser endpoints remain | Eventbrite | – | ToS | – | – | – | – | **Skip** |
| Meetup | GraphQL API needs a paid Pro plan | Meetup | – | ToS | – | – | – | – | **Skip** |
| SpotAngels, Parkopedia | commercial; no open API; ToS forbid scraping | – | – | – | – | – | – | – | **Skip** (and their rates disagree with city pages) |

## 4. Recommended features in detail

### 4.1 Holiday-aware sweeping (rank 1, S)

- **Data**: `data/holidays/<city>.yaml` per city (hand), listing dates and what each suspends:
  `{date: 2026-11-26, name: Thanksgiving, suspends: [sweeping, meters]}`. Oakland publishes 2026 and
  2027 lists. SF, Berkeley, Alameda and Chicago rules come from their pages. SF note: night sweeping in
  commercial areas runs on most holidays, so a holiday can suspend only some codes. Model that as
  `suspends: [sweeping:day]`.
- **Pipeline**: none (no tile rebuild). Serve the holidays from `/api/cities` for each region, or as a
  hashed static JSON next to the tiles.
- **Logic**: `urgency.js` `sweepsOn()` returns false on a suspended date, and `analysis.py` mirrors it,
  so server and client agree (as they already share the code tables). The calendar shows
  the holiday as a muted "No sweeping: Veterans Day".
- **Tests**: the existing JS and Python urgency tests gain holiday cases.
- **Risk**: the list goes stale each January. Add a CI check that fails when a city has no holidays for
  the coming year after 1 December.

### 4.2 Trash days in the Calendar, for every city (rank 2, S–M)

- Oakland: ReCollect, as today. Show every stream in the Calendar as a home-coloured dot, not just on
  the Saved card.
- Berkeley, Alameda, Chicago, SF: no lookup exists, so the home's "…" menu gets **Collection day:
  Mon–Fri** (stored in prefs, synced like cars). The server (or client) applies the city's holiday
  rule from §4.1: Berkeley and ACI shift one day for the rest of the week; Chicago moves a holiday
  pickup to the next day. Chicago blue-cart A/B weeks can come later.
- Manifest: generalise `trash:` into
  `trash: {kind: recollect, area, service_id}` | `{kind: weekly, holiday_rule: shift_week}`.
- Berkeley tie-in: the city asks for carts on the parking strip when trash day is also sweeping
  day. Show that hint when both fall on the same day for a home.
- Follow-up: file a Public Records Act request with Berkeley for refuse route GIS. If it arrives,
  add `kind: zones`.

### 4.3 Residential permit parking (rank 3, M)

- **Static layer** `rpp` per city: Berkeley `Public_Online_RPP_Map_WFL1/10` (polygons), Oakland
  `RPPCurbs0819` (curb lines with a side), SF `hi6h-neyh` (blockface lines with `rpparea1`, `hrlimit`,
  hours), Chicago `u9xt-hiju` (address ranges joined to street segments by the existing
  address-range code in `resolve.py`).
- **Rules table** (hand) per city and area: non-permit time limit, hours and days, and a
  link to the city page.
- **Car card**: "Permit area G: 2 h without a permit, Mon–Fri 8–7". `/api/check` gains
  `rules: [{kind: "rpp", area, text, source}]`.
- **Optional**: a Berkeley home's eligibility from layer 1 (address points), shown on the home card.
- **Map**: a toggle in a new layers control. RPP areas are drawn as thin dashed outlines so they don't
  compete with the sweeping colours.

### 4.4 Calendar subscription feed (rank 4, M)

- `GET /api/calendar/{token}.ics` returns, for each signed-in user, the next 60 days of sweeping for
  each car (from its current street), trash for each home, and holidays. Each event carries a
  `VALARM` the evening before. The token is created and revoked like the car tokens in `auth.py`.
  A subscription in Apple or Google Calendar then reminds the user with no Web Push.
- Limits to explain to users: calendar apps refresh subscriptions every few hours to a day, so a car
  moved just before a sweep may still show the old street's reminder.
- Later: Web Push (iOS 16.4+ for home-screen apps). It needs VAPID keys, a sender job on the Pi and
  stored subscriptions. Treat it as phase 6.

### 4.5 Closures and event no-parking (rank 5, M)

- Live module `feeds/closures.py`, built on `trash.py`'s cache pattern (TTL, keep failures briefly,
  cap memory): SF `8x25-yybr` (hourly), Oakland `Latest_Street_Closures_Nov24` (daily), Chicago
  `xgse-8eg7` (daily), 511 WZDx for Bay Area work zones (≤60 requests/hour, so one regional fetch
  every 5–10 minutes).
- Closures are matched to street segments by geometry (SF and Oakland lines) or by street name and
  address range (Chicago).
- UI: a warning on the car card ("Oak St closed Sun 7–10 am: SF Half Marathon"), a hatched overlay on
  the map, and calendar entries for closures on a car's block.
- The same feeds supply "street fair" entries for the Around tab (§4.9).

### 4.6 Chicago tow and relocation lookup, winter bans (rank 6, S–M)

- The car's "…" menu gets an optional **plate** field (local only; synced only when the user is signed
  in).
- "Was my car towed?" queries `ygr5-vcbg` and `5k2z-suxx` server-side by plate, on demand only. Never
  bulk-store or index plates.
- Static layers for the winter overnight ban and snow routes. Dec 1–Apr 1 3–7 am becomes a calendar
  rule, so it needs no tile change. The 2-inch snow ban becomes active only when NWS forecasts or
  reports ≥2" (or on a hand toggle).

### 4.7 Meters and paid parking (rank 7, M–L)

- SF: meters and `qq7v-hds4` schedules give hours per space. A weekly build attaches
  "metered Mon–Sat 9–6" to each blockface.
- Oakland: meter points `Oakland_Parking_Meters` plus a hand rate/hours table by `AREA`.
- Berkeley: meter points, lots (`Portal_Transportation/3`), and the goBerkeley rates web map if its
  layer holds rates by block. Otherwise link out.
- Show "metered" on the car card with the rate and a caution ("check the meter"), because rates change
  2–3 times a year (Berkeley) and the data lags.

### 4.8 EV charging (rank 8, S)

NREL AFDC for every city: one fetch per region, cached for 24 h, shown as a point layer. Attribute "U.S.
DOE AFDC". Points are few, so the layer can be a static GeoJSON rebuilt nightly rather than PMTiles.

### 4.9 Council meetings and the Around tab (ranks 9–10)

- Council: Legistar for Oakland and Alameda (live, cached for 6 h); Berkeley by scraping its agenda
  page. Show the next meeting and a link to the agenda.
- **Around** tab (a fourth sheet tab, or a section in Calendar): farmers markets (hand YAML with
  weekday, hours and location), library events (Oakland BiblioCommons, Berkeley Communico, Chicago
  `vsdy-d8k7`, SF Our415), park events (Chicago `tn7v-6rnw`, SF Our415), street fairs (§4.5 feeds).
  Filter to within ~1 km of saved places or the map centre.
- Editorial calendars (Berkeleyside, Oaklandside, Funcheap) are **link-outs only** unless Cityside
  agrees otherwise.

### 4.10 Hazards and alerts (rank 11, M)

Evacuation zones (Genasys layers, with `zone_status` polled), fire hazard severity zones, Berkeley
narrow-street no-parking in fire zones 2/3 (relevant to parking), NWS Red Flag warnings, and Spare the
Air. Show them as a banner when a saved place is affected. Low frequency, high stakes: the wording must
point to the official alert and never replace it.

## 5. Data model and pipeline changes

### 5.1 Manifests

`cities.py` validates only the required fields, so new optional blocks are backward compatible:

```yaml
# data/manifests/berkeley.yaml (additions)
holidays: data/holidays/berkeley.yaml
trash: {kind: weekly, holiday_rule: shift_week, holidays: [thanksgiving, christmas, new_year]}
layers:
  rpp:
    source: {kind: arcgis, url: "https://services1.arcgis.com/IYiCpZoSIq9lAxi8/arcgis/rest/services/Public_Online_RPP_Map_WFL1/FeatureServer/10"}
    fields: {area: Area}
    rules: data/rules/berkeley_rpp.yaml
    fgb_path: data/berkeley/rpp.fgb
feeds:
  council: {kind: scrape, url: "https://berkeleyca.gov/your-government/city-council/city-council-agendas"}
  library: {kind: communico, site: berkeleypubliclibrary}
```

Oakland's `trash:` becomes `{kind: recollect, area: OaklandCA, service_id: 608}`, and `trash.py` reads
`kind` (keep accepting the old shape for one release).

### 5.2 Static layers

- New `scripts/build_layers.py [city] [layer]`. Fetchers: `arcgis` (paged `query?f=geojson`,
  `resultOffset`), `socrata` (`$limit`/`$offset`), `file`. Each layer has a normaliser in a new
  `layer_schemas.py` (per-layer standard columns, e.g. RPP: `CITY`, `AREA`, `SIDE`, `RULE_ID`), writes
  an atomic `.fgb` like `build_city_fgb`, and **drops any PII column** (allow-list the fields, never
  "all but").
- `build_pmtiles.py` gains `--theme`: it writes `frontend/tiles/<region>.<theme>.pmtiles` with
  source-layer = theme. Sweeping stays in `<region>.pmtiles`, so a parking-data refresh never touches
  the sweeping tiles or their cache hash. `api/web.py` precaches only the sweeping archive; theme
  archives are fetched when the user turns the layer on.
- `api/state.py` loads the per-region layer FGBs next to the sweeping data for point lookups
  (`/api/check` → `rules`). Memory on the Pi: Berkeley's 65k address points are small; SF meter
  schedules are folded into blockfaces at build time, not kept per space.
- Licence and attribution: each layer's manifest entry carries `attribution:` and `licence:`. The Me
  tab's about line lists the ones for the visible layers (OSM ODbL is already shown there).

### 5.3 Live feeds

- Package `broombuster/feeds/` with a shared `cache.py` extracted from `trash.py` (TTL, failure TTL,
  bounded size), plus `legistar.py`, `closures.py`, `bibliocommons.py`, `afdc.py`, `nws.py`.
- One endpoint per concern under `/api/` (so `API_PATHS` is unchanged): `/api/closures?region=`,
  `/api/around?lat=&lon=`, `/api/council?city=`, `/api/towed?plate=` (Chicago; rate-limited like the
  other mutating/lookup routes).
- Keys (511, NREL, AirNow) are read in `config.py` only, and each feed turns itself off when its key is
  absent.
- Keep fetching on demand with a cache rather than a cron on the Pi: same model as `trash.py`, nothing
  new to deploy. Exception: 511 (60 requests/hour), which gets a single regional fetch shared by all
  users.

### 5.4 Prefs

Homes gain `collection_day` (and `collection_week` for Chicago blue carts). Cars gain an optional
`plate`. Both live in the existing prefs blob synced by `auth.put_prefs`.

## 6. UI touchpoints

| Touchpoint | Change |
|---|---|
| **Map** | A "Layers" button under locate (sheet-style popover): Sweeping (always on), Permit areas, Meters, EV, Closures, Hazards. Theme layers use the colours from `--fill`/`--tint-bg` and dashed outlines, so sweeping urgency stays the dominant signal. |
| **Street sign** (`signs.js`) | Under the sweeping sections, one line per rule at the centre dot: "Permit area G · 2 h", "Metered · $3/h", "Closed Sun 7–10". |
| **Calendar** | New dot kinds: home trash (home colour), holidays (muted label, struck-through sweep tint), closures on a car's block (warning dot), optional council meetings and markets. The day list gains those lines. |
| **Saved: car card** | A "Rules here" block (RPP, meter, tow-away, closure) under the schedule; Chicago "Check if towed". |
| **Saved: home card** | Collection-day picker where no lookup exists; the "carts on parking strip" hint for Berkeley. |
| **Me** | Calendar subscription (copy link / revoke), layer attributions, holiday list link. |
| **New "Around" tab** (phase 5) | Events, markets and meetings near saved places; link-outs for editorial calendars. |
| **Notifications** | Phase 2: the ICS feed with alarms. The iOS Shortcut `summary` gains the most urgent rule ("Parked in permit area G: 2 h limit"). Phase 6: Web Push. |

## 7. Risks

| Risk | Where | Mitigation |
|---|---|---|
| **Unofficial APIs** can change or be blocked without notice | ReCollect (in production), BiblioCommons gateway, Berkeleyside/Oaklandside wp-json | Cached proxy that degrades to "no data" (already the `trash.py` pattern); a contract test per feed in CI that is skipped offline; one `User-Agent` naming the site. Ask Cityside before showing their listings. |
| **Licences**: ArcGIS items without licence text; portal ToS vary | Berkeley, Oakland ArcGIS | Berkeley's portal says "without restrictions"; email Oakland's open-data team to confirm reuse of `RPPCurbs0819`, meters and closures. DataSF is PDDL. Chicago's terms are permissive. OSM is ODbL (attribution; share-alike if a derived database is redistributed; the Berkeley build already derives from OSM, so add attribution for the tiles). AFDC and NWS are public domain. |
| **Stale data presented as rules** | Oakland RPP (2019), curb colours (2005), Berkeley RPP alt (2023), SF regs (2017 edits) | Show the source and its "as of" date on every rule line. Word rules as "Posted signs win". Never colour a street red from a stale rule layer. |
| **Data going stale between rebuilds** | holidays, rates, RPP rules (hand tables) | CI date checks (holidays for next year by 1 Dec); a `last_verified:` date in each hand table, with a warning in `rebuild_city_data.py` when it is older than 12 months. |
| **Scraping fragility** | Berkeley council and events pages, BAAQMD, Chicago address dashboard | Phase these last. Parse defensively (no data rather than wrong data). Cloudflare-protected sites (oaklandca.gov, alamedaca.gov, visitoakland.com) are out of scope: do not work around bot protection. |
| **Dataset IDs move** | SF host move (`data.sfgov.org` → `data.sf.gov`); Chicago republishes sweeping yearly; Oakland's sweeping no longer on Socrata | Pin URLs in manifests; `rebuild_city_data.py` logs redirects; the yearly Chicago note already exists. |
| **PII** | Berkeley RPP parcels layer (owner names, mailing addresses); Chicago tow and relocation plates; 311 addresses | Field allow-lists in normalisers. Plate lookups on demand only: never logged, cached ≤10 min, rate-limited. |
| **Crime and 311 layers** | Oakland, SF, Chicago | Not planned: they invite neighbourhood stigma and add little to "move my car". |
| **Pi load and rate limits** | 511 (60 requests/hour), AFDC, Socrata unauthenticated limits | One shared regional fetch per feed; Socrata app token in `config.py` if limits bite. |
| **Scope creep** | Around tab, hazards | Ship phases 1–3 first; Around stays behind a section in Calendar until usage justifies a tab. |
| **CSP** | any new third-party fetch from the browser | All fetches go through `/api/`; no event images (Our415 photos come from other hosts and would widen `img-src`). |

## 8. Phased rollout

Each phase is one or more PRs to `main`, which auto-deploys after CI. Keep each releasable on its own.

| Phase | Ships | Depends on | Size |
|---|---|---|---|
| **1. Correctness and trash** | Holiday YAMLs for all five cities + `urgency.js`/`analysis.py` support + CI date check; trash days in the Calendar; `trash.kind` with `weekly` + holiday rules for Berkeley, Alameda, Chicago, SF; Berkeley parking-strip hint; SF manifest host fix. File the Berkeley refuse-route PRA request. | – | S–M |
| **2. Rules framework + RPP + ICS** | `build_layers.py`, `layer_schemas.py`, theme PMTiles, `rules` in `/api/check`, sign and car-card rule lines, Layers popover; RPP for Berkeley, SF, Chicago, Oakland; `/api/calendar/{token}.ics`; Shortcut summary includes the RPP rule. | 1 (calendar plumbing) | M–L |
| **3. Live closures + Chicago** | `feeds/` package (shared cache), closures for SF, Oakland, Chicago and 511 WZDx; car-card and calendar warnings; Chicago towed/relocated lookup, winter and snow bans; Chicago sweeping delays. | 2 (segment matching, rule lines) | M |
| **4. Meters and EV** | SF meter schedules, Oakland and Berkeley meters + hand rate tables, Berkeley lots, AFDC EV layer. | 2 | M–L |
| **5. Around** | Farmers markets (hand), library and park events, street fairs from phase 3, council meetings; editorial link-outs. | 3 | M |
| **6. Alerts and push** | Hazard layers, Genasys zone status, NWS / Red Flag / Spare the Air banners; Web Push if the ICS feed proves insufficient. | 2–3 | M–L |

## 9. Still to verify

- Berkeley goBerkeley rate web map (`09b52ae8a7d04cd9b62157ff7d999e93`): which layer, and whether it
  has rates for each block.
- Oakland `HolidayRestrictedStreets` meaning (`HJWCODE`), and whether Oakland or Berkeley restrict hills
  parking on Red Flag days.
- SF `43dv-5hf4` contents (temporary no-parking / tow-away permits?).
- Alameda Citywide Viewer service directory; Alameda's sweeping-holiday and ACI holiday rules.
- Berkeley Communico feed URL format; SFPL BiblioCommons gateway key.
- Oakland open-data licence wording for ArcGIS items.

## 10. Implementation status (2026-10-17)

All six phases are implemented on one branch. What changed from the plan above, and why:

| Plan | Shipped | Why |
|---|---|---|
| Theme layers as per-region PMTiles (tippecanoe) | Static GeoJSON per region (`frontend/layers/<region>/<theme>.geojson`, content-hashed URLs, loaded only when a layer is turned on; ≤2 MB each, label and city only) | tippecanoe is not installed on the build machine, and these layers are small enough for GeoJSON. The sweeping tiles are untouched. |
| Switch Berkeley and Oakland sweeping to their ArcGIS layers | Not switched | Berkeley's layer has 47 route lines (2023) that would still need matching to streets, and Oakland's (2021) is not newer than the committed data. Either way the sweeping tiles would need rebuilding, which needs tippecanoe. Worth revisiting with tippecanoe and a side-by-side diff. |
| Berkeley and Alameda no-sweep holidays | None assumed | Neither city publishes a list. A wrong "no sweeping" costs a ticket, while a wrong warning only costs a move, so only Oakland's and SFMTA's published lists are used. Trash holidays are used for every city. |
| SF holidays: the usual 12 | 3 (New Year's, Thanksgiving, Christmas) | SFMTA's 2026 enforcement page enforces street cleaning on the other holidays. |
| Library events from BiblioCommons | Link-outs only | The gateway API is undocumented and ignores date filters. Chicago's official CPL dataset is used, and SF's Our415 covers Rec & Park and library events. |
| EV layer for every city | Berkeley, Alameda, Chicago built; Oakland and SF pending | DEMO_KEY hit its hourly limit during the build. Run `BB_NREL_API_KEY=… python scripts/build_layers.py --theme ev` with a free key. NREL's API moved to `developer.nlr.gov`. |
| Rule lines on the street sign | On the car's page ("Also here") and in map popups | The sign stays the published sweeping schedule. Rules apply to a parked car, so they live on the car's page. |
| Web Push | Not built | The calendar subscription (with reminders) covers the need without a push sender on the Pi. Revisit if users ask for it. |
| Berkeley "Fire hazard zones" from CAL FIRE FHSZ | Berkeley's own hill Fire Zones 2 and 3 | The CAL FIRE LRA map inside the city covers only a sliver. Fire Zones 2/3 are what the city's narrow-street and Red Flag measures use. |

Data notes found while building:

- Oakland's meter layer had points at (0, 0), and Berkeley's evacuation zones are `BER-…`
  (`BRK` is Burbank). Every layer is now clipped to its city's bbox, and a test checks it.
- Berkeley's 2026 RPP report gives the non-permit limit as 2 hours, Mon–Fri 8 AM–7 PM
  (older reports said 9 AM). Oakland's per-area limits are not published as data, so the
  rule says to follow the signs.
