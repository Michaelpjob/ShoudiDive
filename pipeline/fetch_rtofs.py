"""Fetch NOAA RTOFS Global ocean-model forecast (SST + surface currents).

Phase-1 scope (2026-05-13):
  * Tropical region only — RTOFS adds the most value where HFRNet has
    zero HF-radar coverage (the entire Caribbean basin). For CA and PNW
    the existing persistence-decay SST forecast + HFRNet currents are
    fine first-order signals; layering RTOFS there is a stretch goal.
  * Sample 4 daily forecast leads: f024 (+1d), f072 (+3d), f120 (+5d),
    f168 (+7d). 24-hourly cadence is appropriate for ocean-model SST;
    higher cadence wastes bandwidth (each lead file is ~155 MB).
  * Output: parallel forecast track at `public/data/tropical/rtofs/`.
    Does NOT replace the existing persistence-decay forecast in
    `public/data/tropical/sst5d/`. Frontend wiring is intentionally
    deferred — this fetcher lands the data so we can validate values
    before deciding whether to blend, switch, or keep both.

Why this fetcher exists at all:
  * The audit on 2026-05-13 showed our SST forecast was running on
    pure persistence-decay against a baked-in-heatwave climatology.
    After the OISST swap (commit b02c8130) the baseline is correct,
    but persistence-decay is still a weak skill model — it has no
    advection, no wind forcing, no thermocline. NOAA RTOFS is the
    operational ocean model that includes all of that.
  * For tropical specifically, RTOFS currents fix the bigger gap:
    `fetch_currents.py` falls back to model-inference for Caribbean
    cells because HFRNet has no antennas south of FL east coast.
    RTOFS provides physics-based surface U/V there.

Access path:
  * NOMADS OPeNDAP was retired (SCN 25-81, 2025-Q4). The only
    surviving public path is NOMADS HTTPS NetCDF: `https://nomads.ncep.
    noaa.gov/pub/data/nccf/com/rtofs/prod/rtofs.YYYYMMDD/rtofs_glo_2ds_
    fHHH_prog.nc`. Each file is ~155 MB. We pull, open with xarray,
    subset the bbox in-memory, encode PNGs, discard the source file.
  * ~620 MB / day transfer for the 4 sample leads on tropical alone.
    GHA bandwidth is uncapped on inbound so this is fine. NCEP NOMADS
    will rate-limit if we hammer them, so we keep parallelism = 1.

Output schema:
  public/data/tropical/rtofs/
    sst_d{1,3,5,7}.png       — SST forecast (linear [20,32] °C, same
                               encoding as sst5d/ for downstream reuse)
    uv_d{1,3,5,7}.png        — surface currents RGBA
                               (R=u byte, G=v byte over [-2,2] m/s,
                                A=0 for land/NaN, B=0 reserved)
    summary.json             — { generated_at, init_cycle, leads:[...] }

Run: python pipeline/fetch_rtofs.py
"""
from __future__ import annotations

import json
import sys
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import xarray as xr
from PIL import Image

try:
    from pipeline.regions import active_region
    from pipeline.lib.http import http_get
except ModuleNotFoundError:
    from regions import active_region
    from lib.http import http_get

REGION = active_region()
BBOX = REGION.bbox

# 2026-05-13 Phase-2: expanded to all 3 regions after validating
# tropical (run 25808254550). RTOFS adds value in different ways
# per region:
#   * tropical — fills the HFRNet zero-coverage Caribbean current gap
#   * ca       — second opinion on persistence-decay, esp. during
#                upwelling events when SST diverges rapidly from
#                climatology
#   * pnw      — same, plus Salish Sea / outer-coast SST drift
# Cost: ~620 MB / region / day (4 sample leads × ~155 MB each).
# 3 regions = ~1.9 GB / day total. NCEP NOMADS is uncapped on
# inbound from GHA but we keep parallelism = 1 to be polite.
ENABLED_REGIONS = {"tropical", "ca", "pnw"}

# 24-hourly forecast samples. RTOFS 2ds product publishes hourly
# leads f000..f192; we deliberately skip the dense early hours
# (no value vs the existing persistence-decay) and sample every
# 48 h instead.
LEAD_HOURS = (24, 72, 120, 168)  # → d1, d3, d5, d7

