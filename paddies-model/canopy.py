"""Canopy-dynamics reservoir (P2 + P3) — the stateful shedding engine.

Each bed is a finite, weakening, DEPLETING reservoir integrated day-by-day over
the season. Two pools (canopy area, km², capacity K = the bed's Landsat area):

    R  robust attached canopy (resists shedding)
    V  vulnerable canopy (senescent / heat-weakened — ripe to shed)

    grow    -> R      : logistic regrowth, gated by SST/nutrients (cool = grow)
    weaken  : R -> V  : senescence, ACCELERATED by the thermal excess (P3)
    shed    : V -> paddies : wave-energy dose x (1 + warm interaction) x V,
                            CLAMPED to V  <-- the depletion behaviour
    insitu  : V -> gone : decompose/sink in place (not findable paddies)

The daily `shed` flux is the paddy seeding. Because shed is proportional to V and
clamped to it, a bed already shed out by a hot/stormy summer sheds little more no
matter the forcing ("peak-summer, everything's shed") — while a cool/calm summer
leaves V full for a big late pulse. Path-dependent = "general health".

Output for drift: per-bed per-age `wmap[age] = shed_on_that_day / K` — a
normalized shed RATE (drift then area-weights by canopy km² as before), so the
depletion enters as the rate falling when V is depleted. Same dict shape as
detachment.compute() so the drift/dashboard path is unchanged.

Calibration anchor: Seymour et al. 1989 storm mortality (benign 2-9%, Hs 3.8 m/
15 s -> 31-37%, Hs 6.7 m/13 s -> 65-94% of the vulnerable pool per storm).
"""
from __future__ import annotations

import math

import numpy as np

import config
import exposure as exposure_mod
import kelp_source


def _growth_gate(sst):
    """[FLOOR..1] nutrient/temperature REGROWTH gate: full at/below COOL, decaying
    to a nonzero FLOOR at/above WARM. The floor matters: warm/low-nitrate beds keep
    growing on internal-wave + ammonium N (Gerard 1982 residual 0.9%/day; Leichter
    2023 refugia), so the canopy is NOT starved to zero. SST only throttles
    REGROWTH here — it does NOT set the shedding flux (that's senescence+waves)."""
    if sst != sst:
        return 0.5 * (1.0 + config.CANOPY_GROW_GATE_FLOOR)
    lo, hi = config.CANOPY_GROW_GATE_COOL, config.CANOPY_GROW_GATE_WARM
    g = float(np.clip((hi - sst) / max(hi - lo, 1e-6), 0.0, 1.0))
    fl = config.CANOPY_GROW_GATE_FLOOR
    return fl + (1.0 - fl) * g


def simulate(beds, sst_hist, wave_hist, profiles, ndays=None):
    """Integrate every bed's R/V reservoir forward over `ndays` (oldest->today).
    Returns {name: {'shed': np[ndays] (km²/day, idx 0=today), 'R','V','K',
    'fullness', 'wave_recent','warm_recent'}}."""
    ndays = config.SEASON_DAYS if ndays is None else ndays
    out = {}
    for b in beds:
        name, blng, blat, _r, _isl, area = b
        prof = profiles.get(name)
        K = max(float(area), 1e-6)
        # Mainland (non-island) beds shed MORE per unit wave energy: the shallow
        # mainland fringe fails by entanglement cascade in winter storms
        # (Seymour Point Loma). Winter-weighted because it rides the wave term.
        shore_wave_gain = 1.0 if _isl else (1.0 + config.CANOPY_SHORE_WAVE_GAIN)
        # Spin-up anchored to the Landsat snapshot: a bed currently at a fraction
        # of its all-time extent (CELL_HEALTH < 1 = declined/heat-thinned) starts
        # with more of its canopy already in the vulnerable pool. (P4, snapshot.)
        # DAMPED toward 1 because recent/ever is a NOISY prior: Landsat canopy
        # AREA != biomass (tide/submergence/glint hide 15-30%, sparse beds under-
        # detected), so a low recent/ever may be an observation artifact.
        h0 = kelp_source.CELL_HEALTH.get(name, 1.0)
        h = config.CANOPY_HEALTH_DAMP + (1.0 - config.CANOPY_HEALTH_DAMP) * h0
        R = config.CANOPY_INIT_ROBUST * K * h
        V = config.CANOPY_INIT_VULN * K + config.CANOPY_INIT_ROBUST * K * (1.0 - h)
        shed_series = np.zeros(ndays)
        wave_recent = warm_recent = 0.0
        for d in range(ndays - 1, -1, -1):     # oldest day first -> today (d=0)
            sst = sst_hist.daily_sst(blng, blat, d) if sst_hist is not None else float("nan")
            warm_excess = max(0.0, sst - config.CANOPY_WARM_T0) if sst == sst else 0.0
            wdose = wave_hist.dose(prof, blng, blat, d) if wave_hist is not None else 0.0

            grow = config.CANOPY_GROW * R * max(0.0, 1.0 - (R + V) / K) * _growth_gate(sst)
            weaken = config.CANOPY_WEAKEN * R * (1.0 + config.CANOPY_WARM_WEAKEN * warm_excess)
            shed = V * (config.CANOPY_SHED_BASE + config.CANOPY_SHED * wdose * shore_wave_gain) \
                * (1.0 + config.CANOPY_WARM_INT * warm_excess)
            shed = min(shed, V)                # <-- can't shed what's gone
            insitu = config.CANOPY_INSITU * V

            R = max(0.0, R + grow - weaken)
            V = max(0.0, min(K, V + weaken - shed - insitu))
            shed_series[d] = shed
            if d <= 21:                        # track recent drivers for the narrative
                wave_recent = max(wave_recent, wdose)
                warm_recent = max(warm_recent, warm_excess)
        out[name] = {"shed": shed_series, "R": R, "V": V, "K": K,
                     "fullness": (R + V) / K, "wave_recent": wave_recent,
                     "warm_recent": warm_recent}
    return out


