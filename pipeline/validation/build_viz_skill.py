"""Emit the measured-skill record the app reads to set viz confidence honestly.

Reads the hindcast residuals (published-historical predictions scored against
retained ground truth, produced by hindcast.py) and writes
``public/data/viz_skill.json``: overall + per-region + per-spot n / rmse /
bias / pearson r / in-band %, plus a plain "verdict" the UI can show.

This is the artifact that makes S2 (earned confidence) DATA-DRIVEN: the
frontend confidence dot reads it instead of a hand-typed literal. If the
measured skill does not clear the threshold, viz stays "Modeled" and says so,
with the real number in the tooltip. No refit, no spin, just the record.

Run:  python -m validation.build_viz_skill
"""
from __future__ import annotations

import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
HINDCAST = HERE / "data" / "hindcast_residuals.jsonl"
OUT = REPO / "public" / "data" / "viz_skill.json"

# Skill thresholds mirror integrity_gate.py's S2 gate.
MIN_N = 30
MIN_R = 0.30

# Which spots belong to which app region. CA is the only region with ground
# truth today; others stay unknown (skill=null -> UI keeps them "Modeled").
SPOT_REGION = {
    "La Jolla": "ca", "La Jolla Shores": "ca", "San Diego": "ca",
    "Wreck Alley": "ca", "Point Loma": "ca", "Morro Bay (Cal Poly Pier)": "ca",
}


def _pearson(a, b):
    n = len(a)
    if n < 3:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    if da == 0 or db == 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (da * db)


def _stats(rows):
    pred = [r["predicted_p50_ft"] for r in rows]
    obs = [r["observed_ft"] for r in rows]
    res = [p - o for p, o in zip(pred, obs)]
    n = len(rows)
    rmse = math.sqrt(sum(x * x for x in res) / n)
    bias = sum(res) / n
    mae = sum(abs(x) for x in res) / n
    inband = 100.0 * sum(1 for r in rows if r.get("in_p10_p90")) / n
    r = _pearson(pred, obs)
    return {
        "n": n,
        "rmse_ft": round(rmse, 1),
        "mae_ft": round(mae, 1),
        "bias_ft": round(bias, 1),
        "pearson_r": None if r is None else round(r, 3),
        "in_band_pct": round(inband),
    }


def _verdict(s):
    """Plain-language, honest. Skill is r; centering is bias."""
    r = s["pearson_r"]
    if s["n"] < MIN_N or r is None:
        return "insufficient ground truth to judge skill"
    if r < MIN_R:
        skill = ("no demonstrated skill (does not track day-to-day reality)"
                 if r <= 0.1 else "weak skill")
    else:
        skill = "demonstrated skill"
    bias = s["bias_ft"]
    lean = ("runs optimistic" if bias > 2 else
            "runs pessimistic" if bias < -2 else "roughly centered")
    return f"{skill}; {lean} by {abs(bias):.0f} ft on average"


def main():
    if not HINDCAST.exists():
        raise SystemExit(f"missing {HINDCAST}; run `python -m validation.hindcast` first")
    rows = [json.loads(l) for l in HINDCAST.read_text(encoding="utf-8").splitlines() if l.strip()]

    overall = _stats(rows)
    overall["verdict"] = _verdict(overall)

    regions, spots = {}, {}
    by_region, by_spot = {}, {}
    for r in rows:
        name = r.get("spot_name", "?")
        by_spot.setdefault(name, []).append(r)
        by_region.setdefault(SPOT_REGION.get(name, "unknown"), []).append(r)
    for reg, rs in by_region.items():
        if reg == "unknown":
            continue
        s = _stats(rs)
        s["verdict"] = _verdict(s)
        regions[reg] = s
    for name, rs in by_spot.items():
        if len(rs) < 5:
            continue
        s = _stats(rs)
        s["verdict"] = _verdict(s)
        spots[name] = s

    payload = {
        "provenance": "published-historical hindcast (predictions we actually "
                      "shipped, scored against retained dive-shop / buoy ground "
                      "truth). Not a current-model refit.",
        "thresholds": {"min_n": MIN_N, "min_r": MIN_R},
        "date_span": [min(r["date"] for r in rows), max(r["date"] for r in rows)],
        "overall": overall,
        "regions": regions,
        "spots": spots,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO)}")
    print(f"  overall: n={overall['n']} r={overall['pearson_r']} "
          f"rmse={overall['rmse_ft']}ft bias={overall['bias_ft']:+}ft "
          f"-> {overall['verdict']}")


if __name__ == "__main__":
    main()
