"""Bathymetry-coupled nearshore SST corrections.

Three additive terms, each calibrated against ground-truth dive-log
observations (when residuals accumulate). v1 implementation focuses
on the upwelling correction — biggest signal and we have all the
inputs already published. Solar + tidal are scaffolded with clear
interfaces; they activate when their input feeds are wired.

Why this exists
---------------
A free diver looking at La Jolla Cove vs Pt Loma vs the back side
of Catalina cares about MICROCLIMATE, not regional averages. The
satellite SST product (MUR L4 at 1 km native, regridded to ~5 km
cells) smears those microclimates into a single bbox-cell value.
But three physical processes the satellite can't see produce
predictable nearshore deviations:

  1. **Upwelling at headlands.** When NW alongshore wind blows,
     Ekman transport pushes surface water offshore and cold water
     pops up at coastal promontories. Effect is strongest where
     the bathymetry gradient is steep (cliffs, walls, pinnacles).

  2. **Solar warming on shallow shelves.** Cells where depth < 20 m
     and the marine layer hasn't capped insolation gain 1-2 °F more
     than 5 km offshore on a clear day.

  3. **Tidal mixing at high spots.** Spring tides over seamounts and
     pinnacles entrain colder deep water through the surface.

Each term has a small fixed gain coefficient that gets tuned from
residuals once enough dive-log obs accumulate. The watchdog R3
(zone correlation) flags zones where these terms aren't capturing
the relevant physics — that's the loop that closes the science.

Activation
----------
``APPLY_NEARSHORE_CORRECTIONS`` flag in ``sst_predict.config`` gates
whether ``fetch.py`` actually applies these. Default ON in dev (so
we can audit them on the dev preview), OFF in production until
they've been validated. The watchdog scaffolding still runs either
way — it'll surface false positives loudly if the corrections hurt.
"""
from __future__ import annotations

import math
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
PUBLIC_DATA = ROOT / "public" / "data"


# ----- Tunables ---------------------------------------------------------
#
# All gains start small and conservative. The right values come out of
# the residual archive, not literature. Too aggressive = the watchdog
# fires on Phase-D-induced bias instead of MUR-induced bias. Too small
# = the corrections do nothing and the framework is dead weight. The
# defaults below land in the "barely visible on the dev preview but
# enough to pick up a signal" range.

# Upwelling: |U_along| above this is "upwelling-favorable". Roughly
# matches the threshold below which Ekman transport is overwhelmed by
# friction at the surface. Calibrated from the existing
# along_climo_5d the viz model already uses.
UPWELL_WIND_FLOOR_KT = 5.0           # ~2.5 m/s

# Upwelling cooling per unit (kt × bathy_gradient_norm). Yields a
# typical −0.5 to −1.5 °C cooling at heads-on-NW-day cells, scaling
# to zero offshore where bathy gradient is small.
UPWELL_GAIN_C_PER_UNIT = 0.020

# Cap so a single windy day at a steep wall can't add a 5 °C swing.
UPWELL_MAX_C = 1.5

# Within this distance of shore (km) the upwelling correction applies;
# beyond it, it's zeroed out. 12 km matches the typical coastal
# upwelling tongue scale on the CA coast.
NEARSHORE_DIST_KM = 12.0

# Bbox geometry — same as fetch.py. Hard-coded here so this module
# doesn't import fetch.py (avoids circular import).
BBOX_LAT_MIN, BBOX_LAT_MAX = 31.8, 37.6
BBOX_LNG_MIN, BBOX_LNG_MAX = -124.0, -116.8

# CA-coast representative onshore-normal bearing (deg from N). The
# alongshore-wind decomposition uses the perpendicular. Same constant
# the viz_predict driver chain uses for the upwelling index — keep
# them aligned so both layers see the same upwelling regime.
COAST_NORMAL_DEG = 295.0


# ----- Inputs (read from public/data) ----------------------------------

def _load_image_grid(path: Path) -> Optional[np.ndarray]:
    """Load a published PNG into a normalized float32 array.
    Returns None if the file isn't there — caller falls back to
    skipping that correction term."""
    if not path.exists():
        return None
    try:
        img = Image.open(path).convert("L")
    except OSError:
        return None
    arr = np.asarray(img, dtype=np.float32)
    return arr / 255.0


def _load_bathy_depth_m() -> Optional[np.ndarray]:
    """``bathy.png`` is encoded with depth = 0..6000 m linear in 8-bit.
    Returns depth in meters, or None when the file isn't published yet
    (first-ever CI run before fetch_bathy.py has succeeded)."""
    arr = _load_image_grid(PUBLIC_DATA / "bathy.png")
    if arr is None:
        return None
    return arr * 6000.0


