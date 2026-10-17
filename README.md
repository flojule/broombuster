# BroomBuster

Street-sweeping map for Oakland, San Francisco, Berkeley, Alameda and Chicago.

## Use

1. In **Saved**, tap **+** > *Car* (or right-click / long-press the map > *Add car here*), place it (it starts
   where you are once the location dot is on), and see when its street is swept. On a car's page, **Parked
   here** moves it to where you are; **…** has the rest (set location, address, rename, remove).
2. Add more cars, and a home (+ > *Home*, Oakland) for trash pickup days.
3. **Calendar** shows the sweeping days of your cars and of the street under the centre dot: one week when the
   sheet is small, unfolding into the month as you pull it up. The sign at the top left shows that street's
   schedule as published, one section per side. Zoomed in, each side of a street has its own line, coloured by
   its own urgency, and the sign outlines each side in that colour.
4. In **Me**, sign in with Google to sync cars across devices.
5. Optional: [have your phone update the car's position](docs/phone-setup.md) when you park.

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

Headless WebKit cannot draw Safari's toolbars or the Dynamic Island, so before a layout change ships, check on
an iPhone, in a Safari tab and in the home-screen app: the map reaches the screen edges (the Safari tab stops at
the status bar), the tab bar clears the address bar and home indicator, dragging the sheet snaps to its three
heights (from the grabber, a header, or a list scrolled to its top) with the calendar unfolding smoothly and
the centre dot staying in view, the sign clears the status bar, the locate button shows the blue
dot, and a long press on the map offers "Add car here". If something looks wrong, Safari's Web Inspector over USB shows the live page.

## License

MIT (`LICENSE`). Vendored libraries keep their own licences
(`frontend/vendor/README.md`); city data comes from each city's open-data portal.
