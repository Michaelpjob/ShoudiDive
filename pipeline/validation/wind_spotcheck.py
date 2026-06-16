"""Spot-check the published wind against independent data sources.

Reviews ShouldIDive's wind (the ECMWF + HRRR/GFS blend, buoy-anchored on the
nowcast) against three outside references at the NDBC buoy locations:

  * NDBC buoy observations  — ground truth, height-adjusted to 10 m
  * ECMWF IFS               — via Open-Meteo (Windy's default layer)
  * GFS                     — via Open-Meteo

…for the ``now`` and ``p6h`` windows, at the SAME valid hour so a time lag
doesn't masquerade as model error. Writes a JSON + Markdown report and prints
a table. This is a standing ACCURACY REVIEW, not a gate — it never fails the
build; a watchdog can read the JSON and open an issue if we drift.

Run:
  python -m pipeline.validation.wind_spotcheck                      # local build
  python -m pipeline.validation.wind_spotcheck --source https://shouldidive.com
"""
from __future__ import annotations

import argparse
import io
import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

try:
    from pipeline.wind_buoy_correction import BUOYS, fetch_buoy_winds
except ModuleNotFoundError:  # python -m validation.wind_spotcheck (cwd=pipeline)
    from wind_buoy_correction import BUOYS, fetch_buoy_winds

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "public" / "data"
REPORT_DIR = ROOT / "pipeline" / "validation" / "data"
UA = {"User-Agent": "Mozilla/5.0 (ShoudiDive wind spot-check)"}
KT = 1.94384  # m/s → knots


# ---- loading our published wind ---------------------------------------------

def _read_bytes(source: str, rel: str) -> bytes:
    if source.startswith("http"):
        req = urllib.request.Request(f"{source}/data/{rel}", headers=UA)
        return urllib.request.urlopen(req, timeout=30).read()
    return (OUT_DIR / rel).read_bytes()


def load_ours(source: str):
    manifest = json.loads(_read_bytes(source, "manifest.json"))
    wind = manifest["layers"]["wind"]
    imgs = {}
    for slot, w in wind["windows"].items():
        if slot in ("now", "p6h"):
            png = w["uv_url"].split("/data/", 1)[-1]
            imgs[slot] = Image.open(io.BytesIO(_read_bytes(source, png))).convert("RGBA")
    return manifest, wind, imgs


def decode_uv(img, bbox, lng, lat, uv_range=(-30.0, 30.0)):
    W, H = img.size
    lngMin, latMin, lngMax, latMax = bbox
    x = max(0, min(W - 1, int(round((lng - lngMin) / (lngMax - lngMin) * (W - 1)))))
    y = max(0, min(H - 1, int(round((latMax - lat) / (latMax - latMin) * (H - 1)))))
    R, G, B, A = img.getpixel((x, y))
    if A == 0:
        return None
    lo, hi = uv_range
    u = lo + (R / 255) * (hi - lo)
    v = lo + (G / 255) * (hi - lo)
    return (math.hypot(u, v) * KT, (math.degrees(math.atan2(-u, -v)) + 360) % 360)


# ---- outside model sources (Open-Meteo) -------------------------------------

_OM_CACHE: dict = {}

def open_meteo(lat, lng, date):
    key = (round(lat, 3), round(lng, 3), date)
    if key not in _OM_CACHE:
        url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
            "latitude": lat, "longitude": lng,
            "hourly": "wind_speed_10m,wind_direction_10m", "wind_speed_unit": "kn",
            "models": "ecmwf_ifs025,gfs_seamless",
            "start_date": date, "end_date": date, "timezone": "UTC"})
        req = urllib.request.Request(url, headers=UA)
        _OM_CACHE[key] = json.load(urllib.request.urlopen(req, timeout=30))["hourly"]
    return _OM_CACHE[key]


def model_at(hourly, mdl, tkey):
    try:
        i = hourly["time"].index(tkey)
    except (ValueError, KeyError):
        return None
    s = hourly.get("wind_speed_10m_" + mdl)
    d = hourly.get("wind_direction_10m_" + mdl)
    if not s or i >= len(s) or s[i] is None:
        return None
    return (float(s[i]), float(d[i]))


def _dir_delta(a, b):
    if a is None or b is None:
        return None
    return round(abs((a - b + 180) % 360 - 180), 0)