# NOMADS path. The `prog` files (prognostic = SST + currents + SSH)
# are what we want; `diag` is heat-flux diagnostics, `ice` is sea-ice.
NOMADS_BASE = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/rtofs/prod"

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REGION.data_output_dir(ROOT) / "rtofs"
CACHE_DIR = ROOT / "pipeline" / ".cache" / "rtofs"

# Encoding ranges. SST: same as the existing per-region override so
# rtofs/sst PNGs decode with the same range fetch_sst_5day.py uses.
_overrides = REGION.layer_range_overrides
SST_RANGE = tuple(_overrides.get("sst5d", _overrides.get("sst", (9.0, 25.0))))

# Currents encoded as RGBA, mirroring the existing wind UV PNG
# convention (see fetch_wind.py): R=u byte, G=v byte over UV_RANGE,
# B=0 reserved, A=0 means NaN/land. Range chosen to cover realistic
# ocean surface currents (Gulf Stream cores hit ~2 m/s).
UV_RANGE = (-2.0, 2.0)


# Stage 6a (2026-05-24): per-file Session replaced with the shared
# lib/http.http_get path. Adds exponential-backoff retries on NOMADS
# transient 5xx — previously the streaming download silently
# fell back to "try prior cycle" on any first-attempt failure,
# costing the freshest RTOFS init cycle.


def _candidate_cycles(now: datetime) -> list[tuple[str, str]]:
    """Yield (YYYYMMDD, cycle) pairs to try, freshest first.

    RTOFS cycle init runs at 00z. Files typically land ~6-8 h after
    init, so a cron firing at 06:00 UTC may need to fall back to
    yesterday's 00z (which still covers our 7-day window — the
    f168 lead from yesterday's 00z lands at today + 6 days).
    """
    candidates = []
    for offset_days in range(0, 3):
        d = now - timedelta(days=offset_days)
        # 00z cycle only — RTOFS doesn't publish multiple per day.
        candidates.append((d.strftime("%Y%m%d"), "00z"))
    return candidates


def _file_url(yyyymmdd: str, lead_hours: int) -> str:
    return (
        f"{NOMADS_BASE}/rtofs.{yyyymmdd}/"
        f"rtofs_glo_2ds_f{lead_hours:03d}_prog.nc"
    )


