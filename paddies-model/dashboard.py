"""Build ONE unified Kelp-Paddy Finder — opportunity zones (kelp x
convergence x temp break) + kelp drift cones for all scenarios + a launch
selector, in a single self-contained page, fed off a single download.

    python dashboard.py

Outputs:
  out/index.html   — interactive: Live / +Swell / +Warm tabs + launch picker
  out/overview.png — static one-view comparison of all three
  out/zones_all.json — ranked opportunity zones per scenario
"""
import datetime as dt
import json
import os
import subprocess
import time

import config
import beds as beds_mod
import forcing as forcing_mod
import fusion as fusion_mod
import detachment as detach_mod
import geo
import features
import reports as reports_mod
import drift as drift_mod
import findability as find_mod
import cones as cones_mod
import convergence as conv_mod
import render as render_mod
from landmask import LandMask

_META_KEYS = ("est_floating_paddies", "abundance_band", "frac_floating",
              "frac_beached", "frac_sunk", "why", "scenario")


def _model_git_sha():
    try:
        cwd = os.path.dirname(os.path.abspath(__file__))
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=cwd, text=True, stderr=subprocess.DEVNULL).strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=cwd, text=True, stderr=subprocess.DEVNULL).strip()
        return sha + ("-dirty" if dirty else "")  # flag uncommitted builds so provenance can't silently go stale
    except Exception:
        return None


def _rel_label(off):
    if off == 0:
        return "now"
    if off < 0:
        return f"{-off} day{'s' if off < -1 else ''} ago"
    return f"+{off} day{'s' if off > 1 else ''}"


def _fetch_reports():
    """Pull the live crowd catch-reports to PROMOTE into the opportunity field
    (the literature's #1 gap: a verified recent catch is ground truth). Returns
    [] on any failure so the model degrades cleanly to physics-only."""
    try:
        import requests
        r = requests.get("https://shouldidive.com/api/paddies/reports", timeout=20)
        r.raise_for_status()
        return [x for x in r.json().get("reports", []) if "lat" in x and "lng" in x and "date" in x]
    except Exception as e:
        print(f"  reports fetch failed ({type(e).__name__}); opportunity stays physics-only")
        return []


