# Spot Detail View — Implementation Handover (Phase 1B)

**Audience:** Claude Code (or any coding agent)
**Suggested repo location:** `docs/spot-detail-handover.md`
**Sibling doc:** `docs/kelp-mvp-handover.md` (Phase 1A — the wide-map kelp pill, builds independently)
**Status:** Phase 1B = three-spot pilot, ready to build. Roadmap items (more spots, substrate, in-place transition) are Appendix A — do **not** build them without an explicit go-ahead.

---

## 1. What you're building

A breakout **Spot Detail** view: when a user clicks a saved-spot pin that has a
pre-computed bundle, they get a "View detailed map" affordance that opens a
focused, deep-zoom view of that single dive spot showing:

- **High-resolution bathymetry** (NOAA CUDEM / coastal DEM, ~3–10 m vs the
  regional layer's ~1 km)
- **Depth contours** derived from that DEM
- **Detailed coastline** clipped from the existing high-res OSM coastline
- **Kelp bed polygons** clipped from `kelp-beds.geojson` (produced by Phase 1A)
- **MPA segments** clipped from `mpa-boundaries.geojson`
- **Current regional conditions** at the spot (SST / wind / swell / viz) as a
  textual readout — sampled from `dataSource.js`, not re-rendered

The view ships in a dedicated `SpotDetailView` component (overlay/modal on
desktop, full-screen sheet on mobile). The wide-view map is **unchanged**.

### Three pilot spots

| id | name | rough centre | bundle radius |
|---|---|---|---|
| `lajolla` | La Jolla | -117.28, 32.85 | 4 km |
| `catalina` | Catalina | -118.45, 33.39 | 8 km |
| `monterey` | Monterey | -121.92, 36.62 | 6 km |

All three are already in `REGION_SAVED_SPOTS.ca` in `src/lib/mapData.js`. They
are deliberately chosen for variation: mainland-cove vs Channel-Island shelf vs
NorCal kelp-heavy coast. Any schema assumption that holds across all three is
likely to generalise; anything that breaks on one is something the schema
needs to handle before we fan out to more spots.

### Why this shape

Per-spot pre-computed bundles solve "super zoom" without building a regional
tile pyramid or migrating to MapLibre. Each bundle is ~200–500 KB of static
assets. The frontend loads one bundle and renders at native resolution — no
runtime LOD, no tile math, no viewport-driven streaming. It is the same
architectural pattern as the rest of the pipeline: **pre-compute in Python,
ship static files, frontend reads what's there.**

This pattern also dodges the architectural fork from the earlier review (stay
bespoke SVG vs migrate to MapLibre GL). Spot bundles render fine on the
existing SVG primitives. If a future feature genuinely needs continuous
deep-zoom anywhere along the coast, that decision can be revisited then.

---

## 2. Decisions already made — do not re-litigate

| Decision | Choice |
|---|---|
| Render path | New `SpotDetailView` component, opens as a modal/sheet over the existing map |
| Wide-view map | **Untouched** — `MAX_ZOOM = 8` stays |
| Pilot spots | `lajolla`, `catalina`, `monterey` (CA region) — exactly three |
| Bundle contents (MVP) | Bathy PNG + contours GeoJSON + clipped coastline + clipped kelp + clipped MPA + manifest + sampled conditions |
| Excluded from MVP | Substrate polygons, dive-spot POIs (parking / entries), bottom-type labels, in-place super-zoom (Flavor 1). All in Appendix A. |
| Conditions inside view | Textual readout sampled from `dataSource.js`. Do **not** re-render regional SST/wind/swell rasters at this zoom — they mush. |
| Data location | `public/data/spots/<id>/...` + `public/data/spots/index.json` |
| Spot bundle availability | Pipeline writes `index.json` listing built spots; frontend reads it; no duplicated registry |
| Mobile | Full-screen sheet; reuse the modal close patterns from `MpaPopup` |
| MapLibre | Not in scope. Stay bespoke SVG. |

---

## 3. Pattern references you'll clone

Read these before writing:

- `pipeline/fetch_mpa.py` — bbox clipping + GeoJSON-write pattern
- `pipeline/fetch_bathy.py` — example of a one-shot raster fetcher writing a
  PNG + a JSON sidecar with range/encoding
- `pipeline/fetch_coastline.py` — Overpass OSM fetch + shapely clip
- `src/components/MpaLayer.jsx` — `ringToPath` / `geometryToPath` / `project()`
  / `useMemo` pre-projection — re-use these primitives in the SpotDetailView
- `src/components/BathyLayer.jsx` — `loadBathyFeatures()` shape (single shared
  promise, region-aware path via `dataPath()`)
- Inline `MpaPopup` in `src/App.jsx` (~line 2215) — modal pattern + Escape-to-
  close (clone, do not use the orphaned `src/components/MpaPopup.jsx`)

---

## 4. Phase 1B — task breakdown

### Task A — Extend the spot registry with bundle metadata

In `src/lib/mapData.js`, alongside `REGION_SAVED_SPOTS`, add the bundle radius
per pilot spot. Keep it inline and minimal — do **not** restructure the rest of
the spot entries:

```js
// Spots that have a pre-computed Spot Detail bundle in
// public/data/spots/<id>/. Authoritative list is written by the pipeline at
// public/data/spots/index.json — this radius is only used by the bundle
// builder (Python side) to define the per-spot bbox. Keep the constant
// here so the frontend never has to look up a radius.
export const SPOT_BUNDLE_RADIUS_KM = {
  lajolla:  4,
  catalina: 8,
  monterey: 6,
};
```

The frontend should not gate the "View detailed map" button on this constant —
instead it should consult `public/data/spots/index.json` at runtime (Task C
below). The radius lives here only so the Python builder has a single source.

### Task B — `pipeline/build_spot_bundles.py`

A new orchestrator script. For each pilot spot id, it:

1. Looks up the spot's centre lng/lat (mirror the JS `REGION_SAVED_SPOTS.ca`
   list — duplicate the three centres in Python, do not import from JS) and
   its radius from a Python copy of `SPOT_BUNDLE_RADIUS_KM`.
