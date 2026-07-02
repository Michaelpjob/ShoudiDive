"""Convergence / opportunity engine — where kelp, paddies, and fish align.

Four physically-grounded fields, combined:
  * current CONVERGENCE  (-div u): floating kelp + bait accumulate where
    surface water comes together. (Now fed by HFRNet 6 km -> actually fires.)
  * SST FRONTS (temp breaks, |grad SST|): gamefish stack on breaks.
  * CHL FRONTS (color breaks, |grad log chl|): productivity edges / bait.
  * floating-KELP density (drift model): paddies = structure + bait.

    opportunity = kelp_norm * (BASE + W_CONV*conv + W_SST*sst_front
                               + W_CHL*chl_front) * offshore_factor

The offshore_factor zeroes opportunity near the beach and ramps to full
offshore — inshore paddies rarely hold fish.
"""
from __future__ import annotations

import math

import numpy as np

import config
import cones as cones_mod
import features as features_mod

try:
    from scipy.ndimage import distance_transform_edt, gaussian_filter
    _HAVE_SCIPY = True
except Exception:
    _HAVE_SCIPY = False

W_CONV = 1.0   # convergence front = the PRIMARY locator (best-evidenced signal)
W_SST = 1.0    # SST temperature break
W_CHL = 3.0    # chl color-break (the blue/green edge) — RAISED 1.0->3.0 (tune sweep + reports): anglers run to the clean/blue color break; it was the top front signal for aligning opportunity with the real grounds
BASE = 0.05    # low floor: data sweep wants the signals to lead, not a flat baseline
ZONE_THR_FRAC = 0.50
ZONE_MIN_AREA_DEG2 = 0.003
ZONE_TOP = 6

_STRUCT_CACHE = {}


def _structure_grid(lats, lngs):
    """Bathymetry/structure bonus field: proximity to the named banks/breaks
    (9-Mile, 14-Mile, Cortes, Tanner, 302...). A paddy over structure holds more
    and bigger fish (practitioner top-3, previously unscored). Gaussian proximity
    in [0,1]."""
    key = (len(lats), len(lngs), round(float(lats[0]), 3))
    if key not in _STRUCT_CACHE:
        banks = [(f[2], f[1]) for f in features_mod.OFFSHORE_FEATURES if f[3] == "bank"]
        LAT, LNG = np.meshgrid(lats, lngs, indexing="ij")
        g = np.zeros_like(LAT, dtype=float)
        r = config.STRUCT_RADIUS_NM
        for blat, blng in banks:
            dnm = np.hypot((LAT - blat) * 60, (LNG - blng) * 60 * np.cos(np.radians(LAT)))
            g = np.maximum(g, np.exp(-(dnm * dnm) / (r * r)))
        _STRUCT_CACHE[key] = g
    return _STRUCT_CACHE[key]


def _grad_mag(field, dx, dy):
    mean = float(np.nanmean(field)) if np.isfinite(np.nanmean(field)) else 0.0
    f = np.nan_to_num(field, nan=mean)
    return np.sqrt((np.gradient(f, axis=1) / dx) ** 2 + (np.gradient(f, axis=0) / dy) ** 2)


def _convergence_field(forcing):
    """Surface-current convergence (-div u), clipped to positive. Computed on
    the fine HFRNet 6 km grid when available (this is what sharpens the core),
    else the coarse Open-Meteo grid. Returns (conv2d, lats, lngs, step, src)."""
    fine = forcing.fine_current_mean() if hasattr(forcing, "fine_current_mean") else None
    if fine is not None:
        lats, lngs, u, v, step = fine
        finite = np.isfinite(u) & np.isfinite(v)
        u0, v0 = np.nan_to_num(u), np.nan_to_num(v)
        src = "hfr"
    else:
        lats, lngs, step, src = forcing.lats, forcing.lngs, config.GRID_STEP_DEG, "coarse"
        u0, v0 = forcing.u[-1], forcing.v[-1]
        finite = np.ones_like(u0, dtype=bool)
    clat = 0.5 * (lats[0] + lats[-1])
    dx = step * 111.0 * math.cos(math.radians(clat))
    dy = step * 111.0
    conv = np.clip(-(np.gradient(u0, axis=1) / dx + np.gradient(v0, axis=0) / dy), 0.0, None)
    # zero out the radar-coverage edge: the NaN->0 fill creates a fake
    # divergence ring where real current meets empty cells; erode it away.
    if src == "hfr":
        if _HAVE_SCIPY:
            from scipy.ndimage import binary_erosion
            valid = binary_erosion(finite, iterations=1)
        else:
            valid = finite
        conv = np.where(valid, conv, 0.0)
    return conv, np.asarray(lats), np.asarray(lngs), step, src


