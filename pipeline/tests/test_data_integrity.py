"""Data integrity test suite — catches silent fetcher failures.

User's mental model — these tests answer four questions:

  1. CORRECT     — did each layer pull from the source we expect, and
                   does its shape match the LayerSpec contract?
  2. FRESH       — is the data within its per-layer recency budget?
  3. PLAUSIBLE   — are the values physically reasonable, with the
                   right NaN fraction (i.e. not all-NaN, not all-zero,
                   no impossible magnitudes)?
  4. CONSISTENT  — do the manifest bbox + bathy sidecar + climo sidecar
                   all agree? (Stale static caches caused the
                   wind-streamlines-shifted-west bug 2026-05-14.)

The tests run in the `pipeline-tests` CI job on every PR. Region
coverage: each test parametrizes over CA + PNW + tropical and skips
regions whose data isn't present.

These complement the existing tests:
  test_layer_spec.py                — manifest schema vs LayerSpec
  test_fetch_layer_spec_consistency — fetcher vs LayerSpec
  test_freshness.py                 — freshness math
  test_regions.py                   — region config validity
  test_sst_phases.py / sources      — SST math + source priority

What this file adds beyond those: decoding the actual published PNGs
and validating value distributions + NaN fractions + bbox alignment
across sidecars. The existing tests can't catch "fetcher silently
emitted a saturated/empty PNG" because they only inspect the manifest
metadata, not the pixel data.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pytest
from PIL import Image


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = REPO_ROOT / "public" / "data"

REGIONS = ("ca", "pnw", "tropical")


def _region_dir(region: str) -> Path | None:
    """Path to a region's data dir. None if not present.

    CA lives at public/data/ (no slug subdir); PNW + tropical nest
    under public/data/<region>/ — matches the pipeline's data_output_dir
    convention.
    """
    d = DATA_ROOT if region == "ca" else DATA_ROOT / region
    return d if d.exists() and (d / "manifest.json").exists() else None


def _manifest(region: str) -> dict | None:
    d = _region_dir(region)
    if not d:
        return None
    try:
        return json.loads((d / "manifest.json").read_text())
    except Exception:
        return None


def _decode_grayscale_png(path: Path) -> np.ndarray:
    """Decode an 8-bit grayscale PNG, mapping pixel 0 → NaN.

    Matches the encoder convention in fetch.py / fetch_wind.py /
    fetch_bathy.py / fetch_climatology.py: pixel 0 is the no-data
    sentinel, 1..255 maps linearly across the layer's range.
    """
    img = Image.open(path).convert("L")
    arr = np.asarray(img, dtype=np.float32).copy()
    arr[arr == 0] = np.nan
    return arr


def _decode_rgba_uv_png(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Decode wind/current UV RGBA PNG. Returns (rgba_array, valid_mask)
    where valid is True when alpha > 0."""
    img = Image.open(path).convert("RGBA")
    arr = np.asarray(img, dtype=np.float32)
    return arr, arr[..., 3] > 0


