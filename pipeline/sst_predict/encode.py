"""PNG encoding + manifest entry emission for sst_predict outputs.

Mirror of fetch_wind_5day's writer. Outputs land in:

  public/data/sst_now.png              blender output (replaces fetch.py's
                                       sst_*.png pass-through OUTPUT-COMPATIBLY)
  public/data/sst_now_age_days.png     per-cell age sidecar
  public/data/sst_now_source.png       per-cell source-id sidecar
  public/data/sst_now_p10.png          p10 lower bound
  public/data/sst_now_p90.png          p90 upper bound
  public/data/sst5d/d{0..6}_sst.png    per-day forecast field
  public/data/sst5d/d{0..6}_p10.png    per-day lower bound
  public/data/sst5d/d{0..6}_p90.png    per-day upper bound
  public/data/sst5d/summary.json       per-day stats + best-window for UI

The encoding deliberately matches fetch.py's existing SST PNG schema
(linear °C 9-25, mode='L' grayscale, 71×87) so a phase-2 rollout can
SWAP THE WRITER without breaking the React + RN clients. The 5d
summary.json mirrors fetch_wind_5day's summary.json structure so the
clients pick up the SST forecast UI by reusing the wind5d/swell5d
component.

Status: framework. Implementation in phase 2 (now) + phase 3 (5d).
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Optional

import numpy as np

from . import config


# Output dir matches the existing convention.
ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "public" / "data"
SST5D_DIR = OUT_DIR / "sst5d"


def encode_now(
    *,
    sst_c:         np.ndarray,
    age_days:      np.ndarray,
    source_id:     np.ndarray,
    p10_c:         Optional[np.ndarray] = None,
    p90_c:         Optional[np.ndarray] = None,
    sources_used:  dict,
    coverage_frac: float,
    mean_age_days: float,
) -> dict:
    """Write sst_now*.png and return the manifest entry to merge.

    Manifest entry shape (matches fetch.py's existing sst layer block,
    plus age + source sidecars + p10/p90)::

      "sst": {
          "windows": {
              "now": {
                  "url":            "/data/sst_now.png",
                  "p10_url":        "/data/sst_now_p10.png",
                  "p90_url":        "/data/sst_now_p90.png",
                  "age_days_url":   "/data/sst_now_age_days.png",
                  "source_url":     "/data/sst_now_source.png",
                  "source_legend":  {1: "mur_l4", 2: "viirs_snpp_nrt", ...},
                  "blended":        true,
                  "coverage_frac":  0.91,
                  "mean_age_days":  1.4,
                  "sources":        { "mur_l4": {...}, ... },
                  "valid_at":       "2026-05-06T05:00:00Z"
              },
              "1d": ..., "2d": ..., "3d": ...   // existing pass-through
          },
          "range": [9.0, 25.0],
          "unit":  "degC",
          "grid":  { "width": 71, "height": 87 }
      }
    """
    raise NotImplementedError("phase-2: encode + write + manifest entry")


def encode_forecast(
    *,
    sst_c_horizon:  np.ndarray,    # (HORIZON_DAYS, 87, 71)
    p10_c_horizon:  np.ndarray,
    p90_c_horizon:  np.ndarray,
    confidence:     list[str],
    sources_used:   dict,
    valid_dates:    list[date],
) -> dict:
    """Write sst5d/d*.png + summary.json. Returns manifest entry.

    Manifest entry mirrors wind5d/swell5d so the client UI is shared::

      "sst5d": {
          "summary_url": "/data/sst5d/summary.json",
          "range_c":     [9.0, 25.0],
          "horizon_days": 7,
          "unit":        "degC"
      }

    summary.json::

      {
          "generated_at": "...",
          "horizon_days": 7,
          "range_c":      [9.0, 25.0],
          "days": [
              {
                  "day":          "Today",        // or "+1", "+2", ...
                  "date":         "2026-05-06",
                  "url":          "/data/sst5d/d0_sst.png",
                  "p10_url":      "/data/sst5d/d0_p10.png",
                  "p90_url":      "/data/sst5d/d0_p90.png",
                  "confidence":   "high",
                  "mean_c":       16.8,
                  "anom_c":       +0.4,
                  "min_c":        13.2,
                  "max_c":        19.6
              },
              ...
          ]
      }
    """
    raise NotImplementedError("phase-3: encode + write 5d summary")


def _value_to_uint8(
    value: np.ndarray,
    rng:   tuple[float, float] = config.SST_RANGE_C,
) -> np.ndarray:
    """Map °C to uint8 0..255 via the same linear transform fetch.py
    uses, so existing manifest readers don't need updating.

    NaN cells → 0 (existing convention; the front-end treats 0 as
    no-data and shows a transparent pixel).
    """
    raise NotImplementedError("phase-2 stub")