2. Computes a square bbox around the centre (`radius_km` → degrees via a
   simple lat/lng → meters conversion; reuse `pipeline/regions/_region.py`
   helpers if present, else add a small `degrees_per_km` helper inline).
3. Fetches a **high-resolution coastal DEM** clipped to the bbox. Recommended
   source: **NOAA NCEI CUDEM** (Continuously Updated DEM, 1/9 arc-second ≈
   ~3 m). The CUDEM tiles for California are available via NOAA's THREDDS
   server (`https://www.ngdc.noaa.gov/thredds/...`) and via direct GeoTIFF /
   NetCDF download. Pick whichever is reliable on agent-build time; document
   the URL you used in a comment so the next refresh isn't a treasure hunt.
   Encode as a grayscale PNG (`bathy.png`) with the same encoding contract
   `fetch_bathy.py` already uses (0 = NaN/land, 1..255 linear over a depth
   range; record the range in the bundle manifest).
4. Generates **depth contours** from the DEM with `scipy.ndimage` or
   `skimage.measure.find_contours`, then projects pixel coords back to
   lng/lat. Use these intervals (deeper waters get sparser lines):

   - 1 m intervals from 0–10 m
   - 5 m intervals from 10–50 m
   - 25 m intervals deeper than 50 m

   Write as `contours.geojson`. Round coords to 5 decimals.
5. Clips `public/data/coastline.geojson` to the spot bbox → `coastline.geojson`.
6. Clips `public/data/kelp-beds.geojson` (from Phase 1A) to the spot bbox →
   `kelp.geojson`. If kelp Phase 1A hasn't shipped yet, skip this file — the
   frontend treats it as optional.
