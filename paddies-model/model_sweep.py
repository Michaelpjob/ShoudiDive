"""Several-hundred-config statistical model sweep, scored against the catch-report
GROUND TRUTH (does the opportunity field predict where fish were actually caught?)
plus the reach/coast goals. Fetches forcing once; re-drifts only per seed-pow, then
recomposes the opportunity for every weight combo cheaply.

Skill = mean percentile of the catch-report cells within the opportunity field
(0.50 = no skill / random, ->1.0 = reports land in the highest-opportunity cells).
"""
import itertools
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
from variant_sweep import build_masks, MB

reps = [r for r in json.load(open("_reports.json")).get("reports", []) if "lat" in r and "lng" in r]
print(f"ground-truth catch reports: {len(reps)}")

config.MIN_FINDABLE_AGE_DAYS = 0
raw = F.fetch_raw()
hfr = fusion.prepare(fusion.fetch_hfr(), raw["t0"])
lm = LandMask()
fo = F.make_forcing(raw, "live", hfr=hfr)
now = fo.now_hours()
det = D.compute(fo, B.SCB_BEDS, now, config.RELEASE_AGES_DAYS)
ORIG = list(B.SCB_BEDS)

SEED_POWS = [1.0, 0.5, 0.25]
GRID = dict(BASE=[0.05, 0.2, 0.4], Wc=[0.5, 1.0, 2.0, 3.0], Ws=[0.5, 1.0, 2.0],
           Wh=[0.5, 1.0, 2.0], gamma=[0.4, 0.7, 1.0], reach=[0.0, 30.0], offsupp=[True, False])
combos = list(itertools.product(GRID["BASE"], GRID["Wc"], GRID["Ws"], GRID["Wh"],
                                GRID["gamma"], GRID["reach"], GRID["offsupp"]))
print(f"weight combos/pow: {len(combos)} | total models: {len(combos) * len(SEED_POWS)}")

comps, masks, rep_idx, distMB = {}, None, None, None
rows = []
for pow in SEED_POWS:
    B.SCB_BEDS = [(n, ln, la, r, isl, round(a ** pow, 5)) for (n, ln, la, r, isl, a) in ORIG]
    res = drift.run_drift(fo, lm, det, now_h=now)
    dens = find.build(res["floating"], lm)
    conv.W_CONV = 1.0
    config.KELP_GAMMA = 1.0
    config.OFFSHORE_NEAR_KM, config.OFFSHORE_FAR_KM = 2, 10
    base = conv.build_opportunity(fo, dens, lm)
    lats, lngs = base["lats"], base["lngs"]
    kn = dens["grid"] / (dens["grid"].max() or 1.0)
    off_relax = base["offshore"]
    config.OFFSHORE_NEAR_KM, config.OFFSHORE_FAR_KM = 6, 17
    off_supp = conv._fishability(fo, lats, lngs, lm)
    comps[pow] = (kn, base["conv"], base["sst_front"], base["chl_front"], off_supp, off_relax, lats, lngs)
    if masks is None:
        masks = build_masks(lats, lngs)
        LAT, LNG = np.meshgrid(lats, lngs, indexing="ij")
        distMB = np.hypot((LAT - MB[0]) * 60, (LNG - MB[1]) * 60 * np.cos(np.radians(LAT)))
        rep_idx = [(int(np.argmin(np.abs(lats - r["lat"]))), int(np.argmin(np.abs(lngs - r["lng"])))) for r in reps]
    coast, faroff, reachm = masks
    for (BASE, Wc, Ws, Wh, gamma, reach, offsupp) in combos:
        off = off_supp if offsupp else off_relax
        opp = (kn ** gamma) * (BASE + Wc * base["conv"] + Ws * base["sst_front"] + Wh * base["chl_front"]) * off
        if reach > 0:
            opp = opp * np.exp(-distMB / reach)
        flat = opp[opp > 0]
        if flat.size < 10:
            continue
        ps = [float((flat < opp[j, i]).mean() + 0.5 * (flat == opp[j, i]).mean()) for (j, i) in rep_idx]
        skill = float(np.mean(ps))
        tot = float(opp.sum()) or 1.0
        rows.append([pow, BASE, Wc, Ws, Wh, gamma, reach, offsupp, skill,
                     float(opp[reachm].sum()) / tot, float(opp[coast].sum()) / tot, float(opp[faroff].sum()) / tot])
    print(f"  pow {pow}: scored {sum(1 for r in rows if r[0] == pow)} models")

B.SCB_BEDS = ORIG


def comp(r):
    return 0.55 * r[8] + 0.25 * r[9] + 0.20 * r[10]


rows.sort(key=lambda r: -comp(r))
print(f"\n=== TOP 18 of {len(rows)} models (composite: data-skill .55 + reach .25 + coast .20) ===")
print(f"{'comp':>5} {'skill':>5} {'reach':>5} {'coast':>5} {'faroff':>6} | pow BASE Wc Ws Wh gam reach offsupp")
for r in rows[:18]:
    print(f"{comp(r):>5.3f} {r[8]:>5.2f} {r[9]*100:>4.0f}% {r[10]*100:>4.0f}% {r[11]*100:>5.0f}% | "
          f"{r[0]} {r[1]} {r[2]} {r[3]} {r[4]} {r[5]} {int(r[6])} {r[7]}")
print(f"\nbest data-skill={max(r[8] for r in rows):.3f}  worst={min(r[8] for r in rows):.3f}  (0.50=random)")
# skill of the CURRENT shipped config (pow1.0-ish, BASE.2, Wc1, Ws1, Wh1, gamma1, no reach, relax) for reference
cur = [r for r in rows if r[1] == 0.2 and r[2] == 1.0 and r[3] == 1.0 and r[4] == 1.0 and r[5] == 1.0 and r[6] == 0.0 and not r[7] and r[0] == 1.0]
if cur:
    print(f"current shipped config skill={cur[0][8]:.3f} reach={cur[0][9]*100:.0f}% coast={cur[0][10]*100:.0f}%")

print("\n=== top-3 full (hdr breadth/focus) ===")
for r in rows[:3]:
    kn, cn, sn, hn, offs, offr, lats, lngs = comps[r[0]]
    off = offs if r[7] else offr
    opp = (kn ** r[5]) * (r[1] + r[2] * cn + r[3] * sn + r[4] * hn) * off
    if r[6] > 0:
        opp = opp * np.exp(-distMB / r[6])
    hdr = conv.hdr({"lats": lats, "lngs": lngs, "opp": opp})
    reg08 = next((x["area_km2"] for x in hdr["regions"] if x["level"] == 0.8), 0)
    print(f"  skill {r[8]:.2f} reach {r[9]*100:.0f}% coast {r[10]*100:.0f}% | breadth {reg08} "
          f"focus {int((hdr.get('primary_frac') or 0)*100)}% patches {hdr.get('n_core_patches')} peak {hdr.get('peak')}")
