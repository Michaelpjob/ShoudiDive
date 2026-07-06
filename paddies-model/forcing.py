"""Assemble a trailing-window forcing field over the SCB from Open-Meteo's
free JSON APIs: surface currents (+ windage) for advection, plus
sea-surface temperature and wave height for the detachment/sink models.

fetch_raw() pulls the ocean data ONCE; make_forcing(raw, scenario) derives
each scenario (live / swell / warm) from it without re-fetching — so the
unified dashboard feeds all scenarios off one download.
"""
from __future__ import annotations

import datetime as dt
import math
import time

import numpy as np
import requests

import config

MARINE_VARS = ["ocean_current_velocity", "ocean_current_direction",
               "sea_surface_temperature", "wave_height", "wave_period", "wave_direction"]


def grid_axes():
    b = config.FIELD_BBOX
    lats = np.arange(b["lat_min"], b["lat_max"] + 1e-9, config.GRID_STEP_DEG)
    lngs = np.arange(b["lng_min"], b["lng_max"] + 1e-9, config.GRID_STEP_DEG)
    return lats, lngs


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _fetch_batch(url, lats_b, lngs_b, hourly_vars, extra=None):
    params = {
        "latitude": ",".join(f"{v:.4f}" for v in lats_b),
        "longitude": ",".join(f"{v:.4f}" for v in lngs_b),
        "hourly": ",".join(hourly_vars),
        "past_days": config.PAST_DAYS,
        "forecast_days": config.FORECAST_DAYS,
        "timezone": "UTC",
    }
    if extra:
        params.update(extra)
    try:
        r = requests.get(url, params=params, timeout=120)
        r.raise_for_status()
        data = r.json()
        results = data if isinstance(data, list) else [data]
        if len(results) == len(lats_b):
            return results
        print(f"  batch shape {len(results)} != {len(lats_b)}; per-point fallback")
    except Exception as e:
        print(f"  batch fetch error ({e}); per-point fallback")
    results = []
    for la, ln in zip(lats_b, lngs_b):
        p = dict(params)
        p["latitude"], p["longitude"] = f"{la:.4f}", f"{ln:.4f}"
        rr = requests.get(url, params=p, timeout=60)
        rr.raise_for_status()
        results.append(rr.json())
        time.sleep(0.02)
    return results


def _fetch_field(url, hourly_vars, extra=None, chunk=50):
    lats, lngs = grid_axes()
    LA, LN = np.meshgrid(lats, lngs, indexing="ij")
    flat_la, flat_ln = LA.ravel(), LN.ravel()
    nlat, nlng = len(lats), len(lngs)

    all_results = [None] * len(flat_la)
    for idx in _chunks(list(range(len(flat_la))), chunk):
        res = _fetch_batch(url, [flat_la[i] for i in idx], [flat_ln[i] for i in idx],
                           hourly_vars, extra)
        for k, i in enumerate(idx):
            all_results[i] = res[k]

    times_iso = None
    for res in all_results:
        t = (res or {}).get("hourly", {}).get("time")
        if t:
            times_iso = t
            break
    if not times_iso:
        raise RuntimeError(f"no time axis from {url}")
    t0 = dt.datetime.fromisoformat(times_iso[0])
    hours = np.array([(dt.datetime.fromisoformat(t) - t0).total_seconds() / 3600.0
                      for t in times_iso], dtype=float)
    nt = len(times_iso)

    cubes = {v: np.full((nt, nlat, nlng), np.nan, dtype=float) for v in hourly_vars}
    for flat_i, res in enumerate(all_results):
        j, i = flat_i // nlng, flat_i % nlng
        h = (res or {}).get("hourly", {})
        for v in hourly_vars:
            arr = h.get(v)
            if not arr:
                continue
            a = np.array([np.nan if x is None else x for x in arr], dtype=float)
            m = min(len(a), nt)
            cubes[v][:m, j, i] = a[:m]
    return lats, lngs, hours, t0, cubes


def _uv(speed, direction, toward=True):
    d = np.radians(direction if toward else (direction + 180.0))
    return speed * np.sin(d), speed * np.cos(d)


def _bil_weights(lats, lngs, lat, lng, step):
    lat = min(max(lat, lats[0]), lats[-1])
    lng = min(max(lng, lngs[0]), lngs[-1])
    fj, fi = (lat - lats[0]) / step, (lng - lngs[0]) / step
    j0 = min(max(int(math.floor(fj)), 0), len(lats) - 2)
    i0 = min(max(int(math.floor(fi)), 0), len(lngs) - 2)
    return j0, i0, fj - j0, fi - i0