def _coarse_fields(forcing):
    conv, clats, clngs, cstep, csrc = _convergence_field(forcing)
    lats, lngs = forcing.lats, forcing.lngs
    sst = forcing.scalars["sst"][-1]
    dx = config.GRID_STEP_DEG * 111.0 * math.cos(math.radians(0.5 * (lats[0] + lats[-1])))
    dy = config.GRID_STEP_DEG * 111.0
    sst_front = _grad_mag(sst, dx, dy)
    chl = forcing.scalars.get("chl")
    if chl is not None:
        chl_front = _grad_mag(np.log10(np.clip(chl[-1], 1e-3, None)), dx, dy)
    else:
        chl_front = np.zeros_like(sst_front)
    g = config.GRID_STEP_DEG
    return {"conv": (conv, clats, clngs, cstep), "sst": (sst_front, lats, lngs, g),
            "chl": (chl_front, lats, lngs, g), "src": csrc}


def _mainland_mask(lats, lngs):
    """Boolean grid: is each cell inside the MAINLAND (largest land polygon)?
    Islands are excluded so island + channel water reads as offshore, while
    the mainland surf zone (Encinitas/Solana etc.) is what gets suppressed."""
    land = cones_mod._land_union()
    if land is None:
        return None
    geoms = list(land.geoms) if land.geom_type == "MultiPolygon" else [land]
    mainland = max(geoms, key=lambda g: g.area)
    from shapely.prepared import prep
    from shapely.geometry import Point
    pm = prep(mainland)
    return np.array([[pm.contains(Point(float(ln), float(la))) for ln in lngs] for la in lats])


def _offshore_factor(lats, lngs, landmask):
    mask = _mainland_mask(lats, lngs)
    if mask is None or not _HAVE_SCIPY:
        return np.ones((len(lats), len(lngs)))
    dist_km = distance_transform_edt(~mask) * (config.DENSITY_STEP_DEG * 111.0)
    near, far = config.OFFSHORE_NEAR_KM, config.OFFSHORE_FAR_KM
    # SHORE_CREDIT floors the inshore ramp so shore-shed paddies aren't zeroed
    # out (was a hard 0 within ~3 nm). Cautious + reversible: 0.0 == old behavior.
    floor = getattr(config, "SHORE_CREDIT", 0.0)
    return np.clip((dist_km - near) / max(far - near, 1e-6), floor, 1.0)


_CHL_CACHE = {}


def _get_chl(lats, lngs):
    """Cached fetch of ShoudiDive's chl snapshot on the given grid (once per run)."""
    key = (len(lats), len(lngs), round(float(lats[0]), 3), round(float(lngs[0]), 3))
    if key not in _CHL_CACHE:
        try:
            import sd_source
            _CHL_CACHE[key] = sd_source.fetch_chl(lats, lngs)
        except Exception:
            _CHL_CACHE[key] = None
    return _CHL_CACHE[key]


def _chl_front_grid(lats, lngs):
    """Normalized chl color-break (|grad log chl|) on the density grid — the
    blue/green EDGE. Research: gamefish work the clean side of this interface,
    so it's a positive attractor (replaces the old all-zero chl_front). None if
    no chl."""
    chl = _get_chl(lats, lngs)
    if chl is None:
        return None
    midlat = 0.5 * (lats[0] + lats[-1])
    dx = config.DENSITY_STEP_DEG * 111.0 * math.cos(math.radians(midlat))
    dy = config.DENSITY_STEP_DEG * 111.0
    f = _grad_mag(np.log10(np.clip(chl, 1e-3, None)), dx, dy)
    return f / (f.max() or 1.0)


