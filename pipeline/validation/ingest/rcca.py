"""Reef Check California (RCCA) — kelp-forest survey ingest.

The California Natural Resources Agency mirrors the RCCA 2014-2016
MPA Baseline survey at `data.cnra.ca.gov` as a single 30 MB zip
that includes geocoded transect observations with a per-transect
visibility column. ~1,900 surveys statewide, with hundreds at
NorCal sites (Mendocino, Sonoma, Humboldt) — the rarest and most
valuable input the validation harness will have.

Source: https://data.cnra.ca.gov/dataset/
        citizen-scientist-monitoring-of-rocky-reefs-and-kelp-forests-
        california-north-coast-mpa-ba-2016

The new(er) annual RCCA releases at reefcheck.org require a data-
request form; the data.cnra.ca.gov mirror is open + a CKAN file
download, which is exactly the shape this scraper handles.

## Cadence + cache

This source is *historical*. The MPA Baseline survey covers
2014-2016; nothing new lands. We still run on the standard hourly
cron tick for orchestrator simplicity, but cache the zip on disk
and re-download only if the cached copy is missing or older than
``CACHE_TTL_DAYS``. Per-cron polite-get on the same host is still
floor'd at 5 minutes by `_base.py` — but the cache hit is the
typical path.

## Observation shape

Each RCCA transect row in the survey CSV is one ``observed_secchi_ft``
record. The harness joins on (date, lat, lng) so the per-transect
date matters more than the survey-trip date.

## Confidence

0.90 — agency dataset, monitored by trained Reef Check divers
with calibrated instruments. Slightly below CDIP's 0.95 (federal
buoys) because surveyor vis-estimation methodology still has the
~±5 ft eyeball noise that any underwater vis-eyeball does.
"""
from __future__ import annotations

import csv
import io
import pathlib
import time
import zipfile
from datetime import datetime, timezone

from ._base import BaseScraper


ZIP_URL = (
    "https://data.cnra.ca.gov/dataset/d41bbe90-ce12-4a13-9929-4eddada8531f/"
    "resource/f24b691c-abe3-47b9-8e72-7da2ebcb11b0/download/rcca.zip"
)

# On-disk cache to avoid re-downloading the 30 MB zip every cron tick.
# Lives next to the other ingest sidecars so it travels with the
# validation/ tree.
CACHE_DIR  = pathlib.Path(__file__).parent / "_cache"
CACHE_FILE = CACHE_DIR / "rcca.zip"
CACHE_TTL_DAYS = 30  # historical dataset; refresh monthly is plenty.

# Candidate column names. RCCA schemas have drifted across release
# years; the canonical column for visibility lives under one of these
# names. First match wins.
VIS_COL_CANDIDATES = [
    "Visibility_m", "Visibility (m)", "Visibility",
    "Vis_m", "Vis (m)", "Vis",
    "vis_m", "vis_meters", "VisibilityMeters",
]

# Candidate column names for lat / lng / date / site.
LAT_CANDIDATES  = ["Latitude", "Lat", "latitude", "lat", "site_lat", "SiteLat"]
LNG_CANDIDATES  = ["Longitude", "Lng", "Long", "longitude", "lng", "long",
                   "site_lng", "site_long", "SiteLng", "SiteLong"]
DATE_CANDIDATES = ["Date", "SurveyDate", "Survey_Date", "survey_date",
                   "Sample_Date", "SampleDate", "date"]
SITE_CANDIDATES = ["Site", "SiteName", "Site_Name", "site_name",
                   "Location", "LocationName"]

# Range gate for the vis column. Out-of-range = parser misread or
# bad row. Conservative — RCCA divers don't usually survey in <1 m
# vis or >40 m vis water in CA.
VIS_M_MIN, VIS_M_MAX = 0.5, 40.0
M_TO_FT = 3.28084