def build():
    """Render the paddy field at each "as-of" day in config.TIMELINE_OFFSETS_DAYS
    off ONE forcing fetch (re-running the drift to each frame's target time).
    Past frames = observed (HFRNet+Open-Meteo); future = forecast. Returns
    (data, overview_items, zones_all)."""
    print("== Building Kelp-Paddy Finder timeline (per-day frames) ==")
    build_utc = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    raw = forcing_mod.fetch_raw()
    hfr = fusion_mod.prepare(fusion_mod.fetch_hfr(), raw["t0"])
    landmask = LandMask()
    forcing = forcing_mod.make_forcing(raw, "live", hfr=hfr)
    thermal = forcing_mod.fetch_thermal_history()   # 6-wk SST -> cumulative warm-shed dose
    now_h = forcing.now_hours()
    _bedll = {b[0]: (b[1], b[2]) for b in beds_mod.SCB_BEDS}
    # Catch reports are now crowdsourced live from /api/paddies/reports (the tool
    # fetches them at load); they're no longer baked into data.json.

    pos_hfr = round(max((hfr["step"] * 111.0 if hfr is not None else 24.0) / 1.852, config.POS_FLOOR_NM))
    pos_coarse = round(max(config.GRID_STEP_DEG * 111.0 / 1.852, config.POS_FLOOR_NM))

    reports = _fetch_reports()
    print(f"  promoting {len(reports)} catch reports into the opportunity field")
    model_meta = {
        "build_utc": build_utc,
        "forcing_t0_utc": raw.get("base_meta", {}).get("t0_utc", raw["t0"].isoformat() + "Z"),
        "window_days": raw.get("base_meta", {}).get("window_days"),
        "hfr_coverage": round(float(hfr["coverage"]), 3) if hfr is not None else None,
        "model_git_sha": _model_git_sha(),
        "report_count": len(reports),
        # Fraction of the bbox the land mask covers. 0.0 means land.geojson
        # failed to load -> no water-clipping + no beaching (green renders on
        # land). build_site.py fails loud on this when PADDIES_REQUIRE_REAL_KELP.
        "landmask_cov": round(float(landmask.coverage), 4),
    }
    frames, overview_items, beds_fc = [], [], None
    for off in config.TIMELINE_OFFSETS_DAYS:
        T = now_h + off * 24.0
        det = detach_mod.compute(forcing, beds_mod.SCB_BEDS, T, config.RELEASE_AGES_DAYS,
                                  thermal=thermal)
        result = drift_mod.run_drift(forcing, landmask, det, now_h=T)
        dens = find_mod.build(result["floating"], landmask)
        cone_feats, _ = cones_mod.build(result["floating"], beds_mod.SCB_BEDS)
        fdt = raw["t0"] + dt.timedelta(hours=T)
        # Reports now PROMOTED into the field (recency + distance decayed, no
        # lookahead past this frame's as-of date) -- the literature's #1 signal.
        opp = conv_mod.build_opportunity(forcing, dens, landmask, reports=reports, as_of_dt=fdt)
        hdr = conv_mod.hdr(opp)
        m = result["meta"]
        fl = result["floating"]
        dsts = sorted(geo.haversine_km(_bedll[p["bed"]][0], _bedll[p["bed"]][1], p["lng"], p["lat"]) for p in fl)
        brs = [geo.bearing_deg(_bedll[p["bed"]][0], _bedll[p["bed"]][1], p["lng"], p["lat"]) for p in fl]
        meta = {k: m[k] for k in _META_KEYS}
        meta["peak_hs_m"] = det["peak_hs_m"]
        meta["drift_nm"] = round(dsts[len(dsts) // 2] / 1.852, 1) if dsts else 0
        meta["drift_comp"] = geo.compass(geo.circular_mean_deg(brs)) if brs else "?"
        meta["area50_km2"] = next((r["area_km2"] for r in hdr["regions"] if 0.45 <= r["level"] <= 0.55), 0)
        meta["core_union_km2"] = next((r["area_km2"] for r in hdr["regions"] if r["level"] <= 0.35), 0)
        meta["core_area_km2"] = hdr.get("core_primary_km2", meta["core_union_km2"])
        meta["n_patches"] = hdr.get("n_core_patches", 1)
        meta["primary_frac"] = hdr.get("primary_frac", 1.0)
        meta["current_src"] = opp.get("current_src", "coarse")
        meta["pos_pm_nm"] = pos_hfr if (off <= 0 and meta["current_src"] == "hfr") else pos_coarse
        meta["diffuse"] = ((not hdr.get("peak")) or meta["core_area_km2"] > config.DIFFUSE_AREA_KM2
                           or meta["primary_frac"] < 0.33)
        meta["feature"] = (features.describe(hdr["peak"]["lat"], hdr["peak"]["lng"],
                                             config.FEATURE_SNAP_MAX_NM) if hdr.get("peak") else None)
        meta["offset_days"] = off
        meta["confidence"] = "hindcast" if off <= 0 else "forecast"
        meta["rel"] = _rel_label(off)
        meta["date"] = fdt.strftime("%b ") + str(fdt.day)
        meta.update(model_meta)
        frames.append({"hdr": hdr, "cones": cone_feats, "timeline": det["timeline"], "meta": meta,
                       "offset_days": off})
        print(f"  {meta['rel']:>10s} ({meta['confidence']:8s}): ~{m['est_floating_paddies']:,} floating "
              f"| core {meta['core_area_km2']} km2 | {meta['feature']}")
        if off in (-3, 0, 2):
            overview_items.append({"meta": dict(m, rel=meta["rel"]), "cones": cone_feats, "hdr": hdr})
        if beds_fc is None:
            beds_fc = result["beds"]

    b = config.FIELD_BBOX
    cur_note = (f"Currents: HFRNet 6 km radar ({hfr['coverage']*100:.0f}% obs coverage) + Open-Meteo fill"
                if hfr is not None else "Currents: Open-Meteo ~33 km model (HFR unavailable)")
    data = {
        "bounds": [[b["lat_min"], b["lng_min"]], [b["lat_max"], b["lng_max"]]],
        "launches": {k: [v[0], v[1]] for k, v in config.LAUNCHES.items()},
        "default_launch": config.DEFAULT_LAUNCH,
        "frames": frames,
        "default_frame": (config.TIMELINE_OFFSETS_DAYS.index(0)
                          if 0 in config.TIMELINE_OFFSETS_DAYS else 0),
        "beds": {"type": "FeatureCollection", "features": beds_fc},
        "reports": [],
        "model_meta": model_meta,
        "current_note": cur_note,
        "src_note": "Monte-Carlo drift model: past frames use observed HFRNet currents plus Open-Meteo hindcast inputs; "
                    "future frames are forecast. Green = model-estimated 30/50/80% paddy-likelihood regions. "
                    "Red dots = reviewed community catch reports.",
    }
    return data, overview_items, {}


def main():
    t0 = time.time()
    data, overview_items, zones_all = build()
    os.makedirs("out", exist_ok=True)
    render_mod.write_dashboard(data, "out")
    render_mod.write_overview(overview_items, config.LAUNCHES[config.DEFAULT_LAUNCH],
                              config.DEFAULT_LAUNCH, "out")
    with open(os.path.join("out", "zones_all.json"), "w") as f:
        json.dump(zones_all, f, indent=2)

    print(f"  wrote out/index.html + out/overview.png + out/zones_all.json | "
          f"done in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
