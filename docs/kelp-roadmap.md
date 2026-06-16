# ShouldIDive — Kelp roadmap

Multi-phase plan for the Kelp Bed Zones effort and the "higher-fidelity
zoom" infrastructure work that rides alongside it.

Companion to `outputs/kelp-mvp-handover.md` (the original MVP handover,
which seeded Phase 1 only). Lives in `docs/` so it gets committed to
the repo as durable planning context for future agent sessions.

> **Status convention:** Same as `pipeline/TODO.md` — phases are not
> auto-executed. The agent picks up the topmost unshipped phase when
> the user explicitly unblocks it. Each phase below has its own
> acceptance criteria and an explicit "do not start before X" guard.

---

## TL;DR

Two threads, five phases. Phase 1 just shipped. Phases 2–5 escalate
both **kelp data depth** (admin → canopy → persistence → intelligence)
and **map fidelity infrastructure** (vector polish → vector LOD →
raster tile pyramid → data-out-of-git). The threads are intentionally
interleaved because each kelp deliverable forces an infrastructure
improvement that benefits every other layer.

```
Phase 1 ✅ Admin Bed Zones MVP        (vector)         shipped 2026-05-26 (PR #102)
Phase 2    Vector fidelity unlock     (vector + UX)    next up
Phase 3    Observed Kelp Canopy       (vector, time)   data dependency on Phase 2
Phase 4    Persistent Habitat + tiles (raster, infra)  largest single risk
Phase 5    Cross-layer intelligence   (model + UI)     ships after Phases 3 + 4
```

---

## Why this exists

The CDFW handover supplies **three** kelp datasets — admin beds,
observed canopy surveys, persistence raster — and the data handover
notes that they should ship as separate, comparable overlays, not be
folded into a single "kelp present" pseudo-layer. The MVP handover
shipped the first dataset and explicitly deferred the other two.

A divermarket-facing kelp experience needs all three to be useful:
- **Admin beds** answer "what's the management boundary here?"
- **Observed canopy** answers "is there actually kelp right now, and
  has it grown or shrunk this season?"
- **Persistence** answers "how reliable is this bed across years?" —
  the spot-pin decision driver for trip planning.