def _load_wind_uv_kt() -> Optional[tuple[np.ndarray, np.ndarray]]:
    """Read the published wind UV PNG (today's). Returns (U, V) in kt.
    None when wind hasn't been published this cycle."""
    # The wind layer publishes /data/wind_now_uv.png as RGBA where R/G
    # encode U/V in the manifest's uv_range (typically -30..30 m/s).
    # For v1 simplicity we read the SPEED field (wind_now_speed.png)
    # and the alongshore decomposition we infer from the vector — but
    # speed alone tells us "windy or not" which is the dominant
    # upwelling signal at this resolution.
    speed_path = PUBLIC_DATA / "wind_now_speed.png"
    if not speed_path.exists():
        return None
    try:
        img = Image.open(speed_path).convert("L")
    except OSError:
        return None
    speed_norm = np.asarray(img, dtype=np.float32) / 255.0
    # Speed ramp encoded 0..40 kt linear (matches fetch_wind.py).
    speed_kt = speed_norm * 40.0
    # We don't have direction reliably available without re-decoding
    # the UV PNG. For v1, treat speed as a proxy for upwelling-
    # favorable conditions; assume CA's typical NW summer pattern.
    # Phase D2 will read the proper UV pair.
    return speed_kt, np.zeros_like(speed_kt)


# ----- Bathy-derived helpers --------------------------------------------

def bathy_gradient_norm(depth_m: np.ndarray) -> np.ndarray:
    """Normalised bathymetric gradient magnitude — peaks at headland
    walls and seamount edges, near zero on the open shelf or in
    deep water. Output range 0..1, used as a multiplier for the
    nearshore correction terms.

    The normalisation uses a robust max (99th percentile) so a single
    extreme cell (a vertical drop right at the coast) doesn't squash
    everywhere else to near-zero.
    """
    # Finite-difference gradient. Ignore NaN cells (edges, no-data).
    mask = np.isfinite(depth_m)
    safe = np.where(mask, depth_m, 0.0)
    gy, gx = np.gradient(safe)
    g = np.sqrt(gx * gx + gy * gy)
    g = np.where(mask, g, 0.0)
    if g.max() <= 0:
        return np.zeros_like(g, dtype=np.float32)
    norm_max = float(np.percentile(g[g > 0], 99))
    if norm_max <= 0:
        return np.zeros_like(g, dtype=np.float32)
    return np.clip(g / norm_max, 0.0, 1.0).astype(np.float32)


def coastal_distance_proxy_km(depth_m: np.ndarray) -> np.ndarray:
    """Crude depth-based proxy for distance-to-shore. Cells with depth
    < 200 m are within ~5 km of the coast on the CA shelf; >1000 m is
    open ocean. Used to smoothly attenuate corrections offshore.

    Real distance-to-shore math (from the CA coastline polygon) is
    available via ``viz_predict.zones`` but that pulls in the GeoJSON
    + shapely deps. For v1 the depth proxy is good enough.
    """
    # Map depth 0..200 m → distance 0..5 km, 200..1000 m → 5..15 km,
    # >1000 m → 30 km. Smooth piecewise linear.
    mask_shallow = depth_m < 200.0
    mask_shelf   = (depth_m >= 200.0) & (depth_m < 1000.0)
    out = np.full_like(depth_m, 30.0, dtype=np.float32)
    out[mask_shallow] = depth_m[mask_shallow] / 200.0 * 5.0
    out[mask_shelf]   = 5.0 + (depth_m[mask_shelf] - 200.0) / 800.0 * 10.0
    return out


# ----- Term 1 — Upwelling cooling --------------------------------------

@dataclass
class CorrectionLayer:
    name:        str
    delta_c:     np.ndarray           # additive correction to SST (°C)
    contrib_pct: float                 # fraction of cells where |delta| > 0.05
    max_abs_c:   float                 # max |delta| across the grid