def _fishability(forcing, lats, lngs, landmask):
    """Where a floating paddy actually HOLDS fish. Distance-from-mainland is the
    structural prior (offshore/island grounds). But real reports say nearshore
    paddies turn on when the water is BOTH warm (gamefish move inshore) AND
    clean/blue (green water is "devoid of life"). So: gate out green water
    everywhere, and let warm+clean water lift the nearshore suppression.
        fishability = clarity * max(distance_ramp, warm*clarity)
    Degrades to pure distance if the chl layer is unavailable."""
    dist = _offshore_factor(lats, lngs, landmask)
    if not getattr(config, "WATER_QUALITY_GATE", False):
        return dist
    chl = _get_chl(lats, lngs)
    if chl is None:
        return dist
    # warm gate from this scenario's SST, bilinear-sampled onto the density grid
    sst_c = forcing.scalars["sst"][-1]
    sst_g = np.array([[_bilin(sst_c, forcing.lats, forcing.lngs, la, ln, config.GRID_STEP_DEG)
                       for ln in lngs] for la in lats])
    warm = np.nan_to_num(np.clip((sst_g - config.WARM_ON_C)
                                 / max(config.WARM_FULL_C - config.WARM_ON_C, 1e-6), 0.0, 1.0))
    # clarity gate from chl (log ramp: clean -> 1, green -> 0)
    lc, lg = math.log10(config.CHL_CLEAN_MGM3), math.log10(config.CHL_GREEN_MGM3)
    with np.errstate(invalid="ignore"):
        lchl = np.log10(np.clip(chl, 1e-4, None))
        clarity_raw = np.clip((lg - lchl) / (lg - lc), 0.0, 1.0)
    # clarity is a clean-SIDE preference, NOT a veto: green is suppressed to the
    # floor, never zeroed (research refuted "green = devoid of life" 0-3).
    clarity = config.CLARITY_FLOOR + (1.0 - config.CLARITY_FLOOR) * clarity_raw
    known = np.isfinite(chl)
    clarity = np.where(known, clarity, 1.0)         # missing chl: don't suppress offshore
    nearshore_on = warm * clarity * known           # lights where warm AND clean-ish
    return clarity * np.maximum(dist, nearshore_on)


def _bilin(arr, lats, lngs, lat, lng, step=None):
    step = step if step is not None else config.GRID_STEP_DEG
    lat = min(max(lat, lats[0]), lats[-1])
    lng = min(max(lng, lngs[0]), lngs[-1])
    fj, fi = (lat - lats[0]) / step, (lng - lngs[0]) / step
    j0 = min(max(int(fj), 0), len(lats) - 2)
    i0 = min(max(int(fi), 0), len(lngs) - 2)
    tj, ti = fj - j0, fi - i0
    return (arr[j0, i0] * (1 - tj) * (1 - ti) + arr[j0, i0 + 1] * (1 - tj) * ti
            + arr[j0 + 1, i0] * tj * (1 - ti) + arr[j0 + 1, i0 + 1] * tj * ti)


