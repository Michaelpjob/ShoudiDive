"""5-day SST forecast — persistence + climatology decay.

V1 implementation. Reads today's blended SST nowcast (already corrected
by the buoy + nearshore steps in ``fetch.py``), reads the long-term
SST climatology, and produces a 7-day forecast that decays today's
anomaly toward climatology with a per-zone time constant.

  SST_anomaly(t=0)   = SST_now − climatology(today_DOY)
  SST_forecast(t+τ)  = climatology(today_DOY+τ) + anomaly(0) · exp(-τ/τ_zone)

Per-zone τ is in ``sst_predict.config.PERSISTENCE_TAU_DAYS`` —
nearshore zones have shorter τ (upwelling events flip anomalies in
days), offshore zones have longer τ (~weeks).

This is the simplest skill-positive forecast we can ship: at lead 0
it's exactly today's nowcast; at lead 7 it's exactly climatology;
between those, it's a linear blend in anomaly space. RTOFS / WCOFS
ocean models add measurable skill at days 2-5 — those land in
Phase E2 once the residual archive can validate the gain.

Outputs (mirror ``fetch_wind_5day``'s file layout for client parity):
  public/data/sst5d/d{0..6}.png       per-day SST PNG (linear °C)
  public/data/sst5d/summary.json      per-day stats + bbox metadata
  manifest.layers.sst5d               summary_url + range/grid/unit

Run:  python pipeline/fetch_sst_5day.py
"""
from __future__ import annotations

import json
import math
import sys
import warnings
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DATA = ROOT / "public" / "data"
SST5D_DIR   = PUBLIC_DATA / "sst5d"
MANIFEST_PATH = PUBLIC_DATA / "manifest.json"

# Make sst_predict importable without an explicit setup.
PIPE = Path(__file__).resolve().parent
if str(PIPE) not in sys.path:
    sys.path.insert(0, str(PIPE))

from sst_predict.config import (   # noqa: E402
    PERSISTENCE_TAU_DAYS,
    SIGMA_SST_BY_LEAD,
    LAT_LABELS,
    DIST_LABELS,
    SST_RANGE_C,
    SST_SCALE,
    SST_UNIT_C,
)


HORIZON_DAYS = 7    # match fetch_wind_5day
DAY_LABELS_REL = ["Today", "+1", "+2", "+3", "+4", "+5", "+6"]
CONFIDENCE_BY_DAY = ["high", "high", "medium", "medium", "low", "low", "low"]

# Bbox + grid (matches fetch.py exactly).
BBOX = dict(lat_min=31.8, lat_max=37.6, lng_min=-124.0, lng_max=-116.8)
GRID_H = 291
GRID_W = 361


# ---- Decoders ----------------------------------------------------------

def _decode_png_to_celsius(path: Path) -> np.ndarray | None:
    """Read a published SST PNG (linear 0..255 across SST_RANGE_C) into
    a float32 grid in °C. NaN cells (pixel value 0 = no-data) come
    out NaN."""
    if not path.exists():
        return None
    try:
        img = Image.open(path).convert("L")
    except OSError:
        return None
    raw = np.asarray(img, dtype=np.uint8)
    out = np.full(raw.shape, np.nan, dtype=np.float32)
    valid = raw > 0
    if valid.any():
        lo, hi = SST_RANGE_C
        scaled = (raw[valid].astype(np.float32) - 1) / 254.0
        out[valid] = lo + scaled * (hi - lo)
    return out


