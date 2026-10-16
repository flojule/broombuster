# Phone setup: report where the car parked

The phone sends its position when it disconnects from the car. One automation and one
token per car: the trigger decides *when*, the token decides *which car*.

## 1. Get a token

Sign in, open the car, tap **Link phone**, copy the token (shown once).
**Unlink phone** revokes it.

## 2. Pick the trigger

| Your car | Trigger | Tell cars apart |
|---|---|---|
| Wireless CarPlay | Bluetooth, the car's name | yes |
| Bluetooth only (no CarPlay) | Bluetooth, the car's name | yes |
| Wired CarPlay | Bluetooth if the car is paired (Settings > Bluetooth), else CarPlay | Bluetooth only |

The CarPlay trigger fires for any CarPlay disconnect and cannot pick a car; use it only
if you link a single car. Wireless CarPlay's Wi-Fi link is private and not selectable
as a Wi-Fi trigger.

## 3. iPhone: create the automation

Shortcuts > Automation > **+** > *Bluetooth* (or *CarPlay*) > choose the car > **Is
Disconnected** > **Run Immediately** > *New Blank Automation*. Add:

1. **Get Current Location**
2. **Get Details of Locations**: Latitude (repeat for Longitude and Horizontal Accuracy)
3. **Get Contents of URL**
   - URL: `https://broombuster.com/api/cars/location`
   - Method: POST
   - Headers: `Authorization` = `Bearer <token>`
   - Request Body: JSON, `lat` = Latitude, `lon` = Longitude, `accuracy` = Horizontal Accuracy
4. **Get Dictionary Value** `summary` from the contents, then **Show Notification**

Turn off *Notify When Run*.

## Android

Tasker or MacroDroid: on Bluetooth disconnect from the car, send the same request.

## Request

```
POST /api/cars/location
Authorization: Bearer <token>
{"lat": 37.8044, "lon": -122.2712, "accuracy": 8}
```

Reply `{"summary": "Parked at 12 Oak St, Oakland."}`. `accuracy` is optional (metres);
above 50 the position is refused (422). `401` = token revoked; `404` = car removed.