def build_opportunity(forcing, dens, landmask, reports=None, as_of_dt=None):
    F = _coarse_fields(forcing)
    conv, clats, clngs, cstep = F["conv"]
    sstf, slats, slngs, sstep = F["sst"]
    cn = conv / (conv.max() or 1.0)
    sn = sstf / (sstf.max() or 1.0)

    dlats, dlngs, kelp = dens["lats"], dens["lngs"], dens["grid"]
    # kelp/paddy density is a soft PRESENCE-GATE, not the ranking driver — compress it
    # (gamma<1) AND floor it (DENS_FLOOR) so a prime bank/front/convergence spot still
    # scores where modeled paddy density is only modest (paddies are ubiquitous in the
    # Bight; the fishery is at accumulation+structure, not the densest source). Without
    # the floor, kn multiplied opp to ~0 off the big NW forests -> opportunity ≈ source
    # density (scorecard B6/A4). See config.DENS_FLOOR.
    df = getattr(config, "DENS_FLOOR", 0.0)
    kn = df + (1.0 - df) * (kelp / (kelp.max() or 1.0)) ** config.KELP_GAMMA
    chlf_d = _chl_front_grid(dlats, dlngs)          # real color-break on the density grid
    H, W = kelp.shape
    opp = np.zeros((H, W))
    cg = np.zeros((H, W)); sg = np.zeros((H, W)); hg = np.zeros((H, W))
    for j, la in enumerate(dlats):
        for i, ln in enumerate(dlngs):
            c = _bilin(cn, clats, clngs, la, ln, cstep)
            s = _bilin(sn, slats, slngs, la, ln, sstep)
            h = float(chlf_d[j, i]) if chlf_d is not None else 0.0
            cg[j, i], sg[j, i], hg[j, i] = c, s, h
            if not landmask.is_land(ln, la):
                # front-led: convergence dominates the locator term; (compressed) kelp gates presence
                opp[j, i] = kn[j, i] * (BASE + W_CONV * c + W_SST * s + W_CHL * h)

    off = _fishability(forcing, dlats, dlngs, landmask)
    opp = opp * off
    # --- research-grade extras: structure bonus + report boost (reach lens removed)
    opp = opp * (1.0 + config.STRUCT_WEIGHT * _structure_grid(dlats, dlngs))
    if reports and as_of_dt is not None and getattr(config, "REPORT_PROMOTE", False):
        import reports as reports_mod
        opp = opp * (1.0 + config.REPORT_WEIGHT * reports_mod.assimilate(reports, dlats, dlngs, as_of_dt))
    sig = getattr(config, "OPP_SMOOTH_SIGMA", 0.0)
    if sig > 0 and _HAVE_SCIPY:
        opp = gaussian_filter(opp, sig)   # consolidate nearby patches into fewer, bigger zones (hides no ground)
    return {"lats": dlats, "lngs": dlngs, "opp": opp, "conv": cg, "sst_front": sg,
            "chl_front": hg, "offshore": off, "current_src": F["src"],
            "conv_strength": round(float(conv.max()), 4),
            "sst_front_strength": round(float(sstf.max()), 4),
            "chl_front_strength": round(float(chlf_d.max()) if chlf_d is not None else 0.0, 4)}


def _sample(g, lats, lngs, lat, lng):
    j = int(np.argmin(np.abs(lats - lat)))
    i = int(np.argmin(np.abs(lngs - lng)))
    return float(g[j, i])


def _cell_km2(lats):
    midlat = 0.5 * (lats[0] + lats[-1])
    return (config.DENSITY_STEP_DEG * 111.0) * (config.DENSITY_STEP_DEG * 111.0
                                                * math.cos(math.radians(midlat)))


