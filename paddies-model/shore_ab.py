"""Before/after for cautiously crediting the shore (mainland) kelp source.
Drift ONCE, then re-weight the mainland particles by SHORE_SOURCE_BOOST (exact,
since the density splat is linear in float_w) and rebuild the opportunity at
each level. Reports catch-report skill (the don't-overweight guardrail), how
hot the Long Beach rigs -> 14-Mile-Bank corridor gets, and the mainland share
of the floating field. Renders baseline vs cautious over the San Pedro Channel.
"""
import json
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

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
res = drift.run_drift(fo, lm, det, now_h=now)   # one drift at boost=1.0
floating = res["floating"]
tot_float = sum(p["float_w"] for p in floating) or 1.0
print(f"floating particles: {len(floating)}  mainland share (boost 1.0): "
      f"{100*sum(p['float_w'] for p in floating if not p['island'])/tot_float:.1f}%")

lats0 = None
CORR = {"rigs": (33.58, -118.13), "mid": (33.49, -118.07), "14-mile": (33.41, -118.03)}
rep_idx = None
panels = {}

def run(boost, credit):
    global lats0, rep_idx
    fl = [{**p, "float_w": p["float_w"] * (boost if not p["island"] else 1.0)} for p in floating]
    dens = find.build(fl, lm)
    config.SHORE_CREDIT = credit
    opp = conv.build_opportunity(fo, dens, lm)
    lats = np.array(opp["lats"]); lngs = np.array(opp["lngs"]); g = opp["opp"]
    if lats0 is None:
        lats0 = (lats, lngs)
        rep_idx = [(int(np.argmin(np.abs(lats - r["lat"]))), int(np.argmin(np.abs(lngs - r["lng"])))) for r in reps]
    flat = g[g > 0]
    def pct(la, ln):
        j = int(np.argmin(np.abs(lats - la))); i = int(np.argmin(np.abs(lngs - ln)))
        v = g[j, i]
        return float((flat < v).mean() + 0.5 * (flat == v).mean()) if v > 0 else 0.0
    skill = float(np.mean([pct(reps[k]["lat"], reps[k]["lng"]) for k in range(len(reps))]))
    corr = {name: round(pct(la, ln), 2) for name, (la, ln) in CORR.items()}
    mshare = 100 * sum(p["float_w"] for p in fl if not p["island"]) / (sum(p["float_w"] for p in fl) or 1.0)
    return skill, corr, mshare, (lats, lngs, g)

print(f"\n{'boost':>5} {'credit':>6} {'skill':>6} {'mainland%':>9}  corridor percentile (rigs/mid/14mi)")
configs = [(1.0, 0.0, "BEFORE  (current)"), (1.4, 0.15, "AFTER  (cautious)"), (1.8, 0.15, "stronger (ref)")]
for boost, credit, label in configs:
    sk, corr, msh, grid = run(boost, credit)
    panels[label] = (grid, sk, corr)
    print(f"{boost:>5.1f} {credit:>6.2f} {sk:>6.3f} {msh:>8.1f}%  "
          f"{corr['rigs']:.2f} / {corr['mid']:.2f} / {corr['14-mile']:.2f}")

# ---- 2-panel render: BEFORE vs AFTER over the San Pedro Channel ----
WIN = dict(lng=(-118.75, -117.70), lat=(33.20, 33.85))
catal = [(-118.60,33.48),(-118.50,33.45),(-118.40,33.43),(-118.335,33.355),(-118.305,33.305),
         (-118.46,33.40),(-118.57,33.46),(-118.60,33.48)]
coast = [(-118.42,33.78),(-118.30,33.77),(-118.19,33.765),(-118.07,33.73),(-117.96,33.66),
         (-117.93,33.605),(-117.80,33.52),(-117.70,33.46),(-117.62,33.46)]
gmax = panels["BEFORE  (current)"][0][2].max() or 1.0
fig, axes = plt.subplots(1, 2, figsize=(13, 7), facecolor="white")
for ax, label in zip(axes, ["BEFORE  (current)", "AFTER  (cautious)"]):
    (lats, lngs, g), sk, corr = panels[label]
    LNG, LAT = np.meshgrid(lngs, lats)
    ax.contourf(LNG, LAT, g / gmax, levels=np.linspace(0.02, 1.0, 12), cmap="YlGn", extend="max")
    ax.fill([p[0] for p in catal], [p[1] for p in catal], color="#7c8a99", zorder=3)
    ax.plot([p[0] for p in coast], [p[1] for p in coast], color="#555", lw=2, zorder=3)
    ax.plot([-118.13, -118.03], [33.58, 33.41], "--", color="#d97706", lw=2.5, zorder=4)
    for la, ln in [(33.58,-118.13),(33.58,-118.02)]:
        ax.plot(ln, la, "s", color="#d97706", ms=8, zorder=5)
    ax.plot(-118.03, 33.41, "D", color="#d97706", ms=10, zorder=5)
    ax.text(-118.135, 33.60, "oil rigs", color="#9a3412", fontsize=10, fontweight="bold", ha="right")
    ax.text(-118.02, 33.40, "14-Mile Bank", color="#9a3412", fontsize=10, fontweight="bold")
    ax.text(-118.40, 33.34, "Catalina", color="white", fontsize=10, fontweight="bold")
    ax.set_xlim(*WIN["lng"]); ax.set_ylim(*WIN["lat"])
    ax.set_title(f"{label}\nskill {sk:.3f}  |  corridor pct {corr['rigs']:.2f}/{corr['mid']:.2f}/{corr['14-mile']:.2f}", fontsize=11)
    ax.set_xticks([]); ax.set_yticks([])
fig.suptitle("Shore-source credit: paddy opportunity field, San Pedro Channel", fontsize=13, fontweight="bold")
fig.tight_layout()
fig.savefig("shore_ab.png", dpi=110)
print("\nsaved shore_ab.png")
