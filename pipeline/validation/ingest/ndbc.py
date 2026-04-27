"""NDBC (NOAA National Data Buoy Center) plain-text feed.

Federal complement to CDIP. NDBC publishes a tabular real-time feed
at ``ndbc.noaa.gov/data/realtime2/{stn}.txt`` with the same shape
across every buoy:

    #YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE
    2026 04 27 17 00 270  5.0  6.0   0.8    14   5.2 226 1016.8  16.0  18.1  11.2   MM +1.3    MM

We pull WVHT (significant wave height, m), DPD (dominant period, s),
MWD (mean wave direction, deg), and WTMP (water temp, °C). ``MM`` is
"missing"; we walk most-recent-row downward and skip rows where all
the fields we want are missing.

Confidence weight 0.95 — same caliber as CDIP. The two networks
overlap on a few stations (CDIP buoys are also issued NDBC IDs) but
NDBC adds federal stations CDIP doesn't host, especially the Cape
San Martin / Santa Maria / Santa Monica Basin offshore array. That
fills holes in the central coast + LA County zones where CDIP
currently has no coverage.

If the same physical buoy appears in both feeds, score.py's spatial
KDTree match would happily double-count it. We avoid that by picking
NDBC stations that AREN'T already in CDIPScraper.BUOYS — see the
``BUOYS`` list below.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone

from ._base import BaseScraper


# CA-coast NDBC stations chosen to complement CDIPScraper without
# overlap. Coordinates from NDBC's activestations.xml (verified at
# build time). Skip CDIP-twinned stations like 46224 / 46219 /
# 46028C since CDIPScraper already covers those waters.
#
# NorCal stations (46013, 46014, 46026) are commented out — they sit
# above the ShoudiDive bbox (37.6°N max) so their observations
# wouldn't match any of the model's grid cells. Re-enable when bbox
# extends north.
BUOYS: list[dict] = [
    # SoCal Bight / Channel Islands corridor
    {"stn": "46086", "name": "San Clemente Basin",  "lat": 32.499, "lng": -118.034},
    {"stn": "46047", "name": "Tanner Bank",         "lat": 32.418, "lng": -119.535},
    {"stn": "46025", "name": "Santa Monica Basin",  "lat": 33.765, "lng": -119.077},

    # Central coast
    {"stn": "46011", "name": "Santa Maria",         "lat": 34.937, "lng": -120.999},
    {"stn": "46028", "name": "Cape San Martin",     "lat": 35.763, "lng": -121.893},
    {"stn": "46042", "name": "Monterey",            "lat": 36.787, "lng": -122.408},

    # NorCal (out of bbox, skip until bbox extends):
    # {"stn": "46013", "name": "Bodega Bay",         "lat": 38.235, "lng": -123.317},
    # {"stn": "46014", "name": "Pt Arena",           "lat": 39.225, "lng": -123.980},
    # {"stn": "46026", "name": "San Francisco",      "lat": 37.750, "lng": -122.838},
]


# NDBC realtime2 row regex — matches the data columns after the
# date. Columns are space-separated, variable-width, "MM" means
# missing. Compiled once at import time.
_DATE_RE = re.compile(
    r"^\s*(\d{4})\s+(\d{2})\s+(\d{2})\s+(\d{2})\s+(\d{2})\s+(.+)$"
)


class NDBCScraper(BaseScraper):
    source_id = "ndbc-buoy"
    source_confidence = 0.95
    source_root_url = "https://www.ndbc.noaa.gov/data/realtime2/"

    # NDBC's realtime feed is a public text file with no auth and
    # generous-by-design caching. 5 s between buoys is plenty
    # polite — far below their published rate guidance.
    host_rate_limit_s = 5
    _INTRA_PAUSE_S = 5

    def fetch(self) -> list[dict]:
        out: list[dict] = []
        first = True
        for buoy in BUOYS:
            if not first:
                time.sleep(self._INTRA_PAUSE_S)
            first = False
            try:
                obs = self._fetch_one(buoy)
                if obs is not None:
                    out.append(obs)
            except Exception as exc:  # noqa: BLE001
                print(f"  ndbc {buoy['stn']}: {exc}")
        return out

    def _fetch_one(self, buoy: dict) -> dict | None:
        url = f"{self.source_root_url}{buoy['stn']}.txt"
        r = self._polite_get(url)
        latest = self._parse_latest(r.text)
        if latest is None:
            return None

        ts, hs_m, dpd_s, mwd_deg, wtmp_c = latest

        # NDBC reports zeros (rather than MM) for some sensors that are
        # offline; treat very-small or zero water temp as missing
        # rather than as ice-water — California never gets that cold.
        sst_f = _c_to_f(wtmp_c) if (wtmp_c is not None and wtmp_c > 4) else None
        hs_ft = _m_to_ft(hs_m) if hs_m is not None else None

        return {
            "obs_id":             self.make_obs_id(buoy["stn"], when=ts),
            "timestamp_utc":      ts.isoformat(timespec="minutes").replace("+00:00", "Z"),
            "lat":                float(buoy["lat"]),
            "lng":                float(buoy["lng"]),
            "spot_name":          buoy["name"],
            "observed_secchi_ft": None,  # buoys don't measure water clarity
            "observed_sst_f":     sst_f,
            "observed_swell_ft":  hs_ft,
            "observed_swell_period_s": dpd_s,
            "observed_swell_dir_deg":  mwd_deg,
            "source":             self.source_id,
            "source_url":         url,
            "source_confidence":  self.source_confidence,
            "extraction_method":  "plain-text",
            "raw_excerpt":        None,
            "notes":              f"NDBC buoy {buoy['stn']} / {buoy['name']}",
        }

    @staticmethod
    def _parse_latest(text: str):
        """Walk rows newest-first, return the first row that has any
        usable measurement (Hs OR water temp populated)."""
        for line in text.splitlines():
            if not line or line.startswith("#"):
                continue
            m = _DATE_RE.match(line)
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
            tail = m.group(6).split()
            # NDBC realtime2 column layout (after YY MM DD hh mm):
            #   0: WDIR    1: WSPD    2: GST     3: WVHT
            #   4: DPD     5: APD     6: MWD     7: PRES
            #   8: ATMP    9: WTMP   10: DEWP   11: VIS
            #  12: PTDY   13: TIDE
            wvht = _try_mm(tail, 3, lo=0, hi=30)
            dpd  = _try_mm(tail, 4, lo=0, hi=30)
            mwd  = _try_mm(tail, 6, lo=0, hi=360)
            wtmp = _try_mm(tail, 9, lo=0, hi=35)
            if wvht is None and wtmp is None:
                # Row is all-MM for the fields we want; keep walking
                # toward older rows in case the most recent tick was
                # a partial sensor outage.
                continue
            return ts, wvht, dpd, mwd, wtmp
        return None


def _try_mm(parts: list[str], idx: int, *, lo: float, hi: float) -> float | None:
    if idx >= len(parts):
        return None
    v = parts[idx]
    if v == "MM":
        return None
    try:
        f = float(v)
    except ValueError:
        return None
    if not (lo <= f <= hi):
        return None
    return f


def _c_to_f(c: float) -> float:
    return round(c * 9 / 5 + 32, 1)


def _m_to_ft(m: float) -> float:
    return round(m * 3.281, 1)
