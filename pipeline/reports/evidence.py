"""The data behind a verdict.

When a verdict is outside, marginal or not rated, the report shows what it
would take to understand it: the agency's own words, what the nearest buoy
measures right now, the hour-by-hour wind and seas at that range from our
own forecast frames (so a reader can see when it opens or closes), and the
CDIP MOP nearshore wave model at each shore entry.

  mop_site()        CDIP MOP alongshore point (10 m depth line), 3-hourly,
                    read over OPeNDAP's ASCII form: no netCDF stack needed.
  hourly_point()    our wind (HRRR/GFS/ECMWF) and wave (WaveWatch III) frames,
                    public/data/{wind,swell}/hourly/d{D}_h{HH}_*.png
"""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timezone
from pathlib import Path

from . import sources

MOP_URL = "https://thredds.cdip.ucsd.edu/thredds/dodsC/cdip/model/MOP_alongshore/{id}_forecast.nc"
H_TENTH = 1.27  # Rayleigh: mean of the highest 10% of waves = 1.27 x significant height


def _floats(block: str) -> list[float]:
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", block)]


def parse_dap_ascii(text: str) -> dict[str, list[float]]:
    """OPeNDAP .ascii response -> {variable: values}."""
    out: dict[str, list[float]] = {}
    body = text.split("---------------------------------------------", 1)[-1]
    for m in re.finditer(r"^([A-Za-z]\w*)(?:\[\d+\])?,?\s*(.*?)(?=^[A-Za-z]\w*(?:\[\d+\])?,?\s|\Z)", body, re.M | re.S):
        name, rest = m.group(1), m.group(2)
        vals = _floats(rest)
        if vals:
            out[name] = vals
    return out


def mop_site(mop_id: str, now: datetime | None = None) -> dict:
    url = MOP_URL.format(id=mop_id)
    label = f"CDIP MOP nearshore wave model, point {mop_id}"
    q = url + ".ascii?metaShoreNormal,metaWaterDepth,waveTime,waveHs,waveTp,waveDp"

    def run():
        r = sources._get(q)
        d = parse_dap_ascii(r.text)
        times = [datetime.fromtimestamp(t, tz=timezone.utc) for t in d["waveTime"]]
        series = [{"t": sources.iso(t), "hs_m": hs, "tp_s": tp, "dp_deg": dp}
                  for t, hs, tp, dp in zip(times, d["waveHs"], d["waveTp"], d["waveDp"]) if hs > -10]
        issued = None
        try:  # the run's issue time lives in the global attributes, not the data
            m = re.search(r'date_issued "([^"]+)"', sources._get(url + ".das").text)
            issued = sources.parse_iso(m.group(1)) if m else None
        except Exception:  # noqa: BLE001
            pass
        return sources.source(f"mop_{mop_id}", label, url + ".html", ok=bool(series), time=issued,
                              data={"shore_normal": d.get("metaShoreNormal", [None])[0],
                                    "depth_m": d.get("metaWaterDepth", [None])[0], "series": series}, now=now)
    return sources._guard(f"mop_{mop_id}", label, url, run)


def mop_window(mop: dict, start: datetime, end: datetime) -> dict | None:
    """The biggest MOP step inside a window (3-hourly), in feet."""
    pts = [p for p in mop.get("series", []) if start <= sources.parse_iso(p["t"]) <= end]
    if not pts:
        return None
    big = max(pts, key=lambda p: p["hs_m"])
    hs_ft = big["hs_m"] * 3.28084
    return {"hs_ft": round(hs_ft, 1), "sets_ft": round(hs_ft * H_TENTH, 1), "tp_s": round(big["tp_s"]),
            "dp_deg": round(big["dp_deg"]), "at": big["t"],
            "steps": [{"t": p["t"], "hs_ft": round(p["hs_m"] * 3.28084, 1), "tp_s": round(p["tp_s"]),
                       "dp_deg": round(p["dp_deg"])} for p in pts]}


# --- Our own hourly forecast frames ------------------------------------------

