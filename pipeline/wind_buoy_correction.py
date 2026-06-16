"""Buoy-anchored correction for the blended 10 m wind nowcast.

Pulls recent NDBC buoy wind, height-adjusts it to 10 m, computes the u/v
residual against the blended model field at each buoy, and builds a smooth
Gaussian-kernel correction surface — the same technique
``sst_buoy_correction.py`` uses for SST. It reuses that module's buoy
registry and bilinear sampler so the two stay in sync.

Why
---
The ECMWF+HRRR/GFS blend cancels each model's bias, but the **buoys are
measuring the actual wind right now**. Differencing the blend against the
buoys and kriging the residual snaps the nowcast onto ground truth wherever
ground truth exists, and fades to the plain blend elsewhere.

Caveats (wind is harder to anchor than SST)
-------------------------------------------
* Buoys measure at the anemometer height (~5 m typical for NDBC discus /
  NOMAD hulls), not 10 m, so each reading is scaled to 10 m with a neutral
  power-law profile before differencing. Without this the correction would
  drag the 10 m model *low*.
* Wind decorrelates faster in space than SST, so the kriging length is
  shorter and the per-cell cap tighter — the correction stays local to each
  buoy.
* Only meaningful for the **nowcast**: a buoy reading is "now", so this is
  applied to the ``now`` slot only. Multi-hour/day forecast slots keep the
  plain blend (the buoy can't speak to a future hour).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import requests

# Reuse the SST module's buoy registry + bilinear sampler — single source of
# truth for which CA-coast stations we anchor on. The SST buoys all report
# wind too (verified), so the same six give us SoCal-Bight → Central anchors.
try:
    from pipeline.sst_buoy_correction import BUOYS, _bilinear_at
except ModuleNotFoundError:
    from sst_buoy_correction import BUOYS, _bilinear_at

NDBC_REALTIME2_URL = "https://www.ndbc.noaa.gov/data/realtime2/{stn}.txt"
USER_AGENT = (
    "ShoudiDive-wind-correction/1.0 "
    "(+https://shouldidive.com/about/validation; nowcast buoy probe)"
)
HTTP_TIMEOUT = 30

# realtime2 columns, 0-indexed in split() output:
#   0 YY 1 MM 2 DD 3 hh 4 mm  5 WDIR(deg-from-true)  6 WSPD(m/s)  7 GST ...
WDIR_COL, WSPD_COL = 5, 6

# Wind is gustier than SST → a tighter window better represents "now".
# 3 h smooths 10-min sampling noise while staying close to the current hour
# the nowcast slot is valid at.
BUOY_LOOKBACK_HOURS = 3
# NDBC anemometer height (m). Most CA discus/NOMAD hulls sit at ~5 m; we
# power-law these up to the model's 10 m. (Per-buoy heights could refine
# this later; the ±1 m spread is a ~2% effect.)
ANEMOMETER_HEIGHT_M = 5.0
WIND_PROFILE_ALPHA = 0.11        # neutral marine power-law exponent
# Shorter than SST's 60 km — wind decorrelates faster, so keep the
# correction local to each buoy rather than smearing it across the bbox.
KRIG_LENGTH_KM = 45.0
CORRECTION_MAX_MS = 4.0          # cap |du|,|dv| per cell
RESIDUAL_SANITY_MS = 12.0        # drop a buoy whose residual is implausible


def _height_adjust(wspd_z: float, z: float = ANEMOMETER_HEIGHT_M) -> float:
    """Scale a wind speed at height ``z`` up to 10 m (neutral power law)."""
    return wspd_z * (10.0 / z) ** WIND_PROFILE_ALPHA


def _dir_to_uv(wspd: float, wdir_deg: float) -> tuple[float, float]:
    """Meteorological speed + 'from' direction → (u east, v north) m/s."""
    th = math.radians(wdir_deg)
    return (-wspd * math.sin(th), -wspd * math.cos(th))


@dataclass
class BuoyWind:
    stn: str
    name: str
    lat: float
    lng: float
    u10: float          # mean east-wind over the window, scaled to 10 m
    v10: float
    spd10: float
    n_samples: int
    age_hours: float


def fetch_buoy_winds(*, now: Optional[datetime] = None,
                     lookback_hours: int = BUOY_LOOKBACK_HOURS) -> list[BuoyWind]:
    """Pull the last ``lookback_hours`` of wind from each buoy, averaged as
    u/v vectors (so a veering wind averages correctly) and scaled to 10 m.
    Buoys with no recent wind are silently skipped."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=lookback_hours)
    out: list[BuoyWind] = []

    for buoy in BUOYS:
        try:
            r = requests.get(
                NDBC_REALTIME2_URL.format(stn=buoy["stn"]),
                headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
                timeout=HTTP_TIMEOUT,
            )
        except requests.exceptions.RequestException:
            continue
        if r.status_code != 200:
            continue

        us: list[float] = []
        vs: list[float] = []
        spds: list[float] = []
        latest_age: Optional[float] = None
        for line in r.text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = re.split(r"\s+", line)
            if len(parts) <= WSPD_COL:
                continue
            try:
                yy, mm, dd, hh, mn = (int(parts[i]) for i in range(5))
                ts = datetime(yy, mm, dd, hh, mn, tzinfo=timezone.utc)
                if ts < cutoff:
                    continue
                wd, ws = parts[WDIR_COL], parts[WSPD_COL]
                if wd == "MM" or ws == "MM":
                    continue
                wdir, wspd = float(wd), float(ws)
                if not (0.0 <= wdir <= 360.0) or not (0.0 <= wspd < 60.0):
                    continue
            except (ValueError, IndexError):
                continue
            sp = _height_adjust(wspd)
            u, v = _dir_to_uv(sp, wdir)
            us.append(u)
            vs.append(v)
            spds.append(sp)
            age = (now - ts).total_seconds() / 3600.0
            if latest_age is None or age < latest_age:
                latest_age = age

        if not us:
            continue
        # Direction from the mean vector (robust to veering); speed from the
        # scalar mean (so a veering window doesn't artificially shrink the
        # anchored speed). Rebuild u/v from the two.
        um = sum(us) / len(us)
        vm = sum(vs) / len(vs)
        mean_spd = sum(spds) / len(spds)
        mean_dir = (math.degrees(math.atan2(-um, -vm)) + 360.0) % 360.0
        u10, v10 = _dir_to_uv(mean_spd, mean_dir)
        out.append(BuoyWind(
            stn=buoy["stn"], name=buoy["name"], lat=buoy["lat"], lng=buoy["lng"],
            u10=u10, v10=v10, spd10=mean_spd,
            n_samples=len(us), age_hours=round(latest_age or 0.0, 1),
        ))
    return out


