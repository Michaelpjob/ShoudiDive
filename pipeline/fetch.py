"""Fetch real ocean data for the CA coast bbox and write composites + manifest.

Pulls the latest 3 days of each layer from NOAA CoastWatch ERDDAP, builds
1/2/3-day per-pixel-mean composites, encodes each as an 8-bit PNG, and
writes a manifest the frontend reads at boot.

Sources (all no-auth, served from the same ERDDAP):
  - sst: GHRSST MUR L4, 1 km, gap-filled
  - chl: VIIRS S-NPP + NOAA-20 NRT, 4 km, gap-filled

PNG encoding: pixel value 0 = no-data, 1..255 = layer's range (linear for
sst, log10 for chl). The manifest carries range, scale, and date list so
the frontend decodes and labels correctly.

Run:  python pipeline/fetch.py
Out:  ca-coast-conditions/public/data/{<layer>_{1d,2d,3d}.png, manifest.json}
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests
import xarray as xr
from PIL import Image

# Single source of truth for range / scale / unit per layer. Defined in
# pipeline/lib/layer_spec.py; both this fetcher (encode side) and the
# frontend's dataSource.js (decode side) look at the same numbers, so
# a drift between the two is impossible.
#
# The merge below layers ENCODER-specific fields (dataset, variable,
# host, stride, pre_xy_dims, fallbacks, emit_age_sidecar) on top of
# the contract-specified fields. Changing a range or scale = edit
# LAYER_SPECS, not this file.
#
# Import handles BOTH invocation styles:
#   * `python pipeline/fetch.py`  → cwd repo root, but sys.path[0] = pipeline/.
#                                    Falls through to the second arm.
#   * `python -m pipeline.fetch`   → sys.path[0] = repo root. First arm wins.
# refresh-data.yml uses the script-style invocation (line 72), so the
# second arm is the path the production cron actually takes. Without this
# fallback, the daily refresh fails at module load with
# `ModuleNotFoundError: No module named 'pipeline'` — caught the first
# time on 2026-05-08 21:09 UTC, after PR #21 landed.
try:
    from pipeline.lib.layer_spec import LAYER_SPECS
except ModuleNotFoundError:
    from lib.layer_spec import LAYER_SPECS

BBOX = dict(lat_min=31.8, lat_max=37.6, lng_min=-124.0, lng_max=-116.8)
ERDDAP_BASE = "https://coastwatch.pfeg.noaa.gov/erddap/griddap"
REQUEST_HEADERS = {
    "User-Agent": "shouldidive-data-pipeline/1.0 (+https://shouldidive.com)",
}

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "public" / "data"
CACHE_DIR = ROOT / "pipeline" / ".cache"


def _layer_config(spec_name: str, encoder_extras: dict) -> dict:
    """Build a LAYERS-dict entry by pulling range/scale/unit from
    pipeline.lib.layer_spec.LAYER_SPECS[spec_name] and layering the
    encoder-specific config on top.

    Failing here at import time is the point — if the registry doesn't
    have the layer, this fetcher cannot encode it correctly anyway.
    """
    spec = LAYER_SPECS[spec_name]
    if spec.range is None:
        raise ValueError(
            f"fetch.py: LayerSpec for {spec_name!r} has no range — cannot "
            f"encode a scalar PNG without one. Either fix the spec or move "
            f"this layer to a fetcher that handles its payload type."
        )
    if spec.scale is None:
        raise ValueError(
            f"fetch.py: LayerSpec for {spec_name!r} has no scale"
        )
    return {
        "range": tuple(spec.range),
        "scale": spec.scale,
        "unit": spec.unit,
        **encoder_extras,
    }


LAYERS: dict[str, dict] = {
    "sst": _layer_config("sst", {
        "dataset": "jplMURSST41",
        "variable": "analysed_sst",
        "stride": 2,
        "history_days": 7,
        "max_back": 10,
        # dims after time and before (lat, lng); for MUR there are none
        "pre_xy_dims": "",
        "fallbacks": [
            {
                # NOAA's canonical CoastWatch ERDDAP has occasionally 403'd
                # GitHub Actions egress for jplMURSST41 while still serving
                # other products. Keep Temp history alive with NOAA's
                # near-real-time Geo-polar blended GHRSST analysis instead
                # of silently dropping back to the legacy 1/2/3-day UI.
                "host": "https://coastwatch.noaa.gov/erddap/griddap",
                "dataset": "noaacwBLENDEDsstDNDaily",
                "variable": "analysed_sst",
                "stride": 1,
                "source_label": "NOAA Geo-polar blended SST",
            }
        ],
    }),
    "chl": _layer_config("chl", {
        # Source moved off the deprecating coastwatch.pfeg.noaa.gov mirror:
        # PFEG is now redirecting (302) to coastwatch.noaa.gov, where the
        # SNPP/N20 NRT gap-filled product lives under a different dataset
        # name (DINEOF gap-fill instead of "Gapfilled"). Same data, same
        # 4 km grid, same dimensions order — only the host + dataset name
        # change. PFEG was returning intermittent 503s + 404s for recent
        # dates (5-2 → 503; 5-1, 4-30 → 404) before this switch; the
        # underlying NRT publication lag (typically 2-3 days) is set by
        # NOAA upstream and is unchanged. Same fix pattern as the Kd_490
        # reroute in c407e1b.
        "host":    "https://coastwatch.noaa.gov/erddap/griddap",
        "dataset": "noaacwNPPN20VIIRSDINEOFDaily",
        "variable": "chlor_a",
        "stride": 1,
        # VIIRS gap-filled has a single-element altitude dim at index 0
        "pre_xy_dims": "[0]",
    }),
    "kd490": _layer_config("kd490", {
        # Diffuse attenuation coefficient at 490 nm — direct light-penetration
        # measure, far closer to "what a diver sees" than chl alone. The model
        # (Phase 2) blends Secchi = 1.7/Kd_490 against the chl-derived path,
        # giving Kd priority weight when fresh.
        #
        # Source: NOAA CoastWatch ERDDAP — DINEOF-gap-filled multi-sensor
        # (S-NPP + NOAA-20 VIIRS + Copernicus S-3A OLCI), 2 km global daily.
        # Same dimensions order as our chl product. We tried the NRT VIIRS-only
        # product (`noaacwNPPVIIRSkd490Daily`, 4 km, ~7 day lag) first; on a
        # typical day under coastal marine layer it has only ~11% non-NaN
        # pixels over the CA bbox, and bilinear-interp through NaN holes
        # collapsed the resampled grid to all-NaN. The DINEOF product is
        # specifically designed to fill those holes via empirical orthogonal
        # functions — covers 100% of pixels at the cost of an extra ~4 days
        # of latency (NRT 7d → SQ-DINEOF 11d).
        "host":    "https://coastwatch.noaa.gov/erddap/griddap",
        "dataset": "noaacwNPPN20S3AkdSCIDINEOF2kmDaily",
        "variable": "kd_490",
        # 2 km native resolution gives ~290×290 pixels over our bbox.
        "stride": 1,
        # Same altitude length-1 axis as VIIRS chl.
        "pre_xy_dims": "[0]",
        # SQ DINEOF product publishes ~11 days behind today; widen age-walk
        # to 14 so a single bad fortnight doesn't blank the layer.
        "max_back": 14,
        # Mandatory for the Phase-2 model: age sidecar is consumed by
        # fetch_visibility.py to gate "today's Kd observation" on age==0.
        "emit_age_sidecar": True,
    }),
}


def erddap_url(cfg: dict, d: date, stride: int) -> str:
    """Build an ERDDAP griddap URL. Layer can override `host` to point at
    a different ERDDAP mirror — e.g. Kd_490 must hit coastwatch.noaa.gov
    directly because the pfeg.noaa.gov mirror 403s GitHub Actions egress
    IPs for that specific dataset (other layers on pfeg.noaa.gov work
    fine; the blocking is dataset-specific, not host-wide)."""
    base = cfg.get("host", ERDDAP_BASE)
    return (
        f"{base}/{cfg['dataset']}.nc"
        f"?{cfg['variable']}"
        f"[({d}T00:00:00Z):1:({d}T23:59:59Z)]"
        f"{cfg.get('pre_xy_dims', '')}"
        f"[({BBOX['lat_min']}):{stride}:({BBOX['lat_max']})]"
        f"[({BBOX['lng_min']}):{stride}:({BBOX['lng_max']})]"
    )


def _source_key(cfg: dict) -> str:
    return str(cfg.get("dataset", "source")).replace("/", "_")


def candidate_configs(cfg: dict) -> list[dict]:
    out = [dict(cfg)]
    for fallback in cfg.get("fallbacks", []) or []:
        merged = dict(cfg)
        merged.update(fallback)
        merged.pop("fallbacks", None)
        out.append(merged)
    return out


def fetch_day(
    layer: str,
    cfg: dict,
    d: date,
    stride: int,
    expected_shape: tuple[int, int] | None = None,
) -> np.ndarray | None:
    """Return a 2D array in the layer's native units, or None on failure."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for i, source_cfg in enumerate(candidate_configs(cfg)):
        source_stride = int(source_cfg.get("stride", stride))
        source_key = _source_key(source_cfg)
        suffix = "" if i == 0 else f" via {source_key}"
        nc_path = CACHE_DIR / f"{layer}_{source_key}_{d.isoformat()}_s{source_stride}.nc"
        if not nc_path.exists():
            url = erddap_url(source_cfg, d, source_stride)
            print(f"  GET {layer} {d}{suffix}", flush=True)
            try:
                r = requests.get(url, timeout=180, headers=REQUEST_HEADERS)
            except requests.RequestException as exc:
                print(f"  {layer} {d}{suffix}: {exc.__class__.__name__} - skipping", flush=True)
                continue
            if r.status_code != 200:
                print(f"  {layer} {d}{suffix}: HTTP {r.status_code} - skipping", flush=True)
                continue
            nc_path.write_bytes(r.content)

        with xr.open_dataset(nc_path) as ds:
            var = ds[source_cfg["variable"]]
            # Some ERDDAP date-range queries return >1 time slice; just take the
            # last (most recent) and drop length-1 axes.
            if "time" in var.dims and var.sizes["time"] > 1:
                var = var.isel(time=-1)
            arr = np.asarray(var.values).squeeze()
            units = (var.attrs.get("units") or "").lower()

        if arr.ndim != 2:
            print(f"  {layer} {d}{suffix}: unexpected shape {arr.shape}", flush=True)
            continue

        # PNG image rows go top->bottom = lat_max->lat_min; ERDDAP returns lat ascending.
        arr = np.flipud(arr)

        # MUR analysed_sst is documented as Kelvin but this ERDDAP serves degree_C.
        # Honour the units attribute either way.
        if layer == "sst" and units in ("k", "kelvin", "degrees_kelvin"):
            arr = arr - 273.15

        if expected_shape is not None and arr.shape != expected_shape:
            print(
                f"  {layer} {d}{suffix}: shape {arr.shape} differs from {expected_shape} - trying next source",
                flush=True,
            )
            continue

        return arr

    return None


