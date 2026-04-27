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
    ├── package.json            — RN + Expo deps
    └── src/
        ├── components/
        │   └── MapScreen.jsx   — the only screen for now
        └── lib/
            ├── mapData.js      — BBOX, BBOX_REGION, SAVED_SPOTS
            └── dataSource.js   — manifest fetcher, layer URL resolver

The data layer (`src/lib/dataSource.js`) is intentionally minimal in
v1: it fetches `manifest.json` and resolves URLs. We DON'T decode
PNGs into Float32Arrays in JS like the web frontend does — the
native map handles the PNG render itself with no per-pixel JS work.
Tooltip lookups (when we add them) will use a separate per-cell
endpoint or a small grid JSON, not in-JS PNG decoding.

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