def upwelling_correction(*, bathy_grid_h: int, bathy_grid_w: int) -> Optional[CorrectionLayer]:
    """Compute the per-cell upwelling cooling term.

    delta_c = -gain × max(0, |wind_kt| - floor) × bathy_gradient_norm

    Applied only inside ``NEARSHORE_DIST_KM`` of shore (proxied via
    bathy depth). Returns None when bathy or wind isn't available.
    """
    depth = _load_bathy_depth_m()
    if depth is None:
        return None
    wind = _load_wind_uv_kt()
    if wind is None:
        return None

    # Resample wind to bathy grid if shapes differ. Both are bbox-
    # aligned so a simple bilinear resample via PIL is enough.
    speed_kt, _ = wind
    if speed_kt.shape != depth.shape:
        speed_img = Image.fromarray(
            (np.clip(speed_kt, 0.0, 60.0) / 60.0 * 255).astype(np.uint8)
        )
        speed_img = speed_img.resize(
            (depth.shape[1], depth.shape[0]),
            Image.BILINEAR,
        )
        speed_kt = np.asarray(speed_img, dtype=np.float32) / 255.0 * 60.0

    gradient = bathy_gradient_norm(depth)
    distance = coastal_distance_proxy_km(depth)

    # Upwelling-favorable wind component (only the part above floor)
    excess_kt = np.maximum(0.0, speed_kt - UPWELL_WIND_FLOOR_KT)
    # Distance attenuation — smooth fall-off so we don't get a step.
    attn = np.clip(1.0 - distance / NEARSHORE_DIST_KM, 0.0, 1.0)
    delta = -UPWELL_GAIN_C_PER_UNIT * excess_kt * gradient * attn
    delta = np.clip(delta, -UPWELL_MAX_C, 0.0).astype(np.float32)

    contrib = float(np.mean(np.abs(delta) > 0.05))
    max_abs = float(np.max(np.abs(delta)))
    return CorrectionLayer(
        name="upwelling",
        delta_c=delta,
        contrib_pct=round(contrib, 3),
        max_abs_c=round(max_abs, 3),
    )


# ----- Term 2 — Solar warming on shallow shelves (scaffold) ------------

def solar_correction(*, bathy_grid_h: int, bathy_grid_w: int) -> Optional[CorrectionLayer]:
    """TODO[Phase D2]: read HRRR dswrf + cloud fraction, apply warming
    to cells where depth < 20 m and insolation > threshold.

    Inputs not yet wired into ``public/data``; returning None is
    correct behavior until ``fetch_wind*.py`` exposes the cached
    HRRR shortwave grid as a sidecar PNG.
    """
    return None


# ----- Term 3 — Tidal mixing at high-relief features (scaffold) -------

def tidal_correction(*, bathy_grid_h: int, bathy_grid_w: int) -> Optional[CorrectionLayer]:
    """TODO[Phase D2]: read /data/tides.json daily range, multiply by
    bathy gradient at high-relief cells (seamounts, pinnacles).

    Stub returns None until the magnitude calibration is grounded —
    going live with a wrong sign would surface as a watchdog R3
    correlation regression in the catalina/coronados zones.
    """
    return None


# ----- Composer ---------------------------------------------------------

def compute_all_corrections(*, target_h: int, target_w: int) -> dict:
    """Aggregate the three nearshore correction terms.

    Returns a dict::

      total_delta_c       (target_h, target_w) np.float32 — sum
      layers              list[CorrectionLayer]           — per-term diagnostics

    Caller (fetch.py) decides whether to apply ``total_delta_c``
    based on the APPLY_NEARSHORE_CORRECTIONS feature flag.
    """
    layers: list[CorrectionLayer] = []
    for fn in (upwelling_correction, solar_correction, tidal_correction):
        layer = fn(bathy_grid_h=target_h, bathy_grid_w=target_w)
        if layer is not None:
            layers.append(layer)

    if not layers:
        return {
            "total_delta_c": np.zeros((target_h, target_w), dtype=np.float32),
            "layers":        [],
        }

    # All layers' delta arrays are on the bathy grid; resample to the
    # caller's target grid if needed.
    def _resample(arr: np.ndarray) -> np.ndarray:
        if arr.shape == (target_h, target_w):
            return arr
        # Center on 0; map ±UPWELL_MAX_C → 0..255 for round-trip via PIL.
        scale = max(UPWELL_MAX_C, 0.001)
        u8 = ((np.clip(arr, -scale, scale) + scale) / (2 * scale) * 255.0).astype(np.uint8)
        img = Image.fromarray(u8).resize((target_w, target_h), Image.BILINEAR)
        return ((np.asarray(img, dtype=np.float32) / 255.0) * (2 * scale) - scale).astype(np.float32)

    total = np.zeros((target_h, target_w), dtype=np.float32)
    for layer in layers:
        total += _resample(layer.delta_c)
    return {"total_delta_c": total, "layers": layers}


# ----- Manifest summary -------------------------------------------------

def correction_summary(layers: list[CorrectionLayer]) -> dict:
    return {
        "method": "additive_per_term",
        "layers": [
            {
                "name": L.name,
                "contrib_frac": L.contrib_pct,
                "max_abs_c":    L.max_abs_c,
            }
            for L in layers
        ],
    }
