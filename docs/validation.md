# Street View validation

Checks the sweeping schedule and parking rules the app returns against the signs on the street.
Tool: `scripts/validate_streetview.py`; results: `data/validation/<city>_<layer>.csv`.

## Method

```
sample (random, by length) -> app's answer at the kerb -> 2 panoramas x 2 sides -> blind sign reading
   -> compare -> review of every mismatch -> 95 % upper bound on the mismatch rate
```

| Layer | Unit | Groups (each tested on its own) | Checked |
|---|---|---|---|
| `sweeping` | block side, 6 m from the centre line | `scheduled` (app gives a schedule), `unscheduled` | days, weeks of month, hours |
| `parking` | permit curb (SF, Oakland) or block in a permit area (Berkeley) | `parking` | permit area, time limit (SF) |

Pass: one-sided 95 % Clopper-Pearson upper bound on the mismatch rate <= 5 %, from the readable samples
in draw order (a sample is readable when it is `match` or `mismatch`):

| Readable | Mismatches allowed |
|---|---|
| 59 | 0 |
| 93 | 1 |
| 124 | 2 |
| 160 | 3 |

Not counted: `stale` (newest panorama before 2023-11), `no_imagery`, `no_view`, `unreadable`.
`absent` (a schedule expected, no sign seen) is reviewed by hand and becomes `match` or `mismatch`.

## Reading signs

The reader sees only `images/<id>/` (never `samples.jsonl`, the data or the app). Per sample: `p<k>_<L|R>.jpg`,
a 180° view of one side of the street from panorama k; the kerb of that side runs across the image, the road
at both edges. `zoom IMAGE U0 U1 V0 V1` crops it at 4x (U, V: fractions of the view's width and height).
Output `readings/<id>.json`:

```json
{"id": "oak-sw-0007", "views": [
  {"pano": 0, "view": "L", "coverage": "good", "signs": [
    {"kind": "sweeping", "near_kerb": true, "confidence": "high",
     "text": "NO PARKING 9 AM TO 12 NOON 2ND & 4TH MONDAY STREET SWEEPING",
     "days": ["Mon"], "weeks": [2, 4], "start": "09:00", "end": "12:00"}]}]}
```

| Field | Values |
|---|---|
| `coverage` | `good` (kerb visible end to end), `partial`, `poor` (blocked, blurred, dark) |
| `kind` | `sweeping`, `time_limit`, `permit`, `no_parking`, `other` |
| `near_kerb` | `true` when the sign stands on this view's kerb, before the next cross street |
| `confidence` | `high` only when every field was read, not guessed |
| `weeks` | week-of-month numbers; `[]` = every week |
| `start`, `end` | 24 h `HH:MM` |
| `limit_h`, `permit` | time limit in hours; permit area as printed (`S`, `C`, `154`) |