def anchor(sim, observed_area):
    """Observation correction (Landsat anchor): rescale each bed's R+V so the
    total canopy matches an observed area, preserving the robust:vulnerable
    split. The mechanism for re-grounding the months-long integration on a real
    satellite pass. Today only the season-start snapshot is observed (applied via
    K + CELL_HEALTH at spin-up); wire the SBC LTER quarterly SERIES here to
    correct mid-season (the bundled .nc is snapshot-only, so that's a data
    follow-up, not a code one)."""
    for name, s in sim.items():
        obs = observed_area.get(name)
        if not obs or obs <= 0:
            continue
        tot = s["R"] + s["V"]
        if tot <= 0:
            continue
        f = obs / tot
        s["R"] *= f
        s["V"] *= f
    return sim


def _band(idx):
    if idx < 12:
        return "Minimal"
    if idx < 30:
        return "Low"
    if idx < 55:
        return "Moderate"
    if idx < 78:
        return "High"
    return "Extreme"


def _why(band, dominant, hs, sst):
    hss = "n/a" if hs is None else f"{hs:.1f} m"
    ssts = "n/a" if sst is None else f"{sst:.1f}°C"
    if band in ("Minimal", "Low"):
        return (f"{band} — calm seas (peak {hss}) and cool water ({ssts}); "
                f"little kelp is breaking loose, so few paddies.")
    drv = (f"a {hss} swell tearing kelp loose" if dominant == "swell"
           else f"sustained warm water ({ssts}) weakening and shedding kelp")
    return (f"{band} — {drv}; fresh paddies seeding offshore from the beds that "
            f"still have canopy to give.")


def frame_det(sim, ages, offset_days, sst_hist, wave_hist):
    """Map the reservoir sim to a `detach`-shaped dict for one timeline frame at
    `offset_days` from now (age `a` at that frame = shed `a-offset` days ago).
    wmap[age] = shed_on_that_day / K  (normalized rate; drift area-weights)."""
    per_bed = {}
    all_rates = []
    for name, s in sim.items():
        K = s["K"]
        shed = s["shed"]
        nd = len(shed)
        wmap = {}
        for a in ages:
            idx = int(round(a - offset_days))
            idx = 0 if idx < 0 else (nd - 1 if idx >= nd else idx)
            wmap[a] = float(shed[idx]) / K
            all_rates.append(wmap[a])
        per_bed[name] = wmap

    mean_rate = float(np.mean(all_rates)) if all_rates else 0.0
    index = 100.0 * (1.0 - math.exp(-mean_rate / config.CANOPY_BAND_SCALE))
    band = _band(index)

    # recent regional drivers for the dominant/why narrative
    wave_pk = max((s["wave_recent"] for s in sim.values()), default=0.0)
    warm_pk = max((s["warm_recent"] for s in sim.values()), default=0.0)
    dominant = "swell" if (config.CANOPY_SHED * wave_pk) >= (config.CANOPY_WARM_INT * warm_pk + 1e-9) else "warm water"

    # peak Hs / SST over the recent week for the narrative (grid nanmax)
    peak_hs = _grid_peak(wave_hist.hs[:7]) if wave_hist is not None else None
    peak_sst = _grid_peak(sst_hist.sst_daily[:7]) if sst_hist is not None else None

    nd = len(next(iter(sim.values()))["shed"]) if sim else 1
    timeline = []
    for a in ages:
        idx = int(round(a - offset_days))
        idx = 0 if idx < 0 else (nd - 1 if idx >= nd else idx)
        day_shed = float(np.mean([s["shed"][idx] / s["K"] for s in sim.values()])) if sim else 0.0
        hs_d = _grid_peak(wave_hist.hs[min(idx, wave_hist.ndays - 1)]) if wave_hist is not None else None
        timeline.append({"days_ago": a, "hs_m": round(hs_d, 1) if hs_d is not None else None,
                         "shed": round(day_shed * 1000, 2)})

    return {
        "per_bed": per_bed,
        "index": round(index, 1),
        "band": band,
        "dominant": dominant,
        "peak_hs_m": round(peak_hs, 2) if peak_hs is not None else None,
        "peak_sst_c": round(peak_sst, 1) if peak_sst is not None else None,
        "mean_detach": round(mean_rate, 5),
        "why": _why(band, dominant, peak_hs, peak_sst),
        "timeline": timeline,
    }


def _grid_peak(arr):
    """nanmax of a grid slice (or None if all-NaN), warning-free."""
    import warnings
    with warnings.catch_warnings(), np.errstate(invalid="ignore"):
        warnings.simplefilter("ignore")
        a = np.asarray(arr, dtype=float)
        if not np.isfinite(a).any():
            return None
        return float(np.nanmax(a))