7. Clips `public/data/mpa-boundaries.geojson` to the spot bbox → `mpa.geojson`.
8. Writes `bundle.json` — the manifest tying it all together:

   ```json
   {
     "id": "lajolla",
     "name": "La Jolla",
     "centre": { "lng": -117.28, "lat": 32.85 },
     "bbox":   { "lng_min": -117.31, "lat_min": 32.82,
                 "lng_max": -117.25, "lat_max": 32.88 },
     "generated_at": "2026-05-26T00:00:00Z",
     "layers": {
       "bathy":     { "url": "bathy.png", "width": 480, "height": 480,
                      "depth_range_m": [0, 250], "encoding": "linear_8bit" },
       "contours":  { "url": "contours.geojson", "intervals_m": [1, 5, 25] },
       "coastline": { "url": "coastline.geojson" },
       "kelp":      { "url": "kelp.geojson", "features": 12 },
       "mpa":       { "url": "mpa.geojson",  "features": 3 }
     },
     "sources": {
       "bathy": "NOAA NCEI CUDEM 1/9 arc-sec",
       "coastline": "OSM natural=coastline via Overpass",
       "kelp": "CDFW Administrative Kelp Beds ds3135",
       "mpa": "CDFW MPA ds582"
     }
   }
   ```

9. Writes `public/data/spots/index.json` at the end of the run:

   ```json
   { "spots": ["lajolla", "catalina", "monterey"], "generated_at": "..." }
   ```

Use the same `requests` + `shapely` + `Pillow` + `numpy` stack the existing
pipeline uses (all in `pipeline/requirements.txt`). `scipy` is also already
present; add `scikit-image` to `requirements.txt` only if you use it for
contouring (otherwise use `scipy.ndimage` + a manual marching-squares).

**Skip-if-exists semantics.** Each per-spot subdirectory should be idempotent:
if `bundle.json` exists and is fresher than 30 days, skip rebuilding that spot
unless `--force` is passed. This matches `fetch_bathy.py`'s idempotent pattern
and keeps CI cheap on the daily refresh.

**Region awareness.** For MVP all three spots are CA. Write to
`active_region().data_output_dir(ROOT) / "spots" / spot_id / ...`. PNW/baja/
tropical do not yet have spot bundles; the script should no-op gracefully on
those regions (empty `index.json`).

### Task C — `src/components/SpotDetailView.jsx`

A new top-level overlay component. Props: `{ spot, onClose }` where `spot` is
the `SAVED_SPOTS` entry (`{ id, name, lng, lat }`).

Responsibilities:

1. **Load the bundle.** On mount, fetch `dataPath("/data/spots/" + spot.id +
   "/bundle.json")`. Single shared per-id promise cache, mirroring
   `loadMpaBoundaries`. Then in parallel fetch the referenced GeoJSON files.
   The bathy PNG is loaded as a regular `<image>` so the browser handles it.
2. **Render its own SVG.** Independent viewBox sized to the bundle bbox. Do
   **not** reuse the wide-view `project()` (which is locked to the regional
   BBOX). Add a small helper to `src/lib/mapData.js`:

   ```js
   export function projectInBbox(bbox, lng, lat, w, h) {
     const x = (lng - bbox.lng_min) / (bbox.lng_max - bbox.lng_min) * w;
     const y = (bbox.lat_max - lat)  / (bbox.lat_max - bbox.lat_min) * h;
     return [x, y];
   }
   ```

   Use it inside SpotDetailView for everything. The wide-view `project()`
   stays unchanged.
3. **Layer rendering order** (bottom to top):
   1. Bathy PNG as `<image>` covering the full viewBox extent.
   2. Contour lines as `<path>` strokes (colour-graded by depth, sparser
      strokes at deeper intervals so the visualisation reads cleanly).
   3. Coastline as filled `<path>` polygons (land mask on top of bathy —
      reuse `geometryToPath` from MpaLayer.jsx, factored out if you like, or
      copied inline).
   4. MPA segments as `<path>` polygons reusing `styleForType` from
      `MpaLayer.jsx`.
   5. Kelp polygons as `<path>` polygons reusing `styleForStatus` from
      `KelpLayer.jsx` (Phase 1A). If kelp hasn't shipped, omit gracefully.
   6. A pin at the spot centre, same style as the wide-view saved-spot pin.
