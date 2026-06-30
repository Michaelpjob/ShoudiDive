"""Apples-to-apples: hold the ocean forcing CONSTANT and flip ONLY the shore
credit (OFF 1.0/0.0 vs ON 1.4/0.15). Shows whether the shore knob is what moves
the field, or whether the big day-to-day shifts are the fresh forcing. Renders
both fields full-bight with the kelp beds (island + shore) overlaid so you can
see BOTH source types are present the whole time."""
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
import cones as cones_mod
from landmask import LandMask

reps = [r for r in json.load(open("_reports.json")).get("reports", []) if "lat" in r and "lng" in r]
config.MIN_FINDABLE_AGE_DAYS = 0
raw = F.fetch_raw()
hfr = fusion.prepare(fusion.fetch_hfr(), raw["t0"])
lm = LandMask()
fo = F.make_forcing(raw, "live", hfr=hfr)
now = fo.now_hours()
det = D.compute(fo, B.SCB_BEDS, now, config.RELEASE_AGES_DAYS)
res = drift.run_drift(fo, lm, det, now_h=now)   # ONE drift; reweight per credit
floating = res["floating"]
BEDS = [(la, ln, bool(isl), float(area)) for (n, ln, la, r, isl, area) in B.SCB_BEDS]

def run(boost, credit):
    fl = [{**p, "float_w": p["float_w"] * (boost if not p["island"] else 1.0)} for p in floating]
    dens = find.build(fl, lm)
    config.SHORE_CREDIT = credit
    opp = conv.build_opportunity(fo, dens, lm)
    h = conv.hdr(opp)
    g = opp["opp"]; lats = np.array(opp["lats"]); lngs = np.array(opp["lngs"])
    flat = g[g > 0]
    def pct(la, ln):
        j = int(np.argmin(np.abs(lats - la))); i = int(np.argmin(np.abs(lngs - ln)))
        return float((flat < g[j, i]).mean() + 0.5 * (flat == g[j, i]).mean()) if g[j, i] > 0 else 0.0
    skill = float(np.mean([pct(r["lat"], r["lng"]) for r in reps]))
    msh = 100 * sum(p["float_w"] for p in fl if not p["island"]) / (sum(p["float_w"] for p in fl) or 1.0)
    return opp, h, skill, msh

off = run(1.0, 0.0)
on = run(1.4, 0.15)

print(f"{'config':<26} {'peak(lat,lng)':>16} {'mainland%':>9} {'skill':>6}")
for lbl, (opp, h, sk, msh) in [("OFF  islands only (1.0)", off), ("ON   shore credited (1.4)", on)]:
    pk = h["peak"]; print(f"{lbl:<26} {str(pk['lat'])+','+str(pk['lng']):>16} {msh:>8.0f}% {sk:>6.3f}")

land = cones_mod._land_union()
geoms = list(land.geoms) if land and land.geom_type == "MultiPolygon" else ([land] if land else [])
gmax = off[0]["opp"].max() or 1.0
fig, axes = plt.subplots(1, 2, figsize=(14, 8), facecolor="white")
for ax, (opp, h, sk, msh), label in zip(
        axes, [off, on], ["BEFORE tweak  ·  shore credit OFF (1.0)", "AFTER tweak  ·  shore credit ON (1.4)"]):
    lats = np.array(opp["lats"]); lngs = np.array(opp["lngs"]); LNG, LAT = np.meshgrid(lngs, lats)
    ax.contourf(LNG, LAT, opp["opp"] / gmax, levels=np.linspace(0.03, 1.0, 12), cmap="YlGn", extend="max")
    for gm in geoms:
        try: ax.fill(*gm.exterior.xy, color="#c4ccd2", zorder=3, lw=0)
        except Exception: pass
    for la, ln, isl, area in BEDS:
        ax.plot(ln, la, "o", ms=2.5 + 3.2 * (area ** 0.5), color=("#16a34a" if isl else "#34d399"),
                mec="#052e16", mew=0.4, zorder=4)
    pk = h["peak"]
    if pk: ax.plot(pk["lng"], pk["lat"], "*", ms=26, color="#dc2626", mec="#fff", mew=1.2, zorder=6)
    ax.set_xlim(-121.4, -117.0); ax.set_ylim(31.3, 34.75)
    ax.set_title(f"{label}\npeak {pk['lat']},{pk['lng']}  ·  mainland {msh:.0f}%  ·  skill {sk:.3f}", fontsize=11)
    ax.set_xticks([]); ax.set_yticks([])
# legend
ax.plot([], [], "o", color="#16a34a", mec="#052e16", label="island kelp bed")
ax.plot([], [], "o", color="#34d399", mec="#052e16", label="shore kelp bed")
ax.plot([], [], "*", color="#dc2626", mec="#fff", label="field peak")
axes[1].legend(loc="lower left", fontsize=9, framealpha=0.9)
fig.suptitle("Same ocean data, only the shore knob flips — island AND shore kelp present in both",
             fontsize=13, fontweight="bold")
fig.tight_layout()
fig.savefig("compare_models.png", dpi=110)
print("saved compare_models.png")
