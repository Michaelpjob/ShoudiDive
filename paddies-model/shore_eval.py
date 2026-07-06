"""Cautious shore-credit calibration: drift once, then rebuild the opportunity
field at several SHORE_CREDIT levels and score each against the catch-report
ground truth. We want skill to HOLD (not drop) while a modest amount of weight
returns to the inshore shore-shed band. Skill = mean catch-report percentile
(0.50 = random, ->1.0 = reports land in the hottest cells)."""
import json
import numpy as np

import config
import forcing as F
import fusion
import drift
import findability as find
import convergence as conv
import beds as B
import detachment as D
from landmask import LandMask

reps = [r for r in json.load(open("_reports.json")).get("reports", []) if "lat" in r and "lng" in r]
print("catch reports:", len(reps))

config.MIN_FINDABLE_AGE_DAYS = 0
raw = F.fetch_raw()
hfr = fusion.prepare(fusion.fetch_hfr(), raw["t0"])
lm = LandMask()
fo = F.make_forcing(raw, "live", hfr=hfr)
now = fo.now_hours()
det = D.compute(fo, B.SCB_BEDS, now, config.RELEASE_AGES_DAYS)
res = drift.run_drift(fo, lm, det, now_h=now)
dens = find.build(res["floating"], lm)
lats = np.array(dens["lats"]); lngs = np.array(dens["lngs"])
rep_idx = [(int(np.argmin(np.abs(lats - r["lat"]))), int(np.argmin(np.abs(lngs - r["lng"])))) for r in reps]

# inshore band (<= ~3.2 nm from the mainland), computed once
from scipy.ndimage import distance_transform_edt
mm = conv._mainland_mask(lats, lngs)
dist_km = distance_transform_edt(~mm) * (config.DENSITY_STEP_DEG * 111.0)
inshore = dist_km <= 6.0

def ev(shore):
    config.SHORE_CREDIT = shore
    opp = conv.build_opportunity(fo, dens, lm)
    g = opp["opp"]
    flat = g[g > 0]
    ps = [float((flat < g[j, i]).mean() + 0.5 * (flat == g[j, i]).mean())
          for (j, i) in rep_idx if g[j, i] > 0]
    skill = float(np.mean(ps)) if ps else float("nan")
    tot = float(g.sum()) or 1.0
    inmass = float(g[inshore].sum()) / tot
    h = conv.hdr(opp)
    br = next((x["area_km2"] for x in h["regions"] if x["level"] == 0.8), 0)
    return skill, inmass, h.get("peak"), br

print(f"\n{'SHORE_CREDIT':>12} {'skill':>6} {'inshore_mass':>12} {'80%_area_km2':>13}  peak(lat,lng)")
for sc in (0.0, 0.10, 0.15, 0.25):
    sk, im, pk, br = ev(sc)
    pkstr = f"{pk['lat']},{pk['lng']}" if pk else "-"
    print(f"{sc:>12.2f} {sk:>6.3f} {im*100:>11.1f}% {br:>13d}  {pkstr}")
config.SHORE_CREDIT = 0.15
print("\n(skill 0.50 = random; higher = catches land in hotter cells)")
