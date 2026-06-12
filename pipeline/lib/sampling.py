"""NaN-aware bilinear sampler — for resampling raster data between
the per-fetcher source grids and the higher-resolution viz model grid.

The sampler operates in the bbox lng/lat coordinate system rather than
pixel space. Callers pass:

  * `src_arr` — the source raster as a (src_h, src_w) array. Row 0
    must correspond to the bbox's lat_max (matches the PNG-from-pipeline
    convention used by lib/encode + lib/decode).
  * `lng_grid`, `lat_grid` — target sample points (any shape).
  * `bbox` — dict with `lat_min`, `lat_max`, `lng_min`, `lng_max`.
    The fetcher-side helpers do `pipeline.regions.active_region().bbox`
    once at import time; tests inject a fixed bbox.

Why this lives in lib/ instead of viz_predict/:

bilinear_sample previously lived inline at the top of fetch_visibility.py.
The Stage 6 scope identified two consumers (fetch_visibility.py and a
future generalised spatial sampler) and recommended factoring it out so
the algorithm stays in one place. chl_blend.py has an inline near-twin
that should consume this same helper in a future refactor pass.

NaN-aware behaviour (added 2026-05-21):

The previous version did a plain `v00·(1−tx)(1−ty) + v10·…` blend.
Any NaN corner propagated to the output, so a single-pixel chl
speckle surrounded by NaN cells in the lower-res chl_1d.png made
bilinear_sample return NaN at the higher-res viz grid cell. The
chl_2d/3d fallback (added 2026-05-20) then OVERRODE that NaN with a
smoothed (clear-water) value, and viz predicted "Good" at a cell the
user saw flagged as bloom on the chl LAYER. Caught by direct
inspection of the Baja prod PNGs on 2026-05-21 — 80-ft viz over
chl=14 mg/m³ blooms in south Baja.

Behaviour matches what src/lib/dataSource.js does for frontend
sampling: if all four corners are valid, normal bilinear. If 1-3
are NaN, average the valid corners. If all 4 are NaN, NaN.

Extracted from pipeline/fetch_visibility.py 2026-05-24 as Stage 6c.
"""
from __future__ import annotations

import numpy as np


def bilinear_sample(
    src_arr: np.ndarray,
    src_w: int,
    src_h: int,
    lng_grid: np.ndarray,
    lat_grid: np.ndarray,
    *,
    bbox: dict,
) -> np.ndarray:
    """Sample `src_arr` at `(lng_grid, lat_grid)` with NaN-aware bilinear.

    Parameters
    ----------
    src_arr
        Source raster, shape (src_h, src_w). Row 0 corresponds to
        `bbox["lat_max"]`. NaN cells are treated as missing data.
    src_w, src_h
        Source raster width / height. Passed explicitly because
        callers often pass slices where shape lookups would be
        ambiguous, and for symmetry with the prior signature.
    lng_grid, lat_grid
        Sample point coordinates (any compatible shape).
    bbox
        Geographic bounds of the source raster. Must have keys
        ``lat_min``, ``lat_max``, ``lng_min``, ``lng_max``.

    Returns
    -------
    np.ndarray
        Same shape as `lng_grid` / `lat_grid`. Cells where all 4
        corners are NaN return NaN.
    """
    fx = (lng_grid - bbox["lng_min"]) / (bbox["lng_max"] - bbox["lng_min"]) * (src_w - 1)
    fy = (bbox["lat_max"] - lat_grid) / (bbox["lat_max"] - bbox["lat_min"]) * (src_h - 1)
    fx = np.clip(fx, 0, src_w - 1)
    fy = np.clip(fy, 0, src_h - 1)
    x0 = np.floor(fx).astype(int); x1 = np.minimum(x0 + 1, src_w - 1)
    y0 = np.floor(fy).astype(int); y1 = np.minimum(y0 + 1, src_h - 1)
    tx = fx - x0; ty = fy - y0
    v00 = src_arr[y0, x0]
    v10 = src_arr[y0, x1]
    v01 = src_arr[y1, x0]
    v11 = src_arr[y1, x1]
    # Full-bilinear when all four corners are finite — preserves the
    # smooth gradient response the model was tuned against.
    bilinear = (
        v00 * (1 - tx) * (1 - ty) + v10 * tx * (1 - ty)
      + v01 * (1 - tx) * ty       + v11 * tx * ty
    )
    # Average of valid corners as the NaN-tolerant fallback.
    finite_00 = np.isfinite(v00).astype(np.float64)
    finite_10 = np.isfinite(v10).astype(np.float64)
    finite_01 = np.isfinite(v01).astype(np.float64)
    finite_11 = np.isfinite(v11).astype(np.float64)
    n_valid = finite_00 + finite_10 + finite_01 + finite_11
    with np.errstate(invalid="ignore"):
        v00_safe = np.where(np.isfinite(v00), v00, 0.0)
        v10_safe = np.where(np.isfinite(v10), v10, 0.0)
        v01_safe = np.where(np.isfinite(v01), v01, 0.0)
        v11_safe = np.where(np.isfinite(v11), v11, 0.0)
        avg = (v00_safe + v10_safe + v01_safe + v11_safe) / np.maximum(n_valid, 1.0)
    # All-NaN cells stay NaN; otherwise pick bilinear if all 4 are valid,
    # else the average of valid corners.
    all_finite = (n_valid >= 4.0)
    any_finite = (n_valid >= 1.0)
    return np.where(any_finite, np.where(all_finite, bilinear, avg), np.nan)