def hdr(opp_data, levels=(config.CORE_FRACTION, 0.5, 0.8)):
    """Highest-Density Regions from the Monte-Carlo paddy ensemble: the
    smallest AREA holding `level` fraction of the expected paddy mass (a
    statistical credible region). Returns one shaded region per level
    (clipped to water) + the density peak as the focal point."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from shapely.geometry import Polygon, mapping

    lats, lngs, g = opp_data["lats"], opp_data["lngs"], opp_data["opp"]
    total = float(g.sum())
    if total <= 0:
        return {"regions": [], "peak": None}
    flat = g.flatten()
    order = np.argsort(flat)[::-1]
    csum = np.cumsum(flat[order])
    cell_km2 = _cell_km2(lats)
    land = cones_mod._land_union()
    LNG, LAT = np.meshgrid(lngs, lats)

    regions = []
    core_thr = None
    for lvl in levels:
        k = min(int(np.searchsorted(csum, lvl * total)), len(order) - 1)
        thr = float(flat[order[k]])
        if lvl == levels[0]:
            core_thr = thr
        fig = plt.figure()
        cs = plt.contour(LNG, LAT, g, levels=[thr])
        segs = cs.allsegs[0] if cs.allsegs else []
        plt.close(fig)
        feats = []
        for seg in segs:
            if len(seg) < 4:
                continue
            p = Polygon(seg)
            if not p.is_valid:
                p = p.buffer(0)
            if land is not None and not p.is_empty:
                p = p.difference(land)
            if not p.is_empty and p.geom_type in ("Polygon", "MultiPolygon"):
                feats.append({"type": "Feature", "geometry": mapping(p), "properties": {}})
        regions.append({"level": lvl, "fc": {"type": "FeatureCollection", "features": feats},
                        "area_km2": int(round(int((flat >= thr).sum()) * cell_km2))})

    pj, pi = np.unravel_index(int(np.argmax(g)), g.shape)
    peak = {"lat": round(float(lats[pj]), 3), "lng": round(float(lngs[pi]), 3)}

    # The fine HFR field splits the core into several real convergence patches.
    # The angler runs to ONE: the contiguous patch holding the peak. Report its
    # area (the attainable start-zone) + how dominant it is + how many others.
    core_primary_km2, n_patches, primary_frac = regions[0]["area_km2"], 1, 1.0
    if core_thr is not None and _HAVE_SCIPY:
        from scipy.ndimage import label
        mask = g >= core_thr
        lbl, n_patches = label(mask)
        if n_patches >= 1 and lbl[pj, pi] > 0:
            prim = lbl == lbl[pj, pi]
            core_primary_km2 = int(round(int(prim.sum()) * cell_km2))
            core_mass = float(g[mask].sum())
            primary_frac = round(float(g[prim].sum()) / core_mass, 2) if core_mass > 0 else 1.0
    return {"regions": regions, "peak": peak, "core_primary_km2": core_primary_km2,
            "n_core_patches": int(n_patches), "primary_frac": primary_frac}


def zones(opp_data, top=ZONE_TOP):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from shapely.geometry import Polygon, mapping

    lats, lngs, opp = opp_data["lats"], opp_data["lngs"], opp_data["opp"]
    peak = float(opp.max())
    if peak <= 0:
        return []
    LNG, LAT = np.meshgrid(lngs, lats)
    fig = plt.figure()
    cs = plt.contour(LNG, LAT, opp, levels=[ZONE_THR_FRAC * peak])
    segs = cs.allsegs[0] if cs.allsegs else []
    plt.close(fig)

    land = cones_mod._land_union()
    cand = []
    for seg in segs:
        if len(seg) < 4:
            continue
        p = Polygon(seg)
        if not p.is_valid:
            p = p.buffer(0)
        if p.is_empty or p.area < ZONE_MIN_AREA_DEG2:
            continue
        if land is not None:
            p = p.difference(land)
        if p.is_empty or p.geom_type not in ("Polygon", "MultiPolygon"):
            continue
        c = p.representative_point()
        sc = _sample(opp, lats, lngs, c.y, c.x)
        cv = _sample(opp_data["conv"], lats, lngs, c.y, c.x)
        sf = _sample(opp_data["sst_front"], lats, lngs, c.y, c.x)
        cf = _sample(opp_data["chl_front"], lats, lngs, c.y, c.x)
        cand.append((sc * p.area, sc, cv, sf, cf, c.x, c.y, p))

    cand.sort(reverse=True, key=lambda t: t[0])
    out = []
    for rank, (_, sc, cv, sf, cf, clng, clat, p) in enumerate(cand[:top], 1):
        driver = max((cv, "converging current"), (sf, "temp break"), (cf, "color break"),
                     key=lambda t: t[0])[1]
        out.append({"type": "Feature", "geometry": mapping(p),
                    "properties": {"rank": rank, "score": round(sc / peak, 2),
                                   "conv": round(cv, 2), "front": round(sf, 2),
                                   "chl": round(cf, 2), "driver": driver,
                                   "clat": round(clat, 3), "clng": round(clng, 3)}})
    return out
