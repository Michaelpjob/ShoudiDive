"""Data-fusion seam (HANDOFF addendum P1): real surface currents.

Open-Meteo gives time-varying currents at only ~0.3 deg (~33 km) — far too
coarse to resolve the convergence lines that actually concentrate paddies, so
the credible "core" stays mushy (~1,600 km^2). HFRNet HF-radar gives observed
surface currents at 6 km, hourly — a ~5x resolution jump. That sharper
divergence field is the real lever on a tighter, attainable core.

This module ONLY fetches + quality-controls + caches the HFR current cube and
hands back a plain dict (grid + provenance). The drift/convergence engines
consume it via forcing.py; HFR is currents-only, so waves/wind/SST still come
from Open-Meteo. Where HF radar has no coverage (offshore beyond radar range,
poor geometry), cells are NaN and the caller falls back to the coarse model.

    https://dods.ndbc.noaa.gov/thredds/dodsC/hfradar_uswc_6km  (DAP2, pydap)
"""
from __future__ import annotations

import os
import time

import numpy as np

import config

HFR_URL = "https://dods.ndbc.noaa.gov/thredds/dodsC/hfradar_uswc_6km"
HDOP_MAX = 1.6          # drop cells with poor radar geometry (dilution of precision)
SITES_MIN = 2           # need >=2 radars for a true vector (1 = radial only)
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "cache")
CACHE_TTL_H = 12.0      # refetch if the cached cube is older than this


def _cache_path(bbox, n_hours):
    b = bbox
    key = (f"hfr_{b['lat_min']:.2f}_{b['lat_max']:.2f}_{b['lng_min']:.2f}_"
           f"{b['lng_max']:.2f}_{n_hours}.npz")
    return os.path.join(CACHE_DIR, key)


def _load_cache(path):
    if not os.path.exists(path):
        return None
    if (time.time() - os.path.getmtime(path)) / 3600.0 > CACHE_TTL_H:
        return None
    try:
        z = np.load(path, allow_pickle=True)
        return {"lats": z["lats"], "lngs": z["lngs"], "hours": z["hours"],
                "u": z["u"], "v": z["v"], "coverage": float(z["coverage"]),
                "source": str(z["source"]), "step_deg": float(z["step_deg"])}
    except Exception:
        return None


def _save_cache(path, d):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    np.savez_compressed(path, lats=d["lats"], lngs=d["lngs"], hours=d["hours"],
                        u=d["u"], v=d["v"], coverage=d["coverage"],
                        source=d["source"], step_deg=d["step_deg"])


def fetch_hfr(bbox=None, n_hours=None, use_cache=True, verbose=True):
    """Fetch the HFRNet 6 km current cube over `bbox` for the last `n_hours`
    hourly slices. Returns a dict with native-grid lats/lngs, an hours axis
    (np.datetime64), QC'd u/v cubes (NaN where untrustworthy), and provenance.
    Returns None if the service is unreachable (caller stays on Open-Meteo)."""
    bbox = bbox or config.FIELD_BBOX
    n_hours = n_hours or (config.PAST_DAYS * 24 + 12)
    cpath = _cache_path(bbox, n_hours)
    if use_cache:
        c = _load_cache(cpath)
        if c is not None:
            if verbose:
                print(f"  HFR: cache hit ({c['coverage']*100:.0f}% coverage, "
                      f"{c['u'].shape[0]} h @ {c['step_deg']*111:.1f} km)")
            return c

    try:
        import warnings
        warnings.filterwarnings("ignore")
        import xarray as xr
        t0 = time.time()
        ds = xr.open_dataset(HFR_URL, engine="pydap", decode_times=True)
        b = bbox
        sub = ds[["u", "v", "hdop", "number_of_sites"]].sel(
            lat=slice(b["lat_min"], b["lat_max"]),
            lon=slice(b["lng_min"], b["lng_max"]))
        sub = sub.isel(time=slice(-n_hours, None)).load()
        ds.close()
    except Exception as e:
        if verbose:
            print(f"  HFR: unavailable ({type(e).__name__}: {str(e)[:60]}) "
                  f"-> falling back to Open-Meteo currents")
        return None

    u = np.asarray(sub["u"].values, dtype=float)
    v = np.asarray(sub["v"].values, dtype=float)
    hdop = np.asarray(sub["hdop"].values, dtype=float)
    nsite = np.asarray(sub["number_of_sites"].values, dtype=float)
    bad = ~np.isfinite(u) | ~np.isfinite(v) | (hdop > HDOP_MAX) | (nsite < SITES_MIN)
    u[bad] = np.nan
    v[bad] = np.nan

    lats = np.asarray(sub["lat"].values, dtype=float)
    lngs = np.asarray(sub["lon"].values, dtype=float)
    hours = np.asarray(sub["time"].values)
    # fraction of (cell,time) samples that survived QC -> how much real obs we have
    coverage = float(np.isfinite(u).mean())
    step = float(abs(lats[1] - lats[0])) if lats.size > 1 else 0.054

    d = {"lats": lats, "lngs": lngs, "hours": hours, "u": u, "v": v,
         "coverage": coverage, "step_deg": step,
         "source": f"HFRNet US-West-Coast 6km hourly (NDBC THREDDS), "
                   f"{u.shape[0]} h, {coverage*100:.0f}% obs coverage"}
    if verbose:
        print(f"  HFR: fetched {u.shape} in {time.time()-t0:.1f}s, "
              f"{coverage*100:.0f}% coverage @ {step*111:.1f} km")
    _save_cache(cpath, d)
    return d


def prepare(hfr, t0):
    """Align an HFR cube to forcing.py's time base (float hours since `t0`)
    and convert m/s -> km/h (the units the drift engine advects in). Returns
    a lightweight dict the Forcing object samples; None passes through."""
    if hfr is None:
        return None
    hsec = hfr["hours"].astype("datetime64[s]").astype("int64")
    t0sec = np.datetime64(t0).astype("datetime64[s]").astype("int64")
    return {"lats": np.asarray(hfr["lats"], float), "lngs": np.asarray(hfr["lngs"], float),
            "hours": (hsec - t0sec) / 3600.0, "u": hfr["u"] * 3.6, "v": hfr["v"] * 3.6,
            "step": float(hfr["step_deg"]), "coverage": float(hfr["coverage"]),
            "source": hfr["source"]}


if __name__ == "__main__":
    d = fetch_hfr()
    if d:
        u, v = d["u"], d["v"]
        spd = np.sqrt(u**2 + v**2)
        print(f"grid {len(d['lats'])} x {len(d['lngs'])} @ {d['step_deg']*111:.1f} km")
        print(f"lat {d['lats'].min():.2f}..{d['lats'].max():.2f}  "
              f"lng {d['lngs'].min():.2f}..{d['lngs'].max():.2f}")
        print(f"hours {str(d['hours'][0])[:13]} .. {str(d['hours'][-1])[:13]} (n={len(d['hours'])})")
        print(f"coverage {d['coverage']*100:.0f}%  speed median "
              f"{np.nanmedian(spd):.2f} m/s  max {np.nanmax(spd):.2f} m/s")
        # latest-slice coverage over the SCB (what the drift actually sees now)
        latest = np.isfinite(u[-1]).mean()
        print(f"latest-hour coverage {latest*100:.0f}%")
