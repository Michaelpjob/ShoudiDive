"""Exhaustive model-variant sweep on ONE forcing fetch.

Tests seeding-weight x coast-treatment x field-formation (27 model types),
scoring each on the metrics that capture the two complaints — "too busy"
(breadth / focus / patch count) and "coast underperforming" (coast signal) —
plus actionability (reachable signal) and outer-island domination. Reuses one
forcing fetch and one detachment calc; only the 3 seeding variants re-run the
drift, the rest are cheap opportunity re-evals on the same particles.

  python variant_sweep.py        # prints the scored matrix + a ranked top list
"""
import math

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

MB = (32.77, -117.25)  # Mission Bay launch

# --- variant axes -----------------------------------------------------------
SEED_POWS = [1.0, 0.5, 0.25]                       # canopy-area weighting exponent
COASTS = {"suppress": (6, 17), "relax": (2, 10), "coast-on": (0, 4)}  # OFFSHORE_NEAR/FAR km
FIELDS = {"kelp-led": (1.0, 1.0), "conv-led": (2.2, 0.7), "kelp-gate": (1.0, 0.5)}  # (W_conv, KELP_GAMMA)


def build_masks(lats, lngs):
    """Fixed scoring masks (independent of variant): coastal band (<=~12nm off the
    mainland), reachable (<=40nm of Mission Bay), far-offshore (>=~50nm off mainland)."""
    from scipy.ndimage import distance_transform_edt
    mainland = conv._mainland_mask(lats, lngs)
    if mainland is None:
        mainland = np.zeros((len(lats), len(lngs)), bool)
    dist_km = distance_transform_edt(~mainland) * (config.DENSITY_STEP_DEG * 111.0)
    coast = (dist_km > 0) & (dist_km <= 22)     # ~0-12 nm off the beach
    faroff = dist_km >= 92                       # >~50 nm offshore
    reach = np.zeros((len(lats), len(lngs)), bool)
    for j, la in enumerate(lats):
        for i, ln in enumerate(lngs):
            dnm = math.hypot((la - MB[0]) * 60, (ln - MB[1]) * 60 * math.cos(math.radians(la)))
            reach[j, i] = dnm <= 40
    return coast, faroff, reach


def score(opp_data, hdr, masks):
    coast, faroff, reach = masks
    g = opp_data["opp"]
    tot = float(g.sum()) or 1.0
    reg08 = next((r["area_km2"] for r in hdr["regions"] if r["level"] == 0.8), 0)
    return {
        "reach": float(g[reach].sum()) / tot,
        "coast": float(g[coast].sum()) / tot,
        "faroff": float(g[faroff].sum()) / tot,
        "breadth": reg08,
        "focus": hdr.get("primary_frac", 0) or 0,
        "patches": hdr.get("n_core_patches", 0),
    }


def main():
    raw = F.fetch_raw()
    hfr = fusion.prepare(fusion.fetch_hfr(), raw["t0"])
    lm = LandMask()
    fo = F.make_forcing(raw, "live", hfr=hfr)
    now = fo.now_hours()
    det = D.compute(fo, B.SCB_BEDS, now, config.RELEASE_AGES_DAYS)  # location-only -> reuse across pows
    ORIG = list(B.SCB_BEDS)
    config.MIN_FINDABLE_AGE_DAYS = 0

    # seed-share by region (how each pow rebalances coast vs outer islands)
    def region(cells, isl):
        return sum(c[5] for c in cells if c[4] == isl)

    masks = None
    rows = []
    print(f"\n{'pow':>4} {'coast':>8} {'field':>9} | {'reach':>5} {'coast':>5} {'faroff':>6} | {'breadth':>7} {'focus':>5} {'patch':>5}")
    for pow in SEED_POWS:
        B.SCB_BEDS = [(n, ln, la, r, isl, round(a ** pow, 5)) for (n, ln, la, r, isl, a) in ORIG]
        isl_sh = region(B.SCB_BEDS, True)
        main_sh = region(B.SCB_BEDS, False)
        share = main_sh / (isl_sh + main_sh)
        res = drift.run_drift(fo, lm, det, now_h=now)
        dens = find.build(res["floating"], lm)
        if masks is None:
            masks = build_masks(dens["lats"], dens["lngs"])
        print(f"  -- seed pow {pow}: mainland-coast seed share {share*100:.0f}% (vs outer-island {100-share*100:.0f}%) --")
        for cn, (near, far) in COASTS.items():
            config.OFFSHORE_NEAR_KM, config.OFFSHORE_FAR_KM = near, far
            for fn, (wconv, kg) in FIELDS.items():
                conv.W_CONV = wconv
                config.KELP_GAMMA = kg
                opp = conv.build_opportunity(fo, dens, lm)
                hdr = conv.hdr(opp)
                s = score(opp, hdr, masks)
                rows.append((pow, cn, fn, share, s))
                print(f"{pow:>4} {cn:>8} {fn:>9} | {s['reach']*100:>4.0f}% {s['coast']*100:>4.0f}% {s['faroff']*100:>5.0f}% | "
                      f"{s['breadth']:>7} {int(s['focus']*100):>4}% {s['patches']:>5}")

    B.SCB_BEDS = ORIG
    mb = max(r[4]["breadth"] for r in rows) or 1

    def comp(s):
        # actionable + coast-alive + clean, penalize far-offshore domination
        return (0.30 * s["reach"] + 0.25 * s["coast"] + 0.15 * s["focus"]
                + 0.15 * (1 - s["breadth"] / mb) + 0.15 * (1 - s["faroff"]))

    rows.sort(key=lambda r: -comp(r[4]))
    print("\n=== TOP 8 model types (composite: reach .30 + coast .25 + focus .15 + tight .15 + not-faroff .15) ===")
    for pow, cn, fn, share, s in rows[:8]:
        print(f"  {comp(s):.3f}  pow{pow:<4} {cn:<9} {fn:<9} | reach {s['reach']*100:>3.0f}% coast {s['coast']*100:>3.0f}% "
              f"faroff {s['faroff']*100:>3.0f}% breadth {s['breadth']:>6} focus {int(s['focus']*100):>3}% patch {s['patches']}")


if __name__ == "__main__":
    main()
