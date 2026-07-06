"""ShoudiDive data source — pull the real published layers from
shouldidive.com and feed an exhaustive multi-driver kelp-fate model.

Layers decoded (all over bbox [-128.5,31.8,-116.8,42], resampled to grid):
  current  = RTOFS ocean-model (/data/ca/rtofs/uv_d1.png, persistent
             California Current) BLENDED with the HFRNet-blended surface
             current (/data/currents/buckets, daily-mean of 5 tidal phases)
  wind     = /data/wind/buckets/d0_midday_uv.png  (windage / leeway)
  waves    = /data/swell/buckets/d0_midday_wave.png  Hs(R) Tp(G) Dp(B)
             -> Stokes drift + wave detachment
  SST      = /data/sst_1d.png  (586x511, fronts + sink + thermal detach)
  chl      = /data/chl_1d.png  (color-break fish signal)

All are 'now' snapshots (ShoudiDive publishes forecasts, not a trailing
hindcast) so the drift integrates a STATIC field over the window.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import math
import os
import warnings

import numpy as np
import requests
from PIL import Image

import config
from forcing import Forcing, grid_axes

BASE = "https://shouldidive.com"


_LOCAL = os.environ.get("PADDIES_LOCAL_DATA", "").strip()


def _get(path):
    # Prefer the local published-data checkout when PADDIES_LOCAL_DATA is set
    # so the CI runner does not fetch shouldidive.com (Cloudflare blocks the
    # datacenter IP). Same fresh data; HTTP stays the fallback for local dev.
    if _LOCAL:
        lp = os.path.join(_LOCAL, path.lstrip("/"))
        if os.path.exists(lp):
            with open(lp, "rb") as f:
                return f.read()
    r = requests.get(BASE + path, timeout=60)
    r.raise_for_status()
    return r.content


def _L(path):
    return np.asarray(Image.open(io.BytesIO(_get(path))).convert("L"))


def _rgba(path):
    return np.asarray(Image.open(io.BytesIO(_get(path))).convert("RGBA")).astype(float)


def _decode_scalar(arr, vmin, vmax, log=False, nodata_at=0):
    a = arr.astype(float)
    if log:
        lo, hi = math.log10(vmin), math.log10(vmax)
        val = 10 ** (lo + (a / 255.0) * (hi - lo))
    else:
        val = vmin + (a / 255.0) * (vmax - vmin)
    val[arr <= nodata_at] = np.nan
    return val


def _uv(rgba, lo, hi, to_kmh=True):
    valid = rgba[..., 3] > 0
    span = hi - lo
    u = np.where(valid, lo + (rgba[..., 0] / 255.0) * span, np.nan)
    v = np.where(valid, lo + (rgba[..., 1] / 255.0) * span, np.nan)
    return (u * 3.6, v * 3.6) if to_kmh else (u, v)


def _resample(arr, bbox, tlats, tlngs):
    lng_min, lat_min, lng_max, lat_max = bbox
    Hn, Wn = arr.shape
    fr = np.clip((lat_max - np.asarray(tlats)) / (lat_max - lat_min) * (Hn - 1), 0, Hn - 1)
    fc = np.clip((np.asarray(tlngs) - lng_min) / (lng_max - lng_min) * (Wn - 1), 0, Wn - 1)
    j0 = np.clip(np.floor(fr).astype(int), 0, Hn - 2)
    i0 = np.clip(np.floor(fc).astype(int), 0, Wn - 2)
    tj = (fr - j0)[:, None]
    ti = (fc - i0)[None, :]
    A = arr[np.ix_(j0, i0)]; B = arr[np.ix_(j0, i0 + 1)]
    C = arr[np.ix_(j0 + 1, i0)]; Dd = arr[np.ix_(j0 + 1, i0 + 1)]
    return A * (1 - tj) * (1 - ti) + B * (1 - tj) * ti + C * tj * (1 - ti) + Dd * tj * ti


def _blend(a, b, wa, wb):
    """NaN-aware weighted blend (fall back to whichever is present)."""
    both = (~np.isnan(a)) & (~np.isnan(b))
    out = np.where(np.isnan(a), b, a)
    out = np.where(np.isnan(b), a, out)
    out[both] = wa * a[both] + wb * b[both]
    return out


def fetch_chl(tlats, tlngs):
    """Fetch ShoudiDive's published chl 'now' snapshot (mg/m^3), resampled to
    the given grid. Returns a 2D array, or None if unavailable. Used by the
    convergence water-quality gate as a clean/blue-water signal (low chl =
    clean) — optional, so the model degrades to distance-only without it."""
    try:
        m = json.loads(_get("/data/manifest.json"))
        bbox = m["bbox"]
        cr = m["layers"]["chl"]["range"]
        chl = _decode_scalar(_L("/data/chl_1d.png"), cr[0], cr[1], log=True, nodata_at=0)
        grid = _resample(chl, bbox, np.asarray(tlats), np.asarray(tlngs))
        cov = float(np.isfinite(grid).mean())
        print(f"  chl gate: ShoudiDive chl ok ({cov*100:.0f}% coverage)")
        return grid
    except Exception as e:
        print(f"  chl gate: chl unavailable ({type(e).__name__}); distance-only")
        return None


def fetch_raw():
    m = json.loads(_get("/data/manifest.json"))
    bbox = m["bbox"]
    Ls = m["layers"]
    print("fetching ShoudiDive layers (RTOFS + HFRNet current, wind, waves, SST, chl)...")

    # --- HFRNet-blended current: daily mean of the 5 d0 buckets (cancels tide)
    lo, hi = Ls["current5d"]["uv_range"]
    us, vs = [], []
    for bk in ("predawn", "morning", "midday", "afternoon", "evening"):
        cu, cv = _uv(_rgba(f"/data/currents/buckets/d0_{bk}_uv.png"), lo, hi)
        us.append(cu); vs.append(cv)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        Uh, Vh = np.nanmean(us, axis=0), np.nanmean(vs, axis=0)

    # --- RTOFS ocean-model current (d1 lead) — persistent backbone
    try:
        rt = json.loads(_get("/data/rtofs/summary.json"))
        rlo, rhi = rt["uv_range_ms"]
        Ur, Vr = _uv(_rgba("/data/rtofs/uv_d1.png"), rlo, rhi)
    except Exception as e:
        print(f"  RTOFS fetch failed ({e}); HFR only")
        Ur = Vr = np.full_like(Uh, np.nan)

    # --- wind (windage / leeway)
    try:
        wlo, whi = Ls["wind"]["uv_range"]
        WU, WV = _uv(_rgba("/data/wind/buckets/d0_midday_uv.png"), wlo, whi)
    except Exception as e:
        print(f"  wind fetch failed ({e}); no windage")
        WU = WV = np.full_like(Uh, np.nan)

    # --- waves Hs / Tp / Dp (Stokes drift + wave detachment)
    sw = json.loads(_get("/data/swell/summary.json"))
    hmax, tmax = sw["height_range_m"][1], sw["period_range_s"][1]
    wv = _rgba("/data/swell/buckets/d0_midday_wave.png")
    wvalid = wv[..., 3] > 0
    HS = np.where(wvalid, (wv[..., 0] / 255.0) * hmax, np.nan)
    TP = np.where(wvalid, (wv[..., 1] / 255.0) * tmax, np.nan)
    DP = np.where(wvalid, (wv[..., 2] / 255.0) * 360.0, np.nan)

    # --- SST + chl
    sr = Ls["sst"]["range"]
    SST = _decode_scalar(_L("/data/sst_1d.png"), sr[0], sr[1], nodata_at=1)
    cr = Ls["chl"]["range"]
    CHL = _decode_scalar(_L("/data/chl_1d.png"), cr[0], cr[1], log=True, nodata_at=0)

    # --- resample everything to the working grid, blend currents
    tlats, tlngs = grid_axes()

    def R(a):
        return _resample(a, bbox, tlats, tlngs)

    Ug = np.nan_to_num(_blend(R(Ur), R(Uh), config.CURRENT_BLEND_RTOFS, config.CURRENT_BLEND_HFR))
    Vg = np.nan_to_num(_blend(R(Vr), R(Vh), config.CURRENT_BLEND_RTOFS, config.CURRENT_BLEND_HFR))
    raw = {
        "lats": np.asarray(tlats), "lngs": np.asarray(tlngs),
        "u": Ug, "v": Vg,
        "wind_u": np.nan_to_num(R(WU)), "wind_v": np.nan_to_num(R(WV)),
        "sst": R(SST), "chl": R(CHL),
        "hs": R(HS), "tp": R(TP), "dp": R(DP),
    }
    spd = np.sqrt(Ug ** 2 + Vg ** 2)
    bearing = (math.degrees(math.atan2(float(np.mean(Ug)), float(np.mean(Vg)))) + 360) % 360
    wspd = np.sqrt(raw["wind_u"] ** 2 + raw["wind_v"] ** 2)
    raw["base_meta"] = {
        "scenario": "live",
        "source": "shouldidive: RTOFS+HFR current, wind, swell(Hs/Tp/Dp), SST586, chl",
        "grid": {"nlat": len(tlats), "nlng": len(tlngs), "step_deg": config.GRID_STEP_DEG},
        "mean_current_kmh": round(float(np.mean(spd)), 3),
        "mean_current_bearing_deg": round(bearing, 1),
        "mean_wind_kmh": round(float(np.nanmean(wspd)), 1),
        "mean_hs_m": round(float(np.nanmean(HS)), 2),
        "snapshot": True,
    }
    bm = raw["base_meta"]
    print(f"  grid {bm['grid']} | current {bm['mean_current_kmh']} km/h toward "
          f"{bm['mean_current_bearing_deg']}deg | wind {bm['mean_wind_kmh']} km/h | Hs {bm['mean_hs_m']} m")
    return raw


def make_forcing(raw, scenario="live"):
    sst, hs = raw["sst"].copy(), raw["hs"].copy()
    if scenario == "swell":
        hs = hs + config.SWELL_BOOST_M
    elif scenario == "warm":
        sst = sst + config.WARM_BOOST_C
    u = raw["u"][None, :, :]
    v = raw["v"][None, :, :]
    scalars = {name: raw[name][None, :, :] for name in
               ("wind_u", "wind_v", "chl", "tp", "dp")}
    scalars["sst"] = sst[None, :, :]
    scalars["hs"] = hs[None, :, :]
    t0 = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
          - dt.timedelta(hours=max(config.RELEASE_AGES_DAYS) * 24 + 24))
    meta = dict(raw["base_meta"])
    meta["scenario"] = scenario
    return Forcing(raw["lats"], raw["lngs"], np.array([0.0]), t0, u, v, scalars, meta)
