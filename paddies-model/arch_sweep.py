"""Architecture sweep — the param sweep proved tuning can't fix the outer-island
domination, so test different FORMULATIONS of the opportunity field (what the
green even means), recomposed cheaply from one drift's components:

  A0 kelp-led    : kelp_density x (base+conv+fronts) x offshore   (current)
  A1 conv-first  : convergence x supply_gate(kelp) x fronts x off  (paddies pile on seams; kelp only gates supply)
  A2 reach       : A0 x reachability-decay from the launch          (fishable-from-here lens)
  A3 conv+reach  : A1 x reachability-decay                          (seams you can actually run to)

Seeding fixed at area^0.5 (modest compression) + coast 'relax'.
"""
import numpy as np
from scipy.ndimage import gaussian_filter  # noqa: F401 (kept for local-norm experiments)

import config
import forcing as F
import fusion
import drift
import findability as find
import convergence as conv
import beds as B
import detachment as D
from landmask import LandMask
from variant_sweep import build_masks, score, MB

config.MIN_FINDABLE_AGE_DAYS = 0
config.OFFSHORE_NEAR_KM, config.OFFSHORE_FAR_KM = 2, 10
ORIG = list(B.SCB_BEDS)
B.SCB_BEDS = [(n, ln, la, r, isl, round(a ** 0.5, 5)) for (n, ln, la, r, isl, a) in ORIG]

raw = F.fetch_raw()
hfr = fusion.prepare(fusion.fetch_hfr(), raw["t0"])
lm = LandMask()
fo = F.make_forcing(raw, "live", hfr=hfr)
now = fo.now_hours()
det = D.compute(fo, B.SCB_BEDS, now, config.RELEASE_AGES_DAYS)
res = drift.run_drift(fo, lm, det, now_h=now)
dens = find.build(res["floating"], lm)
conv.W_CONV = 1.0
config.KELP_GAMMA = 1.0
base = conv.build_opportunity(fo, dens, lm)
lats, lngs = base["lats"], base["lngs"]
masks = build_masks(lats, lngs)

kelp = dens["grid"]
kn = kelp / (kelp.max() or 1.0)
cn, sn, hn, off = base["conv"], base["sst_front"], base["chl_front"], base["offshore"]
LAT, LNG = np.meshgrid(lats, lngs, indexing="ij")
distMB = np.hypot((LAT - MB[0]) * 60, (LNG - MB[1]) * 60 * np.cos(np.radians(LAT)))
reach = np.exp(-distMB / 35.0)        # fishable-from-Mission-Bay decay (~35 nm e-fold)
gate = np.clip(kn / 0.12, 0.0, 1.0)   # supply gate: is paddy supply drifting through here at all?
BASE = conv.BASE

archs = {
    "A0 kelp-led":   kn * (BASE + cn + sn + hn) * off,
    "A1 conv-first": cn * gate * (BASE + sn + hn) * off,
    "A2 reach":      kn * (BASE + cn + sn + hn) * off * reach,
    "A3 conv+reach": cn * gate * (BASE + sn + hn) * off * reach,
}

mb = None
rows = []
print(f"\n{'architecture':>14} | {'reach':>5} {'coast':>5} {'faroff':>6} | {'breadth':>7} {'focus':>5} {'patch':>5}")
for nm, g in archs.items():
    od = {"lats": lats, "lngs": lngs, "opp": g}
    hdr = conv.hdr(od)
    s = score(od, hdr, masks)
    pk = hdr.get("peak") or {}
    rows.append((nm, s, pk))
    print(f"{nm:>14} | {s['reach']*100:>4.0f}% {s['coast']*100:>4.0f}% {s['faroff']*100:>5.0f}% | "
          f"{s['breadth']:>7} {int(s['focus']*100):>4}% {s['patches']:>5}  peak {pk.get('lat')},{pk.get('lng')}")

mb = max(r[1]["breadth"] for r in rows) or 1
print("\nranked (reach .35 + coast .25 + focus .2 + tight .2):")
for nm, s, pk in sorted(rows, key=lambda r: -(0.35 * r[1]["reach"] + 0.25 * r[1]["coast"]
                                               + 0.2 * r[1]["focus"] + 0.2 * (1 - r[1]["breadth"] / mb))):
    sc = 0.35 * s["reach"] + 0.25 * s["coast"] + 0.2 * s["focus"] + 0.2 * (1 - s["breadth"] / mb)
    print(f"  {sc:.3f}  {nm:<14} reach {s['reach']*100:.0f}% coast {s['coast']*100:.0f}% breadth {s['breadth']} focus {int(s['focus']*100)}%")

B.SCB_BEDS = ORIG