4. **Pan + zoom inside the view.** Reimplement the same viewBox pan/zoom
   pattern `DesktopView` uses (`vb` state, `clampVb`, wheel-to-zoom,
   touch-pinch). It is acceptable to extract `useMapViewport` as a custom
   hook here and use it in both places — but if doing so balloons scope,
   inline it for now and leave a `// TODO: hoist to shared hook` comment.
   `MAX_ZOOM` inside the spot view can be much higher (e.g. 16); the bundle
   bbox is small enough that the pixel density supports it.
5. **Conditions readout panel.** A small fixed panel (corner of the view)
   showing current SST, wind, swell, viz at `(spot.lng, spot.lat)` sampled
   via the existing `getSST`, `getWindSpeed`, `getSwell*`, `getVizFt`
   helpers in `src/lib/dataSource.js`. Read-only; no time slider inside
   the spot view for MVP.
6. **Header + footer chrome.** Spot name as title, close button (Escape +
   click), a "Back to map" link, and a small `Sources:` footer listing the
   bundle's `sources` keys + the disclaimer text from Phase 1A's kelp
   handover ("Kelp bed zones are management / reference boundaries...").
7. **Layer toggles.** Buttons to show/hide each layer (Bathy / Contours /
   Coastline / Kelp / MPA). State lives in the component (useState); not
   persisted to prefs for MVP.
8. **Analytics.** Fire `track("spot_detail_open", { id: spot.id })` on mount,
   `track("spot_detail_close", { id: spot.id })` on close, and
   `track("spot_detail_layer_toggle", { id, layer, on })` on toggles. **You
   must allowlist these three names** in `functions/api/analytics/event.js`'s
   `ALLOWED_NAMES` set — otherwise they get silently dropped server-side.

### Task D — Trigger the view from the existing spot UX

In `src/App.jsx` `DesktopView`:

1. Add state: `const [spotDetailFor, setSpotDetailFor] = useState(null);`
2. Fetch `public/data/spots/index.json` once on mount (single shared promise,
   same pattern). Store the set of spot ids that have bundles, e.g.
   `const [bundledSpots, setBundledSpots] = useState(new Set());`.
3. In the existing saved-spot info panel / popup (wherever the click-on-pin
   shows spot detail today — search for `activeSpot` usage), if
   `bundledSpots.has(activeSpot)`, render a button:

   ```jsx
   <button
     className="spot-detail-open"
     onClick={() => setSpotDetailFor(SAVED_SPOTS.find(s => s.id === activeSpot))}
   >
     View detailed map →
   </button>
   ```

4. At the bottom of `DesktopView`'s return (just before the `<MobileSheet>`
   block, so it renders above everything else):

   ```jsx
   {spotDetailFor && (
     <SpotDetailView
       spot={spotDetailFor}
       onClose={() => setSpotDetailFor(null)}
     />
   )}
   ```

5. **Mobile.** Pass `spotDetailFor` / `setSpotDetailFor` into `MobileSheet`
   and add the same button to its saved-spot panel. The SpotDetailView itself
   is full-screen on both desktop and mobile, so the same component handles
   both viewports — only the **launch button** needs duplicating.

### Task E — Pipeline workflow integration

Bundles rarely change (the underlying DEM is decadal; coastline is yearly).
Add a step to `.github/workflows/refresh-ca-data.yml` after the **Fetch
coastline** step:

```yaml
- name: Build Spot Detail bundles (idempotent, ~30-day TTL)
  continue-on-error: true
  timeout-minutes: 15
  run: python pipeline/build_spot_bundles.py
```

`continue-on-error: true` matches the other reference-layer steps. The script's
own 30-day skip-if-fresh check keeps the daily cron cheap (it'll do real work
once a month). The existing **Commit refreshed data** step already does
`git add public/data/` and picks up the new files automatically.