def composite(stack: list[np.ndarray]) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN columns are fine
        return np.nanmean(np.stack(stack), axis=0)


def build_age_array(stack: list[np.ndarray], dates: list[date], end: date) -> np.ndarray:
    """For each cell, return the age (in whole days) of the most recent
    valid (non-NaN) value across the stack. Cells with no valid value
    in any frame get 255 (encoded as no-data downstream).

    `stack` and `dates` are chronological (oldest -> newest). We walk
    newest-to-oldest so a cell takes the FRESHEST age it has, not the
    first one we encounter.

    The output orientation matches the input arrays — i.e. it's already
    flipud'd if the inputs went through `fetch_day` (which they do).
    Callers should NOT flip again before saving.
    """
    if not stack or not dates:
        return None
    h, w = stack[-1].shape
    age = np.full((h, w), 255, dtype=np.uint8)
    for arr, d in zip(reversed(stack), reversed(dates)):
        delta = max(0, min(254, (end - d).days))
        # Only fill cells that don't already carry a fresher age (255 = unset).
        fill = np.isfinite(arr) & (age == 255)
        age[fill] = delta
    return age


def encode_age_png(age_arr: np.ndarray, out: Path) -> None:
    """Encode the age array as 8-bit grayscale PNG. Convention:
    pixel 0 = no data, pixel 1..255 = age days (raw value - 1).
    The +1 offset reserves 0 for the no-data sentinel."""
    px = np.where(age_arr == 255, 0, np.minimum(age_arr.astype(np.int16) + 1, 255)).astype(np.uint8)
    Image.fromarray(px, mode="L").save(out, optimize=True)