class Forcing:
    def __init__(self, lats, lngs, hours, t0, u, v, scalars, meta, hfr=None):
        self.lats, self.lngs, self.hours, self.t0 = lats, lngs, hours, t0
        self.u, self.v, self.scalars, self.meta = u, v, scalars, meta
        self.step = config.GRID_STEP_DEG
        self.hfr = hfr   # fused HFRNet 6 km currents (km/h), or None

    def _ti(self, h):
        return int(np.argmin(np.abs(self.hours - h)))

    def _sample_hfr(self, h, lat, lng):
        """Nan-aware bilinear current from the HFR cube, or None if the point
        is outside radar coverage (all 4 corners missing / out of bbox)."""
        H = self.hfr
        la, ln = H["lats"], H["lngs"]
        if lat < la[0] or lat > la[-1] or lng < ln[0] or lng > ln[-1]:
            return None
        # HFR observes the past only — outside its time coverage (future frames,
        # or pre-window past) fall back to Open-Meteo forecast/hindcast.
        if h > H["hours"][-1] + 1.0 or h < H["hours"][0] - 1.0:
            return None
        ti = int(np.argmin(np.abs(H["hours"] - h)))
        j0, i0, tj, ti_ = _bil_weights(la, ln, lat, lng, H["step"])
        uu, vv = H["u"][ti], H["v"][ti]
        ws = ((1 - tj) * (1 - ti_), (1 - tj) * ti_, tj * (1 - ti_), tj * ti_)
        ij = ((j0, i0), (j0, i0 + 1), (j0 + 1, i0), (j0 + 1, i0 + 1))
        un = vn = den = 0.0
        for (jj, ii), w in zip(ij, ws):
            a, b = uu[jj, ii], vv[jj, ii]
            if a == a and b == b:
                un += a * w; vn += b * w; den += w
        if den <= 0.0:
            return None
        return un / den, vn / den

    def sample(self, h, lat, lng):
        if self.hfr is not None:
            uv = self._sample_hfr(h, lat, lng)
            if uv is not None:
                return uv
        ti = self._ti(h)
        j0, i0, tj, ti_ = _bil_weights(self.lats, self.lngs, lat, lng, self.step)

        def bil(c):
            return (c[ti, j0, i0] * (1 - tj) * (1 - ti_) + c[ti, j0, i0 + 1] * (1 - tj) * ti_
                    + c[ti, j0 + 1, i0] * tj * (1 - ti_) + c[ti, j0 + 1, i0 + 1] * tj * ti_)
        return float(bil(self.u)), float(bil(self.v))

    def fine_current_mean(self, window_h=72.0):
        """Recent-mean HFR current on its native 6 km grid (km/h), for the
        convergence field. Mean over the trailing window fills hourly radar
        gaps and cancels tidal flip-flop, leaving the persistent mesoscale
        convergence that actually concentrates paddies. None if no HFR."""
        if self.hfr is None:
            return None
        hh = self.hfr["hours"]
        sel = hh >= (float(hh.max()) - window_h)
        if int(sel.sum()) < 1:
            sel = slice(None)
        import warnings
        with warnings.catch_warnings(), np.errstate(invalid="ignore"):
            warnings.simplefilter("ignore", category=RuntimeWarning)
            u = np.nanmean(self.hfr["u"][sel], axis=0)
            v = np.nanmean(self.hfr["v"][sel], axis=0)
        return self.hfr["lats"], self.hfr["lngs"], u, v, self.hfr["step"]

    def sample_scalar(self, name, h, lat, lng):
        c = self.scalars[name]
        ti = self._ti(h)
        j0, i0, tj, ti_ = _bil_weights(self.lats, self.lngs, lat, lng, self.step)
        vals = [c[ti, j0, i0], c[ti, j0, i0 + 1], c[ti, j0 + 1, i0], c[ti, j0 + 1, i0 + 1]]
        ws = [(1 - tj) * (1 - ti_), (1 - tj) * ti_, tj * (1 - ti_), tj * ti_]
        num = den = 0.0
        for val, w in zip(vals, ws):
            if val == val:
                num += val * w
                den += w
        return num / den if den > 0 else float("nan")

    def now_hours(self):
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        return (now - self.t0).total_seconds() / 3600.0