def _fetch_lead(yyyymmdd: str, lead_hours: int) -> Path | None:
    """Download one rtofs_glo_2ds_fNNN_prog.nc into the local cache.

    Returns None on failure (404, network, etc.) — the caller decides
    whether to walk back to the prior cycle or skip the lead.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{yyyymmdd}_f{lead_hours:03d}_prog.nc"
    if cache_path.exists() and cache_path.stat().st_size > 1_000_000:
        return cache_path

    url = _file_url(yyyymmdd, lead_hours)
    print(f"  GET {url}", flush=True)
    try:
        r = http_get(url, timeout=300, stream=True)
        if r is None or r.status_code != 200:
            code = r.status_code if r is not None else "ERR"
            print(f"    HTTP {code} — try prior cycle")
            return None
        # Stream to disk to avoid holding the 155 MB blob in memory
        # twice (network buffer + write buffer).
        with open(cache_path, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                fh.write(chunk)
    except Exception as e:
        print(f"    {type(e).__name__}: {e}")
        return None
    if cache_path.stat().st_size < 1_000_000:
        # Truncated download — discard.
        cache_path.unlink(missing_ok=True)
        return None
    return cache_path


def _open_subset(nc_path: Path):
    """Open a NOMADS RTOFS 2ds prog NetCDF, subset to BBOX, return
    (sst_c, u_ms, v_ms) — all 2D numpy arrays oriented south-down
    (row 0 = lat_max). NaN where invalid.

    NOMADS RTOFS 2ds prog variable names (verified 2026-05-13):
      sst, sss, ssu, ssv, ssh   (sea-surface temp/sal/u/v/height)
    Some older builds used `temperature`, `u_velocity`, `v_velocity` —
    we introspect dynamically rather than hardcode.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ds = xr.open_dataset(nc_path)

    # Discover variable names. RTOFS 2ds prog convention is
    # 'sst', 'ssu', 'ssv' — but introspect to survive renames.
    def _pick(*names):
        for n in names:
            if n in ds.variables:
                return n
        return None

    var_sst = _pick("sst", "temperature", "water_temp")
    var_u   = _pick("ssu", "u_velocity", "water_u", "u")
    var_v   = _pick("ssv", "v_velocity", "water_v", "v")
    if var_sst is None or var_u is None or var_v is None:
        ds.close()
        raise RuntimeError(
            f"RTOFS prog NetCDF missing expected vars (found: "
            f"{list(ds.data_vars.keys())})"
        )

    # Resolve lat/lon coords. RTOFS Global 2ds files publish a
    # curvilinear grid: ``Latitude(Y, X)`` and ``Longitude(Y, X)`` are
    # 2D coordinate variables; the underlying dimensions are ``Y`` and
    # ``X``. Older NetCDFs in the same pipeline use 1D ``lat`` / ``lon``.
    # Handle both.
    lat_name = next(
        (n for n in ("Latitude", "latitude", "lat") if n in ds.variables),
        None,
    )
    lon_name = next(
        (n for n in ("Longitude", "longitude", "lon") if n in ds.variables),
        None,
    )
    if lat_name is None or lon_name is None:
        ds.close()
        raise RuntimeError(
            f"RTOFS: no lat/lon coord var found "
            f"(have: {list(ds.variables.keys())[:30]})"
        )

    lat_array = np.asarray(ds[lat_name].values)
    lon_array = np.asarray(ds[lon_name].values)

    # NOMADS RTOFS uses longitude in 0-360°. Our bbox is in -180/180°
    # (CA/PNW/tropical all sit west of 0°), so shift.
    lng_min_0360 = (BBOX["lng_min"] + 360.0) % 360.0
    lng_max_0360 = (BBOX["lng_max"] + 360.0) % 360.0
    if lng_min_0360 > lng_max_0360:
        ds.close()
        raise NotImplementedError(
            "RTOFS: dateline-crossing bbox not supported "
            f"(0360 min={lng_min_0360} > max={lng_max_0360})"
        )

    # Find the bounding (Y, X) rectangle that covers the bbox.
    # Curvilinear: walk the 2D coord arrays. Rectilinear: 1D coords
    # collapse to the same logic.
    if lat_array.ndim == 2:
        in_box = (
            (lat_array >= BBOX["lat_min"]) & (lat_array <= BBOX["lat_max"]) &
            (lon_array >= lng_min_0360) & (lon_array <= lng_max_0360)
        )
        if not in_box.any():
            ds.close()
            raise RuntimeError(
                f"RTOFS: bbox falls outside grid "
                f"(lat {lat_array.min():.2f}..{lat_array.max():.2f}, "
                f"lon {lon_array.min():.2f}..{lon_array.max():.2f})"
            )
        y_idx, x_idx = np.where(in_box)
        y0, y1 = int(y_idx.min()), int(y_idx.max()) + 1
        x0, x1 = int(x_idx.min()), int(x_idx.max()) + 1
    elif lat_array.ndim == 1:
        lat_mask = (lat_array >= BBOX["lat_min"]) & (lat_array <= BBOX["lat_max"])
        lon_mask = (lon_array >= lng_min_0360) & (lon_array <= lng_max_0360)
        if not lat_mask.any() or not lon_mask.any():
            ds.close()
            raise RuntimeError(
                f"RTOFS: bbox falls outside 1D grid "
                f"(lat {lat_array.min():.2f}..{lat_array.max():.2f})"
            )
        y_idx = np.where(lat_mask)[0]
        x_idx = np.where(lon_mask)[0]
        y0, y1 = int(y_idx[0]), int(y_idx[-1]) + 1
        x0, x1 = int(x_idx[0]), int(x_idx[-1]) + 1
    else:
        ds.close()
        raise RuntimeError(f"RTOFS: unexpected lat ndim={lat_array.ndim}")

    # Resolve the actual *dimension* names on the data variable
    # (not the coord variable). Last two dims are the spatial pair.
    var_dims = ds[var_sst].dims
    if len(var_dims) < 2:
        ds.close()
        raise RuntimeError(f"RTOFS: var {var_sst} has < 2 dims: {var_dims}")
    y_dim = var_dims[-2]
    x_dim = var_dims[-1]

    def _slice(var: str) -> np.ndarray:
        a = ds[var]
        # Strip leading time / MLev / depth singleton axes.
        while a.ndim > 2:
            a = a.isel({a.dims[0]: 0})
        sub = a.isel({y_dim: slice(y0, y1), x_dim: slice(x0, x1)})
        return np.asarray(sub.values, dtype=np.float32)

    sst = _slice(var_sst)
    u   = _slice(var_u)
    v   = _slice(var_v)

    # Some RTOFS builds ship SST in Kelvin despite the °C convention
    # — auto-detect.
    if np.nanmean(sst) > 100:
        sst = sst - 273.15

    # RTOFS Y goes south→north; PNG row 0 should be lat_max so the
    # frontend's existing decoder draws it the right way up. For 2D
    # coords, sample the corner of the subset rectangle to detect
    # orientation rather than relying on the 1D-only logic.
    if lat_array.ndim == 2:
        # Look at the lat value at (y0, x_mid) vs (y1-1, x_mid).
        x_mid = (x0 + x1 - 1) // 2
        if lat_array[y0, x_mid] < lat_array[y1 - 1, x_mid]:
            sst = sst[::-1, :]
            u   = u[::-1, :]
            v   = v[::-1, :]
    else:
        if lat_array[y_idx[0]] < lat_array[y_idx[-1]]:
            sst = sst[::-1, :]
            u   = u[::-1, :]
            v   = v[::-1, :]

    ds.close()
    return sst, u, v