def encode_png(arr: np.ndarray, cfg: dict, out: Path) -> None:
    lo, hi = cfg["range"]
    if cfg["scale"] == "log10":
        with np.errstate(divide="ignore", invalid="ignore"):
            scaled = (np.log10(arr) - np.log10(lo)) / (np.log10(hi) - np.log10(lo))
    else:
        scaled = (arr - lo) / (hi - lo)
    valid = np.isfinite(scaled)
    px = np.zeros(arr.shape, dtype=np.uint8)
    px[valid] = np.clip(np.round(scaled[valid] * 254 + 1), 1, 255).astype(np.uint8)
    Image.fromarray(px, mode="L").save(out, optimize=True)


def _layer_stats(arr: np.ndarray) -> dict:
    valid = np.isfinite(arr)
    if not valid.any():
        return {
            "mean": None,
            "min": None,
            "max": None,
            "coverage_frac": 0.0,
        }
    vals = arr[valid]
    return {
        "mean": round(float(np.nanmean(vals)), 2),
        "min": round(float(np.nanmin(vals)), 2),
        "max": round(float(np.nanmax(vals)), 2),
        "coverage_frac": round(float(valid.sum() / valid.size), 3),
    }


def build_sst_forecast(
    cfg: dict,
    hist_stack: list[np.ndarray],
    hist_dates: list[date],
    generated_at: str,
    horizon_days: int = 7,
) -> dict | None:
    """Write a beta SST forecast using observed trend persistence.

    This is intentionally conservative: day 0 is the latest observed SST,
    later days carry only a capped recent-trend anomaly that decays toward
    persistence. It restores the forward-looking temperature UI without
    claiming full ocean-model skill.
    """
    if not hist_stack or not hist_dates:
        return None

    latest = hist_stack[-1]
    latest_date = hist_dates[-1]
    if len(hist_stack) >= 2:
        prev = hist_stack[-2]
        date_delta = max(1, (hist_dates[-1] - hist_dates[-2]).days)
        with np.errstate(invalid="ignore", divide="ignore"):
            trend = (latest - prev) / date_delta
        trend = np.where(np.isfinite(trend), trend, 0.0)
    else:
        trend = np.zeros_like(latest)

    # Coastal SST day-to-day skill drops fast. Cap extreme gradients so a
    # single bad satellite edge does not turn into a runaway forecast.
    trend = np.clip(trend, -0.35, 0.35)

    out_dir = OUT_DIR / "sst5d"
    out_dir.mkdir(parents=True, exist_ok=True)
    days = []
    for lead in range(horizon_days):
        decay = float(np.exp(-lead / 3.0))
        arr = latest + trend * lead * decay
        arr = np.where(np.isfinite(latest), arr, np.nan)
        out = out_dir / f"f{lead}_sst.png"
        encode_png(arr, cfg, out)
        stats = _layer_stats(arr)
        confidence = "high" if lead <= 1 else "medium" if lead <= 3 else "low"
        days.append({
            "slot": f"f{lead}",
            "day": lead,
            "date": (latest_date + timedelta(days=lead)).isoformat(),
            "url": f"/data/sst5d/f{lead}_sst.png",
            "mean": stats["mean"],
            "min": stats["min"],
            "max": stats["max"],
            "coverage_frac": stats["coverage_frac"],
            "confidence": confidence,
            "forecast": lead > 0,
            "observed_anchor": lead == 0,
        })
        print(f"  wrote sst5d/f{lead}_sst.png  ({latest.shape[0]}x{latest.shape[1]})")

    summary = {
        "generated_at": generated_at,
        "valid_at": latest_date.isoformat(),
        "tz": "UTC",
        "range": list(cfg["range"]),
        "scale": cfg["scale"],
        "unit": cfg["unit"],
        "grid": {"width": latest.shape[1], "height": latest.shape[0]},
        "horizon_days": horizon_days,
        "beta": True,
        "method": "observed-trend persistence with 3-day decay",
        "default_slot": "f0",
        "latest_slot": "f0",
        "days": days,
    }
    (OUT_DIR / "sst5d" / "summary.json").write_text(json.dumps(summary, indent=2))
    print("  wrote sst5d/summary.json")
    return summary


