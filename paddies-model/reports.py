"""Catch-report assimilation — the strongest, most direct signal of where fish
are (deep-research 2026-06-19: recent reports were the #1 missing signal, and
the only thing that reliably lands the pin on real fish given our other inputs).

A report is a logged catch {lat, lng, date, species, ...}. We boost the
opportunity field around recent catches, decaying with RECENCY (zone-level
persistence holds for days even as individual paddies disperse) and DISTANCE
(a catch lights up a zone, not a point). No lookahead: a frame rendered for a
given as-of date only sees catches on/before that date.

Sources fold together: a committed seed feed (reports.json) + (later) crowd
submissions. This module just consumes the merged list.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os

import numpy as np

import config


def load_reports(path=None):
    """Load the committed seed feed. Returns a list of report dicts (or [])."""
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), config.REPORTS_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return [r for r in data if "lat" in r and "lng" in r]
    except Exception:
        return []


def _as_dt(s):
    try:
        return dt.datetime.fromisoformat(str(s)[:10])
    except Exception:
        return None


def assimilate(reports, lats, lngs, as_of_dt):
    """Return a 0..1 grid: the recency- and distance-decayed boost from catches
    on/before `as_of_dt`. Peak 1.0 at the freshest catch, fading out by
    REPORT_RADIUS_NM and REPORT_MAX_AGE_DAYS."""
    grid = np.zeros((len(lats), len(lngs)))
    if not reports:
        return grid
    LNG, LAT = np.meshgrid(np.asarray(lngs), np.asarray(lats))
    coslat = math.cos(math.radians(0.5 * (lats[0] + lats[-1])))
    rad_deg = config.REPORT_RADIUS_NM / 60.0          # ~nm -> deg latitude
    for r in reports:
        rd = _as_dt(r.get("date", ""))
        if rd is None:
            continue
        age = (as_of_dt - rd).total_seconds() / 86400.0
        if age < -0.5 or age > config.REPORT_MAX_AGE_DAYS:   # no lookahead; not too old
            continue
        w = math.exp(-max(age, 0.0) / config.REPORT_DECAY_DAYS)
        d2 = (LAT - r["lat"]) ** 2 + ((LNG - r["lng"]) * coslat) ** 2
        grid += w * np.exp(-d2 / (rad_deg ** 2))
    m = float(grid.max())
    return grid / m if m > 0 else grid


def count_active(reports, as_of_dt):
    """How many reports are within the active window of `as_of_dt`."""
    n = 0
    for r in reports:
        rd = _as_dt(r.get("date", ""))
        if rd is None:
            continue
        age = (as_of_dt - rd).total_seconds() / 86400.0
        if -0.5 <= age <= config.REPORT_MAX_AGE_DAYS:
            n += 1
    return n
