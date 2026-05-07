"""Buoy-anchored correction surface for the SST nowcast.

Pulls the last 24 h of water-temperature from CA-coast NDBC buoys,
computes the residual against MUR L4 at each buoy location, builds
a smooth Gaussian-kernel correction surface across the bbox, and
returns the corrected grid.

Why this module exists
----------------------
MUR L4 is excellent open-water — but it has known coastal bias on
the order of +0.3–0.5 °C in the SoCal Bight (skin-vs-bulk + the
gap-fill smearing land into nearshore cells). NDBC + CDIP buoys
report bulk water temperature in real time, so the residual at each
anchor is a direct measurement of MUR's local error. Kriging the
anchors gives every cell a correction term that's calibrated to the
ground truth wherever ground truth exists, and zero where it doesn't.

Scope
-----
This module ONLY computes the correction. ``pipeline/fetch.py``
applies it before encoding the SST PNGs. Validation against
ground-truth uses ``pipeline/validation/sst_score.py`` downstream;
buoys feed both this correction AND the scoring path, but the
scorer holds out each buoy from its own correction so we don't
trivially fit the validation set.

Phase-B framework note
----------------------
The buoy list is duplicated from ``validation/ingest/ndbc.py`` for
v1 because that module's list lives inside a class instance and
ingest is structured around the BaseScraper observation pipeline,
not nowcast inputs. Drift detector test in
``pipeline/tests/test_buoy_correction.py`` keeps the two lists in
sync.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import requests


# ----- Buoy registry ----------------------------------------------------
#
# CA-coast NDBC stations chosen for SST coverage of the bbox. Coords
# from NDBC's activestations.xml (verified at build time). Subset of
# validation/ingest/ndbc.py:BUOYS — the ingest module is the single
# source of truth and a unit test enforces this list stays a subset.

BUOYS: list[dict] = [
    # SoCal Bight / Channel Islands corridor
    {"stn": "46086", "name": "San Clemente Basin",  "lat": 32.499, "lng": -118.034},
    {"stn": "46047", "name": "Tanner Bank",         "lat": 32.418, "lng": -119.535},
    {"stn": "46025", "name": "Santa Monica Basin",  "lat": 33.765, "lng": -119.077},
    # Central coast
    {"stn": "46011", "name": "Santa Maria",         "lat": 34.937, "lng": -120.999},
    {"stn": "46028", "name": "Cape San Martin",     "lat": 35.763, "lng": -121.893},
    {"stn": "46042", "name": "Monterey",            "lat": 36.787, "lng": -122.408},
]


# ----- Tunables ---------------------------------------------------------

# Average buoy readings over the last N hours to dampen diurnal noise
# + capture the "background" SST that an L4 satellite product is
# trying to represent. Tighter windows (~6 h) over-fit to night /
# morning bias; looser windows (>48 h) blur out real fronts.
BUOY_LOOKBACK_HOURS = 24

# Kriging length scale. 60 km matches typical CA mesoscale eddy
# spacing — bigger than the buoy network's mean spacing (~110 km
# per buoy) so the correction surface stays smooth, smaller than
# the 580 km bbox span so it isn't trivially uniform. Re-tune once
# sst_score residuals accumulate.
KRIG_LENGTH_KM = 60.0

# Cap each cell's additive correction. A larger correction means
# either the satellite is wildly off (rare) or a single buoy reading
# is bogus (more common). Hard-clamp to fail gracefully toward MUR.
CORRECTION_MAX_C = 1.5

# Hard sanity bound on a single buoy's residual vs MUR. Anything
# outside this range gets dropped from the anchor set — almost
# always means a sensor issue (NDBC stations occasionally publish
# air temp in the WTMP column on certain firmware bugs).
RESIDUAL_SANITY_BOUND_C = 5.0


# ----- HTTP fetch -------------------------------------------------------

NDBC_REALTIME2_URL = "https://www.ndbc.noaa.gov/data/realtime2/{stn}.txt"
USER_AGENT = (
    "ShoudiDive-SST-correction/1.0 "
    "(+https://shouldidive.com/about/validation; daily nowcast probe)"
)
HTTP_TIMEOUT = 30


# realtime2 columns (mapped 1-indexed for human readability):
#   1  YY  2 MM  3 DD  4 hh  5 mm
#   6  WDIR  7 WSPD  8 GST   9 WVHT  10 DPD  11 APD  12 MWD
#   13 PRES  14 ATMP  15 WTMP  16 DEWP  17 VIS  18 PTDY  19 TIDE
WTMP_COL_INDEX = 14   # 0-indexed in split() output


@dataclass
class BuoyReading:
    """One buoy's contribution to the correction surface."""
    stn: str
    name: str
    lat: float
    lng: float
    wtmp_c: float        # mean water temp over last BUOY_LOOKBACK_HOURS
    n_samples: int       # how many hourly rows contributed
    age_hours: float     # age of the most-recent sample
    raw_samples: list[tuple[datetime, float]] = field(default_factory=list, repr=False)


