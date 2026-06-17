"""Build per-spot bundles for the Spot Detail view.

For each pilot spot (id → centre lng/lat + radius km), generate a
self-contained bundle of nav-quality static assets:

  public/data/spots/<id>/
    bundle.json       — manifest tying the rest together
    bathy.png         — high-res grayscale bathymetry (linear depth encoding)
    contours.geojson  — depth contour lines derived from the DEM
    coastline.geojson — high-res OSM coastline, clipped to spot bbox
    kelp.geojson      — CDFW aerial-survey kelp: per-spot multi-year
                        fetch (surveyed extent + best recent canopy);
                        statewide 2016-only clip as fallback
    mpa.geojson       — CDFW MPA polygons, clipped to spot bbox

  public/data/spots/
    index.json        — { spots: [...], generated_at }

The frontend (SpotDetailView.jsx) reads index.json on app boot to know
which saved-spot pins have a "View detailed map" affordance, then loads
the per-spot bundle on demand when the user clicks the affordance.

Design choices vs. the handover (`docs/spot-detail-handover.md`):

  * DEM source: NOAA NCEI "DEM global mosaic" ImageServer first, GMRT
    GridServer as hole-fill + fallback.

    2026-06-10 QA found the La Jolla chart showing a flat ~2 m-deep
    plateau across the whole La Jolla Shores shelf (real depths
    6-25 m) with stair-step edges. Root cause: GMRT has no measured
    swath data on the SoCal nearshore shelf, so it serves its coarse
    predicted-bathymetry background there (~2-5 m over the Shores,
    La Jolla Canyon head diluted to -10 m where reality is -137 m).
    Direct GridServer probes confirmed resolution=max and
    layer=topo-mask return byte-identical grids to resolution=high
    for these bboxes — no GMRT knob fixes it; the synthesis simply
    lacks nearshore source data here.

    The NCEI mosaic (gis.ngdc.noaa.gov DEM_mosaics/DEM_global_mosaic)
    serves the best-available tiled DEM per pixel — CUDEM 1/9 arc-sec
    (~3 m) at Monterey, the 1/3 arc-sec (~10 m) SoCal coastal DEMs at
    La Jolla + Catalina — and blends to coarser sources offshore
    server-side, exactly the "CUDEM nearshore + something offshore"
    blend the handover wanted, in one exportImage call. Probe points
    on the Shores shelf: NCEI -22 m / Scripps Canyon arm -54 m /
    La Jolla Canyon head -137 m, all matching charted depths; GMRT
    said -4.5 / -30 / -10.5 m at the same points. Any pixels the
    mosaic lacks (and the whole spot, if the fetch fails) fall back
    to GMRT, blended in elevation space so NCEI land can't be
    backfilled with phantom GMRT water. The source actually used is
    recorded in bundle.json["sources"]["bathy"].
  * Contours: numpy + custom marching-squares (no scikit-image dep —
    keeps requirements.txt thin). Contour intervals are 1 m / 5 m /
    25 m piecewise, which matches diver-relevant depth bands.
  * Skip-if-fresh: 30-day TTL per spot, idempotent on re-runs.

Run on demand or as a daily refresh step:

  python pipeline/build_spot_bundles.py            # idempotent, skips fresh
  python pipeline/build_spot_bundles.py --force    # rebuild even if fresh
  python pipeline/build_spot_bundles.py --spot lajolla   # one spot only
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]

try:
    from pipeline.regions import active_region
except ModuleNotFoundError:
    from regions import active_region

REGION = active_region()
SPOTS_DIR = REGION.data_output_dir(REPO_ROOT) / "spots"
INDEX_PATH = SPOTS_DIR / "index.json"

# Mirror of frontend's REGION_SAVED_SPOTS.ca for the three pilot spots.
# The Python copy is intentional (handover §4 Task B item 1) — keeps
# the builder dependency-free from JS. Add CA spots here as the pilot
# expands per the kelp/spot roadmap.
SPOT_CENTRES = {
    "lajolla":  {"name": "La Jolla",   "lng": -117.275, "lat": 32.854},
    "catalina": {"name": "Catalina",   "lng": -118.450, "lat": 33.389},
    "monterey": {"name": "Monterey",   "lng": -121.920, "lat": 36.620},
    # Centred on the deep kelp bed west of the peninsula (~70-80 ft),
    # so the detail anchor sits on the cliff regime, not the shoreline.
    "pointloma": {"name": "Point Loma", "lng": -117.270, "lat": 32.685},
    # ── Phase 2: Channel Islands (all 8; Catalina already above) ──────
    # Centres on each island's centroid so the bbox covers it
    # symmetrically; radii (below) sized tip-to-tip. Verify extents
    # against the render and bump radius if clipped (cf. Catalina).
    "anacapa":     {"name": "Anacapa",           "lng": -119.41, "lat": 34.01},
    "santacruz":   {"name": "Santa Cruz Is.",    "lng": -119.740, "lat": 34.000},
    "santarosa":   {"name": "Santa Rosa Is.",    "lng": -120.083, "lat": 33.960},
    "sanmiguel":   {"name": "San Miguel Is.",    "lng": -120.365, "lat": 34.038},
    "sbisland":    {"name": "Santa Barbara Is.", "lng": -119.046, "lat": 33.476},
    "sannicolas":  {"name": "San Nicolas Is.",   "lng": -119.509, "lat": 33.254},
    "sanclemente": {"name": "San Clemente Is.",  "lng": -118.483, "lat": 32.92},
    # ── Phase 2: popular shore-diving hubs (S→N) ─────────────────────
    # 2026-06-16: 7 of these were re-centred SEAWARD (refugio + malibu
    # also zoomed in — see SPOT_RADIUS_KM) after user QA ("seeing the same
    # issue on ca detailed maps", flagged on Refugio): an on-shore centre
    # put a featureless land block over 40-60% of the chart. Each was
    # tuned per-spot to ~20-30% land (the build's [mask] %) so the reef /
    # kelp / depth gradient — the dive — fills the frame, then checked
    # against a local render; same fix as the Baja spots. The overview-map
    # pin (REGION_SAVED_SPOTS) stays at the dive site; only the detail
    # bbox re-frames. redondo / lajolla / monterey / jadecove / mendocino
    # / pointloma already framed water-dominant and were left as-is.
    "laguna":      {"name": "Laguna Beach",  "lng": -117.801, "lat": 33.531},
    "palosverdes": {"name": "Palos Verdes",  "lng": -118.418, "lat": 33.718},
    "redondo":     {"name": "Redondo Beach", "lng": -118.398, "lat": 33.842},
    "malibu":      {"name": "Malibu",        "lng": -118.787, "lat": 33.996},
    "refugio":     {"name": "Refugio",       "lng": -120.068, "lat": 34.450},
    "pointlobos":  {"name": "Point Lobos",   "lng": -121.960, "lat": 36.518},
    "monastery":   {"name": "Monastery",     "lng": -121.950, "lat": 36.534},
    "jadecove":    {"name": "Jade Cove",     "lng": -121.502, "lat": 35.920},
    "saltpoint":   {"name": "Salt Point",    "lng": -123.352, "lat": 38.562},
    "mendocino":   {"name": "Mendocino",     "lng": -123.793, "lat": 39.275},
    # ── Phase 2: Coronado Islands (Baja, just S of the border) ───────
    # Bathy/contours/soundings come cross-border; kelp comes from the
    # SBC LTER Landsat source (CDFW stops at the border) — see
    # fetch_coronados_kelp / BUILD_BAJA_KELP wiring below.
    "coronados":   {"name": "Coronados",     "lng": -117.26, "lat": 32.401},
}

# Mirror of frontend's SPOT_BUNDLE_RADIUS_KM. Same single-source-of-truth
# rationale as SPOT_CENTRES above.
SPOT_RADIUS_KM = {
    "lajolla":  4,
    # 2026-05-27: bumped 8 → 12 km (8 km cut Avalon out of the bbox).
    # 2026-06-10: bumped 12 → 16 km after user QA "the map of catalina
    # is clipped on the edges": the island itself spans ~28 km tip to
    # tip, so 12 km still cut Land's End (-118.6056) and East End
    # (-118.3034) mid-island. 16 km covers the whole island with
    # ~1.6-2.4 km of water past each tip. Pixel pitch at 480 px is
    # ~67 m — coarser than the other spots but still well under the
    # NCEI source artefact scale, and whole-island context matters
    # more for an offshore island than the last metre of sharpness.
    "catalina": 16,
    "monterey": 6,
    # Kelp bed strip runs ~9 km N-S along the peninsula (Ocean Beach to
    # past the Cabrillo tip); 5 km covers the whole bed + margins.
    "pointloma": 5,
    # ── Phase 2: Channel Islands ── radius covers island tip-to-tip +
    # water margin. Big islands (Santa Cruz ~30 km, San Clemente ~34 km)
    # take a Catalina-style large radius → coarser pixel pitch at 480 px,
    # acceptable for whole-island context. Re-check each against render.
    "anacapa":     6,
    # 2026-06-15: 20 → 24 after QA render — 20 km clipped the island's
    # east end (San Pedro Pt); Santa Cruz is ~35 km E-W.
    "santacruz":   24,
    "santarosa":   16,
    "sanmiguel":   11,
    "sbisland":    3,
    "sannicolas":  11,
    "sanclemente": 15,
    # Phase 2: shore-diving hubs (cove/reef scale; PV spans the peninsula).
    "laguna":      4,
    "palosverdes": 7,
    "redondo":     3,
    # 2026-06-16: malibu 5→4 (zoom past the Santa Monica canyon onto the
    # Point Dume kelp), refugio 5→3 (gentle straight shelf read as deep-
    # water-heavy at 10 km; 6 km frames the dive zone).
    "malibu":      4,
    "refugio":     3,
    "pointlobos":  3,
    "monastery":   3,
    "jadecove":    3,
    "saltpoint":   4,
    "mendocino":   4,
    # Coronado Islands chain spans ~10 km N-S (North Coronado → South
    # Coronado); 9 km radius covers all four islets + the deep channel.
    "coronados":   6,
}

# ── Baja region spot bundles ─────────────────────────────────────────────
# When SHOULDIDIVE_REGION=baja the builder swaps the CA dicts above for
# these. Coords mirror REGION_SAVED_SPOTS.baja in src/lib/mapData.js (the
# frontend's saved-spot pins); radii sized to the feature + a water margin
# (islands large; reefs / seamounts / points small). Re-check each against
# the render and bump if clipped, same as the CA islands.
# 2026-06-16 re-tune + QA pass: centres re-anchored on the actual dive feature
# (research + Landsat kelp-centroid cross-check), radii tightened, and framing
# offset toward the dive water so a big island doesn't fill the frame. Dropped
# from the DETAIL set (kept as main-map points only, no bundle): marisla
# (= el-bajo dup) and — per user QA on the live maps — cabo / gordo-banks /
# el-bajo / cerralvo (deep seamounts/pinnacles + a land-heavy cape the coarse
# Mexico DEM can't render as useful dive charts). "loreto" → Isla Coronado.
BAJA_SPOT_CENTRES = {
    # 2026-06-16: same seaward re-framing pass as the CA shore spots —
    # abreojos (mainland point) shifted S off its land block; espiritu-stm /
    # isla-carmen (big islands) shifted W onto the dive coast so the island
    # sits ~1/3 of the frame and the reef water dominates. Each checked
    # against a render. The rest were already water-dominant (islands/reefs
    # framed with water around them; salsipuedes' kelp coast frames fine).
    # Pacific side — kelp coast (California Current)
    "salsipuedes":  {"name": "Salsipuedes",      "lng": -116.787, "lat": 31.974},
    "sacramento":   {"name": "Sacramento Reef",  "lng": -115.790, "lat": 29.760},
    "san-benito":   {"name": "Islas San Benito", "lng": -115.575, "lat": 28.306},
    "cedros":       {"name": "Isla Cedros",      "lng": -115.165, "lat": 28.080},
    "bahia-tort":   {"name": "Bahia Tortugas",   "lng": -114.896, "lat": 27.655},
    "abreojos":     {"name": "Punta Abreojos",   "lng": -113.575, "lat": 26.700},
    # Sea of Cortez — south (La Paz)
    "cabo-pulmo":   {"name": "Cabo Pulmo",       "lng": -109.424, "lat": 23.434},
    "los-islotes":  {"name": "Los Islotes",      "lng": -110.385, "lat": 24.600},
    "espiritu-stm": {"name": "Espiritu Santo",   "lng": -110.422, "lat": 24.520},
    # Sea of Cortez — central (Loreto NP)
    "isla-carmen":  {"name": "Isla Carmen",      "lng": -111.198, "lat": 26.030},
    "isla-danzante":{"name": "Isla Danzante",    "lng": -111.251, "lat": 25.786},
    "loreto":       {"name": "Loreto",           "lng": -111.274, "lat": 26.119},
    # Midriff Islands
    "bahia-angel":  {"name": "Bahia de los Angeles", "lng": -113.510, "lat": 29.035},
}

BAJA_SPOT_RADIUS_KM = {
    "salsipuedes": 5, "sacramento": 6, "san-benito": 5,
    "cedros": 7,         # SE dive coast, island pushed to the W edge
    "bahia-tort": 7,
    "abreojos": 6,
    "cabo-pulmo": 7,     # the reef park
    "los-islotes": 4,    # sea-lion islets (Partida pushed to the edge)
    "espiritu-stm": 6,   # W dive coast, island to one side
    "isla-carmen": 6,    # NW dive coast (Punta Lobos), island to one side
    "isla-danzante": 6,
    "loreto": 6,         # Isla Coronado (dive area N of town)
    "bahia-angel": 8,    # the bay's dive islands
}

if REGION.name == "baja":
    SPOT_CENTRES = BAJA_SPOT_CENTRES
    SPOT_RADIUS_KM = BAJA_SPOT_RADIUS_KM

# Max sounding depth (ft) kept in a spot bundle. 330 ft ≈ 100 m, the
# technical-diving floor; deeper lattice samples are open-ocean noise
# that clutter deep-water spots (see the sounding filter in build_spot).
SOUNDING_MAX_FT = 330

# Curated landmark labels per spot — dive-relevant rather than general
# place names. Each entry is (lng, lat, label, importance) where
# importance is "marquee" (always shown, larger) | "major" | "minor"
# (visible at high zoom only). The frontend renders these as
# screen-space text labels with collision detection, so the list is
# kept small (5-10 per spot) to avoid clutter.
#
# Coordinates from OSM features cross-checked against the NCEI DEM
# (see audit note inside); not authoritative for navigation but
# accurate to roughly a DEM cell (~10-30 m). Add new spots here as the
# pilot expands.
SPOT_LANDMARKS = {
    # Coords re-verified 2026-06-10 after user QA on the new 10 m
    # chart ("marker points are not in the correct spots"): 16 of 42
    # entries failed an elevation sanity audit against the NCEI DEM —
    # dive anchors sampling on land, Catalina's Ship Rock 12 km off on
    # a 339 m ridge, Bird Rock in 240 m mid-channel water. Every entry
    # was re-checked against OSM features (Overpass) plus the NCEI DEM
    # (dive/marine must sample underwater, inland on land, coastal
    # near the waterline; water-area names like coves/harbors sit IN
    # their water). If you add a spot, run the same audit — eyeballing
    # coords off a small web map put markers hundreds of metres out.
    # Each entry: (lng, lat, label, importance[, category]).
    # Categories drive marker style in SpotDetailView:
    #   "dive"    — yellow anchor (key dive sites, mooring buoys)
    #   "coastal" — small dot (beaches, points along shore)
    #   "inland"  — gray dot (landmarks visible from the water for
    #               navigation reference: piers, buildings, peaks)
    #   "marine"  — italicised label, no dot (underwater features —
    #               canyons, banks, named kelp beds)
    # Default category is "coastal" for backwards-compat with old entries.
    "lajolla": [
        # Key dive sites — anchors sit in the water at the entry/site
        (-117.2722, 32.8512, "La Jolla Cove",          "marquee", "dive"),
        (-117.2697, 32.8508, "La Jolla Caves",         "major",   "dive"),
        (-117.2762, 32.8496, "Boomer Beach",           "minor",   "dive"),
        (-117.2798, 32.8472, "Children's Pool",        "major",   "dive"),
        (-117.2632, 32.8517, "Marine Room",            "minor",   "dive"),
        (-117.2580, 32.8595, "Vallecitos",             "minor",   "dive"),
        (-117.2815, 32.8458, "Hospital Reef",          "minor",   "dive"),
        # Coastal landmarks (beaches, points)
        (-117.2747, 32.8509, "Point La Jolla",         "major",   "coastal"),
        (-117.2535, 32.8870, "Black's Beach",          "major",   "coastal"),
        (-117.2810, 32.8320, "Windansea",              "minor",   "coastal"),
        (-117.2556, 32.8614, "La Jolla Shores Beach",  "major",   "coastal"),
        (-117.2542, 32.8700, "Scripps Beach",          "minor",   "coastal"),
        # Inland nav reference (visible from water — chart-style labels).
        # Scripps Pier intentionally samples water: the marker sits
        # mid-pier, and the pier deck is over the surf line.
        (-117.2572, 32.8666, "Scripps Pier",           "major",   "inland"),
        (-117.2506, 32.8659, "Birch Aquarium",         "major",   "inland"),
        (-117.2530, 32.8690, "Scripps Inst.",          "minor",   "inland"),
        (-117.2380, 32.8675, "Revelle Coll.",          "minor",   "inland"),
        (-117.2655, 32.8470, "Village of La Jolla",    "major",   "inland"),
        (-117.2435, 32.8400, "Mt Soledad",             "major",   "inland"),
        (-117.2500, 32.8540, "La Jolla Shores",        "major",   "inland"),
        # Underwater features (italic label only — no marker)
        (-117.2895, 32.8600, "La Jolla Canyon",        "marquee", "marine"),
        (-117.2700, 32.8590, "Mushroom Bch Reef",      "minor",   "marine"),
        # 2026-06-12 user placement: the summer yellowtail ambush zone
        # just outside the kelp line NW of the Cove point (local name,
        # not on NOAA charts).
        (-117.2760, 32.8500, "Yellowtail Hole",        "minor",   "marine"),
        (-117.2660, 32.8520, "Vallecitos Bank",        "minor",   "marine"),
        (-117.2730, 32.8560, "La Jolla Kelp Bed",      "major",   "marine"),
    ],
    "catalina": [
        # 16 km radius covers the whole island, Land's End → East End.
        # Centre stays at (-118.450, 33.389); landmarks span Two
        # Harbors (NW) → Avalon (SE), the full diver-relevant arc.
        (-118.3232, 33.3450, "Avalon Harbor",         "marquee"),
        (-118.3249, 33.3489, "Casino Point",          "major"),
        (-118.4995, 33.4430, "Two Harbors",           "marquee"),
        (-118.4954, 33.4440, "Isthmus Cove",          "major"),
        (-118.4917, 33.4631, "Ship Rock",             "major"),
        (-118.3860, 33.4142, "Italian Gardens",       "minor"),
        (-118.5178, 33.4594, "Big Geiger Cove",       "minor"),
        (-118.4872, 33.4512, "Bird Rock",             "minor"),
        (-118.3665, 33.4062, "Long Point",            "minor"),
    ],
    "monterey": [
        (-121.8930, 36.6072, "Monterey Harbor",       "marquee"),
        (-121.8926, 36.6092, "Coast Guard Pier",      "major"),
        (-121.9160, 36.6263, "Lover's Point",         "major"),
        (-121.9360, 36.6377, "Point Pinos",           "major"),
        (-121.9420, 36.6220, "Asilomar Beach",        "minor"),
        (-121.9530, 36.6210, "Pinnacles (Pt. Pinos)", "minor"),
        (-121.8955, 36.6101, "San Carlos Beach",      "major"),
        (-121.8905, 36.6080, "Breakwater Cove",       "marquee"),
        # Stillwater Cove (Pebble Beach) at the south end of the
        # bbox. The previous coord was 100s of metres off.
        (-121.9450, 36.5640, "Stillwater Cove",       "minor"),
    ],
}

# Pixel size for the spot's bathy PNG. 480 × 480 keeps each bundle tiny
# (~120-180 KB encoded) while delivering above-display-resolution detail
# at the 4-8 km radius (one pixel ≈ 17-33 m on the ground).
BATHY_SIZE = 480

# Depth encoding (mirrors fetch_bathy.py). 0 = NaN/land; 1..255 linear
# over depth_range_m. Per-spot depth ranges are different — La Jolla
# Canyon hits ~400 m within 4 km of shore; Catalina's deepest local
# bathy is ~600-800 m off the windward side; Monterey Canyon is ~1500+ m
# offshore but the inner 6 km is ~200 m. We pick per-spot ranges from
# observation rather than a global "0 to 6000 m" range, so the inshore
# detail (where divers actually go) gets the full 8-bit ramp.
SPOT_DEPTH_RANGES_M = {
    "lajolla":  (0,  500),    # La Jolla Canyon rim drops fast
    # 2026-06-10: 800 → 1300 with the 16 km radius — the wider bbox
    # reaches the Catalina Basin south of the island (~1100+ m), which
    # the old range clamped into a flat max-depth band.
    "catalina": (0, 1300),
    "monterey": (0, 1200),    # Inner Monterey Canyon
    # ── Phase 2: Channel Islands ── per-island floor from local bathy;
    # the outer Channel Islands sit on deep escarpments (San Clemente's
    # west wall, San Nicolas basin). Tune against the render's depth band.
    "anacapa":     (0, 400),
    "santacruz":   (0, 800),
    "santarosa":   (0, 600),
    "sanmiguel":   (0, 500),
    "sbisland":    (0, 600),
    "sannicolas":  (0, 700),
    "sanclemente": (0, 1100),
    # Phase 2: shore-diving hubs. Canyon spots (Redondo, Monastery/Carmel
    # Canyon) drop fast; NorCal/SB reefs are shallow shelf.
    "laguna":      (0, 150),
    "palosverdes": (0, 250),
    "redondo":     (0, 400),
    "malibu":      (0, 200),
    "refugio":     (0, 120),
    "pointlobos":  (0, 200),
    "monastery":   (0, 400),
    "jadecove":    (0, 300),
    "saltpoint":   (0, 100),
    "mendocino":   (0, 100),
    "coronados":   (0, 400),   # deep channel between the islands + mainland
}
DEFAULT_DEPTH_RANGE_M = (0, 500)

# Contour intervals (m). Sparser at depth — 1 m up to 10 m is dive-
# planning detail; 5 m to 50 m is nav-zone; 25 m past 50 m is bottom
# context only. Tuned so each spot ends up with ~20-50 contour lines
# total — readable but not noisy.
CONTOUR_INTERVALS = [
    (0,    10,  1),
    (10,   50,  5),
    (50,   500, 25),
    (500,  5000, 100),
]

# Skip-if-fresh threshold. 30 days matches the handover's recommendation
# — CUDEM is decadal, coastline is yearly, kelp/mpa rarely change.
FRESHNESS_DAYS = 30

GMRT_URL = "https://www.gmrt.org/services/GridServer"

# NOAA NCEI best-available DEM mosaic (CUDEM ninth/third arc-sec tiles
# nearshore, coarser global sources offshore, blended per pixel on the
# server). exportImage with an EPSG:4326 bbox returns an F32 GeoTIFF —
# one HTTP round-trip per spot, no tile stitching. See module docstring
# for why this replaced GMRT as the primary spot-bundle source.
NCEI_MOSAIC_URL = ("https://gis.ngdc.noaa.gov/arcgis/rest/services/"
                   "DEM_mosaics/DEM_global_mosaic/ImageServer/exportImage")

# Per-spot Overpass coastline fetch (PR Spot-K). The wide-map land.geojson
# is simplified to ~10 m for whole-CA viewport rendering — fine at 8×
# zoom max, but at the spot view's 16× zoom the simplification eats
# harbor jetties, breakwaters, and inlet geometry. Per-spot Overpass
# pulls go straight against the OSM coastline ways at native resolution
# (sub-metre vertices), then we round to 5 decimals (~1 m) and store
# in the bundle. ~4-8 km bbox per spot keeps the fetch tiny.
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def km_to_degrees(km: float, lat: float) -> tuple[float, float]:
    """Convert km to (d_lng, d_lat) at a given latitude.

    Latitudes degrees are roughly constant (111 km / °); longitude
    degrees shrink with cos(lat). Used to derive the spot bbox from
    a (centre, radius_km) pair.
    """
    d_lat = km / 111.0
    d_lng = km / (111.0 * max(math.cos(math.radians(lat)), 0.05))
    return d_lng, d_lat


def spot_bbox(centre_lng: float, centre_lat: float, radius_km: float) -> dict:
    """Square-ish bbox in lng/lat space around the centre.

    "Square-ish" because we use cos(lat) to make lng/lat distances
    match metrically — the resulting bbox is wider in degrees than
    tall (at CA latitudes, lng degrees are ~80% as long as lat
    degrees), but rendered with `projectInBbox` it covers the same
    on-the-ground distance both ways.
    """
    d_lng, d_lat = km_to_degrees(radius_km, centre_lat)
    return {
        "lng_min": centre_lng - d_lng,
        "lng_max": centre_lng + d_lng,
        "lat_min": centre_lat - d_lat,
        "lat_max": centre_lat + d_lat,
    }


# ---------------------------------------------------------------------------
# NCEI bathymetry fetch (primary source)
# ---------------------------------------------------------------------------

def fetch_ncei_dem(bbox: dict, px: int = 960):
    """Pull the NOAA NCEI best-available DEM mosaic for the spot bbox.

    Returns (elev_z_m, lats_south_to_north, lons_west_to_east) where
    elev_z_m is metres relative to the DEM datum (negative = below sea
    level, NaN = mosaic nodata) — same contract parse_gmrt_netcdf
    returns, so resample_to_bbox consumes either source unchanged.

    Two-step fetch: exportImage with f=json first (the response's
    `extent` is authoritative — the server EXPANDS the requested bbox
    to make square-degree pixels, so georeferencing the raster off the
    requested bbox would shift everything south by ~700 m), then the
    returned href for the actual F32 GeoTIFF, which PIL reads natively
    (mode "F"; uncompressed tiled, no rasterio/GDAL needed).

    px=960 (2× the 480 output) keeps ≥1 source row per output row over
    the spot bbox after the server's extent expansion, and gives the
    local bilinear resample headroom; at the 24 km Catalina bbox that
    is ~25 m/px, still well above the chart's effective resolution.
    """
    params = {
        "bbox": (f"{bbox['lng_min']},{bbox['lat_min']},"
                 f"{bbox['lng_max']},{bbox['lat_max']}"),
        "bboxSR": "4326",
        "imageSR": "4326",
        "size": f"{px},{px}",
        "format": "tiff",
        "pixelType": "F32",
        "interpolation": "RSP_BilinearInterpolation",
        "f": "json",
    }
    print(f"  GET NCEI DEM mosaic {bbox['lng_min']:.4f},{bbox['lat_min']:.4f} → "
          f"{bbox['lng_max']:.4f},{bbox['lat_max']:.4f} @ {px}px")
    r = requests.get(NCEI_MOSAIC_URL, params=params, timeout=180)
    r.raise_for_status()
    meta = r.json()
    if "error" in meta:
        raise RuntimeError(f"NCEI exportImage error: {meta['error']}")
    ext = meta["extent"]
    w, h = int(meta["width"]), int(meta["height"])

    r2 = requests.get(meta["href"], timeout=180)
    r2.raise_for_status()
    img = Image.open(io.BytesIO(r2.content))
    z = np.asarray(img, dtype=np.float32)
    if z.shape != (h, w):
        raise ValueError(f"NCEI TIFF shape {z.shape} != metadata {(h, w)}")
    # ArcGIS marks nodata with ±3.4e38 in F32 exports (no alpha band).
    z = np.where(np.abs(z) > 1e10, np.nan, z)

    # Pixel-centre coordinates from the (adjusted) extent. TIFF row 0
    # is the north edge; flip to S→N to match resample_to_bbox's
    # ascending-lats contract.
    ps_x = (ext["xmax"] - ext["xmin"]) / w
    ps_y = (ext["ymax"] - ext["ymin"]) / h
    lons = ext["xmin"] + (np.arange(w) + 0.5) * ps_x
    lats = ext["ymin"] + (np.arange(h) + 0.5) * ps_y
    z = z[::-1, :]
    return z, lats, lons


# ---------------------------------------------------------------------------
# GMRT bathymetry fetch + parse (hole-fill + fallback source)
# ---------------------------------------------------------------------------

def fetch_gmrt_dem(bbox: dict) -> bytes:
    """Pull GMRT high-resolution NetCDF for the spot bbox.

    Same endpoint fetch_bathy.py uses, with resolution=high instead
    of med (~61 m grid at spot-bbox scale; resolution=max and
    layer=topo-mask return byte-identical grids here — probed
    2026-06-10). Where GMRT has no measured nearshore data it serves
    its smooth predicted background, which is why this is now the
    fill/fallback source rather than the primary (see module
    docstring).
    """
    params = {
        "north":      bbox["lat_max"],
        "south":      bbox["lat_min"],
        "west":       bbox["lng_min"],
        "east":       bbox["lng_max"],
        "format":     "netcdf",
        "resolution": "high",
    }
    print(f"  GET GMRT high-res {bbox['lng_min']:.4f},{bbox['lat_min']:.4f} → "
          f"{bbox['lng_max']:.4f},{bbox['lat_max']:.4f}")
    r = requests.get(GMRT_URL, params=params, timeout=120)
    r.raise_for_status()
    return r.content


def parse_gmrt_netcdf(nc_bytes: bytes):
    """Extract (elev_z_m, lats_south_to_north, lons_west_to_east) from
    a GMRT NetCDF response. elev_z_m is metres, negative below sea
    level — depth conversion happens in build_spot AFTER the NCEI/GMRT
    blend, so land stays distinguishable from nodata during the blend.

    GMRT serves the GMT-style flat grid format (x_range / y_range /
    z_range / dimension / z[nx*ny]) rather than CF-compliant per-
    coordinate arrays. We handle both formats — same logic as
    fetch_bathy.py, just inlined here to keep this builder
    self-contained.
    """
    import os
    import tempfile
    import xarray as xr

    with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as tf:
        tf.write(nc_bytes)
        tmp_path = tf.name
    try:
        ds = xr.open_dataset(tmp_path, engine="netcdf4")
        ds.load()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if "x_range" in ds.variables and "dimension" in ds.variables:
        x_range = np.asarray(ds["x_range"].values).flatten()
        y_range = np.asarray(ds["y_range"].values).flatten()
        dim     = np.asarray(ds["dimension"].values).flatten()
        nx, ny  = int(dim[0]), int(dim[1])
        z_flat  = np.asarray(ds["z"].values).flatten().astype(np.float32)
        if z_flat.size != nx * ny:
            raise ValueError(f"GMT grid: z size {z_flat.size} != nx*ny {nx * ny}")
        z = z_flat.reshape(ny, nx)
        lons = np.linspace(x_range[0], x_range[1], nx)
        lats = np.linspace(y_range[1], y_range[0], ny)  # N→S
    else:
        # CF-compliant fallback (in case GMRT changes format)
        Z_NAMES = ("z", "altitude", "elevation", "depth", "Band1")
        LON_NAMES = ("lon", "longitude", "x")
        LAT_NAMES = ("lat", "latitude", "y")
        def _pick(names, where):
            for n in names:
                if n in where: return n
            return None
        zname = _pick(Z_NAMES, ds.variables)
        lonname = _pick(LON_NAMES, ds.coords) or _pick(LON_NAMES, ds.variables)
        latname = _pick(LAT_NAMES, ds.coords) or _pick(LAT_NAMES, ds.variables)
        if not (zname and lonname and latname):
            raise KeyError(f"Unrecognized NetCDF schema: vars={list(ds.variables)}")
        z = np.asarray(ds[zname].values, dtype=np.float32)
        lons = np.asarray(ds[lonname].values)
        lats = np.asarray(ds[latname].values)

    # Normalize: lats S→N, lons W→E
    if lats[0] > lats[-1]:
        lats = lats[::-1]
        z = z[::-1, :]
    if lons[0] > lons[-1]:
        lons = lons[::-1]
        z = z[:, ::-1]

    return z.astype(np.float32), lats, lons


def resample_to_bbox(depth, src_lats, src_lons, bbox, out_w, out_h):
    """Bilinear resample depth raster onto a regular (out_h × out_w)
    grid spanning the spot bbox, row 0 = north edge."""
    out_lng = np.linspace(bbox["lng_min"], bbox["lng_max"], out_w)
    out_lat = np.linspace(bbox["lat_max"], bbox["lat_min"], out_h)

    # Fractional source indices
    fx = np.interp(out_lng, src_lons, np.arange(len(src_lons)))
    fy = np.interp(out_lat, src_lats, np.arange(len(src_lats)))

    x0 = np.clip(np.floor(fx).astype(int), 0, len(src_lons) - 1)
    x1 = np.clip(x0 + 1, 0, len(src_lons) - 1)
    y0 = np.clip(np.floor(fy).astype(int), 0, len(src_lats) - 1)
    y1 = np.clip(y0 + 1, 0, len(src_lats) - 1)

    wx = fx - x0
    wy = fy - y0

    # 2026-05-27 ROOT-CAUSE FIX for the "false land section" /
    # "phantom coastline cutting the bay" QA reports: this function
    # used to re-flip the y indices (`y0f = (src_h - 1) - y0`) on the
    # theory that out_lat being N→S required it. It doesn't —
    # np.interp(out_lat, src_lats, arange) ALREADY returns the correct
    # fractional row index into the S→N-ordered depth array for every
    # output latitude, whatever order out_lat is in. The extra flip
    # vertically MIRRORED every spot's bathy (north↔south): La Jolla's
    # headland bathymetry rendered up in the Shores bay as a flat
    # "2 m plateau"/false land, the real shelf rendered down south.
    # Verified against GMRT esriascii truth: the shipped PNG's
    # transect at lng −117.262 matched GMRT at MIRRORED latitudes
    # exactly (PNG 2.0/3.9/9.8/11.8 m ↔ truth −1.96/−3.07/−8.74/
    # −11.65 m at the mirror points). Contours + soundings derive
    # from this grid, so the one fix repairs all three layers.
    out = np.full((out_h, out_w), np.nan, dtype=np.float32)
    for j in range(out_h):
        v00 = depth[y0[j], x0]
        v01 = depth[y0[j], x1]
        v10 = depth[y1[j], x0]
        v11 = depth[y1[j], x1]
        wy_j = wy[j]
        top = v00 * (1 - wx) + v01 * wx
        bot = v10 * (1 - wx) + v11 * wx
        out[j, :] = top * (1 - wy_j) + bot * wy_j
    return out


def encode_depth_png(depth_grid, depth_range_m, out_path: Path):
    """Encode the depth grid as 8-bit grayscale+alpha PNG.

    Channels:
      L (luminance) = depth pixel: 0..255 linear over depth_range_m
      A (alpha)     = 0 for NaN/land cells, 255 for valid depths

    Why two channels: a plain grayscale PNG renders NaN cells as black,
    which produces a visible black strip wherever the GMRT bathy data
    disagrees with the OSM coastline polygon (OSM water + GMRT land =
    bathy renders black, coastline doesn't cover it).

    Using an alpha channel makes those NaN cells transparent so the
    chart-style cyan background shows through cleanly. The valid pixel
    encoding stays linear 1..255 across depth_range_m so the
    pixelToDepthM consumer in SpotDetailView keeps working unchanged.
    """
    d_min, d_max = depth_range_m
    span = max(d_max - d_min, 1.0)
    valid = np.isfinite(depth_grid)
    h, w = depth_grid.shape
    la = np.zeros((h, w, 2), dtype=np.uint8)  # [L, A]
    if valid.any():
        normalized = np.clip((depth_grid[valid] - d_min) / span, 0.0, 1.0)
        la[..., 0][valid] = (1 + np.round(normalized * 254.0)).astype(np.uint8)
        la[..., 1][valid] = 255
    img = Image.fromarray(la, mode="LA")
    img.save(out_path, format="PNG", optimize=True)


# ---------------------------------------------------------------------------
# Depth contours via custom marching-squares
# ---------------------------------------------------------------------------

def _trace_contours_at_level(depth, level, bbox):
    """Naive marching-squares contour extraction at a single depth level.

    Returns a list of (lng, lat) polylines. We don't try to close rings
    or chain segments — the visual rendering pass treats each segment
    independently, which is fine for thin contour lines.

    Why not skimage.measure.find_contours? Adding scikit-image as a
    runtime dep balloons the wheel size by ~40 MB and pulls in matplotlib
    + scipy.sparse for one function. The custom traversal here is ~30
    lines and produces equivalent output for our use case (visual
    contour overlay, not metrology).
    """
    h, w = depth.shape
    # Pre-compute mask of valid (non-NaN) cells
    valid = np.isfinite(depth)
    # Replace NaN with a value above the level so it doesn't generate
    # spurious crossings into land
    d = np.where(valid, depth, level + 1e9)

    # Cell-by-cell marching squares. For each 2×2 cell, check which
    # corners are below the level; emit one or two segments per case.
    segments = []
    dx_lng = (bbox["lng_max"] - bbox["lng_min"]) / (w - 1)
    dy_lat = (bbox["lat_max"] - bbox["lat_min"]) / (h - 1)

    for j in range(h - 1):
        for i in range(w - 1):
            # Corner values: top-left, top-right, bottom-right, bottom-left
            v00 = d[j,     i    ]
            v01 = d[j,     i + 1]
            v11 = d[j + 1, i + 1]
            v10 = d[j + 1, i    ]
            # 4-bit case index
            case = 0
            if v00 < level: case |= 1
            if v01 < level: case |= 2
            if v11 < level: case |= 4
            if v10 < level: case |= 8
            if case == 0 or case == 15:
                continue
            # Edge interpolation helper — `t` is the fractional position
            # along the edge where the contour crosses
            def edge(a, b):
                if a == b: return 0.5
                return (level - a) / (b - a)

            # Corner pixel coords in lng/lat space
            x_l = bbox["lng_min"] + i * dx_lng
            x_r = x_l + dx_lng
            y_t = bbox["lat_max"] - j * dy_lat
            y_b = y_t - dy_lat

            # Crossing points on each edge (only computed if needed)
            pts = {}
            if (case in (1, 14, 3, 12, 5, 10, 7, 8, 9, 6, 11, 4, 13, 2)):
                # top edge (between v00 and v01)
                t = edge(v00, v01)
                pts["T"] = (x_l + t * dx_lng, y_t)
                # right edge (v01 - v11)
                t = edge(v01, v11)
                pts["R"] = (x_r, y_t - t * dy_lat)
                # bottom edge (v10 - v11)
                t = edge(v10, v11)
                pts["B"] = (x_l + t * dx_lng, y_b)
                # left edge (v00 - v10)
                t = edge(v00, v10)
                pts["L"] = (x_l, y_t - t * dy_lat)

            # Case → segment list. 16 cases, but the lookup table is
            # symmetric across "below the level" vs "above" so we only
            # write 8 (cases 0/15 skipped above).
            CASES = {
                1:  [("L", "T")],
                2:  [("T", "R")],
                3:  [("L", "R")],
                4:  [("R", "B")],
                5:  [("L", "T"), ("R", "B")],   # saddle
                6:  [("T", "B")],
                7:  [("L", "B")],
                8:  [("B", "L")],
                9:  [("B", "T")],
                10: [("T", "L"), ("R", "B")],   # saddle
                11: [("R", "B")],
                12: [("L", "R")],
                13: [("T", "R")],
                14: [("L", "T")],
            }
            for (a, b) in CASES.get(case, []):
                if a in pts and b in pts:
                    segments.append((pts[a], pts[b]))
    return segments


def _chain_segments(segments, snap_decimals=5):
    """Chain adjacent (lng, lat) segments into longer polylines.

    Marching-squares emits one 2-point segment per cell crossing. For
    a long contour line with N cells along it, that's N short paths
    in the output. Chaining them into M continuous polylines (M << N)
    is the single biggest size-reduction win — drops file size 5-10x
    vs naive per-segment dump.

    Endpoints are snapped to `snap_decimals` (~1 m at decimal=5) before
    hashing so floating-point jitter on a shared edge doesn't break
    the chain. Two-pass: build endpoint→segment index, then trace
    forward + backward from each unused seed.
    """
    if not segments: return []
    def key(p):
        return (round(float(p[0]), snap_decimals), round(float(p[1]), snap_decimals))

    endpoint_idx = {}
    snapped = []
    for i, (p1, p2) in enumerate(segments):
        ka, kb = key(p1), key(p2)
        snapped.append((ka, kb, p1, p2))
        endpoint_idx.setdefault(ka, []).append(i)
        endpoint_idx.setdefault(kb, []).append(i)

    used = [False] * len(snapped)
    chains = []
    for start in range(len(snapped)):
        if used[start]: continue
        used[start] = True
        ka, kb, p1, p2 = snapped[start]
        chain = [p1, p2]
        cur = kb
        while True:
            nexts = [j for j in endpoint_idx.get(cur, []) if not used[j]]
            if not nexts: break
            j = nexts[0]; used[j] = True
            jka, jkb, jp1, jp2 = snapped[j]
            if jka == cur:
                chain.append(jp2); cur = jkb
            else:
                chain.append(jp1); cur = jka
        cur = ka
        while True:
            nexts = [j for j in endpoint_idx.get(cur, []) if not used[j]]
            if not nexts: break
            j = nexts[0]; used[j] = True
            jka, jkb, jp1, jp2 = snapped[j]
            if jka == cur:
                chain.insert(0, jp2); cur = jkb
            else:
                chain.insert(0, jp1); cur = jka
        chains.append(chain)
    return chains


def _douglas_peucker(points, epsilon_deg):
    """Douglas-Peucker simplify on a polyline of [lng, lat] points.

    Mirrors the JS simplifier in src/lib/vectorSimplify.js. For these
    contour bundles ~1e-4 deg (≈ 11 m) is a sane tolerance — about
    half the bathy PNG's pixel pitch, so simplification can't drop
    detail finer than the source.
    """
    if len(points) < 3 or epsilon_deg <= 0:
        return points
    eps_sq = epsilon_deg * epsilon_deg
    keep = [False] * len(points)
    keep[0] = True
    keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        lo, hi = stack.pop()
        if hi - lo < 2: continue
        a = points[lo]; b = points[hi]
        ax, ay = float(a[0]), float(a[1])
        bx, by = float(b[0]), float(b[1])
        dx = bx - ax; dy = by - ay
        denom = dx * dx + dy * dy
        max_d = -1.0; max_i = -1
        for i in range(lo + 1, hi):
            p = points[i]
            px, py = float(p[0]), float(p[1])
            if denom == 0:
                ex, ey = px - ax, py - ay
            else:
                t = ((px - ax) * dx + (py - ay) * dy) / denom
                if t < 0:   nx, ny = ax, ay
                elif t > 1: nx, ny = bx, by
                else:       nx, ny = ax + t * dx, ay + t * dy
                ex, ey = px - nx, py - ny
            d = ex * ex + ey * ey
            if d > max_d:
                max_d = d; max_i = i
        if max_d > eps_sq and max_i > 0:
            keep[max_i] = True
            stack.append((lo, max_i))
            stack.append((max_i, hi))
    return [points[i] for i in range(len(points)) if keep[i]]


def _smooth_contour_grid(depth_grid, size: int = 3):
    """Median de-speckle the depth grid before contour tracing.

    NCEI's synthesised bathy carries single-pixel speckle + stripe
    artefacts in some offshore bboxes (see the module docstring). Raw
    marching-squares turns that speckle into tens of thousands of tiny
    segments per level, and the chaining pass then blows up — San
    Nicolas hung at ~650 s CPU on a 12 km spot. A 3×3 median filter
    removes the speckle (edge-preserving, unlike a blur) so the segment
    count stays sane, with no visible change on already-clean DEMs.
    Runs on a COPY — the raw grid still feeds the bathy PNG + soundings.
    Land (NaN) is preserved.
    """
    g = np.asarray(depth_grid, dtype=float)
    try:
        from scipy.ndimage import median_filter
    except Exception:
        return g  # scipy absent → skip, keep prior behaviour
    nan_mask = np.isnan(g)
    if nan_mask.all():
        return g
    filled = np.where(nan_mask, float(np.nanmedian(g)), g)
    sm = median_filter(filled, size=size, mode="nearest")
    sm[nan_mask] = np.nan
    return sm


def generate_contours(depth_grid, bbox) -> dict:
    """Walk the depth grid + produce a GeoJSON FeatureCollection of
    MultiLineString contours, one feature per depth level.

    Each feature carries `properties.depth_m` so the frontend can
    style by depth bin (e.g. thicker stroke at every 10 m).

    Three optimisations stack on top of raw marching-squares:
      1. Skip levels deeper than the spot's actual max depth (no point
         tracing a 2000 m contour on a 300 m spot — those passes
         iterate every cell to produce zero segments).
      2. Chain adjacent segments into polylines so the output is
         100s of long lines, not 10000s of 2-point fragments.
      3. Douglas-Peucker simplify each polyline at ~11 m tolerance
         (half the bathy PNG pixel pitch) — drops 60-80% of vertices
         with no visible change at the rendered scale.

    On a typical CA spot bundle (480 × 480 grid, 4-8 km radius), these
    three together drop the contours.geojson from ~5-11 MB down to
    ~100-400 KB.
    """
    # De-speckle the DEM first: NCEI artefacts otherwise explode the
    # marching-squares segment count + hang the chaining pass.
    depth_grid = _smooth_contour_grid(depth_grid)
    # Effective max depth — skip levels past this so we don't trace
    # contours that can't exist (saves CPU + cuts file size).
    try:
        grid_max = float(np.nanmax(depth_grid))
    except (ValueError, RuntimeError):
        grid_max = 500.0
    # 10% padding so we don't accidentally clip a borderline contour
    max_depth_m = grid_max * 1.1

    features = []
    simplify_tol_deg = 1e-4  # ~11 m at CA latitudes — half a pixel

    for (lo, hi, step) in CONTOUR_INTERVALS:
        levels = list(range(lo, hi, step))
        if hi not in levels: levels.append(hi)
        for level in levels:
            if level == 0:
                continue  # shoreline is the coastline overlay's job
            if level > max_depth_m:
                continue  # skip out-of-range — no surface to trace
            segments = _trace_contours_at_level(depth_grid, level, bbox)
            if not segments:
                continue
            chains = _chain_segments(segments)
            coords = []
            for chain in chains:
                simplified = _douglas_peucker(chain, simplify_tol_deg)
                if len(simplified) < 2:
                    continue
                coords.append([
                    [round(float(p[0]), 5), round(float(p[1]), 5)]
                    for p in simplified
                ])
            if not coords:
                continue
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "MultiLineString",
                    "coordinates": coords,
                },
                "properties": {
                    "depth_m": level,
                },
            })
    return {
        "type": "FeatureCollection",
        "features": features,
    }


# ---------------------------------------------------------------------------
# GeoJSON clipping (coastline / kelp / mpa)
# ---------------------------------------------------------------------------

def clip_geojson_to_bbox(src_path: Path, bbox: dict) -> dict | None:
    """Read a GeoJSON file + return a new FeatureCollection containing
    only features whose geometry intersects the bbox.

    We use a feature-level bbox prefilter rather than full geometry
    intersection because (a) the source polygons are small enough
    that bbox overlap = visual overlap for our purposes, and (b) it
    avoids pulling shapely's full geom intersection cost on the
    daily refresh. Properties pass through unchanged.

    Returns None if the source file doesn't exist (e.g. kelp before
    Phase 1A shipped) — caller treats that as "skip this layer."
    """
    if not src_path.exists():
        return None
    with src_path.open("r", encoding="utf-8") as f:
        fc = json.load(f)
    features = fc.get("features", []) or []
    keep = []
    for feat in features:
        geom = feat.get("geometry")
        if not geom:
            continue
        b = _geometry_bounds(geom)
        if b is None:
            continue
        if (b["lng_max"] < bbox["lng_min"] or
            b["lng_min"] > bbox["lng_max"] or
            b["lat_max"] < bbox["lat_min"] or
            b["lat_min"] > bbox["lat_max"]):
            continue
        keep.append(feat)
    return {"type": "FeatureCollection", "features": keep}


def _clip_land_geometrically(land_path, bbox):
    """Geometric intersection clip of land polygons to the spot bbox.

    2026-05-27: the previous bbox-prefilter clip kept WHOLE features
    whose bounds touched the spot bbox — for any mainland spot that
    meant shipping the entire CA mainland polygon (~700 KB, ~60k
    vertices) in every bundle. The giant <path> was both bundle bloat
    and the main culprit behind multi-second first-paint stalls in
    the spot view. A true shapely intersection with a slightly
    buffered bbox keeps only the visible coastline; the straight cut
    edges sit ~300 m outside the viewport so they're never seen.

    Returns a FeatureCollection or None when land.geojson is missing.
    """
    if not land_path.exists():
        return None
    try:
        from shapely.geometry import shape as _shape, box as _box, mapping as _mapping
    except ImportError:
        # Shapely unavailable — fall back to the whole-feature clip.
        return clip_geojson_to_bbox(land_path, bbox)

    with land_path.open("r", encoding="utf-8") as f:
        fc = json.load(f)
    pad = 0.003  # ~300 m past the viewport so cut edges stay offscreen
    clip_box = _box(bbox["lng_min"] - pad, bbox["lat_min"] - pad,
                    bbox["lng_max"] + pad, bbox["lat_max"] + pad)
    keep = []
    for feat in fc.get("features", []) or []:
        geom = feat.get("geometry")
        if not geom:
            continue
        try:
            g = _shape(geom)
            if not g.intersects(clip_box):
                continue
            cut = g.intersection(clip_box)
            if cut.is_empty:
                continue
            keep.append({
                "type": "Feature",
                "geometry": _mapping(cut),
                "properties": feat.get("properties", {}) or {},
            })
        except Exception:
            continue
    return {"type": "FeatureCollection", "features": keep}


def _mask_depth_by_land(depth_grid, bbox, land_path):
    """Set bathy pixels to NaN wherever the OSM land polygon says
    "land". After this, the bathy PNG's transparency boundary
    matches the OSM coastline exactly — no secondary GMRT coastline
    artefact.

    Implementation: load all land features intersecting the spot
    bbox, union them once, build a prepared geometry for fast
    contains() tests, then walk the grid pixel-centre by pixel-centre.
    480 × 480 = 230k points; a prepared shapely poly with ~1k
    vertices runs each contains() in microseconds → whole pass is
    under 5 s per spot.
    """
    try:
        import json as _json
        from shapely.geometry import shape as _shape, Point as _Point, box as _box
        from shapely.ops import unary_union as _unary_union
        from shapely.prepared import prep as _prep
        from shapely.validation import make_valid as _make_valid
    except ImportError:
        print("    [mask] shapely missing — skipping OSM coastline burn-in")
        return depth_grid

    with land_path.open("r", encoding="utf-8") as f:
        fc = _json.load(f)
    features = fc.get("features", []) or []
    spot_box = _box(bbox["lng_min"], bbox["lat_min"],
                    bbox["lng_max"], bbox["lat_max"])
    geoms = []
    for feat in features:
        try:
            g = _shape(feat["geometry"])
            if g.is_empty:
                continue
            if not g.is_valid:
                # The CA mainland polygon (~60k vertices) carries a
                # self-intersection; skipping it here silently left
                # every mainland spot without the coastline burn-in
                # (caught 2026-06-10 — La Jolla's mask pass was a
                # no-op while island spots masked fine).
                g = _make_valid(g)
                if g.is_empty:
                    continue
            if g.intersects(spot_box):
                geoms.append(g)
        except Exception:
            continue
    if not geoms:
        return depth_grid  # spot is entirely offshore — nothing to mask
    land_union = _unary_union(geoms)
    prepared = _prep(land_union)

    h, w = depth_grid.shape
    masked = depth_grid.copy()
    # Pixel centre coords (top-left origin, north-up)
    lngs = bbox["lng_min"] + (np.arange(w) + 0.5) / w * (bbox["lng_max"] - bbox["lng_min"])
    lats = bbox["lat_max"] - (np.arange(h) + 0.5) / h * (bbox["lat_max"] - bbox["lat_min"])
    land_count = 0
    for j in range(h):
        lat = float(lats[j])
        for i in range(w):
            lng = float(lngs[i])
            if prepared.contains(_Point(lng, lat)):
                masked[j, i] = np.nan
                land_count += 1
    print(f"    [mask] burned OSM coastline into bathy: "
          f"{land_count} land pixels masked ({land_count * 100.0 / (w * h):.1f}%)")
    return masked


def _fill_nearshore_nodata(depth_grid, max_iters: int = 45):
    """Fill no-data depth cells by smoothly extending the nearest resolved depth
    inward, up to ~max_iters px from valid water.

    The coarse NCEI/GEBCO mosaic over Baja marks the shallow subtidal shelf as
    z≥0 (land elevation), so build_spot turns it into NaN and the kelp zone
    renders as "land" with a "land" depth readout — the exact bug the user hit.
    This dilation fills that nearshore band with a smooth ramp off the nearest
    real depths. It runs BEFORE the OSM land burn, so true above-water land is
    re-cut to NaN afterwards; only the OSM-water shelf keeps the filled depth.
    """
    import numpy as _np
    d = _np.asarray(depth_grid, dtype=_np.float32).copy()
    for _ in range(max_iters):
        nan = ~_np.isfinite(d)
        if not nan.any():
            break
        acc = _np.zeros_like(d)
        cnt = _np.zeros_like(d)
        for axis, shift in ((0, 1), (0, -1), (1, 1), (1, -1)):
            rolled = _np.roll(d, shift, axis=axis)
            valid = _np.isfinite(rolled)
            acc += _np.where(valid, _np.nan_to_num(rolled), 0.0)
            cnt += valid
        fillable = nan & (cnt > 0)
        if not fillable.any():
            break
        d[fillable] = (acc[fillable] / cnt[fillable])
    return d


def _clip_kelp_to_water(kelp_fc: dict, land_path, bbox: dict) -> dict:
    """Subtract the OSM land polygon from kelp polygons so kelp never paints
    over land. Landsat kelp false-positives on the wet rocky shore (surf /
    shadow read as canopy); against the bathy's OSM land mask that shows up as
    "kelp on land" with a "land" depth readout. Uses the SAME land.geojson the
    bathy mask uses, so kelp ⊆ water by construction.
    """
    if kelp_fc is None or not land_path.exists():
        return kelp_fc
    try:
        import json as _json
        from shapely.geometry import shape as _shape, box as _box, mapping as _mapping
        from shapely.ops import unary_union as _unary_union
        from shapely.validation import make_valid as _make_valid
    except ImportError:
        return kelp_fc
    with land_path.open("r", encoding="utf-8") as f:
        fc = _json.load(f)
    spot_box = _box(bbox["lng_min"], bbox["lat_min"], bbox["lng_max"], bbox["lat_max"])
    geoms = []
    for feat in fc.get("features", []) or []:
        try:
            g = _shape(feat["geometry"])
            if g.is_empty:
                continue
            if not g.is_valid:
                g = _make_valid(g)
                if g.is_empty:
                    continue
            if g.intersects(spot_box):
                geoms.append(g)
        except Exception:
            continue
    if not geoms:
        return kelp_fc  # offshore spot — no land to subtract
    land_union = _unary_union(geoms)
    out_feats = []
    dropped = 0
    for feat in kelp_fc.get("features", []) or []:
        try:
            g = _shape(feat["geometry"])
            if not g.is_valid:
                g = _make_valid(g)
            cut = g.difference(land_union)
            if cut.is_empty:
                dropped += 1
                continue
            if cut.geom_type not in ("Polygon", "MultiPolygon"):
                polys = [gg for gg in getattr(cut, "geoms", [cut])
                         if gg.geom_type in ("Polygon", "MultiPolygon")]
                if not polys:
                    dropped += 1
                    continue
                cut = _unary_union(polys)
            out_feats.append({"type": "Feature",
                              "properties": feat.get("properties", {}),
                              "geometry": _mapping(cut)})
        except Exception:
            out_feats.append(feat)  # keep original if the clip errors
    if dropped:
        print(f"    [kelp] clipped {dropped} all-on-land kelp feature(s) to water")
    return {"type": "FeatureCollection", "features": out_feats}


def fetch_overpass_coastline_for_bbox(bbox: dict) -> dict | None:
    """Pull native-resolution OSM coastline ways for a single spot bbox.

    Uses the same Overpass query shape fetch_coastline.py uses but
    without the simplify pass — at 4-8 km the raw vertex count is
    well under any reasonable budget (typical CA spot bbox has 1-3k
    coastline vertices), and the user explicitly asked for "true
    coastal view" at deep zoom.

    Returns a GeoJSON FeatureCollection with Polygon features (closed
    via the buffered bbox box like fetch_coastline.py) or None if all
    Overpass mirrors fail.
    """
    import time as _time
    try:
        from shapely.geometry import box as _box, mapping as _mapping
        from shapely.ops import polygonize as _polygonize, unary_union as _unary_union
        from shapely.geometry import LineString as _LineString
    except ImportError:
        print("    [coastline] shapely missing — skipping per-spot fetch")
        return None

    pad = 0.005  # ~500 m padding so coastline ways straddling the
                 # spot's edge close cleanly
    q_bbox = {
        "lat_min": bbox["lat_min"] - pad,
        "lat_max": bbox["lat_max"] + pad,
        "lng_min": bbox["lng_min"] - pad,
        "lng_max": bbox["lng_max"] + pad,
    }
    q = (
        f'[out:json][timeout:60];'
        f'way["natural"="coastline"]'
        f'({q_bbox["lat_min"]},{q_bbox["lng_min"]},'
        f'{q_bbox["lat_max"]},{q_bbox["lng_max"]});'
        f'out geom;'
    )
    last_err = None
    data = None
    for url in OVERPASS_ENDPOINTS:
        try:
            r = requests.post(url, data={"data": q}, timeout=90,
                              headers={"User-Agent": "shouldidive/0.1 spot-bundle"})
            r.raise_for_status()
            data = r.json()
            break
        except Exception as e:
            last_err = e
            print(f"    [coastline] {url} failed — {e!s}")
            _time.sleep(2)
    if data is None:
        print(f"    [coastline] all Overpass mirrors failed: {last_err!s}")
        return None

    # Collect LineStrings
    lines = []
    for el in data.get("elements", []):
        if el.get("type") != "way": continue
        geom = el.get("geometry") or []
        if len(geom) < 2: continue
        lines.append(_LineString([(p["lon"], p["lat"]) for p in geom]))
    if not lines:
        # No coastline at this spot — open ocean (e.g. far offshore).
        # Return an empty FC so the bundle still tracks the layer.
        return {"type": "FeatureCollection", "features": []}

    # Close into polygons via the buffered bbox boundary
    box_ring = _box(q_bbox["lng_min"], q_bbox["lat_min"],
                    q_bbox["lng_max"], q_bbox["lat_max"]).boundary
    merged = _unary_union(lines + [box_ring])
    polys = list(_polygonize(merged))

    # Land seed: spot centre is offshore by design (we're on a dive
    # spot!), so we can't seed from the centre. Instead identify the
    # ocean polygon (the one whose centroid is farthest from any
    # coastline vertex) and keep everything else as land.
    if not polys:
        return {"type": "FeatureCollection", "features": []}
    # Strategy: the ocean polygon is typically by FAR the largest in
    # area among the polygonize outputs for an offshore-centred spot
    # bbox. Drop the largest and keep the rest. Works because the
    # ocean spans the whole bbox while each land piece is a fragment.
    polys_sorted = sorted(polys, key=lambda p: p.area, reverse=True)
    if len(polys_sorted) == 1:
        # Only one polygon — no land at this spot (true open ocean)
        return {"type": "FeatureCollection", "features": []}
    # Drop the open-ocean polygon (largest); everything else is land
    land_polys = polys_sorted[1:]
    feats = []
    for poly in land_polys:
        # Skip below 1e-7 deg² (~1 m²) — sub-pixel rocks
        if poly.area < 1e-7:
            continue
        feats.append({
            "type": "Feature",
            "geometry": {
                **_mapping(poly),
                "coordinates": _round_geom_coords(_mapping(poly)["coordinates"], 5),
            },
            "properties": {},
        })
    return {"type": "FeatureCollection", "features": feats}


def _round_geom_coords(coords, decimals):
    """Recursively round coordinate arrays (supports Polygon +
    MultiPolygon nesting depth)."""
    if not coords: return coords
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], decimals), round(coords[1], decimals)]
    return [_round_geom_coords(c, decimals) for c in coords]


def _geometry_bounds(geom):
    """Compute the lng/lat bounding box of a GeoJSON geometry.
    Supports Polygon, MultiPolygon, LineString, MultiLineString.
    """
    coords = geom.get("coordinates")
    if not coords: return None
    pts = []
    t = geom.get("type")
    if t == "Polygon":
        for ring in coords:
            pts.extend(ring)
    elif t == "MultiPolygon":
        for poly in coords:
            for ring in poly:
                pts.extend(ring)
    elif t == "LineString":
        pts.extend(coords)
    elif t == "MultiLineString":
        for line in coords:
            pts.extend(line)
    elif t == "Point":
        pts.append(coords)
    elif t == "MultiPoint":
        pts.extend(coords)
    else:
        return None
    if not pts: return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return {
        "lng_min": min(xs), "lng_max": max(xs),
        "lat_min": min(ys), "lat_max": max(ys),
    }


# ---------------------------------------------------------------------------
# Freshness check + main loop
# ---------------------------------------------------------------------------

def bundle_is_fresh(bundle_dir: Path) -> bool:
    """Return True if bundle.json exists and was generated within
    FRESHNESS_DAYS — i.e. we can skip rebuilding."""
    manifest_path = bundle_dir / "bundle.json"
    if not manifest_path.exists():
        return False
    try:
        with manifest_path.open("r", encoding="utf-8") as f:
            manifest = json.load(f)
        gen_at = manifest.get("generated_at")
        if not gen_at:
            return False
        dt = datetime.fromisoformat(gen_at.replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - dt
        return age < timedelta(days=FRESHNESS_DAYS)
    except (json.JSONDecodeError, ValueError, KeyError):
        return False


# CDFW "Kelp Forests California 2002 to 2016" FeatureServer — same
# service fetch_kelp_canopy.py pulls statewide, but each layer is one
# aerial survey pass. The statewide kelp-canopy.geojson keeps only
# layer 0 (2016), surveyed at the BOTTOM of the 2014-16 marine-heatwave
# kelp crash — the big southern beds (Point Loma!) appear as tiny
# fragments there. Spot charts want the nautical-chart convention
# instead: "kelp area" = surveyed extent across years, plus the
# freshest healthy canopy pass for texture.
KELP_SURVEY_BASE = (
    "https://services1.arcgis.com/jOKMO9vKmc98J4JC/arcgis/rest/services/"
    "Kelp_Forests_California_2002_to_2016/FeatureServer/{layer}/query"
)
KELP_SURVEY_LAYERS = [  # (service layer index, survey year)
    (0, 2016), (1, 2015), (2, 2013), (3, 2011), (4, 2010),
    (5, 2009), (6, 2008), (7, 2006), (8, 2005), (9, 2003),
]


# Spots whose kelp comes from the SBC LTER "Kelp from Landsat" dataset
# instead of CDFW. CDFW's "Kelp Forests California 2002-2016" survey has
# no coverage in Baja (Coronados), thin/absent coverage for NorCal bull
# kelp (Salt Point, Mendocino), and timed out on some islands. The
# compact CA+Baja extract lives at pipeline/data/landsat_kelp_ca.nc and
# is refreshed quarterly by .github/workflows/refresh-kelp.yml so the
# spot bundles always rebuild against fresh canopy.
KELP_SOURCES_LANDSAT = {
    "coronados", "jadecove", "malibu", "palosverdes",
    "mendocino", "saltpoint", "sanclemente", "sannicolas",
    # Baja Pacific kelp coast (California Current). The widened Landsat
    # extract now reaches 27N, so these draw real canopy. Gulf-side Baja
    # spots are deliberately absent — there is no kelp in the Sea of Cortez.
    "salsipuedes", "sacramento", "san-benito", "cedros", "bahia-tort", "abreojos",
}
_LANDSAT_KELP_CACHE = None


def _load_landsat_kelp():
    """Lazily load the compact CA+Baja Landsat kelp extract once."""
    global _LANDSAT_KELP_CACHE
    if _LANDSAT_KELP_CACHE is None:
        import xarray as xr
        p = REPO_ROOT / "pipeline" / "data" / "landsat_kelp_ca.nc"
        ds = xr.open_dataset(p)
        _LANDSAT_KELP_CACHE = (
            ds.longitude.values, ds.latitude.values,
            ds.recent_area.values, ds.attrs.get("recent_quarter", "?"),
        )
    return _LANDSAT_KELP_CACHE


def fetch_spot_kelp_landsat(spot_id: str, bbox: dict):
    """Per-spot kelp from the SBC LTER Landsat extract.

    Emits the same two classes fetch_spot_kelp does, so the builder and
    frontend treat both sources identically:
      * "Kelp Subsurface" — every ever-kelp pixel in the bbox (extent).
      * "Kelp Canopy"     — pixels with canopy in the most recent year.
    Each 30 m pixel becomes a square; squares are unioned + lightly
    simplified. Returns (feature_collection, meta) or None.
    """
    if os.environ.get("BUILD_SKIP_KELP") == "1":
        return None
    try:
        import math
        from shapely.geometry import box as shp_box, mapping
        from shapely.ops import unary_union
    except ImportError:
        return None
    try:
        lon, lat, recent, recent_q = _load_landsat_kelp()
    except Exception as e:
        print(f"    [kelp] Landsat extract unavailable ({e!s}) — falling back")
        return None
    m = ((lon >= bbox["lng_min"]) & (lon <= bbox["lng_max"]) &
         (lat >= bbox["lat_min"]) & (lat <= bbox["lat_max"]))
    if not m.any():
        return None
    lo, la, re = lon[m], lat[m], recent[m]
    latm = (bbox["lat_min"] + bbox["lat_max"]) / 2.0
    dlat = 15.0 / 110540.0
    dlon = 15.0 / (111320.0 * math.cos(math.radians(latm)))

    def _polys(loi, lai):
        if len(loi) == 0:
            return None
        u = unary_union([shp_box(x - dlon, y - dlat, x + dlon, y + dlat)
                         for x, y in zip(loi, lai)])
        return u.simplify(0.00008)  # ~9 m — drop near-collinear pixel edges

    ext = _polys(lo, la)
    cm = re > 0
    can = _polys(lo[cm], la[cm])
    spot_name = SPOT_CENTRES.get(spot_id, {}).get("name", spot_id)
    feats = []
    if ext is not None and not ext.is_empty:
        feats.append({"type": "Feature",
                      "properties": {"id": f"{spot_id}-kelp-extent",
                                     "name": f"{spot_name} surveyed kelp extent",
                                     "className": "Kelp Subsurface"},
                      "geometry": mapping(ext)})
    if can is not None and not can.is_empty:
        feats.append({"type": "Feature",
                      "properties": {"id": f"{spot_id}-kelp-canopy",
                                     "name": f"{spot_name} recent kelp canopy",
                                     "className": "Kelp Canopy"},
                      "geometry": mapping(can)})
    if not feats:
        return None
    fc = {"type": "FeatureCollection", "features": feats}
    meta = {"source": "SBC LTER kelp from Landsat (knb-lter-sbc.74)",
            "canopy_quarter": recent_q, "pixel_m": 30}
    return fc, meta


def fetch_spot_kelp(spot_id: str, bbox: dict):
    """Full-resolution multi-year kelp for one spot bbox.

    Queries every survey year inside the bbox and emits the two classes
    the frontend already styles:
      * "Kelp Subsurface" (dot stipple) — union of ALL years = surveyed
        kelp extent, the chart-style "kelp area".
      * "Kelp Canopy" (diagonal hatch) — the most recent year whose
        in-bbox canopy is >= 25% of the best year's, so a crash-year
        pass can't masquerade as the bed.

    Returns (feature_collection, meta) or None on any failure — the
    caller falls back to clipping the statewide 2016-only file.
    """
    # Fast-iteration / outage escape hatch: skip the live multi-year
    # CDFW survey fetch (which stalls ~30 s/year when the FeatureServer
    # 504s) and let the caller use the local statewide kelp clip. Off by
    # default; only the dev loop / a degraded-CDFW CI run sets it.
    if os.environ.get("BUILD_SKIP_KELP") == "1":
        return None
    try:
        from shapely.geometry import box as shp_box, mapping, shape
        from shapely.ops import unary_union
    except ImportError:
        print("    [kelp] shapely unavailable — falling back to statewide clip")
        return None

    envelope = (f"{bbox['lng_min']},{bbox['lat_min']},"
                f"{bbox['lng_max']},{bbox['lat_max']}")
    # The service returns whole intersecting features UNCLIPPED, and the
    # survey layers store county-scale multipart features — without an
    # intersection here the per-year unions span half of San Diego
    # County: mostly off-chart geometry that bloats the file and skews
    # the canopy-year ranking toward out-of-bbox area.
    clip_box = shp_box(bbox["lng_min"], bbox["lat_min"],
                       bbox["lng_max"], bbox["lat_max"])
    per_year = {}
    for layer_idx, year in KELP_SURVEY_LAYERS:
        params = {
            "where": "1=1",
            "geometry": envelope,
            "geometryType": "esriGeometryEnvelope",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "*",
            "outSR": 4326,
            "f": "geojson",
        }
        feats = None
        for attempt in range(3):
            try:
                r = requests.get(KELP_SURVEY_BASE.format(layer=layer_idx),
                                 params=params, timeout=60)
                r.raise_for_status()
                feats = r.json().get("features", [])
                break
            except Exception as e:
                if attempt == 2:
                    print(f"    [kelp] year {year} query failed ({e!r}) — skipping year")
        if not feats:
            continue
        geoms = []
        for f in feats:
            try:
                g = shape(f["geometry"])
                if not g.is_valid:
                    g = g.buffer(0)
                g = g.intersection(clip_box)
                if not g.is_empty:
                    geoms.append(g)
            except Exception:
                continue
        if geoms:
            per_year[year] = unary_union(geoms)

    if not per_year:
        return None

    # Approximate km² (plate carrée at mid-lat — only used to rank years).
    lat_mid = (bbox["lat_min"] + bbox["lat_max"]) / 2
    km2_per_deg2 = 110.574 * 111.320 * math.cos(math.radians(lat_mid))
    def area_km2(g):
        return g.area * km2_per_deg2

    # Some survey passes (the SCSR years, e.g. 2011) are "kelp bed area"
    # products — smooth digitized outlines that fill the water between
    # paddies, not pixel canopy. They render as a clean rectangular slab
    # over the real patchy beds and skew the canopy-year ranking.
    #
    # The discriminator is VERTEX DENSITY of the largest connected part,
    # which is scale-free (works at any bbox size). Measured across both
    # SoCal pilot spots × all survey years, the two populations separate
    # cleanly:
    #   bed-area (2011):   10 v/km² (La Jolla, a 9-vertex rectangle)
    #                   2,276 v/km² (Point Loma, the 13.7 km² slab —
    #                              larger and more digitized, but still
    #                              an outline)
    #   real pixel canopy: 12,117 v/km² (lowest, La Jolla 2008) up to
    #                              146,000 v/km²
    # The 5,000 v/km² cut sits in the 2,276→12,117 gap with ~2× margin
    # either side. Absolute area can't do this: the same 2011 product is
    # 13.7 km² at Point Loma but 0.87 km² at La Jolla, so any area
    # threshold that catches one misses the other.
    SMOOTH_MAX_V_PER_KM2 = 5000.0
    SMOOTH_MIN_PART_KM2 = 0.1     # ignore tiny smooth specks
    def _part_vertices(part):
        if part.geom_type != "Polygon":
            return 0
        return len(part.exterior.coords) + sum(
            len(r.coords) for r in part.interiors)
    for year in sorted(per_year):
        g = per_year[year]
        parts = list(g.geoms) if g.geom_type == "MultiPolygon" else [g]
        big = max(parts, key=lambda p: p.area)
        a = area_km2(big)
        vd = _part_vertices(big) / a if a > 0 else float("inf")
        if a >= SMOOTH_MIN_PART_KM2 and vd < SMOOTH_MAX_V_PER_KM2:
            print(f"    [kelp] year {year} looks like a bed-area product "
                  f"({a:.1f} km² part at {vd:.0f} v/km²) — excluded")
            del per_year[year]
    if not per_year:
        return None

    best_area = max(area_km2(g) for g in per_year.values())
    canopy_year = None
    for year in sorted(per_year, reverse=True):
        if best_area > 0 and area_km2(per_year[year]) >= 0.25 * best_area:
            canopy_year = year
            break
    if canopy_year is None:
        canopy_year = max(per_year, key=lambda y: area_km2(per_year[y]))

    spot_name = SPOT_CENTRES.get(spot_id, {}).get("name", spot_id)

    # Chart-scale geometry diet — full-precision multi-year unions run
    # >10 MB (Point Loma: 13 MB). Three passes get them to overlay
    # weight without visible loss at the bundle's ~10-20 m/px pitch:
    # part filter (drop noise paddies), simplify, 5-dp coord rounding.
    def _drop_small_parts(g, min_km2):
        from shapely.geometry import MultiPolygon
        min_deg2 = min_km2 / km2_per_deg2
        if g.geom_type == "MultiPolygon":
            kept = [p for p in g.geoms if p.area >= min_deg2]
            if not kept:
                return g  # never empty a real bed entirely
            return MultiPolygon(kept) if len(kept) > 1 else kept[0]
        return g

    def _round_coords(coords):
        if isinstance(coords, (list, tuple)):
            if coords and isinstance(coords[0], (int, float)):
                return [round(c, 5) for c in coords]
            return [_round_coords(c) for c in coords]
        return coords

    def _feature(geom, props):
        gj = mapping(geom)
        gj = {**gj, "coordinates": _round_coords(gj["coordinates"])}
        return {"type": "Feature", "geometry": gj, "properties": props}

    features = []
    # Extent first — the frontend paints features in order, so the dot
    # stipple lands under the canopy hatch.
    extent = _drop_small_parts(
        unary_union(list(per_year.values())), 0.003)
    extent = extent.simplify(1.2e-4, preserve_topology=True)
    if not extent.is_empty:
        features.append(_feature(extent, {
            "id": f"{spot_id}-kelp-extent",
            "name": f"{spot_name} surveyed kelp extent",
            "className": "Kelp Subsurface",
            "years": "2003-2016",
            "areaKm2": round(area_km2(extent), 2),
        }))
    canopy = _drop_small_parts(per_year[canopy_year], 0.0015)
    canopy = canopy.simplify(6e-5, preserve_topology=True)
    if not canopy.is_empty:
        features.append(_feature(canopy, {
            "id": f"{spot_id}-kelp-canopy-{canopy_year}",
            "name": f"{spot_name} kelp canopy ({canopy_year} survey)",
            "className": "Kelp Canopy",
            "year": canopy_year,
            "areaKm2": round(area_km2(canopy), 2),
        }))
    meta = {
        "source": "CDFW aerial kelp surveys 2003-2016 (per-spot, full res)",
        "canopy_year": canopy_year,
        "years_with_data": sorted(per_year),
    }
    return {"type": "FeatureCollection", "features": features}, meta


def build_spot(spot_id: str, *, force: bool) -> bool:
    """Build a single spot bundle. Returns True on success, False on
    skip / soft-fail. Hard errors (HTTP 5xx, parse errors) raise.
    """
    if spot_id not in SPOT_CENTRES:
        print(f"  unknown spot id {spot_id!r} — skipping")
        return False
    centre = SPOT_CENTRES[spot_id]
    radius_km = SPOT_RADIUS_KM.get(spot_id, 5)
    bundle_dir = SPOTS_DIR / spot_id

    if not force and bundle_is_fresh(bundle_dir):
        print(f"  [{spot_id}] fresh bundle exists ≤ {FRESHNESS_DAYS} d — skipping")
        return True

    print(f"\n[{spot_id}] building bundle (radius={radius_km} km)")
    bbox = spot_bbox(centre["lng"], centre["lat"], radius_km)
    bundle_dir.mkdir(parents=True, exist_ok=True)

    # 1. Bathy DEM — NCEI mosaic primary, GMRT hole-fill / fallback.
    # Both sources are resampled onto the output grid in ELEVATION
    # space (z, negative underwater) and only converted to depth after
    # the blend: blending depth grids directly would conflate "NaN
    # because land" with "NaN because no coverage" and let GMRT paint
    # phantom water over cells the better source says are land.
    print(f"  fetching bathy DEM (NCEI mosaic, GMRT fallback)…")
    z_grid = None
    bathy_source = None
    try:
        z, src_lats, src_lons = fetch_ncei_dem(bbox)
        z_grid = resample_to_bbox(z, src_lats, src_lons, bbox,
                                  BATHY_SIZE, BATHY_SIZE)
        bathy_source = ("NOAA NCEI DEM global mosaic exportImage "
                        "(CUDEM/coastal DEMs nearshore, best-available blend)")
    except Exception as e:
        print(f"    NCEI mosaic fetch failed ({e!r}) — falling back to GMRT")

    # GMRT pass — ALWAYS fetch the high-res GMRT grid and blend with NCEI.
    # Two roles: (a) fill NCEI nodata holes; (b) the SHELF FIX — the coarse
    # NCEI mosaic marks the shallow Baja subtidal shelf as land/positive
    # elevation (no real depth), but GMRT resolves the ACTUAL nearshore depth
    # there. Where NCEI says land (z>=0 or NaN) but GMRT says water (z<0), take
    # GMRT's real depth instead of fabricating one — this is why the kelp shelf
    # was reading "0 ft"/"land". CA's CUDEM nearshore is already z<0, so the
    # shelf fix barely touches CA; it rescues Baja. (Was hole-only before, which
    # never triggered for Baja because the bad shelf is positive, not NaN.)
    g_grid = None
    try:
        nc_bytes = fetch_gmrt_dem(bbox)
        gz, g_lats, g_lons = parse_gmrt_netcdf(nc_bytes)
        g_grid = resample_to_bbox(gz, g_lats, g_lons, bbox,
                                  BATHY_SIZE, BATHY_SIZE)
    except Exception as e:
        if z_grid is None:
            print(f"  ERROR: NCEI and GMRT bathy fetches both failed: {e!r}")
            return False
        print(f"    [blend] GMRT unavailable ({e!r}) — NCEI only")
    if g_grid is not None:
        if z_grid is None:
            z_grid = g_grid
            bathy_source = ("GMRT high-resolution GridServer "
                            "(NetCDF; NCEI mosaic unavailable)")
        else:
            holes = ~np.isfinite(z_grid) & np.isfinite(g_grid)
            shelf = (np.isfinite(z_grid) & (z_grid >= 0)
                     & np.isfinite(g_grid) & (g_grid < 0))
            use = holes | shelf
            if use.any():
                z_grid[use] = g_grid[use]
                print(f"    [blend] GMRT: {int(holes.sum())} hole + "
                      f"{int(shelf.sum())} shelf px (NCEI land → GMRT water)")
                if shelf.any():
                    bathy_source += " + GMRT nearshore-shelf depths"
                elif holes.any():
                    bathy_source += (
                        f" + GMRT offshore fill ({100.0 * float(holes.mean()):.1f}% px)")

    try:
        depth_grid = np.where(z_grid < 0, -z_grid, np.nan).astype(np.float32)
        # Fill the shallow-shelf no-data BEFORE the OSM burn. The coarse Baja
        # DEM marks the subtidal kelp shelf as land elevation (z≥0 → NaN), so
        # the kelp zone would otherwise render as "land"; this ramps the nearest
        # real depths into that nearshore band. The OSM burn below re-cuts true
        # above-water land, so only the OSM-water shelf keeps the filled depth.
        depth_grid = _fill_nearshore_nodata(depth_grid)
        # Burn the OSM coastline into the bathy grid: any pixel whose
        # centre falls inside an OSM land polygon becomes NaN.
        # Without this, the bathy PNG carries the GMRT coastline as a
        # transparency boundary AND the OSM coastline shows as the tan
        # land polygon — divers see two coastlines (user QA: "you have
        # two coastal views, one is correct the other is an artifact").
        # Burning ensures the bathy transparency edge EXACTLY matches
        # the visible OSM coastline → only one coastline rendered.
        region_data_dir = REGION.data_output_dir(REPO_ROOT)
        land_path = region_data_dir / "land.geojson"
        if land_path.exists():
            depth_grid = _mask_depth_by_land(depth_grid, bbox, land_path)
        depth_range_m = SPOT_DEPTH_RANGES_M.get(spot_id, DEFAULT_DEPTH_RANGE_M)
        bathy_path = bundle_dir / "bathy.png"
        encode_depth_png(depth_grid, depth_range_m, bathy_path)
        print(f"    wrote {bathy_path.name} ({bathy_path.stat().st_size / 1024:.1f} KB), "
              f"depth range {depth_range_m[0]}–{depth_range_m[1]} m")
    except Exception as e:
        print(f"  ERROR fetching/encoding bathy: {e}")
        return False

    # 2. Contours from the (un-resampled, native-resolution) depth.
    # Operating on the native grid gives finer-grained contour lines;
    # we project crossings back into lng/lat via the source grid's
    # spacing in _trace_contours_at_level. Actually — for simplicity
    # we use the resampled grid since that's already on the bbox's
    # regular lat/lng axes. The contours come out smooth enough for
    # diver viz at 480 × 480 native cell-size (~17-33 m / cell).
    print(f"  generating depth contours…")
    contours = generate_contours(depth_grid, bbox)
    contours_path = bundle_dir / "contours.geojson"
    contours_path.write_text(json.dumps(contours, separators=(",", ":")))
    print(f"    wrote {contours_path.name} "
          f"({len(contours['features'])} levels, "
          f"{contours_path.stat().st_size / 1024:.1f} KB)")

    # 3. Coastline clip
    print(f"  clipping reference overlays…")
    layers_meta = {
        "bathy": {
            "url": "bathy.png",
            "width": BATHY_SIZE,
            "height": BATHY_SIZE,
            "depth_range_m": list(depth_range_m),
            "encoding": "linear_8bit_0nan",
        },
        "contours": {
            "url": "contours.geojson",
            "intervals_m": [iv[2] for iv in CONTOUR_INTERVALS],
            "levels": len(contours["features"]),
        },
    }
    region_data_dir = REGION.data_output_dir(REPO_ROOT)

    # Coastline: clip the global land.geojson (output of
    # fetch_coastline.py — already correctly polygonized via
    # land-seed classification).
    #
    # 2026-05-27: walked back from per-spot Overpass fetch. The
    # Overpass + polygonize-per-spot path produced broken land
    # polygons because the coastline lines often don't reach all four
    # bbox edges, so the polygonizer either returned one mega-polygon
    # spanning both ocean and land, or dropped real land area as
    # "ocean". User QA caught it: bathy bleeding into areas that
    # should be land, "black line cuts half the bay off".
    # The wide-map land.geojson uses a land-seed classifier that
    # handles partial coastline ways correctly — much more reliable,
    # even at ~10 m simplification (which is still well below the
    # spot view's effective pixel size).
    print(f"  clipping coastline from global land.geojson…")
    clipped = _clip_land_geometrically(region_data_dir / "land.geojson", bbox)
    if clipped is not None:
        out_path = bundle_dir / "coastline.geojson"
        out_path.write_text(json.dumps(clipped, separators=(",", ":")))
        layers_meta["coastline"] = {
            "url": "coastline.geojson",
            "features": len(clipped["features"]),
        }
        size_kb = out_path.stat().st_size / 1024
        print(f"    [coastline] {len(clipped['features'])} features, "
              f"{size_kb:.0f} KB (geometric clip)")
    else:
        print(f"    [coastline] land.geojson not present — skipping")

    # Kelp gets a per-spot multi-year fetch at full fidelity — the
    # statewide kelp-canopy.geojson carries only the 2016 crash-year
    # pass and reads near-empty at the big southern beds. MPA keeps the
    # statewide clip (its curated polygons genuinely match spot-detail
    # fidelity).
    kelp_done = False
    if spot_id in KELP_SOURCES_LANDSAT:
        spot_kelp = fetch_spot_kelp_landsat(spot_id, bbox)
    else:
        spot_kelp = fetch_spot_kelp(spot_id, bbox)
    if spot_kelp is not None:
        kelp_fc, kelp_meta = spot_kelp
        kelp_fc = _clip_kelp_to_water(kelp_fc, region_data_dir / "land.geojson", bbox)
        out_path = bundle_dir / "kelp.geojson"
        out_path.write_text(json.dumps(kelp_fc, separators=(",", ":")))
        layers_meta["kelp"] = {
            "url": "kelp.geojson",
            "features": len(kelp_fc["features"]),
            **kelp_meta,
        }
        size_kb = out_path.stat().st_size / 1024
        _ksrc = "Landsat" if "Landsat" in kelp_meta.get("source", "") else "CDFW"
        print(f"    [kelp] {len(kelp_fc['features'])} features "
              f"({_ksrc}) {size_kb:.0f} KB")
        kelp_done = True

    fallback_layers = [] if kelp_done else [("kelp", "kelp-canopy.geojson")]
    for layer_key, src_name in fallback_layers + [
        ("mpa",       "mpa-boundaries.geojson"),
    ]:
        src_path = region_data_dir / src_name
        out_path = bundle_dir / f"{layer_key}.geojson"
        clipped = clip_geojson_to_bbox(src_path, bbox)
        if clipped is None:
            print(f"    [{layer_key}] source {src_path.name} not present — skipping")
            continue
        out_path.write_text(json.dumps(clipped, separators=(",", ":")))
        feature_count = len(clipped["features"])
        layers_meta[layer_key] = {
            "url": f"{layer_key}.geojson",
            "features": feature_count,
        }
        print(f"    [{layer_key}] {feature_count} features → {out_path.name}")

    # Depth soundings — sampled grid of "spot depths" labeled in feet
    # the way real NOAA nautical charts publish them. We sample the
    # native bathy grid on a coarse lng/lat lattice, skip land (NaN)
    # cells, and round to the nearest foot. The frontend then thins
    # them further by zoom (only show 20-30 at the overview, ramp up
    # to all of them at deep zoom).
    #
    # Lattice density: 24 × 24 candidate points per bundle = 576 max
    # samples before NaN/land filtering. After filtering a typical
    # nearshore spot ends up with 150-300 valid soundings — same
    # density NOAA Chart 18772 (San Diego to Long Beach) publishes
    # for similar coverage.
    sounding_features = []
    sn = 24
    src_h, src_w = depth_grid.shape
    lng_step = (bbox["lng_max"] - bbox["lng_min"]) / (sn - 1)
    lat_step = (bbox["lat_max"] - bbox["lat_min"]) / (sn - 1)
    for sj in range(sn):
        for si in range(sn):
            samp_lng = bbox["lng_min"] + si * lng_step
            samp_lat = bbox["lat_max"] - sj * lat_step
            # Map (samp_lng, samp_lat) → cell index in the 480×480 grid.
            gx = int(round((samp_lng - bbox["lng_min"]) / (bbox["lng_max"] - bbox["lng_min"]) * (src_w - 1)))
            gy = int(round((bbox["lat_max"] - samp_lat) / (bbox["lat_max"] - bbox["lat_min"]) * (src_h - 1)))
            gx = max(0, min(src_w - 1, gx))
            gy = max(0, min(src_h - 1, gy))
            d_m = depth_grid[gy, gx]
            if not np.isfinite(d_m) or d_m <= 0:
                continue  # land — no sounding
            d_ft = int(round(float(d_m) * 3.28084))
            # Keep only diver-relevant soundings. Beyond ~100 m (330 ft,
            # the technical-diving floor) the numerals are open-ocean
            # depth no diver reaches — and on a deep-water spot like
            # Catalina, ringed by the 600 m+ San Pedro Channel, the
            # 24×24 lattice otherwise scatters ~450 four-figure numbers
            # (up to 4386 ft) across the chart in a grid that reads as
            # placeholder noise. The contour lines still convey the deep
            # structure; soundings stay a nearshore, dive-planning layer.
            if d_ft <= 0 or d_ft > SOUNDING_MAX_FT:
                continue
            sounding_features.append({
                "type": "Feature",
                "geometry": {"type": "Point",
                             "coordinates": [round(samp_lng, 5), round(samp_lat, 5)]},
                "properties": {"depth_ft": d_ft, "depth_m": round(float(d_m), 1)},
            })
    if sounding_features:
        soundings_path = bundle_dir / "soundings.geojson"
        soundings_path.write_text(json.dumps(
            {"type": "FeatureCollection", "features": sounding_features},
            separators=(",", ":"),
        ))
        layers_meta["soundings"] = {
            "url": "soundings.geojson",
            "features": len(sounding_features),
        }
        print(f"    [soundings] {len(sounding_features)} features → soundings.geojson")

    # Curated landmarks — dive-relevant local place names (harbor,
    # break, point, named reef). Filtered to those inside the spot
    # bbox so the labels don't escape the visible canvas.
    landmark_features = []
    for entry in SPOT_LANDMARKS.get(spot_id, []):
        # Support both 4-tuple (legacy) and 5-tuple (with category)
        if len(entry) == 5:
            lng, lat, label, importance, category = entry
        else:
            lng, lat, label, importance = entry
            category = "coastal"  # default for legacy entries
        if (lng < bbox["lng_min"] or lng > bbox["lng_max"] or
            lat < bbox["lat_min"] or lat > bbox["lat_max"]):
            continue
        landmark_features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {"name": label, "importance": importance,
                           "category": category},
        })
    if landmark_features:
        landmarks_path = bundle_dir / "landmarks.geojson"
        landmarks_path.write_text(json.dumps(
            {"type": "FeatureCollection", "features": landmark_features},
            separators=(",", ":"),
        ))
        layers_meta["landmarks"] = {
            "url": "landmarks.geojson",
            "features": len(landmark_features),
        }
        print(f"    [landmarks] {len(landmark_features)} features → landmarks.geojson")

    # Source attribution — kelp + mpa vary by spot/region (Landsat vs CDFW;
    # CONANP vs CDFW), so reflect what was actually used rather than the CA
    # defaults. kelp: prefer the layer's own source (set by the Landsat
    # fetcher); fall back to the CDFW label when kelp came from the statewide
    # CDFW clip. mpa: CONANP for baja, CDFW elsewhere.
    region_name = REGION.name if hasattr(REGION, "name") else "ca"
    kelp_source_label = (
        layers_meta.get("kelp", {}).get("source")
        or "CDFW BIO_CA_Kelp2016 observed aerial-survey canopy (clipped)"
    )
    mpa_source_label = (
        "CONANP federal ANPs 2020 (marine + island, clipped)"
        if region_name == "baja"
        else "CDFW MPA ds582 (clipped)"
    )

    # 4. Bundle manifest
    manifest = {
        "id": spot_id,
        "name": centre["name"],
        "centre": {"lng": centre["lng"], "lat": centre["lat"]},
        "bbox": bbox,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "layers": layers_meta,
        "sources": {
            "bathy":     bathy_source,
            "coastline": "OSM natural=coastline via fetch_coastline.py — clipped from global land.geojson",
            "kelp":      kelp_source_label,
            "mpa":       mpa_source_label,
            "landmarks": "Curated per-spot dive-site / harbor labels",
            "soundings": "Depth soundings sampled from the spot DEM on a 24x24 lattice",
        },
    }
    manifest_path = bundle_dir / "bundle.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"  wrote {manifest_path.name}")
    return True


def write_index(built_spots: list[str]) -> None:
    """Write public/data/spots/index.json — the frontend reads this to
    know which saved-spot pins have a bundle available."""
    SPOTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "spots": sorted(built_spots),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    INDEX_PATH.write_text(json.dumps(payload, indent=2))
    print(f"\nwrote {INDEX_PATH.relative_to(REPO_ROOT)} "
          f"({len(built_spots)} spots: {', '.join(sorted(built_spots))})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--spot", help="Build only this spot id (default: all)")
    parser.add_argument("--force", action="store_true",
                        help="Rebuild even if bundle.json is < 30 d old")
    args = parser.parse_args()

    region_name = REGION.name if hasattr(REGION, "name") else "ca"
    if region_name not in ("ca", "baja"):
        # ca + baja have curated SPOT_CENTRES. Other regions write an empty
        # index so no "View detailed map" buttons render there — a graceful
        # no-op until their spot lists are curated.
        print(f"[build_spot_bundles] region={region_name} — no spot list yet, "
              f"writing empty index.json")
        write_index([])
        return 0

    target_spots = [args.spot] if args.spot else list(SPOT_CENTRES.keys())
    built = []
    failed = []

    def _on_disk_spots():
        # Spots that currently have a complete bundle on disk. The index is
        # derived from this (not just this run's successes) so a failed
        # rebuild doesn't drop a spot whose prior bundle is still present.
        return [
            d.name for d in sorted(SPOTS_DIR.iterdir())
            if d.is_dir() and (d / "bundle.json").exists()
        ] if SPOTS_DIR.exists() else []

    for spot_id in target_spots:
        try:
            if build_spot(spot_id, force=args.force):
                built.append(spot_id)
            else:
                failed.append(spot_id)
        except Exception as e:
            print(f"  [{spot_id}] FAILED: {e!r}")
            failed.append(spot_id)
        # Re-emit the index after EVERY spot. Building all 22 sequentially
        # can exceed the CI step's hard timeout; a single final write would
        # then be killed before it runs, dropping the whole run's progress
        # from index.json (this stranded the islands + hubs on 2026-06-16
        # and forced a manual seed). Writing incrementally means whatever
        # finished before a timeout stays discoverable + committable; the
        # scan is cheap (a directory listing + a small JSON write).
        write_index(_on_disk_spots())

    # Final write covers the no-target-spots edge and reflects the end
    # state after the last build.
    on_disk = _on_disk_spots()
    write_index(on_disk)

    print(f"\nDone. built={len(built)}, failed={len(failed)}, "
          f"on_disk={len(on_disk)}")
    # Return 0 unless EVERY targeted spot failed — partial success
    # should still let the workflow's commit step pick up the wins.
    return 1 if (failed and not built) else 0


def _self_test():
    """Lightweight self-test that runs without network access — pinned
    against the JSON-serialization + NaN-cast traps that hit prod
    on the first refresh-ca-data run.

    Triggers via `python pipeline/build_spot_bundles.py --self-test`
    so dev-checks can include a smoke run that doesn't need GMRT.
    """
    # 1. Encode-PNG path must not warn or fail on a NaN-heavy grid.
    grid = np.full((8, 8), np.nan, dtype=np.float32)
    grid[3:5, 3:5] = 100.0
    import io as _io
    encode_depth_png(grid, (0, 500), Path("_test_bathy.png"))
    Path("_test_bathy.png").unlink(missing_ok=True)

    # 2. Contours must JSON-serialize without TypeError on float32 input.
    #    Mock a 6×6 grid with a depth ramp that crosses the 5 m contour.
    ramp = np.tile(np.linspace(0.0, 30.0, 6, dtype=np.float32), (6, 1))
    bbox = {"lng_min": -117.3, "lng_max": -117.2,
            "lat_min":   32.8, "lat_max":   32.9}
    fc = generate_contours(ramp, bbox)
    json.dumps(fc)  # would raise on float32 leakage

    print("self-test OK")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _self_test()
        sys.exit(0)
    sys.exit(main())