class RCCAScraper(BaseScraper):
    source_id = "rcca-mpa-baseline"
    source_confidence = 0.90
    source_root_url = "https://data.cnra.ca.gov/"

    def fetch(self) -> list[dict]:
        zip_path = self._ensure_zip()
        if zip_path is None:
            return []
        rows = _walk_zip_for_rows(zip_path)
        if not rows:
            print(f"  {self.source_id}: no parseable rows in {zip_path.name}")
            return []

        kept = 0
        out: list[dict] = []
        seen_keys: set[str] = set()  # in-run dedup on (date, lat, lng)
        for row in rows:
            obs = self._row_to_obs(row)
            if obs is None:
                continue
            key = f"{obs['timestamp_utc']}|{obs['lat']:.4f}|{obs['lng']:.4f}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            out.append(obs)
            kept += 1
        print(f"  {self.source_id}: {len(rows)} rows scanned, {kept} obs emitted")
        return out

    # ---- Zip cache management -----------------------------------------

    def _ensure_zip(self) -> pathlib.Path | None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if CACHE_FILE.exists():
            age_days = (time.time() - CACHE_FILE.stat().st_mtime) / 86400
            if age_days < CACHE_TTL_DAYS:
                return CACHE_FILE
        # Cache miss / stale → re-download.
        try:
            r = self._polite_get(ZIP_URL, stream=True)
            tmp = CACHE_FILE.with_suffix(".zip.tmp")
            with tmp.open("wb") as f:
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        f.write(chunk)
            tmp.replace(CACHE_FILE)
            print(f"  {self.source_id}: downloaded {CACHE_FILE.stat().st_size} bytes")
            return CACHE_FILE
        except Exception as exc:  # noqa: BLE001
            print(f"  {self.source_id}: zip download failed: "
                  f"{exc.__class__.__name__}: {exc}")
            return None

    # ---- Row → observation dict ---------------------------------------

    def _row_to_obs(self, row: dict) -> dict | None:
        vis_m = _first_numeric(row, VIS_COL_CANDIDATES)
        if vis_m is None or not (VIS_M_MIN <= vis_m <= VIS_M_MAX):
            return None
        lat = _first_numeric(row, LAT_CANDIDATES)
        lng = _first_numeric(row, LNG_CANDIDATES)
        if lat is None or lng is None:
            return None
        # NorCal-zone gate: this scraper exists to populate the new
        # `norcal_*` zones. Surveys south of 36° are SoCal-zone and
        # belong to the other (already-calibrated) scrapers, not here.
        if lat < 36.0 or lat > 42.0:
            return None
        if lng < -125.0 or lng > -116.0:
            return None
        date_raw = _first_str(row, DATE_CANDIDATES)
        when = _parse_date_any(date_raw)
        if when is None:
            return None
        site_name = _first_str(row, SITE_CANDIDATES) or "RCCA transect"

        return {
            "obs_id":             self.make_obs_id(site_name, when=when),
            "timestamp_utc":      when.strftime("%Y-%m-%dT%H:%MZ"),
            "lat":                float(lat),
            "lng":                float(lng),
            "spot_name":          site_name,
            "observed_secchi_ft": round(vis_m * M_TO_FT, 1),
            "observed_sst_f":     None,
            "observed_swell_ft":  None,
            "source":             self.source_id,
            "source_url":         self.source_root_url,
            "source_confidence":  self.source_confidence,
            "extraction_method":  "rcca-zip-csv",
            "raw_excerpt":        f"vis={vis_m:.1f} m at {site_name}",
            "notes":              "MPA Baseline 2014-2016 release",
        }


# ---- Module helpers ---------------------------------------------------

def _walk_zip_for_rows(zip_path: pathlib.Path) -> list[dict]:
    """Scan every CSV in the zip; concatenate rows from any CSV that
    has at least one of the visibility column names.

    The RCCA zip ships multiple CSVs (Fish, Invert, UPC, Surveys, etc.)
    and only some carry the visibility column. We don't know the exact
    filename so we sniff every entry and pick the ones that match.
    """
    out: list[dict] = []
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for name in zf.namelist():
                if not name.lower().endswith(".csv"):
                    continue
                with zf.open(name) as fp:
                    text = fp.read().decode("utf-8", errors="replace")
                reader = csv.DictReader(io.StringIO(text))
                headers = reader.fieldnames or []
                if not any(c in headers for c in VIS_COL_CANDIDATES):
                    continue
                rows = list(reader)
                if rows:
                    print(f"    rcca: {name} → {len(rows)} rows")
                    out.extend(rows)
    except zipfile.BadZipFile as exc:
        print(f"  rcca: corrupt zip cache: {exc}")
    return out


def _first_numeric(row: dict, keys: list[str]) -> float | None:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            try:
                return float(row[k])
            except (TypeError, ValueError):
                pass
    return None


def _first_str(row: dict, keys: list[str]) -> str | None:
    for k in keys:
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _parse_date_any(s: str | None) -> datetime | None:
    """Best-effort date parse — RCCA CSVs may use ISO, US, or
    Excel-serial. Returns a UTC-noon datetime so day-bucket joins
    work cleanly.
    """
    if not s:
        return None
    s = s.strip()
    fmts = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%d-%b-%y",
        "%d-%b-%Y",
    ]
    for fmt in fmts:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(hour=12, tzinfo=timezone.utc)
        except ValueError:
            continue
    return None
