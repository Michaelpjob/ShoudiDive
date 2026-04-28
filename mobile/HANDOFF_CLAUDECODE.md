# ClaudeCode handoff - mobile map reset

This handoff packages the current Codex attempt and points the next
agent at a different native map path. The short version is: do not
keep pushing on the current single-image overlay approach. The app now
crashes when the user switches overlays on a real device, which means
the current `react-native-maps` `Overlay` path is not shippable.

## Current truth

- Repo: `Michaelpjob/ShoudiDive`
- Working branch: `codex/mobile-overlay-rollout`
- Reset checkpoint commit: `4ae21c4`
- Draft PR: `https://github.com/Michaelpjob/ShoudiDive/pull/4`
- Mobile app path: `mobile/`
- User test surface: Expo Go on a real iPhone

## Force-run command

If the next agent says they changed anything meaningful, make them run:

```bash
cd mobile
npm run validate:strict
```

That command now covers:

- the existing 7-layer local validation stack
- native `MapScreen` behavior tests
- source-contract tests banning the old brittle paths
- a live contract against the deployed manifest and raster assets

## What Codex already changed

### 1. Cross-platform validation

Windows PowerShell could not run the old bash-only validator. Codex
replaced:

- `mobile/package.json`
- new `mobile/scripts/validate.js`

`npm run validate` now passes on Windows and still runs the same 7
checks.

### 2. Mobile asset routing

Codex taught the mobile client to prefer pre-colored assets from the
manifest:

- `mobile/src/lib/dataSource.js`
- `mobile/src/lib/__tests__/dataSource.test.js`

The pipeline was also updated to emit colored PNGs and `mobile_url`
fields:

- new `pipeline/color_ramps.py`
- `pipeline/fetch.py`
- `pipeline/fetch_visibility.py`

### 3. Map overlay experiment

Codex replaced the Skia path with `react-native-maps` `Overlay`:

- `mobile/src/components/MapScreen.jsx`
- `mobile/src/lib/mapData.js`
- `mobile/src/lib/__tests__/mapData.test.js`

The intention was sound:

- stop doing client-side Skia pixel work
- let the map own geographic placement
- tighten the initial bbox fit so mobile matches web more closely

## What failed

The app now crashes when the user changes overlays on-device.

That likely means the current failure point is not the validation
stack, not the manifest fetch, and not the web fallback. The unstable
piece is the native layer swap path:

- `MapScreen.jsx` mounts `<Overlay key={pngUrl} image={{ uri: pngUrl }} />`
- changing layer/composite swaps the remote image backing the overlay
- Apple Maps / Expo Go appears to be unhappy with that lifecycle

No native crash log was captured from the device in this pass, so this
is still an inference, not a proven root cause. But from a project
direction standpoint, this is enough evidence to stop investing in the
single-raster overlay approach.

## Recommendation: abandon both Skia and single-image Overlay

Do not continue with:

- Skia `Canvas` + manual image colorization
- `react-native-maps` `Overlay` with a single remote PNG over the full bbox

Those two approaches fail in different ways:

- Skia: user saw the grey box / hard-to-debug native render path
- Overlay: user now hits a crash when changing overlays

Both depend on swapping one large remote image over the whole map. That
is exactly the part that has been fragile.

## New path forward: use `UrlTile`

Use tile overlays instead of one giant overlay image.

Why this is the right next move:

1. `react-native-maps` already supports `UrlTile` for remote imagery.
2. Tiles are the native shape map engines expect.
3. Projection stops being our problem. The map engine places tiles in
   Mercator instead of us stretching a bbox image by hand.
4. Layer switches become URL-template swaps, not "destroy one giant
   overlay image and mount another giant overlay image".
5. The pipeline can generate tiles from the already-colored PNG assets,
   so mobile keeps zero per-pixel work on device.

Reference material already in the checkout:

- `mobile/node_modules/react-native-maps/README.md`
  section "Using a custom Tile Overlay"
- `mobile/node_modules/react-native-maps/lib/MapUrlTile.d.ts`

## Proposed implementation plan

### Phase 1: server-side tiles for existing layers

Generate XYZ tiles for:

- `sst` 1d / 2d / 3d
- `chl` 1d / 2d / 3d
- `viz` now