def _encode_celsius_to_png(arr: np.ndarray, path: Path) -> None:
    """Inverse of _decode_png_to_celsius — write the °C grid into the
    same 8-bit linear encoding the rest of the pipeline uses."""
    lo, hi = SST_RANGE_C
    out = np.zeros(arr.shape, dtype=np.uint8)
    valid = np.isfinite(arr)
    if valid.any():
        scaled = np.clip((arr[valid] - lo) / (hi - lo), 0.0, 1.0)
        out[valid] = np.clip((scaled * 254.0) + 1, 1, 255).astype(np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(out, mode="L").save(path, optimize=True)


# ---- Zone classifier (compact reimplementation) ------------------------
#
# We avoid importing ``viz_predict.zones`` to keep this script's deps
# minimal. The classifier here mirrors zones.py's lat-band × dist-to-
# shore logic but uses a depth proxy instead of full geojson distance.

LAT_ZONE_BOUNDS = {
    "central":    (34.45, 90.0),
    "transition": (33.70, 34.45),
    "bight":      (-90.0, 33.70),
}


def _zone_for_pixel(lat: float, depth_m: float) -> str:
    """Same convention sst_predict.config uses."""
    lat_label = "bight"
    for label, (lo, hi) in LAT_ZONE_BOUNDS.items():
        if lo <= lat < hi:
            lat_label = label
            break
    # Depth proxy for distance class (matches viz_predict's NEARSHORE
    # threshold of ~5 km == ~30 m typical CA shelf).
    if depth_m < 30:
        dist_label = "nearshore"
    elif depth_m < 1000:
        dist_label = "islands"
    else:
        dist_label = "offshore"
    return f"{lat_label}_{dist_label}"


def _zone_grid(lats: np.ndarray, depth_m: np.ndarray) -> np.ndarray:
    """Return an HxW array of zone-id strings for τ lookup."""
    H, W = depth_m.shape
    zone = np.empty((H, W), dtype="<U24")
    for i in range(H):
        for j in range(W):
            zone[i, j] = _zone_for_pixel(float(lats[i]), float(depth_m[i, j]))
    return zone


def _tau_grid(zones: np.ndarray) -> np.ndarray:
    """Map each cell's zone to its τ (days), with a sensible default
    for unknown zones."""
    out = np.full(zones.shape, 14.0, dtype=np.float32)
    for z, tau in PERSISTENCE_TAU_DAYS.items():
        out[zones == z] = float(tau)
    return out


# ---- Forecast core -----------------------------------------------------

def persistence_decay_forecast(
    *,
    sst_now_c:    np.ndarray,
    sst_climo_c:  np.ndarray,
    tau_days:     np.ndarray,
    horizon_days: int = HORIZON_DAYS,
) -> np.ndarray:
    """Return (horizon_days, H, W) forecast in °C.

    Day 0 = exactly the nowcast. Day H-1 = exactly climatology.
    Smooth exponential decay between."""
    anomaly = sst_now_c - sst_climo_c
    out = np.full((horizon_days, *sst_now_c.shape), np.nan, dtype=np.float32)
    for d in range(horizon_days):
        decay = np.exp(-d / tau_days)
        out[d] = sst_climo_c + anomaly * decay
    return out


def _stats_for_grid(arr: np.ndarray) -> dict:
    valid = np.isfinite(arr)
    if not valid.any():
        return {"mean": None, "min": None, "max": None, "coverage_frac": 0.0}
    vals = arr[valid]
    return {
        "mean": round(float(np.nanmean(vals)), 2),
        "min":  round(float(np.nanmin(vals)), 2),
        "max":  round(float(np.nanmax(vals)), 2),
        "coverage_frac": round(float(valid.sum() / valid.size), 3),
    }


# ---- Main --------------------------------------------------------------

def main() -> int:
    print("[sst5d] starting persistence-decay forecast")

    sst_now = _decode_png_to_celsius(PUBLIC_DATA / "sst_1d.png")
    if sst_now is None:
        print("[sst5d] sst_1d.png not found — skipping (run fetch.py first)")
        return 0

    sst_climo = _decode_png_to_celsius(PUBLIC_DATA / "sst_climo.png")
    if sst_climo is None:
        # Without climatology the forecast collapses to "today + decay
        # toward today" which is just persistence — useful at short
        # leads but hand-wavy at long leads. Surface the limitation.
        print("[sst5d] sst_climo.png not found — using degenerate persistence "
              "(forecast == nowcast at every lead)")
        sst_climo = sst_now.copy()

    # Resample climatology to nowcast grid if shapes differ (different
    # ERDDAP datasets sometimes deliver slightly different resolutions).
    if sst_climo.shape != sst_now.shape:
        scale_min, scale_max = SST_RANGE_C
        u8 = np.zeros(sst_climo.shape, dtype=np.uint8)
        valid = np.isfinite(sst_climo)
        if valid.any():
            u8[valid] = np.clip(
                ((sst_climo[valid] - scale_min) / (scale_max - scale_min) * 254 + 1),
                1, 255).astype(np.uint8)
        img = Image.fromarray(u8, mode="L").resize(
            (sst_now.shape[1], sst_now.shape[0]), Image.BILINEAR)
        re = np.asarray(img, dtype=np.uint8)
        sst_climo = np.full(re.shape, np.nan, dtype=np.float32)
        ok = re > 0
        if ok.any():
            sst_climo[ok] = scale_min + (re[ok].astype(np.float32) - 1) / 254.0 * (scale_max - scale_min)

    H, W = sst_now.shape
    print(f"[sst5d] grid {H}x{W}, today: mean={np.nanmean(sst_now):.2f}°C "
          f"climo: mean={np.nanmean(sst_climo):.2f}°C")

    # Zone-by-zone τ. Bathymetry feeds the dist class via depth proxy
    # (cheaper than computing geojson distance).
    bathy_path = PUBLIC_DATA / "bathy.png"
    if bathy_path.exists():
        depth = (np.asarray(Image.open(bathy_path).convert("L"), dtype=np.float32)
                 / 255.0 * 6000.0)
        if depth.shape != (H, W):
            depth_img = Image.fromarray(
                ((np.asarray(Image.open(bathy_path).convert("L")))).astype(np.uint8))
            depth_img = depth_img.resize((W, H), Image.BILINEAR)
            depth = np.asarray(depth_img, dtype=np.float32) / 255.0 * 6000.0
    else:
        # Fall through to a bbox-uniform 200 m depth — gives every cell
        # the "transition_islands" τ. Forecast still works, just doesn't
        # benefit from the per-zone time-constant tuning.
        depth = np.full((H, W), 200.0, dtype=np.float32)

    lats = np.linspace(BBOX["lat_max"], BBOX["lat_min"], H)
    zones = _zone_grid(lats, depth)
    tau   = _tau_grid(zones)

    forecast = persistence_decay_forecast(
        sst_now_c=sst_now,
        sst_climo_c=sst_climo,
        tau_days=tau,
        horizon_days=HORIZON_DAYS,
    )

    # Write per-day PNGs + a summary.json mirroring fetch_wind_5day.
    SST5D_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date()
    days = []
    for d in range(HORIZON_DAYS):
        dpath = SST5D_DIR / f"d{d}.png"
        _encode_celsius_to_png(forecast[d], dpath)
        st = _stats_for_grid(forecast[d])
        anom = forecast[d] - sst_climo
        anom_mean = (
            round(float(np.nanmean(anom)), 2)
            if np.any(np.isfinite(anom)) else None
        )
        days.append({
            "day":         DAY_LABELS_REL[d],
            "offset":      d,
            "date":        (today + timedelta(days=d)).isoformat(),
            "url":         f"/data/sst5d/d{d}.png",
            "confidence":  CONFIDENCE_BY_DAY[d],
            "mean_c":      st["mean"],
            "anom_c":      anom_mean,
            "min_c":       st["min"],
            "max_c":       st["max"],
            "coverage_frac": st["coverage_frac"],
        })
        print(f"  d{d} ({DAY_LABELS_REL[d]:>5}): mean={st['mean']}°C "
              f"anom={anom_mean}°C conf={CONFIDENCE_BY_DAY[d]}")

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")
                                .replace("+00:00", "Z"),
        "horizon_days": HORIZON_DAYS,
        "method":       "persistence_decay",
        "tz":           "UTC",
        "range_c":      list(SST_RANGE_C),
        "scale":        SST_SCALE,
        "unit":         SST_UNIT_C,
        "grid":         {"width": W, "height": H},
        "days":         days,
    }
    (SST5D_DIR / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"[sst5d] wrote {SST5D_DIR}/summary.json with {HORIZON_DAYS} days")

    # Patch manifest with the sst5d entry. Keeps existing layers + only
    # adds (or overwrites) the sst5d block. Same minimal-merge pattern
    # fetch_wind_5day uses to coexist with fetch.py's manifest writes.
    manifest = {}
    if MANIFEST_PATH.exists():
        try:
            manifest = json.loads(MANIFEST_PATH.read_text())
        except json.JSONDecodeError:
            manifest = {}
    manifest.setdefault("layers", {})
    manifest["layers"]["sst5d"] = {
        "summary_url":  "/data/sst5d/summary.json",
        "horizon_days": HORIZON_DAYS,
        "range_c":      list(SST_RANGE_C),
        "unit":         SST_UNIT_C,
        "grid":         {"width": W, "height": H},
        "method":       "persistence_decay",
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))
    print(f"[sst5d] manifest entry written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
