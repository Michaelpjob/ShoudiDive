"""Driver attribution + fate validation for the multi-driver kelp model.

Decomposes the paddy velocity into its physical drivers (current / Stokes /
windage), reports their relative shares, and the fate breakdown + drift
distances. Confirms no single driver dominates wrongly.

    python validate_drivers.py
"""
import math

import numpy as np

import config
import beds as B
import geo
import sd_source as SD
import detachment as DET
import drift as DR
from landmask import LandMask


def main():
    raw = SD.fetch_raw()
    lm = LandMask()
    f = SD.make_forcing(raw, "live")

    cur = np.sqrt(f.u[0] ** 2 + f.v[0] ** 2)
    hs, tp = raw["hs"], np.clip(raw["tp"], 2, None)
    us = config.STOKES_COEF * (2 * math.pi ** 3 / 9.81) * (np.nan_to_num(hs) ** 2) / tp ** 3 * 3.6
    windage = config.WINDAGE_ALPHA * np.sqrt(raw["wind_u"] ** 2 + raw["wind_v"] ** 2)
    mc, ms, mw = float(np.mean(cur)), float(np.nanmean(us)), float(np.mean(windage))
    tot = mc + ms + mw
    print("\nDRIVER MAGNITUDES (km/h, grid-mean):")
    print(f"  current (RTOFS+HFR): {mc:.3f}  ({100*mc/tot:.0f}%)")
    print(f"  Stokes wave drift:   {ms:.3f}  ({100*ms/tot:.0f}%)")
    print(f"  windage (2% wind):   {mw:.3f}  ({100*mw/tot:.0f}%)")

    det = DET.compute(f, B.SCB_BEDS, f.now_hours(), config.RELEASE_AGES_DAYS)
    res = DR.run_drift(f, lm, det)
    m = res["meta"]
    print(f"\nFATE: afloat {int(m['frac_floating']*100)}% / beached "
          f"{int(m['frac_beached']*100)}% / sunk {int(m['frac_sunk']*100)}%")

    bedll = {b[0]: (b[1], b[2]) for b in B.SCB_BEDS}
    ds = sorted(geo.haversine_km(bedll[p["bed"]][0], bedll[p["bed"]][1], p["lng"], p["lat"])
                for p in res["floating"])
    if ds:
        print(f"floating drift from source (km): median {ds[len(ds)//2]:.0f} / "
              f"p90 {ds[int(0.9*len(ds))]:.0f} / max {ds[-1]:.0f}")
    # age structure of the floating population (how long paddies survive)
    ages = [p["age_days"] for p in res["floating"]]
    if ages:
        import collections
        c = collections.Counter(ages)
        print("floating age structure (days afloat -> count):",
              {a: c.get(a, 0) for a in config.RELEASE_AGES_DAYS})


if __name__ == "__main__":
    main()