def _encode_linear(arr: np.ndarray, lo: float, hi: float, out_path: Path):
    valid = np.isfinite(arr)
    px = np.zeros(arr.shape, dtype=np.uint8)
    scaled = (arr - lo) / (hi - lo)
    px[valid] = np.clip(np.round(scaled[valid] * 254 + 1), 1, 255).astype(np.uint8)
    Image.fromarray(px, mode="L").save(out_path, optimize=True)


def _encode_uv_rgba(u: np.ndarray, v: np.ndarray, lo: float, hi: float, out_path: Path):
    """RGBA encoding matching the wind UV PNG convention. A=0 means
    no-data / land (downstream decoder gates on alpha)."""
    h, w = u.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    valid = np.isfinite(u) & np.isfinite(v)
    span = hi - lo
    rgba[..., 0][valid] = np.clip(
        np.round((u[valid] - lo) / span * 255), 0, 255
    ).astype(np.uint8)
    rgba[..., 1][valid] = np.clip(
        np.round((v[valid] - lo) / span * 255), 0, 255
    ).astype(np.uint8)
    rgba[..., 3][valid] = 255
    Image.fromarray(rgba, mode="RGBA").save(out_path, optimize=True)


def main() -> None:
    region_name = REGION.name
    if region_name not in ENABLED_REGIONS:
        print(f"[rtofs] region={region_name} not in ENABLED_REGIONS — skip")
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    print(f"[rtofs] region={region_name}  bbox={BBOX}  leads={LEAD_HOURS}")

    # Pick the freshest cycle that has all requested leads available.
    chosen_cycle = None
    cycle_files: dict[int, Path] = {}
    for yyyymmdd, _cycle in _candidate_cycles(now):
        print(f"[rtofs] try cycle {yyyymmdd} 00z")
        cycle_files = {}
        ok = True
        for lead in LEAD_HOURS:
            f = _fetch_lead(yyyymmdd, lead)
            if f is None:
                print(f"  miss f{lead:03d} — trying prior cycle")
                ok = False
                break
            cycle_files[lead] = f
        if ok:
            chosen_cycle = yyyymmdd
            break

    if not chosen_cycle:
        print("[rtofs] no cycle had all 4 leads — aborting (data unchanged)")
        sys.exit(1)
    print(f"[rtofs] using cycle {chosen_cycle} 00z")

    days_summary = []
    for lead in LEAD_HOURS:
        nc_path = cycle_files[lead]
        try:
            sst, u, v = _open_subset(nc_path)
        except Exception as e:
            print(f"  f{lead:03d} subset failed: {e}")
            continue
        day_offset = lead // 24
        sst_url = f"/data/{REGION.data_dir_slug}/rtofs/sst_d{day_offset}.png"
        uv_url  = f"/data/{REGION.data_dir_slug}/rtofs/uv_d{day_offset}.png"
        sst_path = OUT_DIR / f"sst_d{day_offset}.png"
        uv_path  = OUT_DIR / f"uv_d{day_offset}.png"
        _encode_linear(sst, *SST_RANGE, sst_path)
        _encode_uv_rgba(u, v, *UV_RANGE, uv_path)

        sst_finite = sst[np.isfinite(sst)]
        speed = np.sqrt(u * u + v * v)
        speed_finite = speed[np.isfinite(speed)]
        days_summary.append({
            "day_offset": day_offset,
            "lead_hours": lead,
            "sst_url": sst_url,
            "uv_url": uv_url,
            "sst_mean_c": float(np.mean(sst_finite)) if sst_finite.size else None,
            "sst_min_c":  float(np.min(sst_finite))  if sst_finite.size else None,
            "sst_max_c":  float(np.max(sst_finite))  if sst_finite.size else None,
            "current_mean_ms": float(np.mean(speed_finite)) if speed_finite.size else None,
            "current_max_ms":  float(np.max(speed_finite))  if speed_finite.size else None,
            "grid": {"width": int(sst.shape[1]), "height": int(sst.shape[0])},
        })
        print(f"  f{lead:03d} → d{day_offset}: sst {sst_finite.min():.2f}–{sst_finite.max():.2f} °C; "
              f"|UV| ≤ {speed_finite.max():.2f} m/s")

    summary = {
        "generated_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "model": "NOAA_RTOFS_Global_2ds_prog",
        "init_cycle": f"{chosen_cycle}T00:00:00Z",
        "sst_range_c": list(SST_RANGE),
        "uv_range_ms": list(UV_RANGE),
        "unit_sst": "degC",
        "unit_uv": "m/s",
        "days": days_summary,
    }
    summary_path = OUT_DIR / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"[rtofs] wrote {summary_path}")

    if not days_summary:
        print("[rtofs] no successful leads — skipping manifest patch")
        return
    _patch_manifest(summary, summary_path, now)


