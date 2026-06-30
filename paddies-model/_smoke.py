import datetime as dt
import numpy as np
import config, forcing as F, fusion, drift, findability as find, convergence as conv
import beds as B, detachment as D, dashboard
from landmask import LandMask

raw = F.fetch_raw(); hfr = fusion.prepare(fusion.fetch_hfr(), raw["t0"]); lm = LandMask()
fo = F.make_forcing(raw, "live", hfr=hfr); now = fo.now_hours()
det = D.compute(fo, B.SCB_BEDS, now, config.RELEASE_AGES_DAYS)
res = drift.run_drift(fo, lm, det, now_h=now)
print("kelp src:", B.KELP_SOURCE, "| afloat", len(res["floating"]), "beached", len(res["beached"]), "sunk", len(res["sunk"]))
dens = find.build(res["floating"], lm)
reports = dashboard._fetch_reports(); print("reports:", len(reports))
fdt = raw["t0"] + dt.timedelta(hours=now)


def skill(opp):
    lats, lngs, g = opp["lats"], opp["lngs"], opp["opp"]; flat = g[g > 0]
    ps = []
    for r in reports:
        j = int(np.argmin(np.abs(lats - r["lat"]))); i = int(np.argmin(np.abs(lngs - r["lng"])))
        x = g[j, i]; ps.append(float((flat < x).mean() + 0.5 * (flat == x).mean()))
    return float(np.mean(ps)) if ps else 0


opp_honest = conv.build_opportunity(fo, dens, lm)                              # no reports -> honest skill
opp_prod = conv.build_opportunity(fo, dens, lm, reports=reports, as_of_dt=fdt)  # reports promoted
hdr = conv.hdr(opp_prod)
print(f"HONEST skill (no reports in model): {skill(opp_honest):.3f}   (old=0.42, random=0.50)")
print(f"PROD skill (reports promoted): {skill(opp_prod):.3f}")
print("peak:", hdr.get("peak"), "| primary_frac:", hdr.get("primary_frac"), "| patches:", hdr.get("n_core_patches"))
print("0.8 region km2:", next((r["area_km2"] for r in hdr["regions"] if r["level"] == 0.8), 0))
