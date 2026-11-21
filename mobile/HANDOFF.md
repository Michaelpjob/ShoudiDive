# Mobile app handoff — for the next agent

> **⚠️ UPDATE (2026-06-17) — the launch path in this doc is superseded.**
> Expo Go cannot run this app (`@shopify/react-native-skia` isn't in the Go runtime —
> that's the "grey box"). Use the **EAS development build** instead:
> see **[`RUNBOOK.md`](RUNBOOK.md)**. `eas.json` is committed and `expo-dev-client` is
> now a dependency. Ignore every "Expo Go / scan QR in Expo Go" step below. The
> grey-box item (#1) is expected to resolve on a real dev build (Skia baked in), or
> when colorization moves server-side / to the planned Mapbox raster overlay.

You're picking up the React Native (Expo) port of ShouldIDive. The
prior agent (Claude in agent mode, Windows host) shipped the
scaffolding + a 7-layer validation pipeline but did NOT successfully
verify the heatmap renders with proper colours on a real iPhone.
That's the open work.

This document is self-contained — read top to bottom and you should
be able to make progress without reconstructing chat history.

---

## TL;DR

* **Repo**: `Michaelpjob/ShoudiDive`, branch `main`. Mobile app lives
  at `mobile/` as a sibling to `pipeline/` and the existing web app's
  `src/`. The pipeline + web app are fully deployed and stable; the
  mobile app is dev-only.
* **State**: bundle compiles clean, runtime mounts on web (Layer 6),
  12/12 visual criteria pass on web target (Layer 7). Native render
  on actual iPhone is the unverified surface.
* **Last user feedback**: heatmap shows as a "grey box" on their
  iPhone after Skia colorize was wired in. Either the colorize isn't
  loading, the SkImage round-trip is breaking, or the user reloaded
  before the new bundle reached Expo Go. Not diagnosed.
* **The user is on Windows**. iOS Simulator is unavailable. They
  use **Expo Go** on a real iPhone for verification. Treat their
  time as expensive — run `npm run validate` before every push and
  inspect the screenshots yourself in `mobile/test-output/`.

---

## What works (verified)

| Surface | State | Evidence |
|---|---|---|
| Project scaffold | green | `npx create-expo-app` blank template, SDK 54 |
| All deps SDK-aligned | green | Layer 1 (`npx expo install --check`) clean |
| Project config | green | Layer 2 (`expo-doctor`) 17/17 |
| iOS bundle compile | green | Layer 3 (1255 modules, 3.61 MB Hermes bytecode) |
| Web bundle compile | green | Layer 4 (751 modules, 1.49 MB) |
| Data-layer logic | green | Layer 5 (33 jest tests including LUT correctness) |
| Web runtime mount | green | Layer 6 (Puppeteer mounts the app, no JS errors) |
| Layout + state | green | Layer 7 (12/12 visual criteria, screenshots in `test-output/`) |
| Native iOS render | **UNVERIFIED** | only validation path: real iPhone via Expo Go |

---

## What doesn't / what's unfinished

1. **Heatmap colours on real iPhone.** User reports "grey box". The
   web target uses a plain `<Image>` (no colorize) intentionally;
   the native target uses `colorizeImage()` from `src/lib/colors.js`
   which feeds a Skia `<Image>`. The LUT is unit-tested correct.
   Either the Skia readPixels/MakeImage round-trip on iOS Skia
   isn't doing what we expect, or the user reloaded before the
   bundle propagated through Metro. Diagnose with a real device.

2. **Wind + Swell layers.** Chips are present but greyed/disabled
   ("soon" subtitle). The pipeline's `wind5d` + `swell5d` data is
   live at `https://shouldidive.com/data/manifest.json` but the
   mobile app only fetches the static composites (sst/chl/viz).

3. **Tap-to-pin value readout.** Tapping a coordinate should show
   the predicted value at that lat/lng. Not implemented. Web app's
   `bilinear()` in `src/lib/dataSource.js` is the reference.

4. **Saved-spot list screen.** `SAVED_SPOTS` is rendered as Markers
   on the map but no per-spot value cards / scrollable list.

5. **Push notifications.** Not started. The whole pitch for going
   native (vs. PWA) included "good viz day alert" pushes; that's
   future work.

6. **Map projection accuracy.** Skia heatmap is positioned via
   equirectangular math (lng→x linearly, lat→y linearly) but Apple
   Maps uses Web Mercator. ~5% distortion at lat 35°N. If the
   heatmap visibly drifts from the coastline at high zoom, switch
   to Mercator math (~10 lines).

---

## Architecture decisions (and why)

### Repo layout

`mobile/` is a sibling to `pipeline/` and the existing web app's
`src/`, NOT a separate repo. Reasons:

* The mobile app consumes `pipeline/` outputs via the deployed
  Cloudflare Pages CDN. Same source of truth.
* Web app code in `src/` is reference material — color ramps, zone
  classifier, bilinear lookup — but those are PORTED, not imported.
  Direct reuse would couple the two too tightly.
* CI is per-directory; mobile changes don't trigger web deploys.

### Map provider: Apple Maps (`PROVIDER_DEFAULT`)

* Free, no API key
* Native on iOS, Google Maps on Android (also free up to free-tier limits)
* `react-native-maps` ships first-class support
* **NOT Mapbox**: would have given branded styling but adds API-key
  setup that's not justified for v1

### Heatmap rendering: Skia overlay synced to `onRegionChange`

The first attempt used `react-native-maps`'s built-in `<Overlay>`.
That component is broken on Apple Maps with remote URIs — `onLoad`
never fires. Replaced with a Skia `<Canvas>` absolutely positioned
over the MapView, redrawing on every region change. Equirectangular
projection from region.latitude/longitude/deltas to canvas pixels.

### Color ramp pipeline

Pipeline writes mode='L' grayscale PNGs (R = encoded value, 0 = no
data). Each layer has a different ramp:

* SST: linear in 9–25 °C → blue→cyan→yellow→red
* Chl: log10 in 0.05–20 mg/m³ → deep blue (clear) → green (productive)
* Viz: linear in 0–80 ft → orange (poor) → cyan/blue (excellent)

`mobile/src/lib/colors.js` builds a 256-entry LUT once per layer
(decode px→value→ramp), then `colorizeImage(skImage, layer)` does
an O(1) lookup per pixel and returns a colourised SkImage.

The web fallback (`MapScreen.web.jsx`) deliberately renders the
RAW grayscale PNG via a plain `<Image>`. Skia on web requires
CanvasKit (WASM) setup we skipped — the web target's job is layout
validation, not pixel accuracy. **Don't try to add Skia to the web
fallback unless you have a clean reason; we tried and it failed
(Layer 6 caught `MakeImageFromEncoded` undefined).**

### Platform-specific files

`MapScreen.jsx`     — native (iOS / Android)
`MapScreen.web.jsx` — web (no MapView, no Skia)

`App.js` imports `./src/components/MapScreen` **without** the file
extension so Metro picks the right platform variant. Keep the
extension off the import statement.

---

## Validation contract — `npm run validate`

The 7-layer pipeline is the single source of truth. Run it before
EVERY push.

```bash
cd mobile
npm run validate
```

```
▶ Layer 1/7 — expo install --check         SDK / package alignment
▶ Layer 2/7 — expo-doctor                  17 project-health checks
▶ Layer 3/7 — expo export ios              iOS Hermes bundle compile
▶ Layer 4/7 — expo export web              web fallback compile
▶ Layer 5/7 — jest                         33 unit tests (data + colors)
▶ Layer 6/7 — runtime smoke (Puppeteer)    mounts the app, watches errors
▶ Layer 7/7 — visual criteria              12 assertions + screenshots
```

A clean run is **all green, ~60 seconds**. Failures point at the
specific layer + screenshot file.

### What it catches

* Babel-config regressions (the worklets plugin missing → Layer 3 or 6)
* SDK version drift (Layer 1)
* Import path errors (Layer 3 or 4)
* Logic regressions in the data layer + LUT (Layer 5)
* Runtime errors during mount (Layer 6) — caught the
  reanimated-not-installed crash
* Layout / state transitions (Layer 7)

### What it CAN'T catch

* Native rendering on iOS (Skia colormap actually painting on top
  of MKMapView). That requires a real device or iOS Simulator.
* Gesture feel (pinch, rotate, marker tap)
* iOS-specific layout (safe-area, dynamic island)

The user is on Windows so iOS Simulator is off the table from the
local machine. Two ways to close this gap if it matters:

* **EAS Build → TestFlight** ($99/yr Apple Developer). Real device
  testing with crash logs.
* **Maestro Cloud** or similar paid device farm. Programmatic E2E.
* Borrow a Mac and run `npx expo run:ios` against the Simulator.

---

## Gotchas (things the prior agent burned cycles on)

These are real, documented in commit history. Don't repeat them.

### 1. Reanimated v4 + Expo SDK 54 has tight version requirements

* `react-native-reanimated@~4.1.1` (expo install picks this)
* `react-native-worklets` MUST be a direct dep (expo-doctor catches
  this in Layer 2)
* Babel plugin path is `react-native-worklets/plugin` (renamed
  from `react-native-reanimated/plugin` in v3 → v4)
* `babel.config.js` MUST list it last in `plugins`

### 2. NEVER use raw `npm install` for SDK packages

Use `npx expo install <package>`. It picks SDK-aligned versions
from Expo's `bundledNativeModules.json`. The prior agent installed
`babel-preset-expo@55` via raw `npm`, which silently broke the
worklets transform pipeline because v55 is for a future SDK. Layer 1
catches this but only AFTER the install — using `expo install`
prevents it.

### 3. Skia Canvas REQUIRES reanimated at runtime

Even though `@shopify/react-native-skia@2.x` declares reanimated as
an "optional" peer dep, the `<Canvas>` component uses a lazy proxy
that only fails when its first render happens. So a removed
reanimated passes Layer 3 (compile) but crashes on mount. Layer 6
(runtime smoke) catches this. The prior agent removed reanimated
once thinking it was unused, then had to put it back.

### 4. `react-native-maps` doesn't bundle for web

It imports `codegenNativeCommands` which Metro can't resolve on the
web platform. The `.web.jsx` platform variant exists for this; do
NOT try to make `react-native-maps` work on web.

### 5. `<Overlay>` from `react-native-maps` is broken on Apple Maps

`onLoad` never fires for remote URIs. We use a Skia overlay synced
to `onRegionChange` instead. Don't revert to `<Overlay>` thinking it
was a simpler path.

### 6. Visual tests on web are layout-only

The web target uses plain `<Image>` (not Skia) for the heatmap.
Test #12 verifies the PNG fetched + loaded; it does NOT verify
colours. Don't write a "is the heatmap colourful" test on the web
target — the web target intentionally renders grayscale. Write
those checks at the LUT unit-test layer or against a real device.

---

## Recommended first-hour workflow

1. **Pull + install** (~5 min)
   ```bash
   git clone https://github.com/Michaelpjob/ShoudiDive.git
   cd ShoudiDive/mobile
   npm install
   ```

2. **Run the validation pipeline** (~60 sec)
   ```bash
   npm run validate
   ```
   Confirm all 7 layers green. If any fails on a fresh checkout,
   it's an environment issue (Node version, Puppeteer Chromium
   install, etc.) — fix locally before any code change.

3. **Inspect the latest visual screenshots** (~3 min)
   ```bash
   ls test-output/         # find latest UTC-timestamped folder
   ```
   Open every PNG, compare to the criteria in
   `scripts/visual-tests.js`. This is your baseline; your changes
   should not regress these.

4. **Render the app yourself in Claude Preview / a browser**
   ```bash
   npx expo start --web --port 8083
   # then open http://localhost:8083 in Chrome
   ```
   Click chips, switch composites, observe state. Builds the
   muscle memory for what "right" looks like before touching native.

5. **Then connect a real iPhone via Expo Go**
   ```bash
   npx expo start --tunnel    # tunnel mode bypasses LAN issues
   ```
   Install Expo Go from the App Store; scan the QR. **Tunnel
   mode is essential** if you're not co-located with the user's
   network.

6. **Pick the next task from the "What's unfinished" list** above.
   Recommended order:
   * Diagnose grey-heatmap on iPhone (highest user value)
   * Tap-to-pin readout (highest leverage feature)
   * Wind + swell layers
   * Saved-spot list screen
   * Push notifications

---

## Key files

| Path | Purpose |
|---|---|
| `mobile/App.js` | Root, mounts MapScreen via GestureHandlerRootView |
| `mobile/app.json` | Expo config — bundle id, splash, plugins |
| `mobile/babel.config.js` | Babel preset + worklets plugin |
| `mobile/package.json` | Deps (don't `npm install` SDK packages directly) |
| `mobile/scripts/validate.sh` | The 7-layer contract |
| `mobile/scripts/smoke-web.js` | Layer 6 — Puppeteer mount check |
| `mobile/scripts/visual-tests.js` | Layer 7 — 12 visual criteria |
| `mobile/src/components/MapScreen.jsx` | Native screen — MapView + Skia heatmap |
| `mobile/src/components/MapScreen.web.jsx` | Web fallback — Image only, no Skia |
| `mobile/src/lib/mapData.js` | BBOX, BBOX_REGION, SAVED_SPOTS |
| `mobile/src/lib/dataSource.js` | Manifest fetcher, layer URL resolver |
| `mobile/src/lib/colors.js` | Color ramps + Skia colorize |
| `mobile/src/lib/__tests__/*.test.js` | 33 unit tests |
| `mobile/test-output/<utc>/` | Latest visual test screenshots |
| `mobile/README.md` | User-facing dev guide (Expo Go workflow) |

---

## Reference: web app code worth reading

These are NOT to be imported, just read for the algorithms:

| Path | Why |
|---|---|
| `src/lib/mapData.js` | SST + Chl ramps, BBOX, project / unproject |
| `src/lib/dataSource.js` | Manifest decoder, bilinear interpolation, getSST etc. |
| `src/components/DataOverlay.jsx` | Viz + Swell ramps, full canvas render path |
| `pipeline/fetch_visibility.py` | Pipeline that produces the PNGs the app consumes |
| `public/data/manifest.json` | Live deployed manifest (this is the API) |

---

## Reference: pipeline backlog

`pipeline/TODO.md` has two queued PRs (PR1 chl freshness fix, PR2
blended chl source). Those are SERVER-SIDE work — they fix the model
predicting visibility too high on stale-chl days. Not part of the
mobile app handoff, but the user may also have queued you on those.

---

## Honest framing

The prior agent had real technical bottlenecks:

* Couldn't run iOS Simulator from Windows
* Couldn't render the actual app on the user's phone autonomously
* Made several "try this and tell me what happens" requests that
  ate the user's time

The validation pipeline (`npm run validate`) is the prior agent's
honest answer to those bottlenecks: catch every catchable bug
locally before pushing. **Use it.** It works. Most failure modes
the prior agent hit were caught by it AFTER it was built; the
remaining failures shipped because the prior agent skipped running
it before pushing.

The unfinished native-render verification is the actual gap. If you
have a Mac, that gap closes quickly. If you don't, treat the user's
iPhone reload + screenshot loop as the most expensive resource in
the project and minimize calls to it.

Good luck.