> ⚠️ Bundle data adds to the existing git-bloat pressure. Three spots at ~300
> KB each is fine. Past ~20 spots the data needs to move off git (the
> "data-out-of-git" track from the staged remediation plan — same forcing
> function as a tile pyramid would have been). Note this in your PR
> description but do not act on it.

### Task F — Tests

Add three lightweight assertions to keep the new wiring guarded — consistent
with the repo's `appFeatureContracts.test.js` culture:

1. In `tests/appFeatureContracts.test.js`, assert that
   `src/components/SpotDetailView.jsx` exists and exports a default component,
   and that `src/App.jsx` imports it.
2. In a new `tests/spotBundleContract.test.js`, assert the shape of a built
   `bundle.json` (required keys: `id`, `bbox`, `layers.bathy`,
   `layers.contours`). Use a checked-in fixture if you don't want CI hitting
   the live `public/data/spots/lajolla/bundle.json`.
3. The analytics test (if you find one for the `ALLOWED_NAMES` set in
   `functions/api/analytics/event.js`) — add the three new event names.

No changes needed to `cp-visual-paint` (the spot view doesn't render until a
user opens it; the visual-paint job tests the default map state) or to
`web-smoke` (Puppeteer boots the bundle and watches first render; SpotDetailView
stays unmounted on first render). The bundle's JSON freshness can later get its
own live-checkpoint asserting `index.json` reachable and listing the expected
ids; that's a follow-up, not blocking.

---

## 5. Acceptance criteria

Phase 1B is done when:

- [ ] `python pipeline/build_spot_bundles.py` writes
      `public/data/spots/{lajolla,catalina,monterey}/{bundle.json,bathy.png,
      contours.geojson,coastline.geojson,kelp.geojson,mpa.geojson}` and a
      top-level `public/data/spots/index.json`.
- [ ] A second run with no changes is a no-op (idempotent ≤ 30 days).
- [ ] In `npm run dev`, clicking the **La Jolla** pin shows a **View detailed
      map** button; clicking it opens SpotDetailView.
- [ ] The SpotDetailView renders bathy as a smooth grayscale gradient, with
      depth contours over it, the OSM coastline as a land mask, and kelp +
      MPA polygons clipped to the spot bbox.
- [ ] Pan + pinch-zoom work inside the view, up to a deep zoom that the wide
      view cannot reach.
- [ ] The conditions panel shows current SST / wind / swell / viz sampled at
      the spot centre.
- [ ] Escape, the close button, and the back link all dismiss the view; on
      mobile the view is a full-screen sheet.
- [ ] Catalina and Monterey behave the same way with no spot-specific code.
- [ ] `npm run lint`, `npm run build`, `npm test` pass.
- [ ] `dev-checks.yml` is green on `dev`.

---

## 6. How to ship it (from `AGENTS.md`)

Follow the standard gate — do **not** push to `main`:

1. Branch off `main`, commit normally.
2. Push to `dev`; wait for `dev-checks.yml` to go green (~90 s).
3. Eyeball the dev preview at `https://dev.shouldidive.pages.dev` — open each
   of the three pilot spots end-to-end.
4. Open a PR `dev → main` with `gh pr create`. **Do not auto-merge.** A human
   reviews and merges.

If Phase 1A (kelp wide-map pill) hasn't merged yet, this PR can still ship —
the SpotDetailView treats kelp as optional and gracefully renders without it.

---

## 7. Gotchas & non-goals

- **Don't re-render regional rasters at deep zoom.** SST is 1 km native; at
  spot-view zoom levels each SST pixel covers most of the screen. Sample at
  the spot coordinate and show the value as text instead.
- **`project()` is locked to the regional BBOX.** Use the new `projectInBbox`
  helper inside SpotDetailView; do **not** mutate the wide-view `project()`.
