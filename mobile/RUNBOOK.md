# Mobile build & run — RUNBOOK (iOS, from Windows)

This is the canonical "get the app onto your iPhone" guide. It **replaces** the old
Expo Go instructions in `README.md` / `HANDOFF.md`.

> **Why Expo Go does not work here:** the app uses `@shopify/react-native-skia`
> (and the new architecture), which is not in the Expo Go runtime. Launching in
> Expo Go is what produced the "grey box" heatmap. The app must run as a
> **development build** — a custom dev client with the native modules baked in.
> EAS builds that in the cloud on macOS workers, so your **Windows machine never
> needs Xcode or a Mac.**

---

## One-time setup (you — ~30–45 min, mostly waiting on the cloud build)

**Prerequisite:** an **Apple Developer Program** membership ($99/yr) —
enroll at <https://developer.apple.com/programs/>. Approval is usually
minutes to a few hours.

Run everything from the `mobile/` directory:

```bash
# 1. Install dependencies, then lock every native package to the SDK version
npm install
npx expo install --fix

# 2. Install the EAS CLI (or prefix every command below with `npx`)
npm install -g eas-cli

# 3. Log into Expo (creates a free account if you don't have one)
eas login

# 4. Create the EAS project — writes the projectId into app.json
eas init

# 5. Register YOUR iPhone for ad-hoc development builds
eas device:create
#    → choose "Website", open the printed link ON YOUR IPHONE,
#      and install the provisioning profile it offers.

# 6. Build the iOS development client in the cloud (~10–20 min)
eas build --profile development --platform ios
#    → first run asks to log into your Apple account (2FA) and offers to
#      generate signing credentials — say YES; EAS manages them for you.

# 7. When it finishes, EAS prints a QR code / URL.
#    Open it ON YOUR IPHONE and install the dev build.
#    (Then trust it: iOS Settings → General → VPN & Device Management.)
```

## Daily loop (the seamless part — as fast as web)

From `mobile/`:

```bash
npx expo start --dev-client --tunnel
```

Open the **dev build** app on your iPhone and scan the QR. The app loads with
**hot reload** — JS/UI/logic edits appear instantly. `--tunnel` avoids LAN issues
when your PC and phone aren't on a friendly network.

## When do I need a NEW build vs. just hot reload?

| Change | What to run |
|---|---|
| Screens, logic, styling, data, colors | Nothing — hot reload (or `eas update` to share) |
| Add/remove a **native** library, change `app.json` native config, permissions, icons/splash | Rebuild: `eas build --profile development --platform ios` |

You rebuild rarely. 95% of work is the instant hot-reload loop.

## Putting it on other testers' phones (TestFlight)

```bash
eas build --profile preview --platform ios      # or: production
eas submit --platform ios
```

Then add testers in **App Store Connect → TestFlight**.

## Troubleshooting

- `npx expo install --check` flags a version mismatch → run `npx expo install --fix`.
- Build fails on credentials → `eas credentials` to inspect/regenerate.
- The grey-box heatmap: expected to resolve on a real dev build (Skia is now baked
  in). If it persists, the fix is to move colorization server-side (pre-colored RGBA
  PNGs) or to the planned Mapbox raster overlay — we diagnose that live on-device once
  the dev build is running.

## Why this is safe on Windows

`eas build` runs entirely in Expo's cloud. Your machine only ever runs **Metro**
(the JS dev server) and the EAS CLI. There is no local iOS toolchain, no Xcode,
no Mac. The only Apple-side requirement is the $99/yr membership for device
signing — unavoidable for any iPhone install.