def _binfo(b: BuoyWind) -> dict:
    return {
        "stn": b.stn, "name": b.name, "lat": b.lat, "lng": b.lng,
        "buoy_spd_kt": round(b.spd10 * 1.94384, 1),
        "n_samples": b.n_samples, "age_hours": b.age_hours,
    }


def wind_correction_surface(
    *,
    u_grid: np.ndarray,
    v_grid: np.ndarray,
    lats: np.ndarray,
    lngs: np.ndarray,
    buoys: list[BuoyWind],
    length_km: float = KRIG_LENGTH_KM,
    max_ms: float = CORRECTION_MAX_MS,
) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    """Per-cell additive (du, dv) correction in m/s — Gaussian-kernel IDW of
    the buoy u/v residuals (buoy_10m − model). Returns (du, dv, anchor_info).
    Same kernel as sst_buoy_correction, run on each component."""
    H, W = u_grid.shape
    assert lats.size == H and lngs.size == W, "grid/axis size mismatch"

    anchor_info: list[dict] = []
    pts: list[tuple[float, float, float, float]] = []  # lat, lng, du_res, dv_res
    for b in buoys:
        mu = _bilinear_at(u_grid, lats, lngs, b.lat, b.lng)
        mv = _bilinear_at(v_grid, lats, lngs, b.lat, b.lng)
        if not (np.isfinite(mu) and np.isfinite(mv)):
            anchor_info.append({**_binfo(b), "model_spd_kt": None, "resid_ms": None,
                                "skipped": "no model coverage at buoy"})
            continue
        rdu, rdv = b.u10 - mu, b.v10 - mv
        rmag = math.hypot(rdu, rdv)
        info = {**_binfo(b), "model_spd_kt": round(math.hypot(mu, mv) * 1.94384, 1),
                "resid_ms": round(rmag, 1)}
        if rmag > RESIDUAL_SANITY_MS:
            anchor_info.append({**info, "skipped": f"|resid| > {RESIDUAL_SANITY_MS} m/s"})
            continue
        pts.append((b.lat, b.lng, rdu, rdv))
        anchor_info.append({**info, "skipped": None})

    du = np.zeros((H, W), dtype=np.float32)
    dv = np.zeros((H, W), dtype=np.float32)
    if not pts:
        return du, dv, anchor_info

    LAT2D, LNG2D = np.meshgrid(lats, lngs, indexing="ij")
    sw = np.zeros((H, W), dtype=np.float64)
    swu = np.zeros((H, W), dtype=np.float64)
    swv = np.zeros((H, W), dtype=np.float64)
    for (blat, blng, rdu, rdv) in pts:
        dy = (LAT2D - blat) * 111.32
        dx = (LNG2D - blng) * 111.32 * math.cos(math.radians(blat))
        w = np.exp(-(dy * dy + dx * dx) / (length_km * length_km))
        sw += w
        swu += w * rdu
        swv += w * rdv
    with np.errstate(invalid="ignore", divide="ignore"):
        du[:] = np.clip(np.where(sw > 1e-9, swu / sw, 0.0), -max_ms, max_ms).astype(np.float32)
        dv[:] = np.clip(np.where(sw > 1e-9, swv / sw, 0.0), -max_ms, max_ms).astype(np.float32)
    return du, dv, anchor_info


def correction_summary(anchor_info: list[dict],
                       length_km: float = KRIG_LENGTH_KM) -> dict:
    """Compact JSON block for manifest.json + the validation watchdog."""
    active = [a for a in anchor_info if a.get("skipped") is None]
    return {
        "method": "buoy_kriging_uv",
        "length_km": length_km,
        "anemometer_height_m": ANEMOMETER_HEIGHT_M,
        "n_anchors_total": len(anchor_info),
        "n_anchors_active": len(active),
        "mean_resid_ms": (round(float(np.mean([a["resid_ms"] for a in active])), 2)
                          if active else None),
        "anchors": anchor_info,
    }


def main() -> int:
    """Smoke test — fetch buoys + print the 10 m wind table."""
    winds = fetch_buoy_winds()
    print(f"Got {len(winds)} buoys with wind in last {BUOY_LOOKBACK_HOURS}h "
          f"(scaled to 10 m):")
    for w in winds:
        drf = (math.degrees(math.atan2(-w.u10, -w.v10)) + 360) % 360
        print(f"  {w.stn:>5} {w.name:<22} {w.lat:6.2f}N {w.lng:8.2f}W "
              f"= {w.spd10 * 1.94384:5.1f} kt / {drf:3.0f}deg "
              f"(n={w.n_samples}, age={w.age_hours}h)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
