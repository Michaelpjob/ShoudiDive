"""Weighted paddy-density raster.

Each offshore particle is splatted onto a grid as a gaussian, scaled by
its detachment weight, so the field magnitude reflects abundance (calm
day -> faint; swell -> intense). Land cells are zeroed. Display is
normalized against a FIXED reference (config.DENSITY_REF) so magnitude is
comparable across runs.
"""
from __future__ import annotations

import math

import numpy as np

import config


def build_density(offshore, landmask):
    b = config.FIELD_BBOX
    step = config.DENSITY_STEP_DEG
    lats = np.arange(b["lat_max"], b["lat_min"] - 1e-9, -step)   # row 0 = north
    lngs = np.arange(b["lng_min"], b["lng_max"] + 1e-9, step)
    H, W = len(lats), len(lngs)
    grid = np.zeros((H, W), dtype=float)

    sig = max(0.6, config.DENSITY_SIGMA_KM / 111.0 / step)       # cells
    rad = int(max(1, round(3 * sig)))
    two_s2 = 2.0 * sig * sig

    for p in offshore:
        cx = (p["lng"] - b["lng_min"]) / step
        cy = (b["lat_max"] - p["lat"]) / step
        i0, j0 = int(round(cx)), int(round(cy))
        w = p["weight"]
        for dj in range(-rad, rad + 1):
            j = j0 + dj
            if j < 0 or j >= H:
                continue
            for di in range(-rad, rad + 1):
                i = i0 + di
                if i < 0 or i >= W:
                    continue
                grid[j, i] += w * math.exp(-(di * di + dj * dj) / two_s2)

    # Zero out land cells so density never bleeds onto shore.
    for j in range(H):
        for i in range(W):
            if landmask.is_land(lngs[i], lats[j]):
                grid[j, i] = 0.0

    return {"lats": lats, "lngs": lngs, "grid": grid,
            "peak": float(grid.max()), "ref": config.DENSITY_REF}