def _patch_manifest(summary: dict, summary_path: Path, now: datetime) -> None:
    """Add / update the `rtofs5d` entry in this region's manifest.json.

    fetch.py runs BEFORE fetch_rtofs.py and writes the top-level
    manifest without an rtofs5d section. We patch it in afterwards so
    the frontend can discover the layer alongside `sst5d`. Idempotent —
    re-running just overwrites the entry.

    Schema lives next to existing forecast layers; the frontend
    decoder consumes the same fields as sst5d (range, scale, unit,
    summary_url) plus RTOFS-specific extras (uv_range, init_cycle,
    model).
    """
    manifest_path = REGION.data_output_dir(ROOT) / "manifest.json"
    if not manifest_path.exists():
        print(f"[rtofs] no manifest at {manifest_path} — skip patch")
        return

    try:
        manifest = json.loads(manifest_path.read_text())
    except Exception as e:
        print(f"[rtofs] manifest read failed ({e!s}) — skip patch")
        return

    if "layers" not in manifest or not isinstance(manifest["layers"], dict):
        manifest["layers"] = {}

    # Use the manifest-style URL (relative path starting at /data/).
    rel_summary_url = (
        "/" + str(summary_path.relative_to(ROOT / "public")).replace("\\", "/")
    )

    # Borrow grid + bbox from the first day's stats; all four leads
    # share the same subset rectangle.
    first = summary["days"][0]
    manifest["layers"]["rtofs5d"] = {
        "summary_url": rel_summary_url,
        "model": summary["model"],
        "init_cycle": summary["init_cycle"],
        "range": summary["sst_range_c"],   # SST range — matches sst5d for shared decoder
        "scale": "linear",
        "unit": summary["unit_sst"],
        "uv_range": summary["uv_range_ms"],
        "uv_unit": summary["unit_uv"],
        "grid": first["grid"],
        "horizon_days": max(d["day_offset"] for d in summary["days"]),
        "leads_day_offsets": [d["day_offset"] for d in summary["days"]],
        "generated_at": summary["generated_at"],
        "tz": "UTC",
        "beta": True,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"[rtofs] patched manifest.json with rtofs5d entry")


if __name__ == "__main__":
    main()
