"""One-fetch analysis for the four follow-up items:
  1 count anchor  -> report float_amount -> est mapping
  2 cross-val     -> leave-one-out (held-out) report skill, non-circular
  3 smoothing     -> opp gaussian-smooth sigma -> patch count / breadth
"""
import datetime as dt
import numpy as np
from scipy.ndimage import gaussian_filter

import config, forcing as F, fusion, drift, findability as find, convergence as conv
import beds as B, detachment as D, dashboard
from landmask import LandMask

raw = F.fetch_raw(); hfr = fusion.prepare(fusion.fetch_hfr(), raw["t0"]); lm = LandMask()
fo = F.make_forcing(raw, "live", hfr=hfr); now = fo.now_hours()
det = D.compute(fo, B.SCB_BEDS, now, config.RELEASE_AGES_DAYS)
res = drift.run_drift(fo, lm, det, now_h=now); dens = find.build(res["floating"], lm)
m = res["meta"]
print(f"\n=== ITEM 1 (count) === float_amount={m['float_amount']} -> est_floating={m['est_floating_paddies']:,}")
print(f"   anchors: HOBDAY={config.HOBDAY_DENSITY_PER_KM2}/km2 x FISHABLE={config.FISHABLE_AREA_KM2}km2 x min(3, fa/FLOAT_AMOUNT_REF={config.FLOAT_AMOUNT_REF})")
print(f"   at reference fa={config.FLOAT_AMOUNT_REF}: est would be {config.HOBDAY_DENSITY_PER_KM2*config.FISHABLE_AREA_KM2:,} (Hobday-high)")

reports = dashboard._fetch_reports(); fdt = raw["t0"] + dt.timedelta(hours=now)
print(f"reports: {len(reports)}")
opp_base = conv.build_opportunity(fo, dens, lm, reports=reports, as_of_dt=fdt)
lats, lngs = opp_base["lats"], opp_base["lngs"]

print("\n=== ITEM 3 (smoothing) === sigma -> patches / core / 0.8reg / primary%")
for sig in (0.0, 1.0, 1.5, 2.0, 2.5):
    g = opp_base["opp"] if sig == 0 else gaussian_filter(opp_base["opp"], sig)
    hdr = conv.hdr({"lats": lats, "lngs": lngs, "opp": g})
    reg = {r["level"]: r["area_km2"] for r in hdr["regions"]}
    print(f"  sigma {sig}: patches {hdr.get('n_core_patches'):>3} | core {reg.get(0.3,0):>5} | 0.8 {reg.get(0.8,0):>6} | prim {int((hdr.get('primary_frac') or 0)*100):>3}%")


def pctile(opp, r):
    g = opp["opp"]; flat = g[g > 0]
    j = int(np.argmin(np.abs(lats - r["lat"]))); i = int(np.argmin(np.abs(lngs - r["lng"])))
    x = g[j, i]
    return float((flat < x).mean() + 0.5 * (flat == x).mean())


loo = []
for k, r in enumerate(reports):
    others = [x for j, x in enumerate(reports) if j != k]
    opp_loo = conv.build_opportunity(fo, dens, lm, reports=others, as_of_dt=fdt)
    loo.append(pctile(opp_loo, r))
allp = [pctile(opp_base, r) for r in reports]
print(f"\n=== ITEM 2 (cross-val) === leave-one-out (held-out) skill = {np.mean(loo):.3f}")
print(f"   each report is NOT in the model that scores it. 0.50=random. in-model/circular for contrast = {np.mean(allp):.3f}")
print(f"   per-report held-out percentiles: {[round(x,2) for x in sorted(loo)]}")
