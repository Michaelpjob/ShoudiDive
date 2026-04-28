# ShouldIDive mobile app (Expo / React Native)

Native iOS + Android app for the visibility model. It consumes the
pipeline manifest at `https://shouldidive.com/data/manifest.json` and
renders those layer PNGs on top of a native map.

## Why this exists separately from the web app

Mobile Safari was choking on the SVG + `foreignObject` + canvas stack
the web frontend uses. The mobile app keeps the same data model but
uses a native map plus a plain positioned image overlay. The phone does
not do per-pixel color processing anymore; that work can move to the
pipeline where it is easier to verify.

## Day-1 scope

* Apple Maps view zoomed to the model bbox.
* SST / Chl / Visibility heatmaps as full-bbox PNG overlays.
* Saved spot pins with default callouts.
* Layer chip strip + 1/2/3-day composite picker.
* Manifest fetch + reactive re-render when the cycle refreshes.

## Not in here yet

* Wind + Swell layers.
* Tap-to-pin value readout.
* Per-spot value cards.
* Saved-spot list screen.
* Push notifications.
* MPA + Bathy overlays.

## Run it on iPhone

1. Install Expo Go from the iOS App Store.
2. From the repo root:

```bash
cd mobile
npm install
npm start
```

3. Open the Camera app on the iPhone, scan the QR code, and open the
   project in Expo Go.

## Validation

Run this before pushing mobile changes:

```bash
cd mobile
npm run validate
```

`npm run validate` now uses `scripts/validate.js`, a cross-platform Node
runner that works from PowerShell as well as bash shells. The legacy
`scripts/validate.sh` file is still present for reference, but it is no
longer the primary entrypoint.

Validation layers:

1. `expo install --check`
2. `expo-doctor`
3. `expo export --platform ios`
4. `expo export --platform web`
5. `jest`
6. `node scripts/smoke-web.js`
7. `node scripts/visual-tests.js`

What this still cannot prove locally:

* Native image overlay alignment on top of a `MapView`
* `react-native-maps` gesture behavior
* Device-specific layout or performance issues

## Overlay strategy

The native screen now renders a plain React Native `Image` on top of the
map, positioned from the current region math. Mobile prefers
`mobile_url` or `color_url` when the manifest provides one, and falls
back to the canonical grayscale `url` otherwise.

That split keeps the phone-side renderer simple:

* pipeline: generate colorized mobile PNGs
* mobile app: load and place one remote image reliably

## Repo layout

```text
mobile/
  App.js
  app.json
  package.json
  scripts/
    validate.js
    validate.sh
    smoke-web.js
    visual-tests.js
  src/
    components/
      MapScreen.jsx
      MapScreen.web.jsx
    lib/
      dataSource.js
      mapData.js
      __tests__/
```
