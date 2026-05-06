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

BBOX = dict(lat_min=31.8, lat_max=37.6, lng_min=-124.0, lng_max=-116.8)
ERDDAP_BASE = "https://coastwatch.pfeg.noaa.gov/erddap/griddap"

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "public" / "data"
CACHE_DIR = ROOT / "pipeline" / ".cache"

LAYERS: dict[str, dict] = {
    "sst": {
        "dataset": "jplMURSST41",
        "variable": "analysed_sst",
        "range": (9.0, 25.0),
        "scale": "linear",
        "unit": "degC",
        "stride": 2,
        "history_days": 7,
        "max_back": 10,
        # dims after time and before (lat, lng); for MUR there are none
        "pre_xy_dims": "",
    },
    "chl": {
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
        "range": (0.05, 20.0),
        "scale": "log10",
        "unit": "mg/m^3",
        "stride": 1,
        # VIIRS gap-filled has a single-element altitude dim at index 0
        "pre_xy_dims": "[0]",
    },
    "kd490": {
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
        # Coastal CA: ~0.04 (clear offshore SoCal Bight ~40 m Secchi) to ~3
        # (turbid river plumes ~0.6 m Secchi). 0.02–10 gives one bit of
        # headroom on each end; log10 keeps quantization even across 500x.
        "range": (0.02, 10.0),
        "scale": "log10",
        "unit": "m^-1",
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
    },
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


def fetch_day(layer: str, cfg: dict, d: date, stride: int) -> np.ndarray | None:
    """Return a 2D array in the layer's native units, or None on failure."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    nc_path = CACHE_DIR / f"{layer}_{d.isoformat()}_s{stride}.nc"
    if not nc_path.exists():
        url = erddap_url(cfg, d, stride)
        print(f"  GET {layer} {d}", flush=True)
        r = requests.get(url, timeout=180)
        if r.status_code != 200:
            # Cache a marker so we don't keep re-fetching a known-missing day.
            print(f"  {layer} {d}: HTTP {r.status_code} - skipping", flush=True)
            return None
        nc_path.write_bytes(r.content)

    with xr.open_dataset(nc_path) as ds:
        var = ds[cfg["variable"]]
        # Some ERDDAP date-range queries return >1 time slice; just take the
        # last (most recent) and drop length-1 axes.
        if "time" in var.dims and var.sizes["time"] > 1:
            var = var.isel(time=-1)
        arr = np.asarray(var.values).squeeze()
        units = (var.attrs.get("units") or "").lower()

    if arr.ndim != 2:
        print(f"  {layer} {d}: unexpected shape {arr.shape}", flush=True)
        return None

    # PNG image rows go top->bottom = lat_max->lat_min; ERDDAP returns lat ascending.
    arr = np.flipud(arr)

    # MUR analysed_sst is documented as Kelvin but this ERDDAP serves degree_C.
    # Honour the units attribute either way.
    if layer == "sst" and units in ("k", "kelvin", "degrees_kelvin"):
        arr = arr - 273.15

    return arr


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
        a = fetch_day(layer, cfg, d, cfg["stride"])
        if a is not None:
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
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print("wrote manifest.json")


if __name__ == "__main__":
    main()