Suggested output shape:

```text
/data/tiles/sst/1d/{z}/{x}/{y}.png
/data/tiles/sst/2d/{z}/{x}/{y}.png
/data/tiles/sst/3d/{z}/{x}/{y}.png
/data/tiles/chl/1d/{z}/{x}/{y}.png
/data/tiles/viz/now/{z}/{x}/{y}.png
```

Suggested zoom range for first pass:

- min z: 5
- max native z: 9

That is enough for the California coast use case without creating an
explosive number of files.

Important: render tiles from the already-colored assets, not from raw
client-side data decode logic. The mobile app should remain a thin
consumer.

### Phase 2: manifest support

Extend `manifest.json` windows with a tile template, for example:

```json
{
  "url": "/data/sst_2d.png",
  "mobile_url": "/data/sst_2d_color.png",
  "tile_url_template": "/data/tiles/sst/2d/{z}/{x}/{y}.png",
  "z_min": 5,
  "z_max_native": 9
}
```

### Phase 3: mobile client swap

Replace the current `Overlay` block in `mobile/src/components/MapScreen.jsx`
with `UrlTile`.

Target shape:

```jsx
import MapView, { Marker, UrlTile, PROVIDER_DEFAULT } from "react-native-maps";

{tileTemplate && (
  <UrlTile
    key={tileTemplate}
    urlTemplate={tileTemplate}
    flipY={false}
    maximumNativeZ={9}
    maximumZ={12}
    opacity={0.72}
  />
)}
```

Also:

- remove `Image.prefetch`
- remove `BBOX_BOUNDS` if no longer needed
- keep the current `fitToCoordinates(BBOX_RING, ...)`
- consider `setMapBoundaries()` to stop the user from panning far
  outside the supported area

### Phase 4: data source API

Add a tile resolver in `mobile/src/lib/dataSource.js`, for example:

- `getLayerTileUrlTemplate(layer, composite)`

Keep the existing PNG resolver only if it is still useful for a future
fallback.

### Phase 5: device validation

Once `UrlTile` is wired:

1. Launch in Expo Go
2. Switch Temp 1d -> 2d -> 3d repeatedly
3. Switch Temp -> Chl -> Viz repeatedly
4. Pan and zoom near:
   - Monterey
   - Channel Islands
   - La Jolla / Coronados
5. Confirm:
   - no crash
   - no grey box
   - tiles align to coastline
   - no huge initial padding around the bbox

## Why not WMSTile first?

`WMSTile` is a reasonable backup, but `UrlTile` is the simpler next
step because:

- the data is already rasterized
- Cloudflare Pages can serve static tile files directly
- no tile server process is required
- client config is simpler

If `UrlTile` still proves unstable on Apple Maps, then the next
escalation path is:

1. `WMSTile` against a tile service endpoint
2. or a full map stack change later

But `UrlTile` is the right immediate bet.

## Suggested concrete next edits

1. Add a tile-render helper in `pipeline/`
2. Extend manifest windows with `tile_url_template`
3. Replace `Overlay` with `UrlTile` in `MapScreen.jsx`
4. Add a small Jest surface in mobile for tile template resolution
5. Re-run `npm run validate`
6. Real device test in Expo Go

## Files to read first

- `mobile/src/components/MapScreen.jsx`
- `mobile/src/lib/dataSource.js`
- `mobile/src/lib/mapData.js`
- `pipeline/color_ramps.py`
- `pipeline/fetch.py`
- `pipeline/fetch_visibility.py`
- `mobile/node_modules/react-native-maps/README.md`
- `mobile/node_modules/react-native-maps/lib/MapUrlTile.d.ts`

## Notes about rollout status

The branch and PR exist, but they should be treated as a packaging
checkpoint, not a merge-ready solution.

What is trustworthy in that branch:

- Windows validator
- pipeline colored-asset support
- manifest/mobile URL plumbing
- tighter bbox framing logic

What is not trustworthy:

- the current `Overlay`-based native renderer

## Bottom line

The best next agent move is not "debug this Overlay crash harder".
It is "switch the mobile renderer to tile overlays so map projection
and image lifecycle are handled by the map engine instead of one giant
swapped raster".