# ----- Phase B + D — buoy + nearshore corrections ----------------------
#
# These run BEFORE build_sst_forecast above, mutating the stack in
# place so the forecast (and the rolling 1d/2d/3d composites) inherit
# the buoy-anchored correction. They're additive: each returns a small
# manifest block describing what got applied, both null-safe.

def _apply_sst_buoy_correction(*, stack: list, grid_h: int, grid_w: int) -> dict | None:
    """Compute the buoy-anchored correction surface for today and apply
    it in-place to every grid in ``stack``.

    Returns the JSON-serializable summary block destined for
    manifest.json's ``layers.sst.buoy_correction``, or None if the
    fetch / correction failed for any reason. Failure is silent in the
    sense that the layer still ships — just without the buoy correction.
    Log lines are loud enough that the next ``check_published.py``
    health-check run notices a missing ``buoy_correction`` block.
    """
    try:
        # Local import — keep the rest of fetch.py able to run on hosts
        # that don't have ``pipeline.sst_buoy_correction`` available
        # (mostly: editor static-analysis pre-commit, where requests is
        # already a dep but we don't want to make this module a hard
        # gate for those flows).
        from pipeline.sst_buoy_correction import (
            BUOYS as _BUOYS,
            fetch_buoy_readings,
            kriging_correction_surface,
            correction_summary,
        )
    except ImportError as exc:
        print(f"[sst] buoy correction unavailable: {exc}")
        return None

    print(f"[sst] fetching buoy readings (last 24h, {len(_BUOYS)} buoys)…")
    try:
        readings = fetch_buoy_readings()
    except Exception as exc:
        print(f"[sst] buoy fetch failed: {exc}")
        return None
    print(f"[sst] {len(readings)} of {len(_BUOYS)} buoys returned valid data")

    # The bbox is sampled top→bot for lats (lat_max → lat_min) and
    # left→right for lngs (lng_min → lng_max), matching the PNG row
    # convention build_layer enforces below.
    lats = np.linspace(BBOX["lat_max"], BBOX["lat_min"], grid_h)
    lngs = np.linspace(BBOX["lng_min"], BBOX["lng_max"], grid_w)

    correction, anchor_info = kriging_correction_surface(
        sst_grid_c=stack[-1].astype(np.float32),
        lats=lats, lngs=lngs,
        buoys=readings,
    )
    n_active = sum(1 for a in anchor_info if a.get("skipped") is None)
    if n_active == 0:
        print("[sst] no usable buoy anchors — leaving SST uncorrected")
        # Still emit a summary block so health-check notices the gap.
        return correction_summary(anchor_info)

    # Apply the same correction surface to every grid in the stack.
    # See module docstring for the rationale (quasi-stationary spatial
    # bias). NaN cells stay NaN — addition with finite is fine.
    for i, arr in enumerate(stack):
        # Per-cell add; unaffected by NaNs because the correction is
        # always finite (zeros where Kriging said it has no idea).
        stack[i] = arr + correction

    summary = correction_summary(anchor_info)
    rms = summary.get("rms_residual_c")
    print(f"[sst] applied buoy correction — {n_active} active anchors, "
          f"RMS residual = {rms}°C, max |correction| = "
          f"{float(np.max(np.abs(correction))):.2f}°C")
    return summary


