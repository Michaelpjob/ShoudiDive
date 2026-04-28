"""Shared color ramps for server-rendered mobile overlay PNGs."""

from __future__ import annotations

import math

import numpy as np
from PIL import Image

SST_RANGE = (9.0, 25.0)
CHL_RANGE = (0.05, 20.0)
VIZ_RANGE_FT = (0.0, 80.0)

SST_STOPS = [
    (0.00, (12, 38, 130)),
    (0.25, (40, 130, 210)),
    (0.50, (120, 220, 220)),
    (0.70, (240, 220, 110)),
    (0.85, (230, 110, 60)),
    (1.00, (170, 20, 35)),
]

CHL_STOPS = [
    (0.00, (10, 50, 140)),
    (0.25, (30, 130, 200)),
    (0.50, (60, 200, 180)),
    (0.75, (110, 210, 90)),
    (1.00, (50, 130, 40)),
]

VIZ_STOPS_FT = [
    (0.0, (194, 65, 12)),
    (10.0, (234, 179, 8)),
    (20.0, (132, 204, 22)),
    (30.0, (6, 182, 212)),
    (50.0, (3, 105, 161)),
]

_CHL_RANGE_LOG = (math.log10(CHL_RANGE[0]), math.log10(CHL_RANGE[1]))


def _lerp_rgb(a, b, amount):
    return (
        int(round(a[0] + (b[0] - a[0]) * amount)),
        int(round(a[1] + (b[1] - a[1]) * amount)),
        int(round(a[2] + (b[2] - a[2]) * amount)),
    )


def _ramp_lookup(stops, value):
    value = max(stops[0][0], min(stops[-1][0], value))
    for index in range(len(stops) - 1):
        start, end = stops[index], stops[index + 1]
        if start[0] <= value <= end[0]:
            amount = (value - start[0]) / (end[0] - start[0])
            return _lerp_rgb(start[1], end[1], amount)
    return stops[-1][1]


def _value_to_rgb(layer, value):
    if layer == "sst":
        low, high = SST_RANGE
        return _ramp_lookup(SST_STOPS, (value - low) / (high - low))
    if layer == "chl":
        if value <= 0:
            return _ramp_lookup(CHL_STOPS, 0.0)
        low, high = _CHL_RANGE_LOG
        return _ramp_lookup(CHL_STOPS, (math.log10(value) - low) / (high - low))
    if layer == "viz":
        return _ramp_lookup(VIZ_STOPS_FT, value)
    raise ValueError(f"Unsupported color ramp layer: {layer}")


def encode_color_png(arr, layer, out_path):
    rgba = np.zeros((*arr.shape, 4), dtype=np.uint8)
    flat_values = arr.reshape(-1)
    flat_rgba = rgba.reshape(-1, 4)

    for index, value in enumerate(flat_values):
        if not np.isfinite(value):
            continue
        rgb = _value_to_rgb(layer, float(value))
        flat_rgba[index, 0] = rgb[0]
        flat_rgba[index, 1] = rgb[1]
        flat_rgba[index, 2] = rgb[2]
        flat_rgba[index, 3] = 255

    Image.fromarray(rgba, mode="RGBA").save(out_path, optimize=True)
