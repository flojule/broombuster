# Requirements

BroomBuster tells a driver, at a glance, when their street is swept, so they move the car in time.
This document is the source of truth: a change that conflicts with it needs the owner's approval
and an update here first (C1).

## 1. Users and scope

| ID | Requirement |
|---|---|
| S1 | Main user: a driver on a phone, one-handed, outdoors. |
| S2 | Installable web app (PWA, Progressive Web App) on iPhone Safari and Android Chrome; desktop is secondary. |
| S3 | Only the regions in `data/`; US English only. |

## 2. Functions

| ID | Requirement |
|---|---|
| F1 | Map with street lines colored Today / Tomorrow / Clear. |
| F2 | A target at the map center picks the street; the street card shows that street. |
| F3 | Street card: the posted sign (side, days, times) plus a Today / Tomorrow badge; no derived sentences. |
| F4 | Save a car or home from GPS or by panning; a location is saved only after the user adjusts and confirms the spot (Set here). |
| F5 | A saved car shows when to move it ("Move by 9AM"). |
| F6 | Calendar of sweep days for saved cars and homes. |
| F7 | Add to calendar (ICS feed). |
| F8 | Optional: passkey account to sync; phone shortcut reports the car's position. |
| F9 | Works offline with the last data and shows its age. |

## 3. UI/UX rules

| ID | Requirement |
|---|---|
| U1 | Visible wording at an absolute minimum: labels of 3 words or fewer, no sentences, except confirmations of destructive actions. Screen-reader-only labels may be fuller. |
| U2 | No tips, onboarding text or always-on legend; the legend lives only in the Layers menu. |
| U3 | Checking a street: 0 taps. Saving a car: 2 taps or fewer, plus the manual adjustment. |
| U4 | Nothing saved changes without an explicit user action. |
| U5 | Meaning is never carried by color alone: always a word or icon too. |
| U6 | Map first: no panel or label covers the target. |
| U7 | Accessibility: WCAG 2.2 AA contrast (4.5:1 text), 44 px touch targets (Apple Human Interface Guidelines), text at least 12 px, Dynamic Type, reduced motion. |
| U8 | Light and dark themes get equal treatment. |
| U9 | Times in the region's clock; US formats ("9AM", "Mon"); weeks start Sunday. |
| U10 | One term per concept: Sweeping, Today, Tomorrow, Clear, Move by. |
| U11 | No new UI element without a requirement row here. |

## 4. Data

| ID | Requirement |
|---|---|
| D1 | Schedules from official city data, with each city's holidays applied. |
| D2 | Accuracy: Street View mismatch rate at most 5 % at the 95 % upper bound, per city ([validation](validation.md)). |
| D3 | Unknown is shown as unknown; never guessed. |

## 5. Performance

Proposed targets, not yet measured on the Pi or a mid-range phone.

| ID | Requirement |
|---|---|
| P1 | First load on 4G: map usable in under 3 s. |
| P2 | Repeat load (service worker): under 1 s. |
| P3 | Panning: JavaScript under 8 ms per frame at 4x CPU throttle. |
| P4 | `/api/check` p95 under 100 ms on the Pi. |
| P5 | Restart to a healthy `/health` in under 10 s on the Pi. |

## 6. Operations and privacy

| ID | Requirement |
|---|---|
| O1 | `main` is always releasable; it auto-deploys after CI passes. |
| O2 | Reachable only through the Cloudflare tunnel. |
| O3 | No email, no password, no analytics; a location goes to the server only to check a street or sync saved items. |
| O4 | A failed live source means "nothing known", never an error. |

## 7. Change control

| ID | Requirement |
|---|---|
| C1 | A change that conflicts with this document needs the owner's approval and an update here first. |
| C2 | CLAUDE.md links here; every change is checked against it. |
