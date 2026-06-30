"""Render A0 (current kelp-led) vs A2 (reachability lens) opportunity fields
side by side, so the architecture difference is visible, not just tabular."""
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
from variant_sweep import MB

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

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
kn = dens["grid"] / (dens["grid"].max() or 1.0)
cn, sn, hn, off = base["conv"], base["sst_front"], base["chl_front"], base["offshore"]
LAT, LNG = np.meshgrid(lats, lngs, indexing="ij")
reach = np.exp(-np.hypot((LAT - MB[0]) * 60, (LNG - MB[1]) * 60 * np.cos(np.radians(LAT))) / 35.0)
A0 = kn * (0.20 + cn + sn + hn) * off
A2 = A0 * reach

fig, axs = plt.subplots(1, 2, figsize=(12, 6.2), facecolor="#0a1824")
for ax, (nm, g) in zip(axs, [("A0  kelp-led (current)", A0), ("A2  reachability lens", A2)]):
    ax.set_facecolor("#0a1824")
    ax.pcolormesh(LNG, LAT, g / (g.max() or 1.0), cmap="YlGn", shading="auto", vmin=0, vmax=0.55)
    ax.plot(MB[1], MB[0], "o", color="#5fd1e6", ms=9)
    ax.text(MB[1] + 0.05, MB[0], "Mission Bay", color="#5fd1e6", fontsize=9, va="center")
    for iln, ila, inm in [(-118.45, 33.39, "Catalina"), (-118.50, 32.90, "San Clemente Is"),
                          (-119.50, 33.25, "San Nicolas")]:
        ax.plot(iln, ila, "s", color="#c9a7ff", ms=5)
        ax.text(iln - 0.04, ila + 0.04, inm, color="#c9a7ff", fontsize=7, ha="right")
    ax.set_title(nm, color="white", fontsize=13)
    ax.set_xlim(-120.6, -116.9); ax.set_ylim(31.6, 34.4)
    ax.tick_params(colors="#7d92a0", labelsize=7)
plt.tight_layout()
plt.savefig("out/arch_compare.png", dpi=110, facecolor="#0a1824")
print("saved out/arch_compare.png")
B.SCB_BEDS = ORIG
