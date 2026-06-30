"""Validate nearshore mainland kelp-bed behavior vs island beds.

For each bed: how many seeds landed in water (vs skipped on land), and of
those, what fraction beached / sank / stayed afloat, plus the mean drift
distance + heading of the floating ones. Mainland beds should beach far
more than islands (they're pinned against the shore) and contribute less
to the offshore floating supply.

    python validate_beds.py
"""
from collections import defaultdict

import config
import beds as beds_mod
import geo
import forcing as F
import detachment as DET
import drift as DR
from landmask import LandMask


def main():
    raw = F.fetch_raw()
    lm = LandMask()
    bedll = {b[0]: (b[1], b[2]) for b in beds_mod.SCB_BEDS}
    expected = len(config.RELEASE_AGES_DAYS) * config.PARTICLES_PER_RELEASE

    for scen in ("live", "warm"):
        forc = F.make_forcing(raw, scen)
        det = DET.compute(forc, beds_mod.SCB_BEDS, forc.now_hours(), config.RELEASE_AGES_DAYS)
        res = DR.run_drift(forc, lm, det)

        st = defaultdict(lambda: {"isl": False, "f": 0, "b": 0, "s": 0, "famt": 0.0, "pts": []})
        for p in res["floating"]:
            x = st[p["bed"]]; x["isl"] = p["island"]; x["f"] += 1
            x["famt"] += p["float_w"]; x["pts"].append((p["lng"], p["lat"]))
        for p in res["beached"]:
            x = st[p["bed"]]; x["isl"] = p["island"]; x["b"] += 1
        for p in res["sunk"]:
            x = st[p["bed"]]; x["isl"] = p["island"]; x["s"] += 1

        print(f"\n===== scenario {scen}  (expected {expected} seeds/bed) =====")
        print(f"{'bed':28s} typ  plc skip beach% float%  famt drift  hdg")
        agg = {"M": defaultdict(float), "I": defaultdict(float)}
        for name, (blng, blat) in bedll.items():
            x = st[name]
            plc = x["f"] + x["b"] + x["s"]
            skip = expected - plc
            typ = "ISL" if x["isl"] else "main"
            beachp = 100 * x["b"] / max(plc, 1)
            floatp = 100 * x["f"] / max(plc, 1)
            if x["pts"]:
                me = sum(p[0] for p in x["pts"]) / len(x["pts"])
                mn = sum(p[1] for p in x["pts"]) / len(x["pts"])
                dkm = geo.haversine_km(blng, blat, me, mn)
                hdg = geo.compass(geo.bearing_deg(blng, blat, me, mn))
            else:
                dkm, hdg = 0.0, "-"
            print(f"{name:28s} {typ:4s} {plc:3d} {skip:3d}  {beachp:4.0f}  {floatp:4.0f}  "
                  f"{x['famt']:5.1f} {dkm:5.1f}  {hdg}")
            g = "I" if x["isl"] else "M"
            a = agg[g]
            for k, v in (("plc", plc), ("skip", skip), ("f", x["f"]), ("b", x["b"]),
                         ("s", x["s"]), ("famt", x["famt"]), ("n", 1)):
                a[k] += v

        for g, lab in (("M", "MAINLAND"), ("I", "ISLANDS ")):
            a = agg[g]; plc = a["plc"]
            print(f"{lab}: {int(a['n'])} beds | placed {int(plc)} skipped {int(a['skip'])} "
                  f"({100*a['skip']/max(plc+a['skip'],1):.0f}% of seeds) | "
                  f"beach {100*a['b']/max(plc,1):.0f}% float {100*a['f']/max(plc,1):.0f}% "
                  f"sink {100*a['s']/max(plc,1):.0f}% | floating amount {a['famt']:.0f}")
        tot = agg["M"]["famt"] + agg["I"]["famt"]
        print(f"  floating-supply share:  mainland {100*agg['M']['famt']/max(tot,1e-9):.0f}%  /  "
              f"islands {100*agg['I']['famt']/max(tot,1e-9):.0f}%")


if __name__ == "__main__":
    main()
