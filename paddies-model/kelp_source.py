"""Real kelp seeding from the SBC LTER "Kelp from Landsat" canopy extract
(sd-kelp-paddies/pipeline/data/landsat_kelp_ca.nc — per-30 m-pixel recent peak
canopy area, m^2). Replaces the hand-placed SCB_BEDS with ACTUAL kelp density:
bin the recent-canopy pixels into seeding cells, each weighted by its real
canopy area (km^2). The big OUTER islands (San Clemente Is, Santa Rosa, San
Nicolas) carry most of the canopy; Catalina is only ~3% of SoCal kelp, so the
old radius^2 hand weighting badly over-rated it.

Cell tuple matches the bed contract: (name, lng, lat, radius_km, is_island, area_km2)
"""
from __future__ import annotations

import os

import numpy as np

import config

# Landsat canopy source. Prefer the copy BUNDLED into this model dir (data/) —
# the ShoudiDive pipeline checkout has no sibling sd-kelp-paddies repo — and fall
# back to that sibling path for local proto dev. If neither exists, beds.py drops
# to a coarse hand-placed fallback (build_site.py fails loud when CI requires the
# real source, so degraded seeds can't silently ship to prod again).
_HERE = os.path.dirname(os.path.abspath(__file__))
_NC_CANDIDATES = (
    os.path.join(_HERE, "data", "landsat_kelp_ca.nc"),
    os.path.normpath(os.path.join(
        _HERE, "..", "sd-kelp-paddies", "pipeline", "data", "landsat_kelp_ca.nc")),
)
KELP_NC = next((p for p in _NC_CANDIDATES if os.path.exists(p)), _NC_CANDIDATES[0])

# Known offshore island centers (lng, lat) — a cell near one is flagged island
# (island-sourced paddies get sea room to drift; mainland kelp beaches fast).
_ISLANDS = [(-120.37, 34.04), (-120.10, 33.97), (-119.75, 33.99), (-119.40, 34.00),
            (-119.03, 33.48), (-119.50, 33.25), (-118.45, 33.39), (-118.50, 32.90)]


def _is_island(lng, lat, r=0.30):
    return any(abs(lng - iln) < r and abs(lat - ila) < r for iln, ila in _ISLANDS)


def load_cells(bin_deg=0.05, min_area_km2=0.02):
    """Bin the Landsat recent-canopy pixels (within the model bbox) into seeding
    cells weighted by real canopy area. Returns a list of
    (name, lng, lat, radius_km, is_island, area_km2), sorted big-first."""
    import xarray as xr
    ds = xr.open_dataset(KELP_NC)
    lon = ds.longitude.values
    lat = ds.latitude.values
    rec = np.nan_to_num(ds.recent_area.values)  # m^2 / 30 m pixel, recent peak
    b = config.FIELD_BBOX
    inb = ((lat >= b["lat_min"]) & (lat <= b["lat_max"])
           & (lon >= b["lng_min"]) & (lon <= b["lng_max"]) & (rec > 0))
    lon, lat, rec = lon[inb], lat[inb], rec[inb]

    agg = {}  # (kj,ki) -> [area, sum_lat*area, sum_lng*area]
    for ln, la, a in zip(lon, lat, rec):
        key = (round(la / bin_deg), round(ln / bin_deg))
        c = agg.get(key)
        if c is None:
            agg[key] = c = [0.0, 0.0, 0.0]
        c[0] += a; c[1] += la * a; c[2] += ln * a

    cells = []
    for (kj, ki), (area, slaw, slnw) in agg.items():
        akm2 = area / 1e6
        if akm2 < min_area_km2:
            continue
        plat, plng = float(slaw / area), float(slnw / area)  # area-weighted centroid (kelp's real spot)
        cells.append((f"k{kj}_{ki}", round(plng, 4), round(plat, 4),
                      round(bin_deg * 111.0 * 0.5, 2), bool(_is_island(plng, plat)), round(float(akm2), 4)))
    cells.sort(key=lambda c: -c[5])
    return cells