def _bbox(manifest: dict) -> tuple[float, float, float, float]:
    lng0, lat0, lng1, lat1 = manifest["bbox"]
    return lng0, lat0, lng1, lat1


def _pixel(img, w: int, h: int, bbox, lat: float, lng: float):
    """Nearest valid pixel (alpha > 0) to a point, searching a small ring."""
    lng0, lat0, lng1, lat1 = bbox
    x = (lng - lng0) / (lng1 - lng0) * w - 0.5
    y = (lat1 - lat) / (lat1 - lat0) * h - 0.5
    xi, yi = int(round(x)), int(round(y))
    best = None
    for r in range(0, 3):
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                xx, yy = xi + dx, yi + dy
                if 0 <= xx < w and 0 <= yy < h:
                    px = img.getpixel((xx, yy))
                    if px[3] > 0:
                        d = dx * dx + dy * dy
                        if best is None or d < best[0]:
                            best = (d, px)
        if best:
            return best[1]
    return None


def _day_index(summary_path: Path, day: date) -> int | None:
    try:
        anchor = date.fromisoformat(json.loads(summary_path.read_text(encoding="utf-8"))["anchor_date"])
    except (OSError, ValueError, KeyError):
        return None
    return (day - anchor).days


def hourly_point(lat: float, lng: float, day: date, hours: range, manifest: dict, data_dir: Path | None = None) -> list[dict]:
    """Wind (kt, from-direction) and waves (Hs ft, Tp s, Dp) per local hour."""
    from PIL import Image  # local import: only the build needs Pillow

    data_dir = data_dir or sources.DATA
    bbox = _bbox(manifest)
    wi = _day_index(data_dir / "wind" / "summary.json", day)
    si = _day_index(data_dir / "swell" / "summary.json", day)
    out = []
    for hr in hours:
        row: dict = {"hour": hr}
        if wi is not None and wi >= 0:
            p = data_dir / "wind" / "hourly" / f"d{wi}_h{hr:02d}_uv.png"
            if p.exists():
                with Image.open(p) as im:
                    im = im.convert("RGBA")
                    px = _pixel(im, im.width, im.height, bbox, lat, lng)
                if px:
                    u = -30 + px[0] / 255 * 60
                    v = -30 + px[1] / 255 * 60
                    row["wind_kt"] = round(math.hypot(u, v) * 1.94384)
                    row["wind_from_deg"] = round((math.degrees(math.atan2(-u, -v)) + 360) % 360)
        if si is not None and si >= 0:
            p = data_dir / "swell" / "hourly" / f"d{si}_h{hr:02d}_wave.png"
            if p.exists():
                with Image.open(p) as im:
                    im = im.convert("RGBA")
                    px = _pixel(im, im.width, im.height, bbox, lat, lng)
                if px:
                    row["hs_ft"] = round(px[0] / 255 * 12 * 3.28084, 1)
                    row["tp_s"] = round(px[1] / 255 * 25)
                    row["dp_deg"] = round(px[2] / 255 * 360)
        out.append(row)
    return out


def compass(deg: float | None) -> str:
    if deg is None:
        return ""
    names = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return names[int((deg % 360) / 22.5 + 0.5) % 16]


def hour_label(h: int) -> str:
    return f"{h % 12 or 12} {'AM' if h < 12 else 'PM'}"


def windows(rows: list[dict], wind_max: float, seas_max: float) -> list[dict]:
    """Consecutive hours whose modeled wind and waves are within the range's
    'within' tiers. A row missing either value breaks a window."""
    out, cur = [], None
    for r in rows:
        ok = r.get("wind_kt") is not None and r.get("hs_ft") is not None and r["wind_kt"] <= wind_max and r["hs_ft"] <= seas_max
        if ok and cur is None:
            cur = {"from": r["hour"], "to": r["hour"]}
        elif ok:
            cur["to"] = r["hour"]
        elif cur is not None:
            out.append(cur)
            cur = None
    if cur is not None:
        out.append(cur)
    return [{"from": hour_label(w["from"]), "to": hour_label(w["to"] + 1)} for w in out]
