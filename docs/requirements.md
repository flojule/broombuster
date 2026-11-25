# Requirements

Need: a driver knows, at a glance, when their street is swept, and moves the car in time.
A change that conflicts with this document needs the owner's approval and an update here first.

Each row is one requirement. IDs are never reused. Verify: T test (automated), I inspection,
D demonstration, M measurement. Priority: Must unless marked Should.

## 1. Glossary

| Term | Definition |
|---|---|
| Region | A covered city or county; its time zone is the region clock. |
| Side | One curb of a street block. |
| Today | A side of the street is swept on the current region-clock day. |
| Tomorrow | A side is swept on the next region-clock day, and not today. |
| Clear | No side is swept today or tomorrow. |
| Check sign | Swept on a day only the posted sign gives, or the data's dates have expired. |
| Sweeping now | A sweep on the car's side is in progress. |
| Move by | Start time of the next sweep on the car's side, on a Today or Tomorrow day. |
| Target | The point on the map whose street the app shows. |

## 2. Scope

| ID | Requirement | Verify |
|---|---|---|
| S1 | The main user is a driver using a phone one-handed, outdoors. | I |
| S2 | The app shall run in the browser on iPhone (Safari) and Android (Chrome), current and previous major OS versions, with no app store install. | D |
| S3 | The app shall be installable to the phone's home screen. | D |
| S4 | The app shall run on desktop browsers. (Should) | D |
| S5 | Coverage is the regions listed in the [data sources](../data/README.md#sources). | I |
| S6 | Language: US English. | I |
| S7 | Out of scope: push notifications, native apps, other languages, ticket payment. | I |

## 3. Functions

| ID | Requirement | Verify |
|---|---|---|
| F1 | The map shall show each street in view as Today, Tomorrow, Clear or Check sign. | T |
| F2 | The user shall pick a street by moving the map, without tapping it. | D |
| F3 | For the street at the target, the app shall show each side's posted schedule (days, times) and its Today or Tomorrow status. | T |
| F4 | The app shall show the parking rules at the target (permit area, time limit) where the data exists. (Should) | D |
| F5 | The user shall find an address and move the map there. | D |
| F6 | The user shall show or hide each map layer: sweeping, closures, permit areas, meters, fire alerts. | D |
| F7 | The user shall save a car or a home at their GPS (Global Positioning System) position or at the target. | D |
| F8 | Before a location is saved, the user may move it; it is saved only on the user's confirmation. | T |
| F9 | The user shall set which side of the street a car is on. | D |
| F10 | The user shall rename, move and delete a saved car or home. | D |
| F11 | For a saved car, the app shall show Sweeping now, Move by (time), Clear until (day, time) or Check sign. | T |
| F12 | The app shall show a calendar of sweep days for saved cars and homes, at least 12 months ahead. | T |
| F13 | The user shall subscribe to sweep days in an external calendar app. | D |
| F14 | Where the user signs in, saved cars and homes shall sync across their devices. (Should) | D |
| F15 | A phone automation shall be able to report where the car parked. (Should) | D |
| F16 | Without a network, the app shall work with the last loaded data and show its age. | T |
| F17 | Outside coverage, the app shall say so; when zoomed too far out to read a street, it shall say to zoom in. | T |
| F18 | The user shall choose a light, dark or system theme. | D |
| F19 | The app shall show data sources and map attribution. | I |

## 4. UI/UX rules

| ID | Requirement | Verify |
|---|---|---|
| U1 | Visible labels shall be 3 words or fewer, with no sentences; exception: confirmation of a destructive action. | I |
| U2 | Screen-reader-only labels may be longer than U1. | I |
| U3 | No tips, onboarding text or always-visible legend; the legend opens on demand. | I |
| U4 | Reading the street at the map center: 0 taps. | D |
| U5 | Saving a car at the GPS position, from the app open on the map: 2 taps or fewer, plus any optional adjustment. | D |
| U6 | Nothing saved changes without a user action; a phone automation (F15) the user set up counts as one. | T |
| U7 | Meaning is never carried by color alone; a word, icon or line style goes with it. | I |
| U8 | No panel or label covers the target while the sheet is at its lower height. | D |
| U9 | Accessibility: WCAG (Web Content Accessibility Guidelines) 2.2 level AA. | T, I |
| U10 | Usable with VoiceOver (iPhone) and TalkBack (Android). | D |
| U11 | Light and dark themes each meet U9. | T |
| U12 | Times shown in the region clock, US formats ("9AM", "Mon"); weeks start Sunday. | T |
| U13 | One term per concept, as in the glossary. | I |
| U14 | Every visible UI element traces to a row in section 3 or 4. | I |

## 5. Data

| ID | Requirement | Verify |
|---|---|---|
| D1 | Schedules come from official city or contractor data, with each region's holidays applied. | I |
| D2 | Sweeping and parking data: Street View mismatch rate at most 5 % at the 95 % upper bound, per city ([validation](validation.md)). | M |
| D3 | Unknown is shown as unknown, never guessed. | T |
| D4 | A failed live source (closures, alerts) is shown as nothing known, never as an error. | T |
| D5 | A region's data is rebuilt within 30 days of a change in its source. (Should; proposed) | I |

## 6. Performance

Proposed: Core Web Vitals "good" thresholds (web.dev), 75th percentile, Lighthouse mobile
profile (150 ms round trip, 1.6 Mbit/s, 4x CPU slowdown). Not yet measured.

| ID | Requirement | Verify |
|---|---|---|
| P1 | LCP (Largest Contentful Paint) ≤ 2.5 s on first load. | M |
| P2 | INP (Interaction to Next Paint) ≤ 200 ms, map panning included. | M |
| P3 | CLS (Cumulative Layout Shift) ≤ 0.1. | M |
| P4 | Street lookup at the target: server time p95 ≤ 100 ms. | M |
| P5 | Service back after a restart in ≤ 10 s. | M |
| P6 | Availability: best effort; no uptime target. | I |

## 7. Privacy and security

| ID | Requirement | Verify |
|---|---|---|
| O1 | No email, password, analytics or tracking. | I |
| O2 | A location goes to the server only to check a street or to sync saved items. | I |
| O3 | Third parties get only what a function needs (map area for tiles, address or point for geocoding), never a user identifier. | I |
| O4 | The user can delete their account and its data. | T |
| O5 | All traffic uses HTTPS; the server is not reachable directly from the internet. | T |

## 8. Locked design decisions

| ID | Decision | Why |
|---|---|---|
| L1 | Web app (PWA, Progressive Web App), not native. | One codebase, no app store (S2, S3). |
| L2 | A fixed target at the map center picks the street. | One-handed, no precise tap (F2). |
| L3 | Bottom sheet with tabs: Calendar, Saved, Settings. | Map stays visible (U8). |
| L4 | Legend lives in the Layers menu. | U3. |
| L5 | Sign-in by passkey only. | O1. |
| L6 | Calendar subscription by ICS (iCalendar) feed. | Works with every calendar app (F13). |
| L7 | Server on a Raspberry Pi behind a Cloudflare tunnel. | O5 at no hosting cost. |
