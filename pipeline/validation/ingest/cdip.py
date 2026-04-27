"""CDIP coastal-buoy scraper.

Six CA buoys ringing the bbox return swell + SST at 30-minute
resolution. We pull the parameter (``pm``) endpoint per buoy, take
the most recent row that has both Hs and SST populated, and emit a
single observation per buoy per cron tick.

Confidence weight 0.95 — these are calibrated wave-rider buoys
maintained by Scripps. They don't measure visibility, but they DO
measure swell and SST, which are two of the model's biggest input
drivers. So the residuals from CDIP validate ``swell5d`` + ``sst``
against direct measurement, not against the predicted-viz pipeline.

The bigger CDIP API is per-buoy NetCDF (``ndar.cdip``), but the
``justdar?{stn}+pm`` ASCII table is plenty for our needs and avoids
the netCDF dependency in CI.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone

from ._base import BaseScraper, slugify


# Six CA buoys covering Pt Loma → Conception. Coordinates are from
# CDIP's station registry; if any drift, parse the buoy header
# instead. ``role`` is just a human-readable label that ends up in
# the obs ``notes``.
BUOYS: list[dict] = [
    {"stn": "201", "name": "La Jolla Nearshore",  "lat": 32.866, "lng": -117.267},
    {"stn": "100", "name": "Torrey Pines Outer",  "lat": 32.930, "lng": -117.391},
    {"stn": "045", "name": "Oceanside Offshore",  "lat": 33.180, "lng": -117.471},
    {"stn": "067", "name": "San Pedro South Bay", "lat": 33.617, "lng": -118.317},
    {"stn": "076", "name": "Harvest, CA",          "lat": 34.452, "lng": -120.781},
    {"stn": "071", "name": "Harvest Spar",         "lat": 34.450, "lng": -120.780},
]


# CDIP `pm` table looks like:
#   YEAR MO DY HR MN   Hs   Tp   Dp    Depth   Ta    Pres   Wspd Wdir Temp   Temp
#         UTC           m   sec  deg     m     sec    mB     m/s  deg Air(C) Sfc(C)
#   2026 04 26 05 00  0.79 12.50 209           5.21
#   2026 04 26 05 30  0.79 13.33 211           4.95                           18.7
#
# Columns are space-separated but variable-width. Empty cells appear
# as runs of spaces — we split on whitespace and rely on column count
# to map fields. Different stations sometimes omit late columns
# entirely (e.g. no Sfc temp), so we walk fields left-to-right and
# only require Hs to be populated.
PM_DATE_RE = re.compile(
    r"^\s*(\d{4})\s+(\d{2})\s+(\d{2})\s+(\d{2})\s+(\d{2})\s+(.+)$"
)


class CDIPScraper(BaseScraper):
    source_id = "cdip-buoy"
    source_confidence = 0.95
    source_root_url = "https://cdip.ucsd.edu/data_access/justdar.cdip"

    # Override: justdar is a public ASCII API, hammering it once per
    # buoy with 5-minute pauses between would block the entire ingest
    # cron for half an hour. 10 s between buoys is plenty polite.
    _CDIP_INTRA_PAUSE_S = 10

    def fetch(self) -> list[dict]:
        out: list[dict] = []
        first = True
        for buoy in BUOYS:
            if not first:
                time.sleep(self._CDIP_INTRA_PAUSE_S)
            first = False
            try:
                obs = self._fetch_one(buoy)
                if obs is not None:
                    out.append(obs)
            except Exception as exc:  # noqa: BLE001
                # Don't let a single buoy's outage take down the rest.
                print(f"  cdip {buoy['stn']}: {exc}")
        return out

    def _fetch_one(self, buoy: dict) -> dict | None:
        # `+pm+1` = parameter table for the last 1 day. ~48 rows,
        # plenty to find the most recent populated record.
        url = f"{self.source_root_url}?{buoy['stn']}+pm+1"
        r = self._polite_get(url)
        text = r.text

        latest = self._parse_latest(text)
        if latest is None:
            return None

        ts, hs_m, tp_s, dp_deg, sst_c = latest
        return {
            "obs_id":             self.make_obs_id(buoy["stn"], when=ts),
            "timestamp_utc":      ts.isoformat(timespec="minutes").replace("+00:00", "Z"),
            "lat":                float(buoy["lat"]),
            "lng":                float(buoy["lng"]),
            "spot_name":          buoy["name"],
            "observed_secchi_ft": None,                       # buoys don't measure viz
            "observed_sst_f":     _c_to_f(sst_c) if sst_c is not None else None,
            "observed_swell_ft":  _m_to_ft(hs_m) if hs_m is not None else None,
            "observed_swell_period_s": tp_s,
            "observed_swell_dir_deg":  dp_deg,
            "source":             self.source_id,
            "source_url":         url,
            "source_confidence":  self.source_confidence,
            "extraction_method":  "ascii-table",
            "raw_excerpt":        None,
            "notes":              f"buoy {buoy['stn']} / {buoy['name']}",
        }

    @staticmethod
    def _parse_latest(text: str):
        """Return (ts, hs_m, tp_s, dp_deg, sst_c) for the latest row,
        or None if no row has at least Hs populated.

        Walks the file from the bottom (most recent) up so we don't
        have to keep the whole table in memory. Fields after Hs are
        opportunistic — Sfc(C) is often the last column and frequently
        absent on stations without a sea-temperature sensor.
        """
        for line in reversed(text.splitlines()):
            m = PM_DATE_RE.match(line)
            if not m:
                continue
            try:
                ts = datetime(
                    int(m.group(1)), int(m.group(2)), int(m.group(3)),
                    int(m.group(4)), int(m.group(5)),
                    tzinfo=timezone.utc,
                )
            except ValueError:
                continue
            tail = m.group(6)
            # Replace runs of spaces with single spaces, then split.
            # Empty data cells in justdar's `pm` look like extra
            # whitespace; pad columns are positional which we can't
            # recover after split, so we accept the lossy version and
            # rely on Hs always being column 0 of the data section.
            parts = tail.split()
            if not parts:
                continue
            try:
                hs_m = float(parts[0])
            except ValueError:
                continue
            if hs_m <= 0 or hs_m > 30:
                # Bogus row.
                continue
            tp_s   = _try_float(parts, 1, lo=0.5, hi=30)
            dp_deg = _try_float(parts, 2, lo=0,   hi=360)
            # Sfc temp can be column 7 OR 8 depending on whether
            # Pres/Wspd/Wdir/Temp_Air are populated. Pull the LAST
            # numeric value in the row that's plausible as °C.
            sst_c = None
            for cand in reversed(parts[3:]):
                v = _try_float_simple(cand)
                if v is not None and 4.0 <= v <= 30.0:
                    sst_c = v
                    break
            return ts, hs_m, tp_s, dp_deg, sst_c
        return None


def _try_float(parts: list[str], idx: int, *, lo: float, hi: float) -> float | None:
    if idx >= len(parts):
        return None
    v = _try_float_simple(parts[idx])
    if v is None or v < lo or v > hi:
        return None
    return v


def _try_float_simple(s: str) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _c_to_f(c: float) -> float:
    return round(c * 9 / 5 + 32, 1)


def _m_to_ft(m: float) -> float:
    return round(m * 3.281, 1)
