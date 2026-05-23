"""Shared PNG encoders for the data pipeline.

Stage 6 refactor — consolidates the linear/log10/age-sidecar PNG
encoders that fetch.py, fetch_climatology.py, fetch_wind.py, and
others each spell out by hand. The output is byte-identical to the
prior per-file copies — the pixel arithmetic below is a literal copy
of the prior encoders, not a rewrite.

Convention (shared with the React frontend's :file:`src/lib/dataSource.js`
decoder):

  * 8-bit grayscale PNGs (mode ``"L"``).
  * Pixel value ``0`` is the **no-data sentinel** — NaN / +-inf /
    (for log10) non-positive cells.
  * Pixel values ``1..255`` map linearly across ``[lo, hi]`` (linear
    scale) or across ``[log10(lo), log10(hi)]`` (log10 scale).
  * Image is saved with ``optimize=True`` so the on-disk byte hash is
    deterministic for a given input.

Test coverage:
  * ``pipeline/tests/test_lib_encode.py`` asserts byte-identical
    output against the legacy encoders for hand-crafted arrays.
  * Existing PNG-decode tests in
    ``pipeline/tests/test_data_integrity.py`` keep verifying that
    published PNGs round-trip through these decoders correctly.

Why the math looks "fiddly":

  ``np.round(scaled * 254 + 1)`` then ``clip(1, 255)``  maps ``[0, 1]``
  into ``[1, 255]`` (the +1 reserves pixel 0 for no-data). The 254
  factor (not 255) is deliberate — a scaled value of exactly 1.0
  produces ``254 + 1 = 255``, while a scaled value of 0.0 produces
  ``0 + 1 = 1``. Anything outside ``[0, 1]`` gets clipped to the
  range endpoints rather than wrapping.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


def _save_grayscale(px: np.ndarray, path: Path | str) -> None:
    """Save an ``np.uint8`` 2D array as an 8-bit grayscale PNG.

    Centralised so future tweaks to ``optimize=`` / compression level
    happen in one place. Matches the prior fetchers' invocation:

    ::

        Image.fromarray(px, mode="L").save(path, optimize=True)
    """
    Image.fromarray(px, mode="L").save(path, optimize=True)


def encode_linear_png(
    arr: np.ndarray,
    lo: float,
    hi: float,
    path: Path | str,
) -> None:
    """Encode a 2D float array as an 8-bit grayscale PNG, linear scale.

    NaN cells (and any other non-finite values) write pixel 0. Finite
    values map linearly across ``[lo, hi]`` into pixel ``[1, 255]``,
    with out-of-range values clipped to the endpoints.

    Byte-identical to the prior :func:`pipeline.fetch.encode_png`
    (linear branch) and :func:`pipeline.fetch_climatology.encode_linear`.

    Parameters
    ----------
    arr
        2D float array; shape becomes the PNG's height x width.
    lo, hi
        Range endpoints. ``hi > lo`` (no defensive check — the
        existing encoders accept this contract implicitly).
    path
        Output file. Parent directory must exist.
    """
    # NB: compute `scaled` over the entire array (including NaN) so that
    # the np.isfinite mask covers both the input-NaN cells AND any
    # (impossible-for-linear) infinities. Matches fetch.py's flow.
    scaled = (arr - lo) / (hi - lo)
    valid = np.isfinite(scaled)
    px = np.zeros(arr.shape, dtype=np.uint8)
    px[valid] = np.clip(np.round(scaled[valid] * 254 + 1), 1, 255).astype(np.uint8)
    _save_grayscale(px, path)


def encode_log10_png(
    arr: np.ndarray,
    lo: float,
    hi: float,
    path: Path | str,
) -> None:
    """Encode a 2D float array as an 8-bit grayscale PNG, log10 scale.

    NaN, +/-inf, AND non-positive values all write pixel 0 (log10 is
    undefined there). Finite positive values map across
    ``[log10(lo), log10(hi)]`` into pixel ``[1, 255]``.

    Byte-identical to the prior :func:`pipeline.fetch.encode_png`
    (log10 branch). The fetch_climatology variant computed the valid
    mask before the log10 (versus after, here); both produce the same
    output array because cells that yield non-finite log10 also yield
    non-finite ``scaled`` and the resulting ``px`` is zero either way.

    Parameters
    ----------
    arr
        2D float array.
    lo, hi
        Range endpoints in linear units (the helper takes their log10
        internally). Both must be positive — the caller is responsible
        for that. Legacy fetchers' specs (chl: (0.05, 20), kd490:
        (0.02, 10)) satisfy this trivially.
    path
        Output file.
    """
    # ``np.errstate(divide="ignore", invalid="ignore")`` suppresses the
    # RuntimeWarning that log10(0) → -inf and log10(<0) → NaN trigger.
    # Those non-finite values get masked off by the ``isfinite`` check
    # one line below — same outcome with quieter logs.
    with np.errstate(divide="ignore", invalid="ignore"):
        scaled = (np.log10(arr) - np.log10(lo)) / (np.log10(hi) - np.log10(lo))
    valid = np.isfinite(scaled)
    px = np.zeros(arr.shape, dtype=np.uint8)
    px[valid] = np.clip(np.round(scaled[valid] * 254 + 1), 1, 255).astype(np.uint8)
    _save_grayscale(px, path)


def encode_age_sidecar_png(
    ages: np.ndarray,
    path: Path | str,
) -> None:
    """Encode a 2D age-in-days array as an 8-bit grayscale PNG.

    Encoding convention (matches the prior
    :func:`pipeline.fetch.encode_age_png`):

      * Input cells with value ``255`` are treated as "no data" and
        write pixel ``0``.
      * Other input cells write ``min(input + 1, 255)`` — the +1
        offset reserves pixel ``0`` for the sentinel, so input
        ``0`` (today's observation, the freshest case) writes pixel
        ``1``, input ``1`` (yesterday) writes ``2``, etc., up to
        a ceiling of ``255``.

    Used for the ``chl_1d_age_days.png`` and ``kd490_1d_age_days.png``
    sidecars consumed by the visibility model (``fetch_visibility.py``)
    to gate "today's observation" features on age==0 cells.

    Parameters
    ----------
    ages
        2D array of age values in days. Sentinel 255 = no data.
        Dtype is coerced to ``int16`` internally so the ``+1``
        operation can't overflow.
    path
        Output file.
    """
    # ``ages.astype(np.int16) + 1`` then ``np.minimum(..., 255)`` keeps
    # the legacy encoder's behavior bit-for-bit. We compute the +1 in
    # int16 to dodge wrap-around if the input came in as uint8 with
    # value 255 (in which case the np.where takes priority anyway —
    # belt-and-braces).
    px = np.where(
        ages == 255,
        0,
        np.minimum(ages.astype(np.int16) + 1, 255),
    ).astype(np.uint8)
    _save_grayscale(px, path)
