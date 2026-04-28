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

from color_ramps import encode_color_png

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
        # dims after time and before (lat, lng); for MUR there are none
        "pre_xy_dims": "",
    },
    "chl": {
        "dataset": "nesdisVHNnoaaSNPPnoaa20NRTchlaGapfilledDaily",
        "variable": "chlor_a",
        "range": (0.05, 20.0),
        "scale": "log10",
        "unit": "mg/m^3",
        "stride": 1,
        # VIIRS gap-filled has a single-element altitude dim at index 0
        "pre_xy_dims": "[0]",
    },
}


def erddap_url(cfg: dict, d: date, stride: int) -> str:
    return (
        f"{ERDDAP_BASE}/{cfg['dataset']}.nc"
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


def build_layer(layer: str, cfg: dict, end: date, want: int = 3, max_back: int = 7) -> dict | None:
    """Fetch up to `want` valid days walking back from `end`. Different layers
    publish on different lags, so each layer finds its own latest 3."""
    print(f"[{layer}] looking for {want} day(s) ending {end} (stride={cfg['stride']})")
    stack_rev: list[np.ndarray] = []
    actual_rev: list[date] = []
    for i in range(max_back):
        d = end - timedelta(days=i)
        a = fetch_day(layer, cfg, d, cfg["stride"])
        if a is not None:
            stack_rev.append(a)
            actual_rev.append(d)
            if len(stack_rev) >= want:
                break

    if not stack_rev:
        print(f"[{layer}] no data fetched, skipping layer")
        return None

    stack = list(reversed(stack_rev))
    actual = list(reversed(actual_rev))

    h, w = stack[-1].shape
    composites = {"1d": stack[-1:], "2d": stack[-2:], "3d": stack}
    manifest_layer = {
        "range": list(cfg["range"]),
        "scale": cfg["scale"],
        "unit": cfg["unit"],
        "grid": {"width": w, "height": h},
        "windows": {},
    }
    for win, st in composites.items():
        if not st:
            continue
        c = composite(st)
        out = OUT_DIR / f"{layer}_{win}.png"
        color_out = OUT_DIR / f"{layer}_{win}_color.png"
        encode_png(c, cfg, out)
        encode_color_png(c, layer, color_out)
        manifest_layer["windows"][win] = {
            "url": f"/data/{layer}_{win}.png",
            "mobile_url": f"/data/{layer}_{win}_color.png",
            "dates": [d.isoformat() for d in actual[-len(st):]],
        }
        print(f"  wrote {out.name} + {color_out.name}  ({h}x{w})")
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
    manifest["generated_at"] = (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )
    manifest.setdefault("layers", {}).update(manifest_layers)
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print("wrote manifest.json")


if __name__ == "__main__":
    main()