def _apply_sst_nearshore_correction(*, stack: list, grid_h: int, grid_w: int) -> dict | None:
    """Phase D — apply the bathy-coupled nearshore corrections (upwelling
    cooling at headlands, with solar/tidal scaffolded). Gated by the
    APPLY_NEARSHORE_CORRECTIONS flag in ``sst_predict.config``.

    Same fault-tolerance contract as the buoy correction: any failure
    returns None and leaves ``stack`` untouched. Health-check sees the
    missing manifest block.
    """
    try:
        # Local imports — same justification as the buoy correction's
        # local import. Keeps fetch.py runnable on hosts without the
        # full sst_predict toolchain.
        from sst_predict import config as sst_config
        from sst_predict.nearshore import (
            compute_all_corrections,
            correction_summary,
        )
    except ImportError as exc:
        print(f"[sst] nearshore correction unavailable: {exc}")
        return None

    if not getattr(sst_config, "APPLY_NEARSHORE_CORRECTIONS", False):
        print("[sst] nearshore correction disabled by config flag")
        return None

    print("[sst] computing nearshore corrections (upwelling + scaffolds)…")
    try:
        result = compute_all_corrections(target_h=grid_h, target_w=grid_w)
    except Exception as exc:
        print(f"[sst] nearshore correction failed: {exc}")
        return None

    layers = result.get("layers", [])
    if not layers:
        print("[sst] no nearshore terms active (inputs not published yet)")
        return correction_summary([])

    total = result["total_delta_c"]
    for i, arr in enumerate(stack):
        stack[i] = arr + total

    print(f"[sst] applied nearshore correction — {len(layers)} term(s) "
          f"active, max |delta| = {float(np.max(np.abs(total))):.2f}°C")
    return correction_summary(layers)


