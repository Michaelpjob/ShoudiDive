"""Static reference overlays for the Kelp Paddy Finder map.

Three frame-of-reference marker sets, baked into data.json at build time so the
CSP-clean tool reads them from its own bundle (no extra same-origin fetches):

  * spots   — the main app's saved DIVE SPOTS (name + center), filtered to the
              SoCal field (the NorCal spots fall off the paddy map).
  * banks   — named offshore FISHING BANKS from features.py (9-Mile, Cortes…).
  * harbors — a curated SoCal LAUNCH-HARBOR list (supersedes the old 4-launch
              set); each is selectable as the ruler/launch anchor.

`apply(data, repo_root)` mutates a data dict in place; both build_site.py (full
model run) and the lightweight regen path call it, so the two stay in sync.
"""
import glob
import json
import os

import features

# SoCal Bight field bounds — must match the tool's FIELD_BBOX / _lib.js BBOX.
# Markers outside it would sit off the paddy map, so we drop them.
LAT_MIN, LAT_MAX = 31.0, 34.8
LNG_MIN, LNG_MAX = -121.5, -116.8

DEFAULT_LAUNCH = "San Diego (Mission Bay)"

# Curated SoCal launch harbors / ramps, ordered N -> S for a tidy dropdown.
# name -> [lat, lng]. Entrance-ish coords; exact enough for reference markers.
HARBORS = {
    "Santa Barbara": [34.40, -119.69],
    "Ventura": [34.247, -119.267],
    "Oxnard / Channel Islands": [34.16, -119.22],
    "Marina del Rey": [33.961, -118.447],
    "Redondo / King Harbor": [33.845, -118.398],
    "San Pedro / LA Harbor": [33.708, -118.273],
    "Long Beach": [33.754, -118.118],
    "Huntington Harbour": [33.726, -118.073],
    "Newport Harbor": [33.603, -117.901],
    "Dana Point": [33.46, -117.70],
    "Oceanside": [33.211, -117.397],
    "San Diego (Mission Bay)": [32.77, -117.25],
    "San Diego Bay (Shelter Is.)": [32.713, -117.232],
}


def _in_field(lat, lng):
    return LAT_MIN <= lat <= LAT_MAX and LNG_MIN <= lng <= LNG_MAX


def _dive_spots(repo_root):
    """Name + bbox-center for each main-app dive spot inside the SoCal field."""
    out = []
    pattern = os.path.join(repo_root, "public", "data", "spots", "*", "bundle.json")
    for bj in sorted(glob.glob(pattern)):
        try:
            d = json.load(open(bj, encoding="utf-8"))
        except Exception:
            continue
        b = d.get("bbox") or {}
        if not all(k in b for k in ("lat_min", "lat_max", "lng_min", "lng_max")):
            continue
        lat = round((b["lat_min"] + b["lat_max"]) / 2, 4)
        lng = round((b["lng_min"] + b["lng_max"]) / 2, 4)
        if _in_field(lat, lng):
            name = d.get("name") or os.path.basename(os.path.dirname(bj))
            out.append({"name": name, "lat": lat, "lng": lng})
    return out


def _banks():
    """Named offshore banks (the fishing structure) from features.py."""
    out = []
    for name, lng, lat, ftype in features.OFFSHORE_FEATURES:
        if ftype == "bank" and _in_field(lat, lng):
            label = name[4:] if name.startswith("the ") else name  # tidy the article
            out.append({"name": label, "lat": round(lat, 4), "lng": round(lng, 4)})
    return out


def apply(data, repo_root):
    """Bake the reference overlays + the fuller harbor list into a data dict."""
    data["launches"] = dict(HARBORS)
    data["default_launch"] = DEFAULT_LAUNCH
    data["reference"] = {"spots": _dive_spots(repo_root), "banks": _banks()}
    return data
