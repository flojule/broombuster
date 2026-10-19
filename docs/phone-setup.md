# Phone setup

The phone reports the car's position when it disconnects from the car. One automation and one token per car:
the trigger decides *when*, the token decides *which car*.

## 1. Token

Sign in, open the car, **Link phone**, copy the token (shown once). **Unlink phone** revokes it.

## 2. Trigger

| Car | Trigger | Tells cars apart |
|---|---|---|
| Wireless CarPlay, or Bluetooth only | Bluetooth, the car's name | yes |
| Wired CarPlay | Bluetooth if the car is paired, else CarPlay | Bluetooth only |

The CarPlay trigger fires on any CarPlay disconnect and cannot pick a car: use it only with a single linked car.

## 3. iPhone

Shortcuts > Automation > **+** > *Bluetooth* (or *CarPlay*) > the car > **Is Disconnected** > **Run Immediately** >
*New Blank Automation*. Turn off *Notify When Run*. Add:

1. **Get Current Location**
2. **Get Details of Locations**: Latitude, Longitude, Horizontal Accuracy
3. **Get Contents of URL**: `https://broombuster.com/api/cars/location`, POST, header `Authorization` = `Bearer <token>`,
   JSON body `lat` = Latitude, `lon` = Longitude, `accuracy` = Horizontal Accuracy
4. **Get Dictionary Value** `summary`, then **Show Notification**

Android: Tasker or MacroDroid, on Bluetooth disconnect from the car, send the same request.

## Request

```
POST /api/cars/location
Authorization: Bearer <token>
{"lat": 37.8044, "lon": -122.2712, "accuracy": 8}
```

Reply: `{"summary": "Parked at 12 Oak St, Oakland."}`.

- `accuracy` is optional, in metres. Above 50 m the report is refused (422).
- A refusal also answers with a `summary` saying why ("Position too rough (±65 m); the car was not moved."). The app shows the last refusal on the car's page.
- Numbers may be text, with a decimal comma or a unit (`"35 m"`, `"115 ft"`).
- `401`: token revoked. `404`: car removed.