- **DEM tile licensing.** NOAA CUDEM is public domain. Document the exact
  endpoint you used in `build_spot_bundles.py` so the next refresh isn't a
  forensic exercise.
- **Bundle paths are region-aware.** Use `dataPath("/data/spots/...")` in the
  frontend; use `active_region().data_output_dir(ROOT)` in the pipeline. CA
  bundles land at `public/data/spots/`; a future Baja bundle would land at
  `public/data/baja/spots/`.
- **Analytics allowlist is server-side.** Adding `spot_detail_open` etc. to
  `track()` calls without also adding them to
  `functions/api/analytics/event.js`'s `ALLOWED_NAMES` set silently drops the
  events. Edit both.
- **Non-goal: in-place super zoom.** No raising `MAX_ZOOM` on the wide-view
  map, no zoom-threshold auto-transition into a spot view. That's Appendix A.
- **Non-goal: substrate, POIs, parking, entries.** Roadmap, Appendix A.
- **Non-goal: MapLibre.** Stay on the existing SVG renderer.
- **Non-goal: writing a tile pyramid.** Per-spot bundles are intentionally the
  whole architectural answer here.

---

## Appendix A — Roadmap (DO NOT BUILD YET)

These are recorded for context. Each needs its own go-ahead and likely the
data-out-of-git decision (Stage 7 of the earlier remediation plan) before it's
viable at scale.

### A1 — Fan out to more spots

Once the three-spot pilot lands cleanly, extend `SPOT_BUNDLE_RADIUS_KM` to
cover more of `REGION_SAVED_SPOTS.ca` (Santa Cruz Island, Malibu, Point
Conception, Coronados, etc.). The bundle pipeline already loops; the frontend
already reads the index. The natural ceiling is the data-storage decision:
~20 spots is fine in git, ~100 is not.

### A2 — Substrate polygons (bottom type)

Source: California Seafloor Mapping Program (CSMP) habitat classifications —
rock / sand / mud at high resolution. Add a `substrate.geojson` to each bundle
with simplified polygons. Style as low-opacity fill colours
(grey/yellow/brown). Real ecological signal for divers.

### A3 — Dive-relevant POIs

Source: OSM `amenity=parking`, `man_made=pier`, custom-curated entry-point
markers. Add `pois.geojson`. Visible at high zoom only. Adds the "where do I
actually enter the water" answer the conditions data can't give.

### A4 — In-place super-zoom (Flavor 1)

Raise `MAX_ZOOM` on the wide-view map and add a zoom-threshold trigger that
automatically opens the SpotDetailView when the user pinches deep over a
bundled spot. Smoother UX, but only worth doing after A1 broadens spot
coverage — otherwise users zoom in and hit a "no detail here" wall.

### A5 — 3D / shaded-relief bottom views

The DEM you already have in each bundle is enough to render hillshaded relief
or even an interactive 3D bottom via three.js. Strong demo value, no new
pipeline work.

### A6 — Move spot bundles to R2 / object storage

The Stage 7 data-out-of-git migration becomes load-bearing here. Once that
lands, bundle counts can grow without bloating the git repo.

---

## Appendix B — Source references

- **NOAA NCEI CUDEM** (Continuously Updated DEM, 1/9 arc-sec ≈ 3 m):
  `https://www.ncei.noaa.gov/products/coastal-elevation-models`
- **NOAA THREDDS** server (CUDEM tiles):
  `https://www.ngdc.noaa.gov/thredds/regionalDems.html`
- **OSM Overpass API** (already used by `fetch_coastline.py`):
  `https://overpass-api.de/api/interpreter`
- **CDFW Administrative Kelp Beds (ds3135)** — already documented in
  `docs/kelp-mvp-handover.md` (Phase 1A).
- **CDFW Marine Protected Areas (ds582)** — already documented in
  `pipeline/fetch_mpa.py`.
- **California Seafloor Mapping Program** (substrate, A2 roadmap):
  `https://wildlife.ca.gov/Conservation/Marine/CSMP`