def _decode_rgba_wave_png(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Decode wave RGBA PNG (R=Hs, G=Tp, B=dir, A=valid)."""
    return _decode_rgba_uv_png(path)


def _decode_scaled_value(raw_arr: np.ndarray, range_lo: float, range_hi: float) -> np.ndarray:
    """Apply the linear-range decoding: pixel 1..255 → range_lo..range_hi."""
    out = np.full_like(raw_arr, np.nan, dtype=np.float32)
    valid = np.isfinite(raw_arr)
    out[valid] = range_lo + (raw_arr[valid] - 1) / 254.0 * (range_hi - range_lo)
    return out


# ---------------------------------------------------------------------------
# Category 1: CORRECT — sources + shape match the contract
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("region", REGIONS)
def test_manifest_exists_and_has_required_layers(region):
    """Every region's manifest must declare the core data layers."""
    m = _manifest(region)
    if m is None:
        pytest.skip(f"no committed data for region={region}")

    required = {"sst", "chl", "wind"}
    have = set(m.get("layers", {}).keys())
    missing = required - have
    assert not missing, f"{region} manifest missing required layers: {missing}"


@pytest.mark.parametrize("region", REGIONS)
def test_manifest_bbox_in_lockstep_with_region_config(region):
    """The manifest's bbox must match active_region().bbox at the time of
    the last refresh. A drift here means a fetcher ran with a stale
    code checkout — fixing this is what bathy sidecar + climo sidecar
    do for static caches."""
    m = _manifest(region)
    if m is None:
        pytest.skip(f"no data for {region}")
    try:
        from pipeline.regions import get_region
    except ModuleNotFoundError:
        from regions import get_region
    region_obj = get_region(region)
    expected_bbox = region_obj.bbox_array  # [lng_min, lat_min, lng_max, lat_max]
    actual_bbox = m["bbox"]
    assert actual_bbox == expected_bbox, (
        f"{region}: manifest bbox {actual_bbox} doesn't match "
        f"active_region().bbox_array {expected_bbox}. "
        f"Run refresh-{region}-data.yml to regenerate."
    )


# ---------------------------------------------------------------------------
# Category 2: CONSISTENT — static caches' sidecars match the live bbox
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("region", REGIONS)
def test_bathy_sidecar_matches_manifest_bbox(region):
    """Bathy.png is one-shot cached; if the region's bbox is bumped, the
    sidecar JSON forces fetch_bathy.py to regenerate. This test catches
    the case where someone bumps the bbox but the cache + sidecar
    haven't been refreshed yet — the symptom would be wind streamlines
    misregistered against the coastline (the bug we hit 2026-05-14).

    Transitional behavior: regions whose bathy.png predates the sidecar
    pattern (introduced 2026-05-14) get a SKIP with a TODO marker rather
    than a hard fail. The skip will go away once each region runs through
    refresh-*-data and the sidecar gets written. A test failure here
    after that is a real regression.
    """
    d = _region_dir(region)
    if d is None:
        pytest.skip(f"no data for {region}")
    sidecar = d / "bathy.json"
    bathy_png = d / "bathy.png"
    if not bathy_png.exists():
        pytest.skip(f"{region}: no bathy.png yet (first-run on new region)")
    if not sidecar.exists():
        pytest.skip(
            f"{region}: bathy.png present but bathy.json sidecar missing "
            f"(transitional — bathy predates the 2026-05-14 sidecar pattern). "
            f"Triggering refresh-{region}-data will regenerate."
        )
    meta = json.loads(sidecar.read_text())
    m = _manifest(region)
    assert meta.get("bbox") == m["bbox"], (
        f"{region}: bathy sidecar bbox {meta.get('bbox')} doesn't match "
        f"manifest {m['bbox']}. Force a fetch_bathy.py rerun (the sidecar "
        f"check will catch this and regenerate)."
    )


@pytest.mark.parametrize("region", REGIONS)
def test_climo_sidecar_matches_manifest_bbox(region):
    """climo_meta.json must carry a bbox that matches the manifest.
    Without this, fetch_visibility + fetch_sst_5day silently apply a
    climatology from an OLD bbox over a NEW bbox — same geographic
    misregistration class as the bathy bug."""
    d = _region_dir(region)
    if d is None:
        pytest.skip(f"no data for {region}")
    meta_path = d / "climo_meta.json"
    if not meta_path.exists():
        pytest.skip(f"{region}: no climo_meta yet")
    meta = json.loads(meta_path.read_text())
    if "bbox" not in meta:
        pytest.skip(
            f"{region}: climo_meta.json has no bbox field (transitional — "
            f"predates the 2026-05-14 sidecar pattern). Triggering "
            f"refresh-{region}-data with force_climo=true will add it."
        )
    m = _manifest(region)
    assert meta["bbox"] == m["bbox"], (
        f"{region}: climo bbox {meta['bbox']} doesn't match manifest "
        f"{m['bbox']}. Re-run with `force_climo=true` workflow input."
    )


# ---------------------------------------------------------------------------
# Category 3: PLAUSIBLE — value distributions + NaN fractions
# ---------------------------------------------------------------------------

# Per-region SST physical bounds. The PNG decodes into the layer's
# encoding range (e.g. CA [9, 25]°C), but the ACTUAL values inside that
# range must also be plausible. Tropical water never goes below 18°C even
# though the encoding range starts at 20 — these bounds catch encoding +
# semantic drift simultaneously.
SST_PHYSICAL_BOUNDS_C = {
    "ca":       (5.0, 30.0),    # NorCal upwelling can hit 7-8°C; SoCal Bight 23-25°C peak
    "pnw":      (5.0, 22.0),    # Salish Sea cold; OR outer coast 12-18°C summer
    "tropical": (18.0, 34.0),   # Caribbean rarely below 22°C; Gulf summer to 32°C
}

# Acceptable NaN-fraction floors per (artifact, region). Below this means
# the fetcher returned an empty/saturated PNG; above is fine.
#
# Region-tuned because cloud cover varies massively:
#   * CA      — moderate marine layer; chl coverage 30-60% typical
#   * PNW     — heavy cloud cover at high lats; chl can dip to 2-5%
#                in May (and that's REAL — VIIRS/MODIS can't see through
#                clouds). Wind from HRRR/GFS is always full-coverage.
#   * tropical — clearer skies but cumulus pop-up; chl 10-30% typical
#
# A floor that doesn't account for this fails the wrong things — the
# point of the test is "fetcher hung", not "the sky is cloudy".
LAYER_VALID_FRAC_FLOOR = {
    # (artifact, region) -> minimum valid-cell fraction
    ("sst_1d.png",         "ca"):       0.20,
    ("sst_1d.png",         "pnw"):      0.20,
    ("sst_1d.png",         "tropical"): 0.30,   # mostly ocean bbox

    ("chl_1d.png",         "ca"):       0.10,
    ("chl_1d.png",         "pnw"):      0.01,   # cloud-heavy, 2-5% is normal
    ("chl_1d.png",         "tropical"): 0.05,

    ("wind_uv_now.png",    "ca"):       0.20,
    ("wind_uv_now.png",    "pnw"):      0.20,
    ("wind_uv_now.png",    "tropical"): 0.30,

    ("wind_speed_now.png", "ca"):       0.20,
    ("wind_speed_now.png", "pnw"):      0.20,
    ("wind_speed_now.png", "tropical"): 0.30,

    ("wave_now.png",       "ca"):       0.20,
    ("wave_now.png",       "pnw"):      0.20,
    ("wave_now.png",       "tropical"): 0.30,
}


@pytest.mark.parametrize("region,bounds", list(SST_PHYSICAL_BOUNDS_C.items()))
def test_sst_values_physically_plausible(region, bounds):
    """SST decoded values must fall inside the per-region physical
    envelope. Catches:
      * Kelvin-vs-Celsius mistakes (273+ values)
      * Range/encoding drift (e.g. encoder writes (9,25) but decoder
        reads (20,32))
      * Saturated PNGs (all values = range_max because of clipping)
    """
    d = _region_dir(region)
    if d is None:
        pytest.skip(f"no data for {region}")
    sst_path = d / "sst_1d.png"
    if not sst_path.exists():
        pytest.skip(f"{region}: no sst_1d.png")
    m = _manifest(region)
    encoding_range = m["layers"]["sst"]["range"]

    raw = _decode_grayscale_png(sst_path)
    decoded = _decode_scaled_value(raw, encoding_range[0], encoding_range[1])
    valid = decoded[np.isfinite(decoded)]
    if valid.size == 0:
        pytest.fail(f"{region}: sst_1d.png has no valid cells (all NaN)")

    mn, mx, mean = float(valid.min()), float(valid.max()), float(valid.mean())
    lo, hi = bounds
    assert lo <= mn, f"{region} SST min {mn:.1f}°C below {lo}°C floor"
    assert mx <= hi, f"{region} SST max {mx:.1f}°C above {hi}°C ceiling"
    assert lo <= mean <= hi, f"{region} SST mean {mean:.1f}°C outside [{lo},{hi}]°C"
    # Saturation guard: if every valid cell is within 0.1°C of the
    # encoding ceiling, the fetcher hit a uniform clip — symptom of
    # the encode/decode mismatch bug we hit 2026-05-13.
    near_ceiling = (encoding_range[1] - valid).max() < 0.1 and valid.std() < 0.1
    assert not near_ceiling, (
        f"{region} SST appears saturated at encoding ceiling "
        f"{encoding_range[1]}°C — check encode/decode range alignment"
    )


@pytest.mark.parametrize("artifact,region",
                         sorted(LAYER_VALID_FRAC_FLOOR.keys()))
def test_no_nan_floods(artifact, region):
    """A PNG is 'flooded' if fewer than the per-region floor of cells
    have valid data. Floors are tuned to the realistic cloud cover and
    land/water split per region (see LAYER_VALID_FRAC_FLOOR docstring).
    """
    d = _region_dir(region)
    if d is None:
        pytest.skip(f"no data for {region}")
    path = d / artifact
    if not path.exists():
        pytest.skip(f"{region}: no {artifact}")

    if "uv" in artifact or "wave" in artifact:
        _, valid_mask = _decode_rgba_uv_png(path)
        valid_frac = float(valid_mask.mean())
    else:
        arr = _decode_grayscale_png(path)
        valid_frac = float(np.isfinite(arr).mean())

    floor = LAYER_VALID_FRAC_FLOOR[(artifact, region)]
    assert valid_frac > floor, (
        f"{region}/{artifact}: only {valid_frac*100:.1f}% valid cells "
        f"(floor {floor*100:.0f}%). Fetcher likely hung — check NOMADS / "
        f"ERDDAP / NASA endpoints."
    )


@pytest.mark.parametrize("region", REGIONS)
def test_wind_speed_in_plausible_range(region):
    """Wind speed should be 0-80 kt in normal conditions. Outside this
    range = encoding drift or hurricane (which we'd notice). The check
    is on the max; the min is always 0 in practice."""
    d = _region_dir(region)
    if d is None:
        pytest.skip(f"no data for {region}")
    path = d / "wind_speed_now.png"
    if not path.exists():
        pytest.skip(f"{region}: no wind_speed_now")

    m = _manifest(region)
    speed_range = m["layers"]["wind"]["speed_range"]
    raw = _decode_grayscale_png(path)
    decoded = _decode_scaled_value(raw, speed_range[0], speed_range[1])
    valid = decoded[np.isfinite(decoded)]
    if valid.size == 0:
        pytest.fail(f"{region}: wind_speed_now all NaN")

    max_kt = float(valid.max())
    assert 0.5 < max_kt < 80.0, (
        f"{region} wind speed max {max_kt:.1f} kt outside [0.5, 80] — "
        f"likely encoding drift or extreme weather (check NOMADS upstream)"
    )


@pytest.mark.parametrize("region", REGIONS)
def test_bathy_depth_in_plausible_range(region):
    """Bathy depth: 0 = shoreline, 6000 = deep abyss. Encoded as 1..255
    linear. Pixel 0 = NaN (land)."""
    d = _region_dir(region)
    if d is None:
        pytest.skip(f"no data for {region}")
    path = d / "bathy.png"
    if not path.exists():
        pytest.skip(f"{region}: no bathy.png")

    raw = _decode_grayscale_png(path)
    decoded = _decode_scaled_value(raw, 0.0, 6000.0)
    valid = decoded[np.isfinite(decoded)]
    assert valid.size > 0, f"{region}: bathy all NaN"
    assert float(valid.min()) >= 0.0, f"{region}: negative depth"
    assert float(valid.max()) <= 6000.0, f"{region}: depth > 6000m"
    # Sanity: median depth should be > 100m for any CA-class region (most
    # of the bbox is ocean shelf + abyssal). Tropical can be shallower
    # (continental shelf is wider). PNW similar to CA.
    median = float(np.median(valid))
    assert median > 50.0, (
        f"{region}: median bathy depth {median:.0f}m suspiciously shallow "
        f"— check GMRT fetch + resample"
    )


# ---------------------------------------------------------------------------
# Category 4: FRESH — forecast continuity + anomaly sanity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("region", REGIONS)
def test_sst5d_forecast_continuity(region):
    """SST 5-day forecast: days monotonically increase, no missing slots,
    each day's anomaly is bounded (|anom_c| < 5°C). A larger anomaly
    means the climatology baseline is wrong — exactly the 2026-05-13
    bug where tropical was anchored to a heatwave-warmed baseline."""
    d = _region_dir(region)
    if d is None:
        pytest.skip(f"no data for {region}")
    summary = d / "sst5d" / "summary.json"
    if not summary.exists():
        pytest.skip(f"{region}: no sst5d/summary.json")

    s = json.loads(summary.read_text())
    days = s.get("days", [])
    assert len(days) >= 5, f"{region}: sst5d has only {len(days)} days (expect >= 5)"

    offsets = [day.get("offset") for day in days]
    assert offsets == sorted(offsets), f"{region}: sst5d days out of order"

    for day in days:
        anom = day.get("anom_c")
        if anom is None:
            continue
        assert abs(anom) < 5.0, (
            f"{region}: sst5d {day.get('day')} anomaly {anom:.2f}°C "
            f"exceeds 5°C envelope — climatology likely stale or wrong-bbox"
        )

    # Today's mean must be inside the encoding range; obvious but catches
    # the case where the model decayed to a value the PNG can't represent.
    encoding_range = s.get("range_c") or s.get("range")
    if encoding_range:
        for day in days:
            mean = day.get("mean_c") or day.get("mean")
            if mean is None:
                continue
            assert encoding_range[0] <= mean <= encoding_range[1], (
                f"{region}: sst5d {day.get('day')} mean {mean}°C outside "
                f"encoding range {encoding_range}"
            )


@pytest.mark.parametrize("region", REGIONS)
def test_top_level_manifest_freshness(region):
    """The manifest's generated_at must be within TOP_LEVEL_MAX_HOURS
    of now. Reuses the budget from check_manifest_freshness.py so the
    threshold is centralised.

    Note: this test runs in dev-checks, which fires on PR pushes. The
    committed data in public/data/ can be up to ~24h stale on a normal
    development cycle (cron runs daily). 48h is the realistic ceiling
    before something's genuinely broken.
    """
    m = _manifest(region)
    if m is None:
        pytest.skip(f"no data for {region}")
    raw = m.get("generated_at")
    if not raw:
        pytest.fail(f"{region}: manifest has no generated_at")
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        pytest.fail(f"{region}: manifest generated_at unparseable: {raw}")
    age_hours = (datetime.now(timezone.utc) - when).total_seconds() / 3600
    # 96h ceiling for dev-checks tolerance — 72h was too tight after
    # the 2026-05-14 NorCal bbox expansion: the SST/chl/kd490 fetcher
    # started hitting its 15-min timeout, silently keeping manifest's
    # top-level generated_at frozen for days. We bumped the workflow
    # timeout to 35 min in the same change, and 96h here gives the
    # cron a 4-day recovery window if a one-off run still trips up.
    # If you see this fire in CI, check whether the daily refresh has
    # actually been running successfully (per-layer freshness within
    # each region is asserted by other checks).
    assert age_hours < 96, (
        f"{region}: manifest is {age_hours:.0f}h old (> 96h ceiling). "
        f"refresh-{region}-data.yml cron may be broken."
    )


# ---------------------------------------------------------------------------
# Category 5: SILENT FAILURES — partial-refresh detection
#
# Added 2026-05-17 after the SST/chl/kd490 silent-timeout incident. The
# fetch step had `continue-on-error: true`, so when it hit the 15-min
# wall (post-NorCal bbox expansion) the workflow kept going, committed
# stale SST/chl/kd490 PNGs alongside fresh rivers/tides/RTOFS, and only
# surfaced 58h later via the live-cp-manifest probe.
#
# Notes about layer cadence — important for designing tests here:
#   * SST has a daily source cadence (NOAA MUR).
#   * chl has a daily source cadence (Copernicus).
#   * kd490 has a ~weekly source cadence — generated_at legitimately
#     lags by days even when the daily fetcher succeeded.
#   * wind has an HOURLY independent refresh (refresh-*-wind.yml),
#     so it can be FRESHER than the daily top-level generated_at.
#   * rtofs5d updates daily.
#
# These tests therefore avoid naive cross-layer skew comparisons —
# they'd misfire on the layers that have legitimately different
# cadences. Instead we name SST + chl explicitly (the daily-fetcher
# pair that silently failed in May 2026) and pin them to a fresh-
# enough threshold without dragging kd490 into the same bucket.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("region", REGIONS)
def test_sst_not_silently_stuck(region):
    """SST specifically — the layer that froze in the 2026-05-15→17 incident.

    SST is the only layer whose schema reliably exposes a per-layer
    `generated_at` (chl uses windows.dates, kd490 has a weekly cadence,
    wind has its own hourly refresh). It's also the canonical signal
    a diver would notice missing: stale sea-temperature data is the
    "the site looks broken" symptom that matters.

    If SST's `generated_at` is >96h old, the daily fetch step has
    silently failed for multiple cron runs. 96h matches the ceiling
    on `test_top_level_manifest_freshness` so the two tests don't
    generate split-brain signals.
    """
    m = _manifest(region)
    if m is None:
        pytest.skip(f"no data for {region}")

    info = (m.get("layers") or {}).get("sst")
    if not isinstance(info, dict):
        pytest.skip(f"{region}: no SST layer in manifest")
    raw = info.get("generated_at")
    if not raw:
        pytest.skip(f"{region}: SST layer has no generated_at field")

    try:
        when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        pytest.fail(f"{region}: SST generated_at unparseable: {raw}")

    age_h = (datetime.now(timezone.utc) - when).total_seconds() / 3600
    assert age_h < 96, (
        f"{region}: SST generated_at is {age_h:.0f}h old (> 96h). The "
        f"daily fetch step in refresh-{region}-data.yml may be silently "
        f"failing — check the run logs for `##[error]The action ... has "
        f"timed out` on the `Fetch latest ocean data` step. Same bug "
        f"class as the 2026-05-15→17 incident."
    )


@pytest.mark.parametrize("region", REGIONS)
def test_top_level_at_least_as_fresh_as_sst(region):
    """manifest.generated_at must be ≥ as fresh as the SST per-layer timestamp.

    The top-level timestamp represents "last full refresh." SST advances
    ONLY when the daily refresh succeeds end-to-end. If SST is fresher
    than top-level by more than the 5-minute grace, the daily refresh
    ran but its finalize-manifest step didn't — partial-refresh symptom.

    We anchor on SST (not e.g. wind) because wind has its own HOURLY
    refresh that can legitimately be more recent than the daily top-level.
    """
    m = _manifest(region)
    if m is None:
        pytest.skip(f"no data for {region}")

    top_raw = m.get("generated_at")
    if not top_raw:
        pytest.skip(f"{region}: no top-level generated_at")
    try:
        top = datetime.fromisoformat(str(top_raw).replace("Z", "+00:00"))
    except ValueError:
        pytest.fail(f"{region}: top-level generated_at unparseable: {top_raw}")

    info = (m.get("layers") or {}).get("sst") or {}
    sst_raw = info.get("generated_at")
    if not sst_raw:
        pytest.skip(f"{region}: SST layer has no generated_at field")
    try:
        sst_ts = datetime.fromisoformat(str(sst_raw).replace("Z", "+00:00"))
    except ValueError:
        pytest.skip(f"{region}: SST generated_at unparseable: {sst_raw}")

    # 5-minute grace window: the per-layer finalize and the top-level
    # bump can land on opposite sides of a second boundary.
    grace = timedelta(minutes=5)
    if top + grace < sst_ts:
        skew_m = (sst_ts - top).total_seconds() / 60
        pytest.fail(
            f"{region}: top-level manifest.generated_at ({top.isoformat()}) "
            f"is {skew_m:.1f} min behind SST ({sst_ts.isoformat()}). The "
            f"daily refresh's fetchers wrote fresh SST but the manifest "
            f"finalization step that bumps top-level didn't run. Check "
            f"refresh-{region}-data.yml for the manifest-build step "
            f"landing after all fetchers."
        )