# ---- main -------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", default="local",
                    help="'local' (public/data) or a base URL e.g. https://shouldidive.com")
    args = ap.parse_args()
    source = OUT_DIR.as_posix() if args.source == "local" else args.source.rstrip("/")

    manifest, wind, imgs = load_ours(args.source if args.source != "local" else "local")
    bbox = manifest["bbox"]
    uvr = tuple(wind.get("uv_range", (-30.0, 30.0)))
    buoys = {b.stn: b for b in fetch_buoy_winds()}

    print(f"Wind spot-check  ({args.source})  our source: {wind.get('source')}")
    print(f"wind.generated_at = {wind.get('generated_at')}")
    rows = []
    for slot in ("now", "p6h"):
        if slot not in imgs:
            continue
        valid = wind["windows"][slot]["valid_at"]
        date, tkey = valid[:10], valid[:16]
        print(f"\n== {slot}  valid {valid}  (kt / dir-from) ==")
        for b in BUOYS:
            ours = decode_uv(imgs[slot], bbox, b["lng"], b["lat"], uvr)
            hh = open_meteo(b["lat"], b["lng"], date)
            ec = model_at(hh, "ecmwf_ifs025", tkey)
            gf = model_at(hh, "gfs_seamless", tkey)
            bw = buoys.get(b["stn"]) if slot == "now" else None
            buoy = (bw.spd10 * KT, (math.degrees(math.atan2(-bw.u10, -bw.v10)) + 360) % 360) if bw else None

            def f(t):
                return "  --   " if not t else f"{t[0]:4.1f}/{t[1]:03.0f}"
            print(f"  {b['stn']:>5} {b['name'][:20]:<20} "
                  f"ours {f(ours)} | buoy {f(buoy)} | ECMWF {f(ec)} | GFS {f(gf)}")
            rows.append({
                "slot": slot, "valid_at": valid, "stn": b["stn"], "name": b["name"],
                "lat": b["lat"], "lng": b["lng"],
                "ours_kt": round(ours[0], 1) if ours else None,
                "ours_dir": round(ours[1]) if ours else None,
                "buoy_kt": round(buoy[0], 1) if buoy else None,
                "ecmwf_kt": round(ec[0], 1) if ec else None,
                "gfs_kt": round(gf[0], 1) if gf else None,
                "d_buoy_kt": round(abs(ours[0] - buoy[0]), 1) if (ours and buoy) else None,
                "d_ecmwf_kt": round(abs(ours[0] - ec[0]), 1) if (ours and ec) else None,
                "d_gfs_kt": round(abs(ours[0] - gf[0]), 1) if (ours and gf) else None,
                "d_buoy_dir": _dir_delta(ours[1] if ours else None, buoy[1] if buoy else None),
            })

    def _mean(key):
        vals = [r[key] for r in rows if r.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "checked_source": args.source,
        "our_wind_source": wind.get("source"),
        "our_generated_at": wind.get("generated_at"),
        "n_points": len(rows),
        "mean_abs_delta_vs_buoy_kt": _mean("d_buoy_kt"),
        "mean_abs_delta_vs_ecmwf_kt": _mean("d_ecmwf_kt"),
        "mean_abs_delta_vs_gfs_kt": _mean("d_gfs_kt"),
        "mean_abs_dir_delta_vs_buoy_deg": _mean("d_buoy_dir"),
        "rows": rows,
    }
    print(f"\nmean |Δ| vs buoy {summary['mean_abs_delta_vs_buoy_kt']} kt / "
          f"{summary['mean_abs_dir_delta_vs_buoy_deg']}°  |  "
          f"vs ECMWF {summary['mean_abs_delta_vs_ecmwf_kt']} kt  |  "
          f"vs GFS {summary['mean_abs_delta_vs_gfs_kt']} kt")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "wind_spotcheck.json").write_text(json.dumps(summary, indent=2),
                                                    encoding="utf-8")
    _write_md(summary)
    print(f"wrote {REPORT_DIR/'wind_spotcheck.json'} + .md")
    return 0


def _write_md(s: dict) -> None:
    lines = [
        "# Wind spot-check", "",
        f"_{s['generated_at']} — checked `{s['checked_source']}`_", "",
        f"- our source: **{s['our_wind_source']}** (gen {s['our_generated_at']})",
        f"- mean |Δspeed| vs **buoy** {s['mean_abs_delta_vs_buoy_kt']} kt, "
        f"|Δdir| {s['mean_abs_dir_delta_vs_buoy_deg']}°",
        f"- mean |Δspeed| vs **ECMWF** {s['mean_abs_delta_vs_ecmwf_kt']} kt, "
        f"vs **GFS** {s['mean_abs_delta_vs_gfs_kt']} kt", "",
        "| slot | buoy | ours kt/dir | buoy kt | ECMWF kt | GFS kt | Δbuoy | Δecmwf |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in s["rows"]:
        lines.append(
            f"| {r['slot']} | {r['name']} | "
            f"{r['ours_kt']}/{r['ours_dir']} | {r['buoy_kt']} | {r['ecmwf_kt']} | "
            f"{r['gfs_kt']} | {r['d_buoy_kt']} | {r['d_ecmwf_kt']} |")
    (REPORT_DIR / "wind_spotcheck.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
