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
import os
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


_HIST_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "cache")
_HIST_TTL_H = 12.0


def _hist_cache_load(name):
    p = os.path.join(_HIST_CACHE_DIR, name)
    if os.path.exists(p) and (time.time() - os.path.getmtime(p)) / 3600.0 < _HIST_TTL_H:
        try:
            return np.load(p, allow_pickle=False)
        except Exception:
            return None
    return None


def _hist_cache_save(name, **arrays):
    try:
        os.makedirs(_HIST_CACHE_DIR, exist_ok=True)
        np.savez(os.path.join(_HIST_CACHE_DIR, name), **arrays)
    except Exception:
        pass


def _get_retry(url, params, timeout=120, tries=4):
    """GET with exponential backoff on 429/5xx — the reservoir's seasonal SST +
    wave fetches are heavy and can trip Open-Meteo's rate limit (esp. in bursts).
    Raises after the last try so callers still degrade gracefully."""
    delay = 10.0
    for i in range(tries):
        r = requests.get(url, params=params, timeout=timeout)
        if r.status_code == 429 or r.status_code >= 500:
            if i < tries - 1:
                print(f"    {r.status_code} rate-limited; backing off {delay:.0f}s...")
                time.sleep(delay)
                delay *= 2
                continue
        r.raise_for_status()
        return r
    r.raise_for_status()
    return r


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
        r = _get_retry(url, params, timeout=120)
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


# ---------------------------------------------------------------------------
# Trailing thermal history -> cumulative warm-shed DOSE (detachment warm term)
# ---------------------------------------------------------------------------
# Warm-water canopy loss is cumulative over ~weeks, not same-day (see config
# detachment notes). We need SST further back than the 21-day drift window: the
# oldest release cohort (age ~21 d) needs its prior ~6-week dose, i.e. SST to
# ~63 d ago. This is an SST-ONLY, decoupled fetch so the main forcing stays lean.
THERMAL_DAYS_BACK = 70


class ThermalHistory:
    """Daily-mean SST on the coarse grid, with a trailing-window thermal-dose
    sampler. `dose(...)` returns the MEAN of max(0, SST - thresh) over the
    `window_days` ending `age_days` before now (degC) -- a degree-week-style
    dose that rewards SUSTAINED warmth, not a 2-day blip."""

    def __init__(self, lats, lngs, sst_daily, step):
        self.lats, self.lngs, self.sst_daily, self.step = lats, lngs, sst_daily, step
        self.ndays = sst_daily.shape[0]   # sst_daily[0]=today, [d]=d days ago

    def _bil(self, cube, d, lat, lng):
        j0, i0, tj, ti_ = _bil_weights(self.lats, self.lngs, lat, lng, self.step)
        ws = ((1 - tj) * (1 - ti_), (1 - tj) * ti_, tj * (1 - ti_), tj * ti_)
        ij = ((j0, i0), (j0, i0 + 1), (j0 + 1, i0), (j0 + 1, i0 + 1))
        num = den = 0.0
        for (jj, ii), w in zip(ij, ws):
            v = cube[d, jj, ii]
            if v == v:
                num += v * w
                den += w
        return num / den if den > 0 else float("nan")

    def daily_sst(self, lng, lat, day_ago):
        """Daily-mean SST (°C) at a point, `day_ago` days before now."""
        d = int(round(day_ago))
        if d < 0 or d >= self.ndays:
            return float("nan")
        return self._bil(self.sst_daily, d, lat, lng)

    def dose(self, lat, lng, thresh, age_days, window_days):
        d0 = int(round(age_days))
        d1 = min(self.ndays, d0 + int(round(window_days)))
        if d0 >= self.ndays:
            return 0.0
        excess = []
        for d in range(d0, d1):
            v = self._bil(self.sst_daily, d, lat, lng)
            if v == v:
                excess.append(max(0.0, v - thresh))
        return float(np.mean(excess)) if excess else 0.0


class WaveHistory:
    """Daily wave state on the coarse grid (Hs_max, Tp, dominant Dp per day).
    dose(profile, lng, lat, day_ago) -> exposure-weighted wave ENERGY above
    threshold that day (the daily shed forcing for the canopy reservoir)."""

    def __init__(self, lats, lngs, hs, tp, dp, step):
        self.lats, self.lngs, self.hs, self.tp, self.dp, self.step = lats, lngs, hs, tp, dp, step
        self.ndays = hs.shape[0]   # [0]=today, [d]=d days ago

    def _bil(self, cube, d, lat, lng):
        j0, i0, tj, ti_ = _bil_weights(self.lats, self.lngs, lat, lng, self.step)
        ws = ((1 - tj) * (1 - ti_), (1 - tj) * ti_, tj * (1 - ti_), tj * ti_)
        ij = ((j0, i0), (j0, i0 + 1), (j0 + 1, i0), (j0 + 1, i0 + 1))
        num = den = 0.0
        for (jj, ii), w in zip(ij, ws):
            v = cube[d, jj, ii]
            if v == v:
                num += v * w
                den += w
        return num / den if den > 0 else float("nan")

    def dose(self, profile, lng, lat, day_ago):
        import exposure as _ex
        d = int(round(day_ago))
        if d < 0 or d >= self.ndays:
            return 0.0
        hs = self._bil(self.hs, d, lat, lng)
        tp = self._bil(self.tp, d, lat, lng)
        dp = self._bil(self.dp, d, lat, lng)
        if hs != hs or tp != tp or tp <= 0:
            return 0.0
        energy = hs * hs * tp
        ex = _ex.exposure(profile, dp) if dp == dp else 1.0
        return ex * max(0.0, energy - config.WAVE_E_CRIT)


