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

# Sentinel-2 current-canopy sidecar (fetch_kelp_sentinel2.py output) — the fresh,
# higher-cadence condition signal blended onto the Landsat baseline. Optional: if
# absent, load_cells falls back to pure-Landsat CELL_HEALTH (no S2 nudge).
_S2_CANDIDATES = (
    os.path.join(_HERE, "data", "sentinel2_kelp_scb.json"),
    os.path.normpath(os.path.join(_HERE, "..", "kelp-drift-proto", "data", "sentinel2_kelp_scb.json")),
)
S2_JSON = next((p for p in _S2_CANDIDATES if os.path.exists(p)), _S2_CANDIDATES[0])

# Known offshore island centers (lng, lat) — a cell near one is flagged island
# (island-sourced paddies get sea room to drift; mainland kelp beaches fast).
_ISLANDS = [(-120.37, 34.04), (-120.10, 33.97), (-119.75, 33.99), (-119.40, 34.00),
            (-119.03, 33.48), (-119.50, 33.25), (-118.45, 33.39), (-118.50, 32.90)]


def _is_island(lng, lat, r=0.30):
    return any(abs(lng - iln) < r and abs(lat - ila) < r for iln, ila in _ISLANDS)


# Per-bed canopy CONDITION (recent_area / ever_area) = how much of a bed's
# all-time Landsat extent it currently holds. ~1 = healthy/full; <1 = currently
# depressed (declined / heat-thinned). A snapshot observation signal used to
# initialise the canopy reservoir's robust/vulnerable split (canopy.py). This is
# the buildable half of the "Landsat anchor" (P4); the mid-SEASON time-series
# correction needs the full SBC LTER quarterly series (the bundled .nc is a
# recent+ever snapshot only, no time dimension).
CELL_HEALTH = {}


def load_cells(bin_deg=0.05, min_area_km2=0.02):
    """Bin the Landsat recent-canopy pixels (within the model bbox) into seeding
    cells weighted by real canopy area. Returns a list of
    (name, lng, lat, radius_km, is_island, area_km2), sorted big-first. Also
    populates CELL_HEALTH[name] = recent/ever canopy condition in [0,1]."""
    import xarray as xr
    ds = xr.open_dataset(KELP_NC)
    lon = ds.longitude.values
    lat = ds.latitude.values
    rec = np.nan_to_num(ds.recent_area.values)  # m^2 / 30 m pixel, recent peak
    evr = np.nan_to_num(ds.ever_area.values) if "ever_area" in ds else rec  # all-time peak
    b = config.FIELD_BBOX
    inb = ((lat >= b["lat_min"]) & (lat <= b["lat_max"])
           & (lon >= b["lng_min"]) & (lon <= b["lng_max"]) & (rec > 0))
    lon, lat, rec, evr = lon[inb], lat[inb], rec[inb], evr[inb]

    agg = {}  # (kj,ki) -> [recent, sum_lat*recent, sum_lng*recent, ever]
    for ln, la, a, e in zip(lon, lat, rec, evr):
        key = (round(la / bin_deg), round(ln / bin_deg))
        c = agg.get(key)
        if c is None:
            agg[key] = c = [0.0, 0.0, 0.0, 0.0]
        c[0] += a; c[1] += la * a; c[2] += ln * a; c[3] += e

    cells = []
    CELL_HEALTH.clear()
    for (kj, ki), (area, slaw, slnw, ever) in agg.items():
        akm2 = area / 1e6
        if akm2 < min_area_km2:
            continue
        plat, plng = float(slaw / area), float(slnw / area)  # area-weighted centroid (kelp's real spot)
        name = f"k{kj}_{ki}"
        CELL_HEALTH[name] = float(min(1.0, area / ever)) if ever > 0 else 1.0
        cells.append((name, round(plng, 4), round(plat, 4),
                      round(bin_deg * 111.0 * 0.5, 2), bool(_is_island(plng, plat)), round(float(akm2), 4)))
    cells.sort(key=lambda c: -c[5])
    _blend_sentinel2(cells)          # gentle, gated, bounded S2 condition nudge (Stage 3)
    return cells


def _blend_sentinel2(cells):
    """Blend the fresh Sentinel-2 current-canopy sidecar onto the Landsat CELL_HEALTH
    condition (Stage 3). GENTLE + GATED + BOUNDED: S2 only nudges each bed's condition
    (never its capacity K = the Landsat baseline), by <= ~15%, and only where the read
    is confident. Cross-sensor scale is auto-calibrated regionally (beta = median
    S2/Landsat). No-op if disabled or the sidecar is missing. Returns a diag dict."""
    if not getattr(config, "S2_BLEND_ENABLE", False):
        return {}
    try:
        import json
        with open(S2_JSON) as fh:
            sc = json.load(fh).get("cells", {})
    except Exception:
        return {}
    if not sc:
        return {}
    ls_by = {c[0]: c[5] for c in cells}
    # Regional cross-sensor scale: median(S2 / Landsat) over confident substantial beds.
    ratios = [sc[n]["area_km2"] / ls_by[n] for n in sc
              if n in ls_by and ls_by[n] > config.S2_MIN_LS_KM2
              and sc[n]["area_km2"] > 0.002 and sc[n].get("n_water", 0) >= config.S2_MIN_WATER_PX]
    if len(ratios) < 8:
        return {}
    beta = float(np.median(ratios))
    if beta <= 0:
        return {}
    n_blend = 0
    for name, ls in ls_by.items():
        s = sc.get(name)
        if not s or ls < config.S2_MIN_LS_KM2 or s.get("n_water", 0) < config.S2_MIN_WATER_PX:
            continue                                   # low confidence -> keep pure Landsat
        cr = s["area_km2"] / (beta * ls)               # 1.0 = at the regional-typical current:baseline ratio
        cr = min(max(cr, config.S2_CR_LO), config.S2_CR_HI)   # hard-clamp the noisy tails
        mult = 1.0 + config.S2_BLEND_WEIGHT * (cr - 1.0)      # bounded ~+/-15% nudge
        h0 = CELL_HEALTH.get(name, 1.0)
        CELL_HEALTH[name] = float(min(1.0, max(config.S2_HEALTH_FLOOR, h0 * mult)))
        n_blend += 1
    print(f"  Sentinel-2 blend: beta={beta:.3f}, {n_blend}/{len(cells)} cells nudged "
          f"(<= +/-{config.S2_BLEND_WEIGHT * (config.S2_CR_HI - 1) * 100:.0f}% condition), sidecar {os.path.basename(S2_JSON)}")
    return {"beta": beta, "n_blended": n_blend}
