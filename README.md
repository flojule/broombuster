# BroomBuster

Street-sweeping map for Oakland, San Francisco, Berkeley, Alameda and Chicago.

## Use

1. In **Saved**, tap **+** > *Car* (or right-click / long-press the map > *Add car here*), place it (it starts
   where you are once the location dot is on), and see when its street is swept. On a car's page, **Parked
   here** moves it to where you are; **…** has the rest (set location, address, rename, remove).
2. Add more cars, and a home (+ > *Home*) for trash pickup days: looked up by address in Oakland,
   elsewhere pick the collection day on the home's page; city holidays move it as the city does.
3. **Calendar** shows the sweeping days of your cars and of the street under the centre dot: one week and
   the selected day when the sheet is small, unfolding into the month as you pull it up (on a desktop, a
   six-week panel). The card at the top left shows what applies on that street: its sweeping as published
   (per side when the sides differ), then its permit area, time limit, meter, winter ban or closure. Zoomed in,
   each side of a street has its own line, coloured by its own urgency. City holidays without sweeping (Oakland,
   SF) are struck through, and the map does not ask you to move on them. Homes' pickup days are the square dots.
4. The layers button (top right) adds permit areas, meters, EV charging, fire zones, farmers markets and, in
   the Bay Area, street closures for the next two weeks; under them is the legend (street colours and the
   card's marks). A car's page lists what applies where it
   is ("Also here"), with a warning for a closure on its block; in Chicago it can check whether the car was towed
   or relocated. Weather and air alerts (Red Flag, snow, Spare the Air) show under the Saved summary.
5. **Nearby** lists this week's farmers markets, events, street fairs and council meetings around the centre target.
6. In **Settings**, sign in with Google to sync cars across devices, and add your cars' sweeping and your homes'
   pickups to your calendar app (a subscription link, with a reminder the evening before).
7. Optional: [have your phone update the car's position](docs/phone-setup.md) when you park.

## Run locally

```bash
uv sync --locked --all-extras
./run.sh        # http://localhost:8000
```

Deploying and operating: [docs/operations.md](docs/operations.md).

Layout check: `scripts/visual_scenarios.py` screenshots every platform (desktop WebKit and Chromium, iPhone
Safari tab and home-screen app, landscape, iPad, Android Chrome) x light/dark x view into `build/visual/`, and
fails if the centre target is covered, panels overlap, the app is the wrong height, a street has no colour or
axe-core finds a serious WCAG 2.2 AA problem. It runs on every pull request (Visual workflow, screenshots
attached to the run):

```bash
uv tool run --from playwright python scripts/visual_scenarios.py [--only iphone-safari] [--view month] [--theme dark]
```

Headless WebKit cannot draw Safari's toolbars, the Dynamic Island or the glass blur (backdrop-filter), so before a layout change ships, check on
an iPhone, in a Safari tab and in the home-screen app: the map reaches the screen edges (the Safari tab stops at
the status bar), the tab bar clears the address bar and home indicator, dragging the sheet snaps to its two
heights (from the grabber, a header, or a list scrolled to its top) with the calendar unfolding smoothly and
the centre dot staying in view, the street card clears the status bar, the locate button shows the blue
dot, a long press on the map offers "Add car here", text on the glass (sheet, menus, labels) stays readable over a
busy map, and the map stays in view with the sheet at its tallest. If something looks wrong, Safari's Web Inspector over USB shows the live page.

## License

MIT (`LICENSE`). Vendored libraries keep their own licences
(`frontend/vendor/README.md`); city data comes from each city's open-data portal.