def fetch_raw():
    """Pull the TRAILING-HINDCAST field (past_days, hourly, TIME-VARYING):
    currents, SST, waves Hs/Tp/Dp, and wind — the raw material for
    event-driven shedding + day-by-day drift to now."""
    print("fetching trailing hindcast (Open-Meteo: currents, waves Hs/Tp/Dp, SST)...")
    lats, lngs, hours, t0, cubes = _fetch_field(config.MARINE_URL, MARINE_VARS)
    cu, cv = _uv(cubes["ocean_current_velocity"], cubes["ocean_current_direction"],
                 toward=config.CURRENT_DIR_TOWARD)
    sst, hs, tp = cubes["sea_surface_temperature"], cubes["wave_height"], cubes["wave_period"]
    dp = cubes["wave_direction"]

    wu = wv = None
    if config.USE_WIND:
        print("fetching trailing winds (Open-Meteo weather)...")
        try:
            _, _, _, _, wc = _fetch_field(config.WEATHER_URL,
                                          ["wind_speed_10m", "wind_direction_10m"])
            wu, wv = _uv(wc["wind_speed_10m"], wc["wind_direction_10m"], toward=False)
        except Exception as e:
            print(f"  wind fetch failed ({e}); no windage")

    n = cu.shape[0] if wu is None else min(cu.shape[0], wu.shape[0])
    cu, cv = np.nan_to_num(cu[:n]), np.nan_to_num(cv[:n])
    sst, hs, tp, dp, hours = sst[:n], hs[:n], tp[:n], dp[:n], hours[:n]
    wu = np.nan_to_num(wu[:n]) if wu is not None else np.zeros_like(cu)
    wv = np.nan_to_num(wv[:n]) if wv is not None else np.zeros_like(cv)

    spd = np.sqrt(cu[-1] ** 2 + cv[-1] ** 2)
    bearing = (math.degrees(math.atan2(float(np.mean(cu[-1])), float(np.mean(cv[-1])))) + 360) % 360
    base_meta = {
        "source": "open-meteo trailing hindcast (currents+waves+wind+SST, time-varying)",
        "grid": {"nlat": len(lats), "nlng": len(lngs), "step_deg": config.GRID_STEP_DEG},
        "window_days": round(float(hours[-1] - hours[0]) / 24.0, 1),
        "mean_current_kmh": round(float(np.mean(spd)), 3),
        "mean_current_bearing_deg": round(bearing, 1),
        "mean_wind_kmh": round(float(np.mean(np.sqrt(wu[-1] ** 2 + wv[-1] ** 2))), 1),
        "t0_utc": t0.isoformat() + "Z",
        "snapshot": False,
    }
    print(f"  field {base_meta['grid']} | {base_meta['window_days']}d window | current "
          f"{base_meta['mean_current_kmh']} km/h toward {base_meta['mean_current_bearing_deg']}deg "
          f"| wind {base_meta['mean_wind_kmh']} km/h")
    return {"lats": lats, "lngs": lngs, "hours": hours, "t0": t0, "u": cu, "v": cv,
            "sst": sst, "hs": hs, "tp": tp, "dp": dp, "wind_u": wu, "wind_v": wv,
            "base_meta": base_meta}


def make_forcing(raw, scenario="live", hfr=None):
    sst, hs = raw["sst"].copy(), raw["hs"].copy()
    if scenario == "swell":
        hs = hs + config.SWELL_BOOST_M
    elif scenario == "warm":
        sst = sst + config.WARM_BOOST_C
    elif scenario == "storm":
        # inject a localized storm STORM_DAYS_AGO days ago: a Hs spike on
        # those time-slices only, so one dated cohort sheds and then drifts.
        now_h = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
                 - raw["t0"]).total_seconds() / 3600.0
        center = now_h - config.STORM_DAYS_AGO * 24.0
        tmask = np.abs(raw["hours"] - center) <= config.STORM_WINDOW_H / 2.0
        hs[tmask] = hs[tmask] + config.STORM_HS_BOOST_M
    scalars = {"sst": sst, "hs": hs, "tp": raw["tp"], "dp": raw["dp"],
               "wind_u": raw["wind_u"], "wind_v": raw["wind_v"]}
    meta = dict(raw["base_meta"])
    meta["scenario"] = scenario
    if hfr is not None:
        meta["current_source"] = hfr["source"]
        meta["current_grid_km"] = round(hfr["step"] * 111.0, 1)
    return Forcing(raw["lats"], raw["lngs"], raw["hours"], raw["t0"],
                   raw["u"], raw["v"], scalars, meta, hfr=hfr)


def build_forcing(scenario="live"):
    return make_forcing(fetch_raw(), scenario)
