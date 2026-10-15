"""Predicted visibility, stated plainly, with its track record.

The viz model's published p50 has moved with dive-shop reports over the last
60 days (r = 0.38, n = 50, Aug 15 to Oct 14) but ran about twice what the
shops posted (9 ft high on average). Clarity errors are proportional, so the
report scales the model's number by the ratio reports have landed at
(geometric mean of posted / predicted; 0.48 on the seed), with a range from
the 15th-85th percentile of how far reports fell from the scaled number. On
the seed the ratio beats a flat offset: 72% vs 62% inside the posted range,
MAE 2.6 vs 2.8 ft, and no zero-foot readings at murky cells. The correction is recomputed every build from the last 60 days of
pairs: the seed hindcast (data/vis_pairs_seed.json) plus the pairs the
report's own archive collects from here on (trends.json "vis_pred").

Registered in knobs_registry.json as reports.vis_bias_correction (fit).
"""
from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path

from . import sources

SEED = Path(__file__).resolve().parent / "data" / "vis_pairs_seed.json"
WINDOW_DAYS = 60
MIN_PAIRS = 15
# The report's own reference point for archive pairs: Just Get Wet posts as "La Jolla".
REF_POINT = {"id": "la-jolla-ref", "lat": 32.852, "lng": -117.272, "source": "dive-shop-justgetwet"}


def load_grids(manifest: dict, data_dir: Path | None = None) -> dict | None:
    from lib.decode import decode_linear_png

    from .fish import Grid
    data_dir = data_dir or sources.DATA
    L = (manifest.get("layers") or {}).get("viz") or {}
    w = (L.get("windows") or {}).get("now") or {}
    lo, hi = L.get("range_ft", [0.0, 80.0])
    out = {}
    for key, url_key in (("p10", "p10_url"), ("p50", "url"), ("p90", "p90_url")):
        url = w.get(url_key)
        p = data_dir / Path(url).name if url else None
        if not p or not p.exists():
            return None if key == "p50" else out
        out[key] = Grid(decode_linear_png(p, lo, hi), manifest["bbox"])
    out["valid_at"] = w.get("valid_at")
    return out


def sample(grids: dict, lat: float, lng: float) -> dict | None:
    v = grids["p50"].value(lat, lng, ring=2)
    if v is None:
        return None
    return {"p50": round(v, 1),
            "p10": round(grids["p10"].value(lat, lng, ring=2), 1) if grids.get("p10") else None,
            "p90": round(grids["p90"].value(lat, lng, ring=2), 1) if grids.get("p90") else None}


def _pct(vals: list[float], q: float) -> float:
    s = sorted(vals)
    k = (len(s) - 1) * q
    f = math.floor(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def _corr(a: list[float], b: list[float]) -> float | None:
    n = len(a)
    if n < 3:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va == 0 or vb == 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)


def archive_pairs(archive_days: list[dict], posts: list[dict]) -> list[dict]:
    """Pairs from our own archive: the day's predicted p50 at the reference
    point against that day's shop post."""
    by_date = {p["date"]: p for p in posts}
    out = []
    for d in archive_days:
        pred = (d.get("vis_pred") or {}).get(REF_POINT["id"])
        post = by_date.get(d["date"])
        if pred and post:
            out.append({"date": d["date"], "source": REF_POINT["source"], "spot": "La Jolla",
                        "obs_lo": post["lo_ft"], "obs_hi": post["hi_ft"], "p50": pred["p50"],
                        "p10": pred.get("p10"), "p90": pred.get("p90")})
    return out


def calibration(today: date, archive_days: list[dict], posts: list[dict]) -> dict:
    try:
        seed = json.loads(SEED.read_text(encoding="utf-8"))["pairs"]
    except (OSError, ValueError, KeyError):
        seed = []
    pairs = {(p["date"], p.get("spot")): p for p in seed}
    for p in archive_pairs(archive_days, posts):
        pairs[(p["date"], p["spot"])] = p
    since = (today - timedelta(days=WINDOW_DAYS)).isoformat()
    window = sorted((p for p in pairs.values() if p["date"] >= since), key=lambda p: p["date"])
    if len(window) < MIN_PAIRS:
        return {"n": len(window), "applied": False, "since": since}
    obs = [(p["obs_lo"] + p["obs_hi"]) / 2 for p in window]
    pred = [p["p50"] for p in window]
    bias = sum(m - o for m, o in zip(pred, obs)) / len(window)
    log_ratio = [math.log(max(o, 1.0)) - math.log(max(m, 1.0)) for m, o in zip(pred, obs)]
    k = math.exp(sum(log_ratio) / len(log_ratio))
    log_resid = [lr - math.log(k) for lr in log_ratio]  # where reports landed vs the scaled number
    r = _corr(pred, obs)
    return {"n": len(window), "applied": True, "since": window[0]["date"], "until": window[-1]["date"],
            "scale": round(k, 3), "bias_ft": round(bias, 1), "r": round(r, 2) if r is not None else None,
            "mae_after_ft": round(sum(abs(o - m * k) for m, o in zip(pred, obs)) / len(window), 1),
            "band_lo_x": round(math.exp(_pct(log_resid, 0.15)), 2), "band_hi_x": round(math.exp(_pct(log_resid, 0.85)), 2),
            "pairs": [{"date": p["date"], "obs_lo": p["obs_lo"], "obs_hi": p["obs_hi"],
                       "pred_ft": round(p["p50"] * k, 1)} for p in window
                      if p.get("spot") in ("La Jolla", "La Jolla Cove", "La Jolla Shores")]}


def stated(raw: dict | None, cal: dict) -> dict | None:
    """The number the report states: the model's p50 less its recent bias,
    with the range recent reports actually fell in around it."""
    if not raw:
        return None
    if not cal.get("applied"):
        return {"ft": round(raw["p50"]), "lo_ft": round(raw["p10"]) if raw.get("p10") is not None else None,
                "hi_ft": round(raw["p90"]) if raw.get("p90") is not None else None, "raw_p50": raw["p50"], "adjusted": False}
    mid = raw["p50"] * cal["scale"]
    return {"ft": round(mid), "lo_ft": round(mid * cal["band_lo_x"]), "hi_ft": round(mid * cal["band_hi_x"]),
            "raw_p50": raw["p50"], "adjusted": True}
