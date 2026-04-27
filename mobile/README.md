# ShouldIDive — mobile app (Expo / React Native)

Native iOS + Android app for the visibility model. Reuses the data
pipeline at `https://shouldidive.com/data/manifest.json`; the mobile
app is a pure consumer of those PNGs.

## Why this exists separately from the web app

Mobile Safari was choking on the SVG + foreignObject + canvas stack
the web frontend uses. Native maps render the same heatmap PNGs as
GPU-accelerated overlays via `react-native-maps`, with native pan /
zoom, native gestures, and zero foreignObject quirks. Same data,
better runtime.

## Day-1 scope (what's in here)

* Apple Maps view zoomed to the model bbox.
* SST / Chl / Visibility heatmaps as full-bbox PNG overlays.
* Saved spot pins (8 known dive locations) with default callouts.
* Layer chip strip + 1/2/3-day composite picker.
* Manifest fetch + reactive re-render when the cycle refreshes.

## Day-1 NOT in here yet

* Wind + Swell layers (need 5-day forecast feed wiring).
* Tap-to-pin value readout.
* Per-spot value cards.
* Saved-spot list screen.
* Push notifications.
* MPA + Bathy overlays.

## Run it on your iPhone today

1. Install **Expo Go** from the iOS App Store (free).
2. From the repo root:
   ```bash
   cd mobile
   npm install              # one-time
   npm start                # starts the dev server + shows a QR code
   ```
3. Open the Camera app on your iPhone and point it at the QR code in
   the terminal. Tap the notification to open Expo Go. The app loads.
4. Edits to `mobile/src/...` hot-reload over Wi-Fi.

## Repo layout

    mobile/
    ├── App.js                  — root component, just mounts MapScreen
    ├── app.json                — Expo config (name, bundle id, splash)
    ├── babel.config.js         — Babel preset (just babel-preset-expo)
    ├── package.json            — RN + Expo deps
    ├── scripts/
    │   └── validate.sh         — 4-layer pre-push validation (see below)
    └── src/
        ├── components/
        │   └── MapScreen.jsx   — the only screen for now
        └── lib/
            ├── mapData.js      — BBOX, BBOX_REGION, SAVED_SPOTS
            ├── dataSource.js   — manifest fetcher, layer URL resolver
            └── __tests__/      — jest tests for the data layer

The data layer (`src/lib/dataSource.js`) is intentionally minimal in
v1: it fetches `manifest.json` and resolves URLs. We DON'T decode
PNGs into Float32Arrays in JS like the web frontend does — the
native map handles the PNG render itself with no per-pixel JS work.
Tooltip lookups (when we add them) will use a separate per-cell
endpoint or a small grid JSON, not in-JS PNG decoding.

## Validation — `npm run validate`

Before pushing any non-trivial change to `mobile/`, run the full
5-layer validation pipeline from the `mobile/` directory:

```bash
npm run validate
```

What each layer catches:

| # | Layer                  | What it catches                                                         | Speed |
|---|------------------------|-------------------------------------------------------------------------|-------|
| 1 | `expo install --check` | SDK / package version drift (e.g. wrong jest version)                   | ~2s   |
| 2 | `expo-doctor`          | Project-wide config (entry point, peer-deps, scheme conflicts, …)       | ~10s  |
| 3 | `expo export ios`      | iOS Hermes bundle compile — Babel + import errors                       | ~15s  |
| 4 | `expo export web`      | Web fallback compiles (so the app can be rendered in a browser, see below) | ~10s  |
| 5 | `jest`                 | Data-layer regressions (manifest fetch, URL resolution, subscribers)    | ~3s   |

A clean run looks like:

    ▶ Layer 1/5 — expo install --check        Dependencies are up to date
    ▶ Layer 2/5 — expo-doctor                  17/17 checks passed
    ▶ Layer 3/5 — expo export                  Bundled 6339ms (938 modules, 2.43 MB)
    ▶ Layer 4/5 — expo export web              Bundled 4204ms (413 modules, 745 kB)
    ▶ Layer 5/5 — jest                         26 passed, 0 failed (1.2s)
    ✓ All 5 validation layers passed.
      Mobile bundle is in a deployable state.

If any layer fails, the script exits non-zero and skips remaining
layers — you fix the failure locally before involving a device or
TestFlight.

## The autonomous render loop

The whole reason web target exists in this repo is so the dev (me,
Claude, in agent mode) can render the app, click chips, and screenshot
the result without ever asking the human to scan a QR code on their
phone. The web build uses platform-specific shims:

* `MapScreen.jsx`     — native (iOS / Android), uses `react-native-maps` + Skia
* `MapScreen.web.jsx` — web stand-in, no real map, just the layer PNG as a stretched `<Image>`

Metro picks the right file via the `.web.jsx` suffix when
`platform=web`. App.js intentionally imports without a file extension
so this resolution works.

Workflow:

```bash
cd mobile
npx expo start --web --port 8083    # boots Metro on the web target
# then in a second terminal / via Claude Preview:
#   open http://localhost:8083
```

Then click chips, change composites, etc. — the same state machine
runs as on iOS/Android, the only thing missing is the actual
GPU-rendered map under the heatmap. That gap is acceptable for
catching UI/state bugs but is the reason a real iPhone / iOS
Simulator is still part of the loop for visual sign-off.

### What the validator can NOT catch (intentional gap)

Pure-bash testing on Windows / Linux can't simulate native rendering
behaviour. So the validator does NOT catch:

* Skia heatmap actually painting on top of a MapView
* `react-native-maps` region-change / pan-zoom behaviour
* Touch / gesture interactions
* Animation performance under real load

Those go into a Detox or Maestro suite later (which require a Mac +
iOS Simulator, or a connected Android emulator).

### Adding new tests

Tests live next to the code they test, in `__tests__/` directories:

    src/lib/__tests__/dataSource.test.js
    src/lib/__tests__/mapData.test.js

Anything in `**/__tests__/**/*.test.{js,jsx}` runs automatically. The
`jest-expo` preset handles RN + Skia + react-native-maps mocking, so
you can import them in tests without booting a device.

`npm run test:watch` reruns tests on save while you're iterating.

## Shipping to App Store (later)

Workflow when we're ready to graduate from Expo Go:

```bash
npm install -g eas-cli
eas login
eas build --platform ios     # produces a .ipa, upload to TestFlight
eas build --platform android # produces a .aab, upload to Play Console
```

Apple Developer account ($99/year) required for TestFlight + App
Store. Google Play Console ($25 one-time) for Play Store. Neither
is needed for Expo Go iteration — only when we publish.
