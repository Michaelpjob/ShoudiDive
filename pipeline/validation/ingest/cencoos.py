"""CeNCOOS shore-station ingest — turbidity → Secchi for NorCal nearshore.

Pulls the last ~7 days of turbidity / SST / chlorophyll from two
Central-and-Northern California Ocean Observing System (CeNCOOS) ERDDAP
stations and converts the turbidity reading into an approximate
Secchi-depth-equivalent for the visibility validation harness:

    monterey-wharf-real-time-samplin
                             — Moss Landing Marine Labs, Monterey
                               Commercial Wharf, lat 36.605 °N,
                               lng -121.889 °W, ~20-min cadence.
                               Same MLML sensor package as the retired
                               `mlml_monterey` dataset (ERDDAP
                               time_coverage_end 2019-11-29, so every
                               windowed query 404'd); MLML republished
                               the wharf under this id from 2023-03-09.
                               Gappy: no samples ~2025-10-22..2026-01-27
                               or 2026-09-15..10-06.

    edu_calpoly_marine_morro — Cal Poly Marine Studies Pier (Morro Bay)
                               lat ~35.336 °N, lng ~-120.866 °W
                               control station near the southern boundary
                               of the new `norcal` lat band (36.00 °N)

Useful as a Tier-1 ground-truth feed because the readings are
continuous, programmatic, and dated — exactly the shape the
validation harness (`pipeline/validation/norcal_residuals.py`) needs.
Documented in `docs/norcal-vis-validation-sources.md`.

## Turbidity → Secchi conversion

CeNCOOS reports `sea_water_turbidity` in NTU (nephelometric turbidity
units). Secchi depth and turbidity are inversely correlated:

    Secchi_m ≈ K / (turbidity_NTU + offset)

The CONSTANTS below use a coastal-CA fit (Davies-Colley 1988 style)
chosen for plausibility against the Monterey Bay range; the absolute
calibration is intentionally rough at this stage. The validation
harness will compare these "observed" Secchi values against
`viz_p50_ft` and surface the bias; PR-NC-4 will tighten the
constants once we have residuals on the table.

This source's `source_confidence` is set to 0.70 (below dive shops
at 0.85) to reflect that the value is a *derived* Secchi rather than
a divemaster's eyeball estimate. Score.py will weight it accordingly.

## Output

One observation per station per UTC day — the orchestrator's dedup
on `obs_id` already collapses same-day re-runs; downsampling to one
record per day matches the cadence of the rest of the validation
JSONL (`pipeline/validation/data/observations.jsonl`).
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone
from statistics import mean

import requests

from ._base import FEED_EMPTY, FEED_ENDED, FEED_FAILED, FEED_OK, BaseScraper


# ---------------------------------------------------------------------
# Station roster
# ---------------------------------------------------------------------
#
# Each station maps a CeNCOOS ERDDAP `datasetID` to its (lat, lng,
# canonical name). Variables fetched are the standard ERDDAP CF-named
# columns; the QC-flag columns are pulled but not yet trusted (a
# follow-up can drop rows with `*_qc_agg != 1`, the IOOS QARTOD "pass"
# code, once we've eyeballed enough data to confirm the flag semantics
# match the docs).

STATIONS = [
    {
        "dataset_id": "monterey-wharf-real-time-samplin",
        "spot_name":  "Monterey Wharf (MLML)",
        "lat":        36.605,
        "lng":        -121.889,
    },
    {
        "dataset_id": "edu_calpoly_marine_morro",
        "spot_name":  "Morro Bay (Cal Poly Pier)",
        "lat":        35.336,
        "lng":        -120.866,
    },
]

# Days of data to pull on each cron tick. The orchestrator runs hourly;
# 7 days of window with same-day dedup gives a small backfill cushion
# if a single cron run fails without losing observations.
LOOKBACK_DAYS = 7

# Variables to request. Conservative — only the ones we actually
# convert + emit. Pulling fewer columns means smaller ERDDAP responses.
ERDDAP_VARS = [
    "time",
    "latitude",
    "longitude",
    "sea_water_turbidity",
    "sea_water_temperature",
    "mass_concentration_of_chlorophyll_in_sea_water",
]

# Turbidity → Secchi conversion constants. See the docstring.
# `Secchi_m = TURB_K / (NTU + TURB_OFFSET)`.
#
# Intentional broad fit: NTU 1 → ~3.5 m Secchi (clear coastal),
# NTU 5 → ~1.0 m, NTU 10 → ~0.5 m. Matches the Monterey Bay
# eyeball range. PR-NC-4 will refit.
TURB_K       = 7.0
TURB_OFFSET  = 1.0

# Floor / ceiling on the Secchi conversion. Below ~0.3 m we have
# zero practical interest (it's storm runoff); above ~30 m the fit
# breaks down (we have no NorCal observations in that range to
# calibrate against). Out-of-range values are dropped, not clamped.
SECCHI_FT_MIN  = 1.0
SECCHI_FT_MAX  = 100.0

M_TO_FT = 3.28084

# A station with no samples in the window costs one extra request for
# its ERDDAP metadata. If time_coverage_end is older than this, the
# dataset is retired and gets reported as ended (swap it out) rather
# than as a quiet week. 180 d because the Monterey wharf went ~3 months
# without samples over winter 2025-26 and then came back.
ENDED_AFTER_DAYS = 180


class CeNCOOSScraper(BaseScraper):
    source_id = "cencoos"
    source_confidence = 0.70
    source_root_url = "https://erddap.cencoos.org/erddap/"

    # ERDDAP is a data service built for scripted pulls; same cadence
    # policy as CDIP. The 300 s default slept 5 minutes between the two
    # stations on every run.
    host_rate_limit_s = 10

    def fetch(self) -> list[dict]:
        out: list[dict] = []
        for station in STATIONS:
            ds = station["dataset_id"]
            try:
                rows = self._fetch_station(station)
            except requests.HTTPError as exc:
                # ERDDAP answers 404 when the time window matches no
                # rows, so a 404 here means "nothing recent", not "bad
                # URL". _report_no_samples() works out which.
                if exc.response is None or exc.response.status_code != 404:
                    self._report_failed(station, exc)
                    continue
                rows = []
            except Exception as exc:  # noqa: BLE001
                self._report_failed(station, exc)
                continue
            daily = _group_daily(rows)
            kept = 0
            for date_utc, day_rows in daily.items():
                obs = self._to_obs(station, date_utc, day_rows)
                if obs is None:
                    continue
                out.append(obs)
                kept += 1
            print(f"  {self.source_id}: {ds} → "
                  f"{len(rows)} samples, {kept} daily obs")
            last_sample = _latest_time(rows)
            if kept:
                self.report_feed(ds, FEED_OK, observation_count=kept,
                                 last_data_utc=last_sample)
            elif rows:
                self.report_feed(
                    ds, FEED_EMPTY, last_data_utc=last_sample,
                    detail=(f"{len(rows)} samples in last {LOOKBACK_DAYS} d, "
                            f"none with usable turbidity"),
                )
            else:
                self._report_no_samples(station)
        return out

    def _report_failed(self, station: dict, exc: Exception) -> None:
        ds = station["dataset_id"]
        print(f"  {self.source_id}: {ds} "
              f"fetch failed: {exc.__class__.__name__}: {exc}")
        self.report_feed(ds, FEED_FAILED,
                         detail=f"{exc.__class__.__name__}: {exc}")

    def _report_no_samples(self, station: dict) -> None:
        """No samples in the window: ask ERDDAP whether the dataset ended."""
        ds = station["dataset_id"]
        window = f"no samples in last {LOOKBACK_DAYS} d"
        try:
            end = self._coverage_end(ds)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                print(f"  {self.source_id}: {ds} ENDED: not on ERDDAP")
                self.report_feed(ds, FEED_ENDED, detail=(
                    f"{window}; dataset not on ERDDAP (info 404), "
                    f"removed or renamed"))
                return
            self.report_feed(ds, FEED_EMPTY, detail=(
                f"{window}; coverage lookup failed: {exc.__class__.__name__}"))
            return
        except Exception as exc:  # noqa: BLE001
            self.report_feed(ds, FEED_EMPTY, detail=(
                f"{window}; coverage lookup failed: {exc.__class__.__name__}"))
            return
        if end is None:
            self.report_feed(ds, FEED_EMPTY, detail=(
                f"{window}; metadata has no time_coverage_end"))
            return
        age_d = (datetime.now(timezone.utc) - end).days
        if age_d > ENDED_AFTER_DAYS:
            print(f"  {self.source_id}: {ds} ENDED upstream: "
                  f"time_coverage_end {end:%Y-%m-%d} ({age_d} d ago)")
            self.report_feed(ds, FEED_ENDED, last_data_utc=end, detail=(
                f"ERDDAP time_coverage_end {end:%Y-%m-%d} ({age_d} d ago); "
                f"retire or replace this dataset"))
        else:
            self.report_feed(ds, FEED_EMPTY, last_data_utc=end, detail=(
                f"{window}; last upstream sample {end:%Y-%m-%d}"))

    # ---- ERDDAP fetch + CSV parse -------------------------------------

    def _coverage_end(self, dataset_id: str) -> datetime | None:
        """``time_coverage_end`` from the dataset's ERDDAP info page."""
        url = f"{self.source_root_url}info/{dataset_id}/index.csv"
        r = self._polite_get(url)
        return _parse_coverage_end(r.text)

    def _fetch_station(self, station: dict) -> list[dict]:
        since = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
        # ERDDAP's tabledap CSV endpoint. Comma-separated; first two
        # rows are header + units. URL-encode the `>=` constraint.
        url = (
            f"{self.source_root_url}tabledap/{station['dataset_id']}.csv"
            f"?{','.join(ERDDAP_VARS)}"
            f"&time%3E={since.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        )
        r = self._polite_get(url)
        return _parse_erddap_csv(r.text)

    # ---- Per-day aggregation → observation dict -----------------------

    def _to_obs(self, station: dict, date_utc: str, day_rows: list[dict]) -> dict | None:
        ntus = [r["sea_water_turbidity"] for r in day_rows
                if r.get("sea_water_turbidity") is not None]
        ssts = [r["sea_water_temperature"] for r in day_rows
                if r.get("sea_water_temperature") is not None]
        if not ntus:
            # No turbidity reading → no Secchi → no scoreable obs.
            return None
        ntu_mean = mean(ntus)
        secchi_m = TURB_K / (max(ntu_mean, 0.0) + TURB_OFFSET)
        secchi_ft = secchi_m * M_TO_FT
        if not (SECCHI_FT_MIN <= secchi_ft <= SECCHI_FT_MAX):
            return None
        sst_f = (mean(ssts) * 9 / 5 + 32) if ssts else None

        # Timestamp the obs at noon UTC of the sample day — keeps the
        # validation joiner happy (it bucket-matches to model day boundaries).
        when = datetime.strptime(date_utc, "%Y-%m-%d").replace(
            hour=12, tzinfo=timezone.utc,
        )
        return {
            "obs_id":             self.make_obs_id(station["spot_name"], when=when),
            "timestamp_utc":      when.strftime("%Y-%m-%dT%H:%MZ"),
            "lat":                float(station["lat"]),
            "lng":                float(station["lng"]),
            "spot_name":          station["spot_name"],
            "observed_secchi_ft": round(secchi_ft, 1),
            "observed_sst_f":     round(sst_f, 1) if sst_f is not None else None,
            "observed_swell_ft":  None,
            "source":             self.source_id,
            "source_url":         f"{self.source_root_url}tabledap/{station['dataset_id']}.html",
            "source_confidence":  self.source_confidence,
            "extraction_method":  "erddap-turbidity-to-secchi",
            "raw_excerpt": (
                f"daily mean NTU={ntu_mean:.2f} → Secchi={secchi_m:.2f} m "
                f"(n={len(ntus)} samples; "
                f"K={TURB_K}, offset={TURB_OFFSET})"
            ),
            "notes": f"dataset_id={station['dataset_id']}",
        }


# ---- Module helpers ---------------------------------------------------

def _parse_erddap_csv(text: str) -> list[dict]:
    """ERDDAP tabledap CSV has two header rows: column names then
    units. Skip the units row and coerce numeric columns to float.
    Empty cells become None.
    """
    if not text or not text.strip():
        return []
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if len(rows) < 3:
        return []
    headers = rows[0]
    # rows[1] is the units row; skip it.
    out: list[dict] = []
    numeric_cols = {
        "latitude",
        "longitude",
        "sea_water_turbidity",
        "sea_water_temperature",
        "mass_concentration_of_chlorophyll_in_sea_water",
    }
    for raw in rows[2:]:
        if not raw:
            continue
        d: dict = {}
        for k, v in zip(headers, raw):
            v = (v or "").strip()
            if v == "" or v.upper() == "NAN":
                d[k] = None
                continue
            if k in numeric_cols:
                try:
                    d[k] = float(v)
                except ValueError:
                    d[k] = None
            else:
                d[k] = v
        out.append(d)
    return out


def _group_daily(rows: list[dict]) -> dict[str, list[dict]]:
    """Bucket rows by UTC date (YYYY-MM-DD) keyed off the `time` column.
    Rows with missing/bad timestamps are dropped silently — there's no
    sensible thing to do with them downstream.
    """
    out: dict[str, list[dict]] = {}
    for r in rows:
        dt = _parse_time(r.get("time"))
        if dt is None:
            continue
        date_key = dt.strftime("%Y-%m-%d")
        out.setdefault(date_key, []).append(r)
    return out


def _parse_time(t: str | None) -> datetime | None:
    """ERDDAP ISO time string -> aware UTC datetime, None if unparseable."""
    if not t:
        return None
    try:
        dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _latest_time(rows: list[dict]) -> datetime | None:
    times = [dt for dt in (_parse_time(r.get("time")) for r in rows) if dt]
    return max(times) if times else None


def _parse_coverage_end(text: str) -> datetime | None:
    """Pull NC_GLOBAL ``time_coverage_end`` out of an ERDDAP
    ``info/<id>/index.csv`` body (rows are Row Type, Variable Name,
    Attribute Name, Data Type, Value)."""
    for row in csv.reader(io.StringIO(text or "")):
        if (len(row) >= 5 and row[1] == "NC_GLOBAL"
                and row[2] == "time_coverage_end"):
            return _parse_time(row[4].strip())
    return None
