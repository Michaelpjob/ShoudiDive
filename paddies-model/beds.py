"""Kelp source seed cells for the SCB.

Built from REAL Landsat canopy density (SBC LTER "Kelp from Landsat", via
kelp_source.load_cells) — each cell weighted by its actual recent kelp area
(km^2), at the area-weighted location of the real canopy. Falls back to a
coarse hand-placed list only if the Landsat extract can't be read.

Tuple: (name, lng, lat, radius_km, is_island, area_km2)
"""
import math

import kelp_source

# Fallback ONLY (used if the Landsat extract is missing) — the old hand-placed
# beds, with a rough area proxy from the drawn radius so the area weighting still
# runs. These badly over-rate Catalina (the whole reason we moved to real canopy).
_HAND = [
    # --- mainland (north -> south) ---
    ("Santa Barbara / Goleta",      -119.85, 34.42, 8, False),
    ("Ventura / Carpinteria",       -119.60, 34.36, 12, False),
    ("Point Dume / Mugu",           -119.00, 34.07, 8, False),
    ("Malibu",                      -118.74, 34.02, 7, False),
    ("Santa Monica Bay",            -118.55, 33.95, 8, False),
    ("Palos Verdes",                -118.40, 33.74, 8, False),
    ("Huntington / Long Beach",     -118.10, 33.69, 9, False),
    ("Newport / Crystal Cove",      -117.85, 33.59, 5, False),
    ("Dana Point / Laguna",         -117.75, 33.51, 7, False),
    ("San Clemente",                -117.55, 33.39, 5, False),
    ("Camp Pendleton / San Onofre", -117.43, 33.31, 6, False),
    ("Carlsbad / Oceanside",        -117.32, 33.15, 8, False),
    ("Del Mar / Cardiff",           -117.28, 32.97, 7, False),
    ("La Jolla",                    -117.27, 32.85, 5, False),
    ("Point Loma",                  -117.27, 32.69, 7, False),
    ("Coronado / Imperial Beach",   -117.18, 32.60, 8, False),
    # --- Channel Islands (the offshore engine) ---
    ("San Miguel Island",           -120.37, 34.04, 8, True),
    ("Santa Rosa Island",           -120.10, 33.97, 10, True),
    ("Santa Cruz Island",           -119.75, 33.99, 12, True),
    ("Anacapa Island",              -119.40, 34.00, 5, True),
    ("Santa Barbara Island",        -119.03, 33.48, 3, True),
    ("San Nicolas Island",          -119.50, 33.25, 6, True),
    ("Catalina Island",             -118.45, 33.39, 14, True),
    ("San Clemente Island",         -118.50, 32.90, 12, True),
]


def _hand_cells():
    # area proxy: treat ~8% of the drawn disk as canopy so the weighting runs.
    return [(n, ln, la, float(r), isl, round(0.08 * math.pi * r * r, 3))
            for (n, ln, la, r, isl) in _HAND]


try:
    SCB_BEDS = kelp_source.load_cells()
    KELP_SOURCE = "landsat-canopy"
    if not SCB_BEDS:
        raise ValueError("no kelp cells in bbox")
except Exception as _e:  # noqa: BLE001 — any read/parse failure -> safe fallback
    SCB_BEDS = _hand_cells()
    KELP_SOURCE = f"hand-fallback ({type(_e).__name__}: {_e})"