def build_layer(layer: str, cfg: dict, end: date, want: int = 3, max_back: int = 7) -> dict | None:
    """Fetch up to `want` valid days walking back from `end`. Different layers
    publish on different lags, so each layer finds its own latest 3.
    Per-layer override via cfg["max_back"] for products with longer NRT lag
    (Kd_490 commonly publishes 5-7 days behind today)."""
    max_back = int(cfg.get("max_back", max_back))
    history_days = int(cfg.get("history_days", 0) or 0)
    want_fetch = max(want, history_days)
    print(f"[{layer}] looking for {want_fetch} day(s) ending {end} (stride={cfg['stride']}, max_back={max_back})")
    stack_rev: list[np.ndarray] = []
    actual_rev: list[date] = []
    for i in range(max_back):
        d = end - timedelta(days=i)
        expected_shape = stack_rev[0].shape if stack_rev else None
        a = fetch_day(layer, cfg, d, cfg["stride"], expected_shape=expected_shape)
        if a is not None:
            if stack_rev and a.shape != stack_rev[0].shape:
                print(f"  {layer} {d}: shape {a.shape} differs from {stack_rev[0].shape} - skipping", flush=True)
                continue
            stack_rev.append(a)
            actual_rev.append(d)
            if len(stack_rev) >= want_fetch:
                break

    if not stack_rev:
        print(f"[{layer}] no data fetched, skipping layer")
        return None

    stack = list(reversed(stack_rev))
    actual = list(reversed(actual_rev))

    h, w = stack[-1].shape

    # Phase B — buoy-anchored correction (SST only).
    #
    # The full bbox has 6 NDBC water-temp buoys reporting hourly. Their
    # 24-h mean residual against today's MUR is a direct measurement of
    # the satellite's local bias. We krieg those anchors into a smooth
    # correction surface and apply it to every grid in the stack
    # (today + each historical day). The buoy snapshot is "today's"
    # correction applied uniformly — accepted approximation since the
    # spatial bias pattern is quasi-stationary on the day-over-day
    # scale. See ``pipeline/sst_buoy_correction.py`` for the full
    # rationale + per-anchor sanity gates.
    buoy_correction_summary = None
    nearshore_correction_summary = None
    if layer == "sst":
        buoy_correction_summary = _apply_sst_buoy_correction(
            stack=stack,
            grid_h=h, grid_w=w,
        )
        # Phase D — bathy-coupled nearshore enhancement. Applied AFTER
        # the buoy correction so that the resulting field is "buoy-
        # anchored mean + microclimate adjustments". Both corrections
        # add small (sub-1.5 °C) terms; combined cap is enforced by
        # each module's own clamp.
        nearshore_correction_summary = _apply_sst_nearshore_correction(
            stack=stack,
            grid_h=h, grid_w=w,
        )

    composites = {
        "1d": stack[-1:],
        "2d": stack[-min(2, len(stack)):],
        "3d": stack[-min(3, len(stack)):],
    }
    manifest_layer = {
        "range": list(cfg["range"]),
        "scale": cfg["scale"],
        "unit": cfg["unit"],
        "grid": {"width": w, "height": h},
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "windows": {},
    }
    if buoy_correction_summary is not None:
        manifest_layer["buoy_correction"] = buoy_correction_summary
    if nearshore_correction_summary is not None:
        manifest_layer["nearshore_correction"] = nearshore_correction_summary
    for win, st in composites.items():
        if not st:
            continue
        c = composite(st)
        out = OUT_DIR / f"{layer}_{win}.png"
        encode_png(c, cfg, out)
        manifest_layer["windows"][win] = {
            "url": f"/data/{layer}_{win}.png",
            "dates": [d.isoformat() for d in actual[-len(st):]],
        }
        print(f"  wrote {out.name}  ({h}x{w})")

    if history_days:
        history_dir = OUT_DIR / layer / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        hist_stack = stack[-history_days:]
        hist_dates = actual[-history_days:]
        hist_days = []
        for idx, (arr, d) in enumerate(zip(hist_stack, hist_dates)):
            offset = idx - (len(hist_stack) - 1)
            slot = "d0" if offset == 0 else f"d{offset}"
            out = history_dir / f"{slot}.png"
            encode_png(arr, cfg, out)
            stats = _layer_stats(arr)
            hist_days.append({
                "slot": slot,
                "offset": offset,
                "date": d.isoformat(),
                "url": f"/data/{layer}/history/{slot}.png",
                "mean": stats["mean"],
                "min": stats["min"],
                "max": stats["max"],
                "coverage_frac": stats["coverage_frac"],
            })
            print(f"  wrote {layer}/history/{slot}.png  ({h}x{w})")
        manifest_layer["history_summary_url"] = f"/data/{layer}/summary.json"

        latest = hist_days[-1] if hist_days else None
        prev = hist_days[-2] if len(hist_days) >= 2 else None
        trend = None
        if latest and prev and latest["mean"] is not None and prev["mean"] is not None:
            trend = {
                "delta_c": round(latest["mean"] - prev["mean"], 2),
                "period": "1d",
            }
        summary = {
            "generated_at": manifest_layer["generated_at"],
            "tz": "UTC",
            "range": list(cfg["range"]),
            "scale": cfg["scale"],
            "unit": cfg["unit"],
            "grid": {"width": w, "height": h},
            "days": hist_days,
            "latest_slot": latest["slot"] if latest else None,
            "trend": trend,
        }
        (OUT_DIR / layer / "summary.json").write_text(json.dumps(summary, indent=2))
        print(f"  wrote {layer}/summary.json")
        if layer == "sst":
            forecast_summary = build_sst_forecast(
                cfg,
                hist_stack,
                hist_dates,
                manifest_layer["generated_at"],
            )
            if forecast_summary:
                manifest_layer["forecast_summary_url"] = "/data/sst5d/summary.json"

    # Per-cell freshness sidecar.
    #
    # The visibility model (`viz_predict`) reads observation layers
    # (chl, kd490) as today's-observation channels and applies
    # persistence-with-decay as the obs ages. Without the sidecar the
    # downstream `fetch_visibility.py` hardcodes age=0 and the model
    # treats stale obs as fresh — see `pipeline/TODO.md` PR1 for the
    # full diagnosis. SST is gap-filled MUR L4 (always "fresh" by
    # design) and doesn't need a sidecar.
    #
    # Layer-driven via cfg["emit_age_sidecar"]; chl-family is grandfathered
    # by the prefix check so we don't have to set the flag in two places.
    if cfg.get("emit_age_sidecar") or layer.startswith("chl"):
        age_arr = build_age_array(stack, actual, end)
        if age_arr is not None:
            age_out = OUT_DIR / f"{layer}_1d_age_days.png"
            encode_age_png(age_arr, age_out)
            valid = age_arr < 255
            stale = valid & (age_arr > 0)
            n_total = int(valid.sum())
            n_stale = int(stale.sum())
            mean_age = float(age_arr[valid].mean()) if n_total else 0.0
            print(
                f"  wrote {age_out.name}  ({h}x{w}) "
                f"— {n_stale}/{n_total} cells stale, mean age {mean_age:.2f} days"
            )
            if "1d" in manifest_layer["windows"]:
                manifest_layer["windows"]["1d"]["age_days_url"] = (
                    f"/data/{layer}_1d_age_days.png"
                )

    return manifest_layer


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--end-date",
        type=date.fromisoformat,
        default=None,
        help="Last day to include (default: yesterday UTC).",
    )
    p.add_argument(
        "--layer",
        default="all",
        choices=["all", *LAYERS.keys()],
        help="Which layer to fetch (default: all).",
    )
    args = p.parse_args()

    end = args.end_date or datetime.now(timezone.utc).date()
    selected = LAYERS.keys() if args.layer == "all" else [args.layer]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest_layers: dict[str, dict] = {}
    for layer in selected:
        if layer == "chl":
            # Multi-source blender — see pipeline/chl_blend.py for the full
            # source roster + per-cell freshest-wins algorithm. Falls back
            # gracefully to NOAA-only when EARTHDATA_TOKEN is unset.
            from chl_blend import build_blended_chl  # local import — avoids
                                                     # importing xarray etc.
                                                     # when --layer != chl
            out = build_blended_chl(end)
        else:
            out = build_layer(layer, LAYERS[layer], end)
        if out is not None:
            manifest_layers[layer] = out

    if not manifest_layers:
        print("Nothing fetched. Exiting.", file=sys.stderr)
        sys.exit(1)

    # Merge into existing manifest so layers we didn't touch (e.g. wind) survive.
    manifest_path = OUT_DIR / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
    else:
        manifest = {
            "bbox": [BBOX["lng_min"], BBOX["lat_min"], BBOX["lng_max"], BBOX["lat_max"]],
            "layers": {},
        }
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if args.layer == "all":
        manifest["generated_at"] = generated_at
    else:
        manifest.setdefault("partial_generated_at", {})[args.layer] = generated_at
    manifest.setdefault("layers", {}).update(manifest_layers)
    if "sst" in manifest_layers:
        sst = manifest_layers["sst"]
        manifest["layers"]["sst7d"] = {
            "summary_url": sst.get("history_summary_url", "/data/sst/summary.json"),
            "grid": sst.get("grid"),
            "range": sst.get("range"),
            "scale": sst.get("scale"),
            "unit": sst.get("unit"),
            "generated_at": sst.get("generated_at"),
            "tz": "UTC",
        }
        if sst.get("forecast_summary_url"):
            manifest["layers"]["sst5d"] = {
                "summary_url": sst.get("forecast_summary_url"),
                "grid": sst.get("grid"),
                "range": sst.get("range"),
                "scale": sst.get("scale"),
                "unit": sst.get("unit"),
                "generated_at": sst.get("generated_at"),
                "tz": "UTC",
                "beta": True,
                "method": "observed-trend persistence",
            }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print("wrote manifest.json")


if __name__ == "__main__":
    main()
