"""PNG decoders matching the encoders in lib/encode.py + dataSource.js.

The web frontend's dataSource.js owns the canonical contract for how
pipeline PNGs encode floating-point fields into 8-bit pixels. The
pipeline's `lib/encode.py` writes those PNGs. Up until Stage 6a, the
pipeline-side decoders (used by fetch_visibility.py + the viz model
to round-trip its own outputs and to read sidecars from other
fetchers) lived inline at the top of fetch_visibility.py.

That created two problems:
  1. The encode-decode contract was split across 3 files (encode.py,
     dataSource.js, fetch_visibility.py). A change to any of those
     three needed coordinated edits.
  2. Any future fetcher that needed to round-trip its own outputs
     had no canonical place to import these from — and chl_blend.py
     had already started growing its own ad-hoc decoders inline.

This module is the decode-side companion to lib/encode.py. Encoders
and decoders share the same `lo`/`hi` range conventions; the test
file (test_lib_decode.py) verifies round-trip exactness for a
representative set of input arrays.

Conventions (must stay in sync with lib/encode.py):
  - Linear scalar fields: 8-bit grayscale, pixel 0 = NaN/missing,
    pixels 1..255 map linearly to (lo, hi).
  - Log10 scalar fields: same as linear but in log10(lo)..log10(hi)
    space (chlorophyll uses this).
  - UV vector fields: RGBA, R = u byte, G = v byte (both lo..hi
    linear), alpha=0 = missing.
  - Wave triples: RGBA, R = Hs (0..12 m), G = peak period (0..25 s),
    B = peak direction (0..360°), alpha=0 = missing.
  - Age sidecars: 8-bit grayscale, pixel 0 = no data (decoded to
    999.0 sentinel — `np.isnan` checks downstream get noisy, and
    999 is well above any realistic age threshold), pixels 1..255
    map to `pixel - 1` days.

Extracted from pipeline/fetch_visibility.py 2026-05-24 as Stage 6a
of the pipeline refactor.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


def decode_linear_png(path: Path, lo: float, hi: float) -> np.ndarray:
    """8-bit grayscale: pixel 0 = NaN, pixels 1..255 linear in [lo, hi]."""
    img = np.array(Image.open(path))  # mode L, shape (h, w)
    out = np.full(img.shape, np.nan, dtype=np.float32)
    valid = img > 0
    out[valid] = lo + ((img[valid].astype(np.float32) - 1) / 254) * (hi - lo)
    return out


def decode_log10_png(path: Path, lo: float, hi: float) -> np.ndarray:
    """8-bit grayscale where 0 = NaN, 1..255 maps to log10 [lo, hi].

    Returned values are in the original (non-log) units — the log10
    is internal to the encoding. Used for chlorophyll where the
    natural range spans 0.05–20 mg/m³ and a linear 0..255 mapping
    would lose precision in the low end where most cells live.
    """
    img = np.array(Image.open(path))
    valid = img > 0
    log_lo = np.log10(lo)
    log_hi = np.log10(hi)
    out = np.full(img.shape, np.nan, dtype=np.float32)
    out[valid] = 10.0 ** (
        log_lo + ((img[valid].astype(np.float32) - 1) / 254) * (log_hi - log_lo)
    )
    return out


def decode_uv_png(path: Path, lo: float, hi: float) -> tuple[np.ndarray, np.ndarray]:
    """RGBA: R = U byte, G = V byte (both lo..hi linear), alpha=0 means NaN.

    Returns (u, v) as a pair of float32 arrays with NaN in cells where
    the alpha channel was 0.
    """
    img = np.array(Image.open(path))  # shape (h, w, 4)
    valid = img[..., 3] > 0
    span = hi - lo
    u = np.full(img.shape[:2], np.nan, dtype=np.float32)
    v = np.full(img.shape[:2], np.nan, dtype=np.float32)
    u[valid] = lo + (img[..., 0][valid].astype(np.float32) / 255) * span
    v[valid] = lo + (img[..., 1][valid].astype(np.float32) / 255) * span
    return u, v


def decode_wave_png(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Wave RGBA: R = height (0..12 m), G = period (0..25 s), B = direction (0..360°).

    Returns (height, period, direction) as float32 arrays with NaN
    where alpha was 0. Direction is degrees true (0 = from north).
    """
    img = np.array(Image.open(path))
    valid = img[..., 3] > 0
    h = np.full(img.shape[:2], np.nan, dtype=np.float32)
    p = np.full(img.shape[:2], np.nan, dtype=np.float32)
    d = np.full(img.shape[:2], np.nan, dtype=np.float32)
    h[valid] = (img[..., 0][valid].astype(np.float32) / 255) * 12.0
    p[valid] = (img[..., 1][valid].astype(np.float32) / 255) * 25.0
    d[valid] = (img[..., 2][valid].astype(np.float32) / 255) * 360.0
    return h, p, d


def decode_age_png(path: Path) -> np.ndarray:
    """Decode an age-days sidecar PNG.

    Convention: pixel value 0 = no data (encoded as 999.0 in the
    output, since `np.isnan` checks are noisy and 999 is well above
    any realistic age threshold). Pixels 1..255 map to age = pixel - 1
    days.

    Returned array has the same orientation as the source PNG —
    row 0 = lat_max — so it can be passed straight to
    `bilinear_sample`.
    """
    img = np.array(Image.open(path))
    out = np.full(img.shape, 999.0, dtype=np.float32)
    valid = img > 0
    out[valid] = img[valid].astype(np.float32) - 1.0
    return out
