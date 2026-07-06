"""Findability surface — where the currently-FLOATING paddies are.

A gaussian splat of floating particles weighted by float_w
(detachment x survival), so the field is "how likely am I to find a live
paddy here right now." Beached and sunk particles are excluded; land
cells are zeroed.
"""
from __future__ import annotations

import math

import numpy as np

import config


def build(floating, landmask):
    b = config.FIELD_BBOX
    step = config.DENSITY_STEP_DEG
    lats = np.arange(b["lat_max"], b["lat_min"] - 1e-9, -step)   # row 0 = north
    lngs = np.arange(b["lng_min"], b["lng_max"] + 1e-9, step)
    H, W = len(lats), len(lngs)
    grid = np.zeros((H, W), dtype=float)

    sig = max(0.6, config.DENSITY_SIGMA_KM / 111.0 / step)
    rad = int(max(1, round(3 * sig)))
    two_s2 = 2.0 * sig * sig

    for p in floating:
        w = p["float_w"]
        if w <= 0:
            continue
        cx = (p["lng"] - b["lng_min"]) / step
        cy = (b["lat_max"] - p["lat"]) / step
        i0, j0 = int(round(cx)), int(round(cy))
        for dj in range(-rad, rad + 1):
            j = j0 + dj
            if 0 <= j < H:
                for di in range(-rad, rad + 1):
                    i = i0 + di
                    if 0 <= i < W:
                        grid[j, i] += w * math.exp(-(di * di + dj * dj) / two_s2)

    for j in range(H):
        for i in range(W):
            if landmask.is_land(lngs[i], lats[j]):
                grid[j, i] = 0.0

    return {"lats": lats, "lngs": lngs, "grid": grid,
            "peak": float(grid.max()), "ref": config.DENSITY_REF}