def fetch_buoy_readings(*, now: Optional[datetime] = None,
                        lookback_hours: int = BUOY_LOOKBACK_HOURS) -> list[BuoyReading]:
    """Pull the last ``lookback_hours`` of WTMP from each buoy.

    Returns one ``BuoyReading`` per buoy that had at least one valid
    sample in the window. Buoys with no recent data are silently
    skipped — the correction surface degrades gracefully to whatever
    anchors are available.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=lookback_hours)
    out: list[BuoyReading] = []

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

        samples: list[tuple[datetime, float]] = []
        latest_age_hours: Optional[float] = None
        for line in r.text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = re.split(r"\s+", line)
            if len(parts) <= WTMP_COL_INDEX:
                continue
            try:
                yy, mm, dd, hh, mn = (int(parts[i]) for i in range(5))
                ts = datetime(yy, mm, dd, hh, mn, tzinfo=timezone.utc)
                if ts < cutoff:
                    continue
                wtmp_str = parts[WTMP_COL_INDEX]
                if wtmp_str == "MM":
                    continue
                wtmp = float(wtmp_str)
                # Defensive — water temperature must be in the
                # liquid-ocean range. Catches the rare WTMP-as-air
                # bug + obvious sensor failure.
                if not (0.0 < wtmp < 35.0):
                    continue
            except (ValueError, IndexError):
                continue
            samples.append((ts, wtmp))
            age = (now - ts).total_seconds() / 3600.0
            if latest_age_hours is None or age < latest_age_hours:
                latest_age_hours = age

        if not samples:
            continue
        mean_wtmp = sum(v for _, v in samples) / len(samples)
        out.append(BuoyReading(
            stn=buoy["stn"],
            name=buoy["name"],
            lat=buoy["lat"],
            lng=buoy["lng"],
            wtmp_c=round(mean_wtmp, 2),
            n_samples=len(samples),
            age_hours=round(latest_age_hours or 0.0, 1),
            raw_samples=samples,
        ))
    return out


# ----- Sampling helpers -------------------------------------------------

def _bilinear_at(grid: np.ndarray,
                 lats: np.ndarray, lngs: np.ndarray,
                 lat: float, lng: float) -> float:
    """Bilinear-sample ``grid`` (shape H×W) at the given (lat, lng).

    lats[0] = lat_max (top row), lats[-1] = lat_min (bottom row).
    lngs[0] = lng_min, lngs[-1] = lng_max. Returns NaN if (lat, lng)
    is outside the grid OR if all four surrounding cells are NaN.
    """
    H, W = grid.shape
    if not (lats[-1] <= lat <= lats[0]):
        return float("nan")
    if not (lngs[0] <= lng <= lngs[-1]):
        return float("nan")
    r = (lats[0] - lat) / (lats[0] - lats[-1]) * (H - 1)
    c = (lng - lngs[0]) / (lngs[-1] - lngs[0]) * (W - 1)
    r0, c0 = int(r), int(c)
    r1, c1 = min(r0 + 1, H - 1), min(c0 + 1, W - 1)
    fr, fc = r - r0, c - c0
    v00 = grid[r0, c0]; v01 = grid[r0, c1]
    v10 = grid[r1, c0]; v11 = grid[r1, c1]
    finite = [v for v in (v00, v01, v10, v11) if np.isfinite(v)]
    if not finite:
        return float("nan")
    if len(finite) < 4:
        # Edge-of-coverage cell — fall back to nearest non-NaN.
        return float(finite[0])
    return float(
        v00 * (1 - fr) * (1 - fc)
        + v01 * (1 - fr) * fc
        + v10 * fr * (1 - fc)
        + v11 * fr * fc
    )


# ----- Correction surface ----------------------------------------------

def kriging_correction_surface(
    *,
    sst_grid_c:  np.ndarray,
    lats:        np.ndarray,
    lngs:        np.ndarray,
    buoys:       list[BuoyReading],
    length_km:   float = KRIG_LENGTH_KM,
    max_c:       float = CORRECTION_MAX_C,
) -> tuple[np.ndarray, list[dict]]:
    """Build a per-cell additive correction (°C) for the SST grid.

    Returns (correction, anchor_info) where:
        correction shape == sst_grid_c.shape, dtype float32
        anchor_info       list of per-buoy dicts (for manifest emission)

    Algorithm: simple Gaussian-kernel inverse-distance weighting of
    per-buoy residuals. Kriging proper would solve kernel parameters
    from the residual variogram — we use a fixed kernel here for
    simplicity and re-tune ``length_km`` from the empirical RMSE in
    sst_score once enough residual signal accumulates. For 6 anchors
    the variogram fit would be under-determined anyway.
    """
    H, W = sst_grid_c.shape
    assert lats.size == H, f"lats size {lats.size} != grid H {H}"
    assert lngs.size == W, f"lngs size {lngs.size} != grid W {W}"

    anchor_info: list[dict] = []
    anchor_points: list[tuple[float, float, float]] = []   # (lat, lng, residual)

    for b in buoys:
        mur_c = _bilinear_at(sst_grid_c, lats, lngs, b.lat, b.lng)
        if not np.isfinite(mur_c):
            anchor_info.append({
                "stn": b.stn, "name": b.name,
                "lat": b.lat, "lng": b.lng,
                "wtmp_c": b.wtmp_c, "mur_c": None,
                "residual_c": None,
                "n_samples": b.n_samples, "age_hours": b.age_hours,
                "skipped": "no MUR coverage at buoy location",
            })
            continue

        residual = b.wtmp_c - mur_c
        if abs(residual) > RESIDUAL_SANITY_BOUND_C:
            anchor_info.append({
                "stn": b.stn, "name": b.name,
                "lat": b.lat, "lng": b.lng,
                "wtmp_c": b.wtmp_c, "mur_c": round(mur_c, 2),
                "residual_c": round(residual, 2),
                "n_samples": b.n_samples, "age_hours": b.age_hours,
                "skipped": f"|residual| > {RESIDUAL_SANITY_BOUND_C} °C — likely sensor issue",
            })
            continue

        anchor_points.append((b.lat, b.lng, residual))
        anchor_info.append({
            "stn": b.stn, "name": b.name,
            "lat": b.lat, "lng": b.lng,
            "wtmp_c": b.wtmp_c, "mur_c": round(mur_c, 2),
            "residual_c": round(residual, 2),
            "n_samples": b.n_samples, "age_hours": b.age_hours,
            "skipped": None,
        })

    correction = np.zeros((H, W), dtype=np.float32)
    if not anchor_points:
        return correction, anchor_info

    # Local equirectangular distance — accurate to ~1% across the bbox
    # at 35 °N, far cheaper than per-cell haversine over a 70×90 grid.
    LAT2D, LNG2D = np.meshgrid(lats, lngs, indexing="ij")
    sum_w  = np.zeros((H, W), dtype=np.float64)
    sum_wr = np.zeros((H, W), dtype=np.float64)
    for (blat, blng, resid) in anchor_points:
        dy_km = (LAT2D - blat) * 111.32
        dx_km = (LNG2D - blng) * 111.32 * math.cos(math.radians(blat))
        d2 = dy_km * dy_km + dx_km * dx_km
        w = np.exp(-d2 / (length_km * length_km))
        sum_w  += w
        sum_wr += w * resid

    with np.errstate(invalid="ignore", divide="ignore"):
        full = np.where(sum_w > 1e-9, sum_wr / sum_w, 0.0)
    correction[:] = np.clip(full, -max_c, max_c).astype(np.float32)
    return correction, anchor_info


# ----- Manifest emission ------------------------------------------------

def correction_summary(anchor_info: list[dict],
                       length_km: float = KRIG_LENGTH_KM) -> dict:
    """Compact JSON-serializable description of the correction state,
    suitable for inclusion in manifest.json. Lets the React/RN clients
    surface "corrected with N buoys" + per-buoy QC info if they want to
    show provenance. Also a hand-off for the validation watchdog —
    a sudden drop in active anchors is itself a finding."""
    active = [a for a in anchor_info if a.get("skipped") is None]
    return {
        "method": "kriging_gaussian",
        "length_km": length_km,
        "n_anchors_total": len(anchor_info),
        "n_anchors_active": len(active),
        "rms_residual_c": (
            round(float(np.sqrt(np.mean([a["residual_c"] ** 2 for a in active]))), 3)
            if active else None
        ),
        "anchors": anchor_info,
    }


# ----- CLI helper -------------------------------------------------------

def main() -> int:
    """Manual smoke test — fetches the buoys + prints residual table.
    Useful when the watchdog opens a "buoy correction degraded"
    Issue and a human wants to inspect the live data."""
    readings = fetch_buoy_readings()
    print(f"Got {len(readings)} buoys with WTMP in last {BUOY_LOOKBACK_HOURS}h")
    for r in readings:
        print(f"  {r.stn:>5} {r.name:<24} {r.lat:6.2f}°N {r.lng:8.2f}°W "
              f"= {r.wtmp_c:5.2f} °C (n={r.n_samples}, age={r.age_hours}h)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