def _fetch_daily_field(url, daily_vars, days_back, chunk=50):
    lats, lngs = grid_axes()
    LA, LN = np.meshgrid(lats, lngs, indexing="ij")
    flat_la, flat_ln = LA.ravel(), LN.ravel()
    nlat, nlng = len(lats), len(lngs)
    all_results = [None] * len(flat_la)
    for idx in _chunks(list(range(len(flat_la))), chunk):
        params = {
            "latitude": ",".join(f"{flat_la[i]:.4f}" for i in idx),
            "longitude": ",".join(f"{flat_ln[i]:.4f}" for i in idx),
            "daily": ",".join(daily_vars),
            "past_days": days_back, "forecast_days": 1, "timezone": "UTC",
        }
        r = _get_retry(url, params, timeout=120)
        data = r.json()
        res = data if isinstance(data, list) else [data]
        for k, i in enumerate(idx):
            all_results[i] = res[k]
    times = None
    for res in all_results:
        t = (res or {}).get("daily", {}).get("time")
        if t:
            times = t
            break
    if not times:
        raise RuntimeError("no daily time axis")
    nt = len(times)
    cubes = {v: np.full((nt, nlat, nlng), np.nan) for v in daily_vars}
    for fi, res in enumerate(all_results):
        j, i = fi // nlng, fi % nlng
        dd = (res or {}).get("daily", {})
        for v in daily_vars:
            arr = dd.get(v)
            if not arr:
                continue
            a = np.array([np.nan if x is None else x for x in arr], dtype=float)
            m = min(len(a), nt)
            cubes[v][:m, j, i] = a[:m]
    return lats, lngs, cubes


def fetch_wave_history(days_back=None):
    """Daily wave history (Hs_max, Tp, dominant Dp) on the coarse grid, indexed
    [0]=today .. [d]=d days ago, for the seasonal canopy-shed forcing. Returns a
    WaveHistory or None on failure (reservoir then uses the short 21-day forcing)."""
    days_back = config.SEASON_DAYS if days_back is None else days_back
    cn = f"wave_{days_back}.npz"
    cz = _hist_cache_load(cn)
    if cz is not None:
        print(f"  wave history: cache hit ({days_back}d)")
        return WaveHistory(cz["lats"], cz["lngs"], cz["hs"], cz["tp"], cz["dp"], config.GRID_STEP_DEG)
    try:
        print(f"fetching {days_back}d wave history (seasonal shed forcing)...")
        lats, lngs, cubes = _fetch_daily_field(
            config.MARINE_URL,
            ["wave_height_max", "wave_period_max", "wave_direction_dominant"], days_back)
    except Exception as e:
        print(f"  wave-history fetch failed ({e}); reservoir shed -> short-window only")
        return None
    # daily time axis runs oldest->newest; reverse so index 0 = most recent day
    hs = cubes["wave_height_max"][::-1]
    tp = cubes["wave_period_max"][::-1]
    dp = cubes["wave_direction_dominant"][::-1]
    cov = float(np.isfinite(hs).mean())
    print(f"  wave history: {hs.shape[0]} daily layers, {cov*100:.0f}% filled")
    _hist_cache_save(cn, lats=lats, lngs=lngs, hs=hs, tp=tp, dp=dp)
    return WaveHistory(lats, lngs, hs, tp, dp, config.GRID_STEP_DEG)


def fetch_thermal_history(days_back=THERMAL_DAYS_BACK):
    """SST-only coarse-grid daily history for the cumulative warm-shed dose.
    Returns a ThermalHistory, or None on failure (detachment then falls back to
    the instantaneous warm proxy so a fetch outage can't break the run)."""
    cn = f"sst_{days_back}.npz"
    cz = _hist_cache_load(cn)
    if cz is not None:
        print(f"  SST history: cache hit ({days_back}d)")
        return ThermalHistory(cz["lats"], cz["lngs"], cz["sst_daily"], config.GRID_STEP_DEG)
    try:
        print(f"fetching {days_back}d SST history (cumulative warm-shed dose)...")
        lats, lngs, hours, t0, cubes = _fetch_field(
            config.MARINE_URL, ["sea_surface_temperature"],
            extra={"past_days": days_back, "forecast_days": 1})
    except Exception as e:
        print(f"  thermal-history fetch failed ({e}); warm term -> instantaneous fallback")
        return None
    sst = cubes["sea_surface_temperature"]
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    now_h = (now - t0).total_seconds() / 3600.0
    days_ago = np.floor((now_h - hours) / 24.0 + 1e-9).astype(int)   # 0=today, +into past
    ndays = days_back + 1
    nlat, nlng = len(lats), len(lngs)
    sst_daily = np.full((ndays, nlat, nlng), np.nan)
    with np.errstate(invalid="ignore"):
        for d in range(ndays):
            sel = days_ago == d
            if sel.any():
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", category=RuntimeWarning)
                    sst_daily[d] = np.nanmean(sst[sel], axis=0)
    cov = float(np.isfinite(sst_daily).mean())
    print(f"  SST history: {ndays} daily layers, {cov*100:.0f}% filled")
    _hist_cache_save(cn, lats=lats, lngs=lngs, sst_daily=sst_daily)
    return ThermalHistory(lats, lngs, sst_daily, config.GRID_STEP_DEG)