Phase 5 binds them into actionable diver guidance (e.g. "La Jolla is
in active bed #42, 78% persistence, 2024 survey showed peak canopy").
That's the eventual user value; Phases 2–4 are the load-bearing
infrastructure between admin polygons and useful intelligence.

---

## The two threads

### Thread A — Kelp data depth

| Phase | Dataset | Type | Cadence |
|---|---|---|---|
| 1 ✅ | CDFW Administrative Kelp Beds (ds3135) | Vector polygons (87) | Stable; refresh-on-fetch |
| 3 | CDFW Aerial Kelp Surveys + KelpWatch (Landsat) | Time-varying vector | Annual / seasonal |
| 4 | CDFW Kelp Persistence (ds3151) | Raster (5 m, 2002–2016) | One-shot; archive |
| 5 | Derived: kelp_density_factor, persistence_score, etc. | Model features | Refreshed daily with viz |

### Thread B — Map fidelity infrastructure

| Phase | What unlocks | Catalyst |
|---|---|---|
| 1 ✅ | First SVG vector overlay alongside MPA + bathy + coastline | Admin beds need vector path rendering |
| 2 | MAX_ZOOM bump 8 → 16 + zoom-aware vector styling + spot-pin polish | Kelp polygons are crisp at any zoom — admin overlays no longer rate-limited by raster fidelity |
| 3 | Vector simplification by zoom level (Douglas-Peucker LOD) | Canopy surveys are 10× denser than admin beds — naive load tanks |
| 4 | Tile pyramid + data-out-of-git storage migration | Persistence is 5 m raster, can't ship in git history |
| 5 | Cross-layer composition (kelp × viz × spot rank) | All kelp layers live + composable |

The threads are **coupled**: every kelp phase pushes the fidelity infra
forward, and every fidelity unlock makes the kelp data more useful.
That's the design — don't try to ship one thread without the other.

---

## Phase 1 — Admin Bed Zones MVP ✅ SHIPPED 2026-05-26

PR [#102](https://github.com/Michaelpjob/ShoudiDive/pull/102) on
`feat-kelp-mvp` → `main`. Also deployed to dev preview from the `dev`
branch.

**What shipped:**
- `pipeline/fetch_kelp.py` — paginated FeatureServer fetch, bbox-clip,
  4-decimal coords, status distribution printout
- `src/components/KelpLayer.jsx` + `KelpPopup.jsx` — vector SVG overlay
  cloned from MPA pattern, status-keyed green palette
- `PrefsContext` gets `kelpOn: true` (auto-defaults via spread merge)
- `usePopupState` gains `selectedKelp` + clear-on-off effect
- `MapShell` wires through, threads to `DesktopLayout` + `MobileSheet`
  (3 pill locations)
- `REGIONS_WITH_KELP = new Set(["ca"])` — chip hides outside CA
- `refresh-ca-data.yml` runs the fetch step daily
- `appFeatureContracts` asserts `aria-pressed={kelpOn}` stays wired

**What's deferred** (per handover §7 non-goals): MapLibre, tile work,
LOD. Those start in Phase 2.

---

## Phase 2 — Vector fidelity unlock + kelp polish

**Goal:** Cash in the "SVG vectors stay crisp at any zoom" payoff that
Phase 1's handover flagged but didn't actually deliver. Take the win
across every existing vector layer (MPA, kelp, bathy, coastline, place
labels, saved spot pins) — not just kelp.

**Why now:** Phase 1 proved the vector layer family works. The
`MAX_ZOOM = 8` constant in `useMapViewport.js` was a SoCal-friendly
ceiling when MUR SST 1 km was the canonical content. Vector polygons
have no equivalent rate limit. Bumping the ceiling is ~10 LOC; the
real work is the surrounding polish that makes the higher zoom
**readable** rather than just **possible**.

### Features

#### PR-K2-1 — MAX_ZOOM bump + clamp-aware pan (~30 LOC)

- `useMapViewport.js`: `MAX_ZOOM = 8` → `MAX_ZOOM = 16`
- Verify `clampVb` keeps pan within the bbox at the new ceiling
  (existing math should hold; add a test that asserts no division
  pathology at 16× the way `useMapViewport.test.js` does at 8×)
- Update the recenter button title to read "Recenter (zoom 1×)" so
  users understand the new range
- **Acceptance:** at zoom 16 over the SF/Monterey kelp cluster, you
  can read individual bed boundaries that overlap at 8×

#### PR-K2-2 — Zoom-aware vector styling (~80 LOC)

Vector strokes scale with `zoomLevel` so admin polygons don't render
as fat blue marker outlines at zoom 12+. Mirrors the existing
`vector-effect: non-scaling-stroke` work in saved-spot pins.

- KelpLayer + MpaLayer + BathyLayer: stroke width gradient
  `1.6 / Math.min(zoomLevel, 4)` so 1× = 1.6 px, 4× = 0.4 px, capped
- Kelp fill opacity easing — fill is 14% at 1×, but at 8×+ that's
  visual noise. Drop to 6% above zoom 4.
- Touch target compensation: at high zoom kelp polygons can be smaller
  than 44 × 44 px (Apple HIG minimum). When `zoomLevel > 4`, expand
  the SVG hit-test region via `stroke-width` increase on a transparent
  hit-stroke (already a pattern in BathyLayer)
- **Acceptance:** zoom from 1× to 16× and watch kelp + MPA + bathy
  edges thin smoothly, not staircase

#### PR-K2-3 — "Zoom to bed" + spot-pin proximity polish (~120 LOC)

- KelpPopup gains a "Zoom to bed" action that animates `zoomAt` to
  fit the polygon bounds (compute from feature geometry, ~10 LOC)
- Saved-spot pins, at `zoomLevel > 8`, gain a thin connector line to
  the nearest kelp-bed edge (proximity hint — "this spot is *in* this
  bed"). Cheap geometric query against the loaded kelp features
- Mobile: pinch-zoom snaps to MAX_ZOOM with a visible "MAX" badge
  near the zoom controls so users know they've hit the ceiling

### Plan

Three small PRs, sequenced. PR-K2-1 lands first (mechanical), PR-K2-2
needs visual review of the stroke gradient, PR-K2-3 is the most
discretionary and can land independently.

**Estimated total:** ~230 LOC, 1–2 day total agent time. Single sprint.

### Dependencies

- Phase 1 admin beds shipped (✅)
- No new data sources, no new APIs, no infra changes

### Risks

- LOW. All changes are reversible via the same constant flip.
- Existing visual regression tests (`cp-visual-paint`) may flag the
  stroke changes — these are intentional. Update fixture screenshots.

### Acceptance criteria (phase-level)

- [ ] At 16× zoom over Pt. Loma / La Jolla, individual kelp beds are
      legible and the popup zoom-to-bed action snaps correctly
- [ ] Mobile pinch-zoom hits MAX_ZOOM smoothly, shows the indicator
- [ ] No `cp-visual-paint` regressions (or fixture refreshes are
      explicit and reviewed)

---

## Phase 3 — Observed Kelp Canopy (time-varying vector)

**Goal:** Ship the second kelp dataset from the data handover —
actual observed canopy extent per survey year, presented on the same
timeline machinery the SST + wind layers already use. This is the
**"is there kelp here right now?"** answer.

**Why this shape:** Canopy data is *time-varying vector*. The repo
already has timeline machinery (`SstTimeline`, `useTimelineSelections`,
slot keys, summary.json). Canopy fits that shape — annual or seasonal
slots, vector polygons per slot. The hardest piece is the **vector
simplification by zoom level**, because canopy survey polygons are
~10× denser than admin beds (CDFW publishes them at ~1 m precision,
not 10 m).

### Features

#### PR-K3-1 — Data ingest + storage shape decision (~250 LOC) — BLOCKED

**2026-05-27 finding (during execution):** CDFW Aerial Kelp Surveys are
NOT published as a public FeatureServer. The CDFW Open Data portal
(`data-cdfw.opendata.arcgis.com`) returns only Administrative Beds
(ds3135, already shipped) + Persistence raster (ds3151, Phase 4) +
PISCO fish-density datasets. The historical aerial surveys were
published as PDF reports / static GIS shapefiles attached to those
reports, not via a queryable API. Without a public vector endpoint,
the Phase 3 plan of "clone admin-bed pattern for canopy" doesn't
work directly.

Two candidate sources, evaluated against the same diagnostic harness:

| Source | Type | Cadence | Coverage | Public API? |
|---|---|---|---|---|
| CDFW Aerial Kelp Surveys | Vector polygons (intended) | Annual | CA coast | ❌ Not surfaced on data-cdfw portal |
| KelpWatch (Bell et al. / Landsat) | Raster (netCDF, 30 m) | Quarterly | Global Macrocystis | ✅ via Dryad DOI / Google Earth Engine |
| KelpWatch derived vector | Raster → polygonized | Quarterly | CA only after clip | Requires per-quarter raster ingest + polygonize |

**Pivot decision needed from user before PR-K3-1 resumes:**

- **Option A** — Ship Phase 3 as a raster overlay too (folds into
  Phase 4 infra timeline). KelpWatch netCDF → tile pyramid via the
  same path as persistence raster. Loses some of the "vector LOD
  shows the win" framing but is the only path that actually works
  with public data. **Recommended.**
- **Option B** — File a CDFW data request for the aerial-survey
  shapefiles + republish them ourselves. Adds a ~4-week loop on a
  human agency and creates an attribution/license question. Not
  recommended.
- **Option C** — Polygonize KelpWatch raster at ingest time inside
  the pipeline (`rasterio.features.shapes`). Keeps the "vector"
  framing but requires the same netCDF ingest as Option A + an extra
  polygonization step. Worth doing only if the simplified raster
  rasterizes to a useful vector that survives DP simplification
  cleanly.

Until the user picks A / B / C, PR-K3-1 is **parked**. PR-K3-2
(vector LOD) shipped independently and is ready to consume whichever
vector source lands. PR-K3-3 + PR-K3-4 (timeline + popup) are
likewise ready, paused on the data shape decision.

Implementation:
- `pipeline/fetch_kelp_canopy.py` — pulls per-survey-year GeoJSON
- Output: `public/data/kelp-canopy/<year>.geojson` (or
  `public/data/<region>/kelp-canopy/<year>.geojson` for region-aware)
- New `manifest.json` entry: `kelpCanopy: { years: [2018, 2019, ...] }`
  with per-year file size + feature count (mirrors SST 7-day summary)
- Vector simplification at ingest time using `shapely.simplify` with
  tolerance varying by survey year (older surveys with cruder polygons
  get aggressive simplification; recent ones stay precise)

#### PR-K3-2 — Vector LOD render path (~400 LOC, foundational)

The infra investment that pays dividends across every vector layer.

- New `src/lib/vectorSimplify.js` — Douglas-Peucker implementation
  (or use `simplify-js` if licensing checks out)
- `useMapViewport.js` exports a `simplifyTolerance` derived from
  `zoomLevel`: at zoom 1× tolerance = 0.01° (~1 km), at zoom 16×
  tolerance = 0.0001° (~10 m)
- `KelpCanopyLayer.jsx` (new) memoizes simplified paths per
  `{tolerance, features}` pair so we don't re-simplify on every pan
- Retrofit `MpaLayer.jsx` + `BathyLayer.jsx` + `KelpLayer.jsx` to use
  the same tolerance (admin beds are already small enough that this
  is a no-op for them, but the consistency matters)
- Web Worker offload for first-load simplification on large surveys
  (some years have 10k+ polygons) — gated behind `crossOriginIsolated`
  with main-thread fallback

#### PR-K3-3 — Timeline scrubber for canopy years (~200 LOC)

- `KelpCanopyTimeline.jsx` — single-row year scrubber, cloned from
  `SstTimeline.jsx` cell-center geometry. Snaps to survey years.
- Integration with `useTimelineSelections` — adds `canopyYear` state
  alongside `windSel` / `swellSel` / `currentSel`
- Cell-center playhead math from the 2026-05-26 timeline-alignment
  fix (already merged) applies directly
- Confidence dot: survey years with cloud-day coverage gaps get a
  lower confidence tier (use `src/lib/confidence.js` extension)

#### PR-K3-4 — Canopy popup + cross-layer compose (~150 LOC)

- KelpCanopyPopup shows: peak canopy area in the bed, change since
  prior year (% delta), distance to admin bed centroid
- When BOTH `kelpOn` (admin beds) and `canopyOn` are active, render
  canopy with higher z-order and admin beds as faint outlines
  underneath — the canopy is what's actually there; the admin
  boundary is reference
- Mobile peek strip: "Canopy" chip status row shows latest survey
  year + delta arrow (↑ +12% from 2023, etc.)

### Plan

Sequence: PR-K3-1 → PR-K3-2 → PR-K3-3 → PR-K3-4. PR-K3-2 is the
infra prerequisite for K3-3 + K3-4 and any future dense vector
layer (could be reused for Phase 4 if persistence has any vector
component).

**Estimated total:** ~1000 LOC across 4 PRs. 1–2 weeks agent time.
PR-K3-2 (LOD) is the highest-effort single piece.

### Dependencies

- Phase 2 zoom bump (✅ if Phase 2 lands) — without it, LOD has
  nothing to scale against
- Data source decision (Aerial Surveys vs. KelpWatch) — see PR-K3-1

### Open questions (need decisions before specific PRs start)

1. **Year-based vs. season-based timeline?** Surveys are annual but
   canopy phenology is strongly seasonal (winter trough → summer
   peak). If we go season-based, we have to interpolate gaps. If
   year-based, we tell a coarser story. **Default: year-based for
   v1**, season as Phase 5 enrichment.
2. **Latest-available vs. selected-year default?** SST defaults to
   "now"; canopy could default to "most recent complete survey."
3. **Storage budget:** ~20 survey years × ~200 KB each = 4 MB
   committed to git. Acceptable v1, but if KelpWatch (seasonal,
   ~80 surveys) is later added that's 16 MB+ — pushes against the
   Phase 4 data-out-of-git decision earlier.

### Risks

- MEDIUM. Vector LOD is genuinely tricky (PR-K3-2 is where bugs
  hide). Mitigation: ship the LOD path behind a feature flag and
  validate against PR-K3-2's existing admin-bed render before
  flipping the canopy layer on.
- Data source latency: CDFW survey publication lags 1–2 years.
  KelpWatch is fresher (~quarterly) but the conversion adds risk.

### Acceptance criteria (phase-level)

- [ ] Canopy layer renders smoothly at all zoom levels with no
      visible polygon degeneration (Douglas-Peucker too aggressive)
      or jank (no simplification)
- [ ] Year scrubber at the bottom of the map updates the canopy
      overlay live as the user drags
- [ ] Popup shows survey-year, peak canopy area, delta from prior
- [ ] Mobile chip + peek strip mirror desktop wiring
- [ ] No CA pages-bundle size regression > 200 KB (LOD library +
      worker — fits inside the existing budget)

---

## Phase 4 — Persistent Kelp Habitat + Tile Pyramid

**Goal:** Ship the third kelp dataset (CDFW Kelp Persistence ds3151) —
the raster that summarizes 2002–2016 presence/absence at 5 m. Use it
as the **pilot** for the tile-pyramid + data-out-of-git architecture
that the project has needed for a year but couldn't justify on any
single previous layer.

**Why this shape:** Persistence is 5 m raster. At the existing CA
bbox (~1500 × 800 km coverage area) and ~1 km nearshore band that's
nontrivial, that's ~100k × 5k pixels = 500 MB raw. Even at 1-bit
presence/absence with PNG compression that's 50 MB+ — multiple
orders of magnitude over what fits in git history.

This phase is the **biggest single risk** in the roadmap. It changes
the deployment model (introduces R2 + a tile worker), introduces a
new cache layer in the browser, and forces decisions about cache
invalidation, fallback behavior, and CDN cost. Mitigate by piloting
on a single small geographic footprint before going wide.

### Features

#### PR-K4-1 — Storage decision + R2 bucket provisioning (~50 LOC + ops) — USER-BLOCKED

**Cannot ship autonomously.** This PR requires the user to:

1. Provision the R2 bucket `shouldidive-tiles` in their Cloudflare
   account (or pick an alternate name)
2. Generate R2 access keys and add them to the repo's secrets as
   `R2_ACCESS_KEY_ID` + `R2_SECRET_ACCESS_KEY`
3. Decide on the tile subdomain (`tiles.shouldidive.com` requires
   DNS + Worker route binding) or accept `*.pages.dev` for v1
4. Approve the cost model — R2 is pay-per-egress; expected ≈ $1–5/mo
   for the CA-only kelp footprint, but burgers if Phase 5 adds
   bathy + other layers

Once those four are settled, the PR itself is small (provisioning
config + secrets wiring). Until then, every downstream Phase 4 PR
(K4-2 through K4-5) is parked.

**Why no agent-side workaround:** Cloudflare account provisioning
requires the account-owner's login. There's no per-repo R2 path that
sidesteps this. The MVP-friendly fallback is "keep persistence
raster out of v1" — but that's exactly what Phase 4 exists to ship,
so the right move is to wait for the user.

Decision matrix (need explicit user go-ahead before this PR):

| Storage option | Pros | Cons | Recommendation |
|---|---|---|---|
| Cloudflare R2 | Same-account, pay-per-egress, free first 10 GB | New service, new auth | ✅ For v1 |
| AWS S3 | Industry standard | New cloud relationship | ❌ |
| GitHub LFS | Stays in repo | Costs money + bandwidth caps | ❌ |
| IPFS / Pinata | Decentralized | Latency, novelty | ❌ |

Implementation:
- Provision `shouldidive-tiles` R2 bucket
- Cloudflare Worker at `tiles.shouldidive.com/<layer>/<z>/<x>/<y>.png`
  that pulls from R2 with HTTP caching
- Pipeline: new `pipeline/publish_tiles.py` that uses `rasterio` +
  `mercantile` to slice rasters into XYZ tiles + upload to R2
- New env vars: `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`,
  `R2_BUCKET_NAME`

#### PR-K4-2 — Persistence raster ingest + tile generation (~300 LOC)

- `pipeline/fetch_kelp_persistence.py` — one-shot ingest (this data
  doesn't update; cache locally after first fetch)
- Re-projects from CDFW source CRS (likely NAD83 / CA Albers) to
  Web Mercator for XYZ tile delivery
- Generates zoom levels 8 → 16 (~25k tiles for CA kelp footprint)
- Uploads to R2 via the worker provisioned in PR-K4-1
- Idempotent: skips tiles whose source pixels haven't changed
  (relevant only for re-runs; primary use case is one-shot)

#### PR-K4-3 — Frontend tile-loader + persistence overlay (~400 LOC)

- New `src/components/KelpPersistenceLayer.jsx` — viewport-driven
  tile loader, fetches only visible (z, x, y) triples
- LRU memory cache (~100 tiles, ~5 MB) + IndexedDB layer (~500 tiles,
  ~25 MB, persists across sessions)
- Tile error handling: missing tiles fade in as transparent (R2
  returns 404, not 5xx)
- Render path: `<g><image href="...">` per tile, viewBox-aware
  positioning — mirrors how MapLibre would do it but rolled by hand
  to stay inside the existing SVG renderer

#### PR-K4-4 — Persistence legend + cross-layer composition (~150 LOC)

- New legend variant: 0% (never observed) → 100% (every survey)
  gradient (purple → green)
- When admin beds + persistence both on, render persistence raster
  *inside* the admin-bed clip path so user sees "this is the bed
  AND this is how reliably kelp shows up in it"
- Popup integration: clicking a bed shows persistence stats
  computed from the underlying raster (sampled at bed centroid +
  bounds)
- Mobile: persistence chip in the same overlay row as MPA + Kelp +
  Bottom

#### PR-K4-5 — Pilot geographic footprint + validation (~50 LOC)

**Critical risk-reduction step.** Before generating tiles for the
full CA bbox, generate them for a single 10 km × 10 km cell
(suggest Monterey Bay kelp cluster). Verify:
- Tile generation completes in <5 min
- R2 storage costs < $1/month for the pilot footprint
- Frontend loader handles 404 gracefully (some tiles will be all
  water = 404 by design)
- Cross-layer compose looks correct with admin beds + persistence
  both on

Only after PR-K4-5 passes do we generate full-bbox tiles.

### Plan

Sequence: PR-K4-1 (provisioning) → PR-K4-2 (ingest) → PR-K4-5
(pilot) → PR-K4-3 (loader) → PR-K4-4 (compose). PR-K4-5 deliberately
sits between ingest and loader so we don't ship a loader against
unvalidated tile output.

**Estimated total:** ~950 LOC across 5 PRs + ops work (R2 + Worker).
**3–4 weeks** agent time — this is the longest phase. Significant
chunks are ops (R2 setup, Worker deploy) that aren't strictly LOC.

### Dependencies

- Phase 3 canopy ships first (so the timeline machinery + LOD layer
  are battle-tested before we add raster tiles on top)
- Cloudflare R2 + Workers (existing CF account, just new bindings)
- Decision on storage architecture (PR-K4-1)

### Risks

- HIGH. Single-biggest risk in the roadmap. New runtime dependency
  (R2 + Worker), new auth, new cache layer. Cache invalidation
  semantics need careful design (persistence data never changes, so
  this is actually the *easiest* case to start with — invalidate
  never).
- Mitigation: pilot footprint in PR-K4-5 is the gate. If the pilot
  takes longer than 1 week or runs into > 2 unforeseen issues, halt
  and reassess the storage choice.

### Acceptance criteria (phase-level)

- [ ] Pilot footprint (Monterey Bay) renders persistence raster
      smoothly at all zoom levels
- [ ] Cross-layer compose (admin bed + persistence) reads correctly
- [ ] Tile cache hits > 95% on a typical user session (load
      Monterey, pan to Pt. Loma, pan back — second Monterey view
      should be IndexedDB cache hit)
- [ ] R2 + Worker monthly costs stay under $5 for full CA bbox
- [ ] Frontend bundle increase < 80 KB (tile loader is the only new
      dep)

---

## Phase 5 — Cross-layer kelp intelligence

**Goal:** Stop showing kelp data as raw layers. Start using it to
*answer diver questions*. This is the phase where "Kelp pill on /
off" becomes "tell me if today is a good kelp dive at La Jolla."

**Why now:** Phases 3 + 4 must be solid first. Phase 5 builds on
their outputs (canopy + persistence) to derive useful signals.

### Features

#### PR-K5-1 — Kelp-aware viz model term (~200 LOC, pipeline)

- `viz_predict/features.py` gains `kelp_density_factor(lat, lng)`:
  combines current canopy (from Phase 3) + persistence
  (from Phase 4) into a single density signal
- `viz_predict/config.py`: per-zone `kelp_density` coefficient.
  Positive in CA central + bight (kelp filters particles → better
  viz), unused or zero elsewhere (e.g. tropical, where the term
  doesn't apply)
- `viz_predict/model.py`: adds `kelp_density * coefficient` term to
  `driver_adjustment`
- Behind `ENABLE_KELP_VIZ_TERM=1` for one week of A/B before default
- Validation: residuals against Reef Check + MBARI Secchi obs to
  confirm the term is moving residuals in the right direction

#### PR-K5-2 — Spot-pin kelp scorecard (~150 LOC, frontend)

Each saved-spot card grows a "Kelp" section:
- Bed number (if in admin bed)
- Latest survey canopy: high / medium / low / none
- Persistence: % of survey years with kelp present
- Seasonal phenology: "kelp peaks Aug-Oct here" (derived from
  multi-year canopy seasonality)

#### PR-K5-3 — Storm-strip warnings (~200 LOC, pipeline + frontend)

When wave Hs in an active kelp bed exceeds 8 ft (typical canopy
detachment threshold per CDFW reports):
- Pipeline: new `kelp_storm_warn.json` per bed, refreshed daily
  alongside swell data
- Frontend: yellow banner in KelpPopup "Recent storm — canopy may
  be stripped, viz could degrade for 5–7 days"
- Tracked for 7 days post-event; auto-clears

#### PR-K5-4 — Annual kelp health trends (~250 LOC)

- Long-term trend analysis per bed: is canopy increasing, stable,
  declining over 5-year window?
- Decline detection flag — beds with > 30% canopy loss over recent
  windows get a sad-trombone icon in KelpPopup
- Cross-reference to known stressors (marine heatwaves, urchin
  barren events) where data is available

### Plan

PR-K5-1 + PR-K5-2 + PR-K5-3 are independent and can ship in
parallel by separate agents. PR-K5-4 depends on having ~3 years
of canopy data ingested (depends on Phase 3 archive coverage).

**Estimated total:** ~800 LOC across 4 PRs. 2–3 weeks agent time.

### Dependencies

- Phases 3 + 4 shipped and stable for ~1 month
- Validation data accumulated (Reef Check, MBARI Secchi obs)
- User feedback on whether the spot-card density is the right
  shape (open product question)

### Risks

- LOW-MEDIUM. The viz-model term (PR-K5-1) has the highest risk
  because it changes a model output that's already user-visible.
  A/B gating + residual validation mitigate.
- Storm-strip warning (PR-K5-3) could be noisy if the threshold is
  wrong. Iterate after 1–2 storm events.

### Acceptance criteria (phase-level)

- [ ] Saved-spot kelp scorecard renders for every CA spot that's
      inside an admin bed
- [ ] Viz model term A/B test shows reduced residuals (or
      revert + revisit)
- [ ] Storm-strip warning fires correctly on the next Hs > 8 ft
      event over a CA kelp bed
- [ ] No regression in existing viz model accuracy (regression
      guard catches it)

---

## Cross-cutting concerns

### Validation

Every phase that touches the viz model (Phase 5 most directly,
Phase 3 + 4 indirectly via new data inputs) **must** validate
residuals against Reef Check + MBARI Secchi observations before
promotion. The harness lives in `pipeline/validation/score.py` and
the watchdog in `pipeline/validation/watchdog.py` — both already
fire on every `refresh-ca-data.yml` run.

### Storage architecture trajectory

Today: everything in git (`public/data/`). Bundle is ~80 KB per
day's data refresh, manageable in git history.

After Phase 3: still in git, ~4 MB committed for canopy archive.
Pressing against the comfortable budget but workable.

After Phase 4: persistence raster on R2. First piece of data NOT
in git. This is a one-way door — once we have the R2 + Worker
pattern, every future high-resolution layer (bathymetry tiles,
NASA WorldView overlay, OSTIA SST) can use the same path.

### Region scope

Phase 1 ships CA-only. Phases 2–5 should be designed CA-first but
keep the `active_region()` plumbing so the data layer plugs into
any future region with kelp:
- Baja: no CDFW analog. SEMARNAT manages a different scheme. If
  Baja kelp matters, add a Baja-specific source.
- PNW: WA DNR + ORDF have kelp data. PNW kelp ecology is similar
  enough that the same vector/canopy/persistence triple shape applies.
- Tropical: no Macrocystis. Sargassum + reef cover are the analogs
  but architecturally distinct.

### Testing strategy per phase

- Phase 2: visual regression (`cp-visual-paint` 3 viewports ×
  layers), explicit zoom-clamp unit test
- Phase 3: contract tests for manifest entries + canopy timeline
  state shape, integration test for LOD library
- Phase 4: tile generation reproducibility test, frontend cache hit
  rate test, pilot footprint visual smoke
- Phase 5: residual validation + A/B harness as defaults

---

## Sequencing rules

1. **Never skip the pilot in Phase 4.** PR-K4-5 (Monterey Bay
   pilot) is a hard gate before full-bbox tile generation. The
   ops risk is too high to skip.
2. **Phase 5 waits for stability.** Phases 3 + 4 must run for a
   month with no rollbacks before Phase 5 modifies the viz model.
3. **Phase 2 unblocks all later phases.** If Phase 2 is delayed,
   Phases 3 + 4 still work (with sub-optimal rendering); but if
   Phase 2 is *broken* (e.g. zoom-clamp pathology) it blocks
   everything.
4. **Don't bundle phases into single PRs.** Each phase has its
   own validation surface. Combining them defeats the gate model.

---

## Open questions (need user decisions before specific PRs)

| Question | Blocks | Recommendation |
|---|---|---|
| Vector simplification library: `simplify-js` (MIT) vs. roll our own Douglas-Peucker? | PR-K3-2 | `simplify-js` for v1, evaluate roll-our-own if licensing changes |
| Canopy data source: Aerial Surveys vs. KelpWatch | PR-K3-1 | Aerial Surveys for vector workflow simplicity |
| Year-based vs. season-based canopy timeline | PR-K3-3 | Year-based for v1 |
| R2 vs. S3 for tile storage | PR-K4-1 | R2 (same-account, lower cost) |
| Tile pyramid pilot footprint | PR-K4-5 | Monterey Bay (10 km × 10 km) |
| Kelp viz term gating: A/B flag duration | PR-K5-1 | 1 week minimum before default |
| Persistence raster: ship as overlay only, or also feed viz model? | PR-K4-4 / PR-K5-1 | Overlay only in Phase 4; feed viz in Phase 5 |

---

## Out of scope (don't build without separate go-ahead)

- MapLibre / Leaflet migration. Every phase here works on the
  existing SVG renderer. Tile pyramid (Phase 4) is the closest
  thing to needing a real map library, but it stays SVG via
  `<g><image>` per tile.
- Real-time satellite kelp detection (e.g. live MODIS / VIIRS
  daily). Outside the data-handover scope.
- Kelp-specific dive booking integration (operator API, etc.).
  Product, not data.
- Other CA marine biology overlays (urchin barrens, otter range,
  rockfish closures). Separate roadmaps if/when prioritized.

---

## Companion docs

- `outputs/kelp-mvp-handover.md` — original Phase 1 handover
- `pipeline/TODO.md` — pipeline + dashboard backlog (canonical
  format this doc mirrors)
- `docs/expansion-regions.md` — multi-region scoping doc
- `docs/expansion-baja.md` / `docs/expansion-norcal.md` — region
  expansion specs
