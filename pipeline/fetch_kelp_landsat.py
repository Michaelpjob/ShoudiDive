#!/usr/bin/env python
"""Refresh the compact CA+Baja Landsat kelp extract.

Pulls the latest revision of the SBC LTER "Kelp from Landsat" dataset
(EDI knb-lter-sbc.74 — a ~2.6 GB NetCDF, station-list schema with
area(time, station) in m^2 per 30 m pixel) and extracts the CA+Baja
ever-kelp pixels (extent) plus the recent-year canopy into a small
committed NetCDF (pipeline/data/landsat_kelp_ca.nc, ~1 MB).

build_spot_bundles.py reads that compact file to draw per-spot kelp for
the spots CDFW doesn't cover (Coronados / Baja, NorCal bull kelp, the
islands CDFW timed out on). This script is run quarterly by
.github/workflows/refresh-kelp.yml so the spot bundles always rebuild
against fresh canopy without a 2.6 GB download in the daily pipeline.

Where the NetCDF comes from: since 2026-07-30 EDI's PASTA API refuses
anonymous requests (HTTP 403 "... (Public Access) is not authorized ..."),
which broke every quarterly run from 2026-07-05 on. The same data entity is
still public through DataONE: the CN Solr index lists every revision, and
EDI's own GMN member node (gmn.lternet.edu) proxies the PASTA bytes with a
public read policy. So the default path is DataONE (no credentials), with
the download checked against DataONE's SHA-1. If that path ever closes, set
EDI_API_KEY (an EDI access key, https://auth.edirepository.org/auth/ui/key)
and the script falls back to PASTA, passing the key as `?key=` the way
EDI's own EDIutils client does.

Usage:
  python pipeline/fetch_kelp_landsat.py                 # download latest (DataONE, then PASTA)
  python pipeline/fetch_kelp_landsat.py --resolve-only  # print the source, no download
  python pipeline/fetch_kelp_landsat.py --source X.nc   # use a local NetCDF
"""
import argparse
import hashlib
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple, Optional
from urllib.parse import quote

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = REPO_ROOT / "pipeline" / "data" / "landsat_kelp_ca.nc"
PASTA = "https://pasta.lternet.edu/package"
DATAONE_CN = "https://cn.dataone.org/cn/v2"
EDI_KEY_PAGE = "https://auth.edirepository.org/auth/ui/key"
SCOPE, PKG_ID = "knb-lter-sbc", "74"
# CA + Baja Pacific kelp box. Reaches down to 26.4N / -113.5 so the Baja
# Pacific kelp forests (Isla Cedros ~28.2N, Islas San Benito ~28.3N,
# Bahia Tortugas ~27.7N, Punta Abreojos ~26.7N) are captured alongside the
# full CA coast (to ~40.5N). 2026-06-16: southern edge dropped 27.0 → 26.4
# to bring Abreojos inside coverage. The California-Current kelp dataset
# carries no Sea-of-Cortez stations, so the eastern margin harmlessly spans
# the peninsula. Margin on all sides.
BBOX = (26.4, 40.5, -125.0, -113.5)   # lat_min, lat_max, lon_min, lon_max
# 2026-06-16: 4 → 12 quarters. A single year's canopy was hostage to one
# bad season (La Jolla read 0.02 km² off the 4-quarter window); the last
# 3 years' PEAK is a steadier "current bed" while still excluding the
# decade-old pre-collapse extent.
RECENT_QUARTERS = 12                   # last 3 years' peak = the "canopy" class


class EdiAccessError(RuntimeError):
    """EDI / DataONE refused the request or answered with something unparseable."""


class KelpSource(NamedTuple):
    url: str
    rev: int
    via: str                                  # "DataONE" or "PASTA"
    params: Optional[dict] = None             # query params for the download (PASTA key)
    checksum: Optional[tuple] = None          # (algorithm, hex digest) when known
    size: Optional[int] = None                # bytes, when known


AUTH_HINT = (
    "EDI has required authentication on its PASTA API since 2026-07-30. "
    f"Create an access key at {EDI_KEY_PAGE} (sign in with Google, Microsoft, "
    "GitHub or ORCID) and "
    "store it as the EDI_API_KEY repository secret."
)


def _pasta_get(path, key=None):
    """GET a PASTA text endpoint; raise EdiAccessError on anything but a 200.

    The error never includes the request URL, which carries the key.
    """
    try:
        r = requests.get(f"{PASTA}/{path}", params={"key": key} if key else None,
                         timeout=60)
    except requests.RequestException as e:
        raise EdiAccessError(f"PASTA /{path}: {type(e).__name__}") from None
    if r.status_code != 200:
        body = " ".join(r.text.split())[:200]
        hint = f" {AUTH_HINT}" if r.status_code in (401, 403) else ""
        raise EdiAccessError(
            f"PASTA /{path} -> HTTP {r.status_code}: {body}.{hint}")
    return r.text


def parse_revisions(text):
    """PASTA's revision list is whitespace-separated integers; anything else
    (an auth refusal, an HTML error page) is an error, not a ValueError."""
    toks = text.split()
    if not toks or not all(t.isdigit() for t in toks):
        raise EdiAccessError(
            f"PASTA revision list for {SCOPE}.{PKG_ID} is not a list of "
            f"integers: {' '.join(toks)[:200]!r}. {AUTH_HINT}")
    return [int(t) for t in toks]


def latest_netcdf_url(key):
    """Resolve the newest revision's NetCDF data entity on PASTA (needs a key)."""
    rev = max(parse_revisions(_pasta_get(f"eml/{SCOPE}/{PKG_ID}", key)))
    ents = _pasta_get(f"data/eml/{SCOPE}/{PKG_ID}/{rev}", key).split()
    named = [(e, _pasta_get(f"name/eml/{SCOPE}/{PKG_ID}/{rev}/{e}", key).strip())
             for e in ents]
    # The package ships a single NetCDF data entity, named by a
    # descriptive title (e.g. "Satellite kelp biomass since 1984") rather
    # than a filename. Match it by keyword, falling back to the sole entity.
    kw = ("biomass", "kelp", "canopy", "satellite", "landsat", ".nc")
    pick = next((p for p in named if any(k in p[1].lower() for k in kw)), None)
    if pick is None and named:
        pick = named[0]
    if pick is None:
        raise EdiAccessError(f"no data entity in {SCOPE}.{PKG_ID} r{rev}")
    ent, name = pick
    print(f"  EDI {SCOPE}.{PKG_ID} r{rev} via PASTA: {name}")
    return KelpSource(f"{PASTA}/data/eml/{SCOPE}/{PKG_ID}/{rev}/{ent}", rev,
                      "PASTA", params={"key": key})


def dataone_netcdf():
    """Resolve the newest revision's NetCDF through DataONE (anonymous).

    DataONE indexes each PASTA data entity under its PASTA URL as the PID,
    so the revision is the path segment after the package id. The CN's
    resolve call answers 303 -> the member-node object URL; it is read
    without following the redirect so the 2.4 GB body isn't pulled here.
    """
    prefix = f"https://pasta.lternet.edu/package/data/eml/{SCOPE}/{PKG_ID}/"
    q = "id:" + re.sub(r"([:/\-])", r"\\\1", prefix) + "* AND fileName:*.nc"
    try:
        r = requests.get(f"{DATAONE_CN}/query/solr/", timeout=60, params={
            "q": q, "wt": "json", "rows": "1000",
            "fl": "id,fileName,size,checksum,checksumAlgorithm"})
        r.raise_for_status()
        docs = r.json()["response"]["docs"]
    except (requests.RequestException, ValueError, KeyError) as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        raise EdiAccessError(
            f"DataONE Solr query failed ({status or type(e).__name__})") from None
    revs = [(int(m.group(1)), d) for d in docs
            if (m := re.match(re.escape(prefix) + r"(\d+)/", d.get("id", "")))]
    if not revs:
        raise EdiAccessError(f"DataONE lists no NetCDF for {SCOPE}.{PKG_ID}")
    rev, doc = max(revs, key=lambda p: p[0])
    pid = doc["id"]
    try:
        res = requests.get(f"{DATAONE_CN}/resolve/{quote(pid, safe='')}",
                           allow_redirects=False, timeout=60)
    except requests.RequestException as e:
        raise EdiAccessError(f"DataONE resolve failed ({type(e).__name__})") from None
    url = res.headers.get("Location")
    if not url:
        m = re.search(r"<url>([^<]+)</url>", res.text)
        url = m.group(1) if m else None
    if res.status_code not in (200, 303) or not url:
        raise EdiAccessError(
            f"DataONE resolve {pid} -> HTTP {res.status_code}, no object URL")
    checksum = None
    if doc.get("checksum") and doc.get("checksumAlgorithm"):
        checksum = (doc["checksumAlgorithm"], doc["checksum"].lower())
    print(f"  EDI {SCOPE}.{PKG_ID} r{rev} via DataONE: {doc.get('fileName')}")
    return KelpSource(url, rev, "DataONE", checksum=checksum, size=doc.get("size"))


def resolve_netcdf():
    """DataONE first (no credentials); PASTA with EDI_API_KEY as the fallback."""
    errors = []
    try:
        return dataone_netcdf()
    except EdiAccessError as e:
        errors.append(f"DataONE: {e}")
    key = os.environ.get("EDI_API_KEY", "").strip()
    if not key:
        errors.append(f"PASTA: skipped, EDI_API_KEY is not set. {AUTH_HINT}")
    else:
        try:
            return latest_netcdf_url(key)
        except EdiAccessError as e:
            errors.append(f"PASTA: {e}")
    raise EdiAccessError(
        f"could not resolve the {SCOPE}.{PKG_ID} NetCDF:\n  " + "\n  ".join(errors))


def _hasher(algorithm):
    """DataONE names algorithms like 'SHA-1' / 'MD5'; hashlib wants 'sha1'."""
    return hashlib.new(algorithm.replace("-", "").lower())


def download(src, dest):
    print(f"  downloading ({src.via}) -> {dest}")
    h = _hasher(src.checksum[0]) if src.checksum else None
    with requests.get(src.url, params=src.params, stream=True, timeout=900) as r:
        if r.status_code in (401, 403):
            raise EdiAccessError(
                f"{src.via} download refused: HTTP {r.status_code}. {AUTH_HINT}")
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
                if h:
                    h.update(chunk)
    got = os.path.getsize(dest)
    print(f"  {got / 1e6:.0f} MB")
    if src.size is not None and got != int(src.size):
        raise EdiAccessError(
            f"short download: {got} bytes, DataONE lists {src.size}")
    if h and h.hexdigest() != src.checksum[1]:
        raise EdiAccessError(
            f"{src.checksum[0]} mismatch: got {h.hexdigest()}, "
            f"DataONE lists {src.checksum[1]}")


def extract(nc_path, rev=None):
    import numpy as np
    import xarray as xr
    ds = xr.open_dataset(nc_path)
    lon = ds.longitude.values
    lat = ds.latitude.values
    print("  loading area grid (decompresses the source)…")
    area = ds.area.values                       # (time, station) m^2/pixel
    ever = np.nanmax(area, axis=0)              # peak over all years
    recent = np.nanmax(area[-RECENT_QUARTERS:, :], axis=0)  # last 3 yr peak
    la_min, la_max, lo_min, lo_max = BBOX
    keep = (
        (lat >= la_min) & (lat <= la_max)
        & (lon >= lo_min) & (lon <= lo_max)
        & (ever > 0)
    )
    n = int(keep.sum())
    recent_q = str(ds.time.values[-1])[:10]
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    out = xr.Dataset(
        {
            "longitude":   ("station", lon[keep].astype("float32")),
            "latitude":    ("station", lat[keep].astype("float32")),
            "recent_area": ("station", np.nan_to_num(recent[keep]).astype("float32")),
            "ever_area":   ("station", ever[keep].astype("float32")),
        },
        attrs={
            "source": f"SBC LTER {SCOPE}.{PKG_ID}"
                      f"{f' r{rev}' if rev else ''} Kelp from Landsat",
            "recent_quarter": recent_q,
            "recent_window_quarters": RECENT_QUARTERS,
            "pixel_m": 30,
            "bbox": str(BBOX),
        },
    )
    enc = {v: {"zlib": True, "complevel": 6} for v in out.data_vars}
    out.to_netcdf(OUT_PATH, encoding=enc)
    print(f"  wrote {OUT_PATH.relative_to(REPO_ROOT)} "
          f"({os.path.getsize(OUT_PATH) // 1024} KB, {n} stations, "
          f"canopy quarter {recent_q})")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", help="local NetCDF path (skip the EDI download)")
    ap.add_argument("--resolve-only", action="store_true",
                    help="print the resolved source and exit (no download)")
    args = ap.parse_args()
    if args.source:
        extract(args.source)
        return 0
    try:
        src = resolve_netcdf()
    except EdiAccessError as e:
        # GitHub annotations are one line; %0A keeps the per-source reasons.
        msg = str(e)
        if os.environ.get("GITHUB_ACTIONS"):
            msg = "::error::" + msg.replace("\n", "%0A")
        print(msg, file=sys.stderr)
        return 1
    if args.resolve_only:
        print(f"  r{src.rev} via {src.via}"
              + (f", {int(src.size) / 1e9:.2f} GB" if src.size else "")
              + (f", {src.checksum[0]} {src.checksum[1]}" if src.checksum else ""))
        return 0
    with tempfile.TemporaryDirectory() as td:
        dest = os.path.join(td, "kelp.nc")
        download(src, dest)
        extract(dest, src.rev)
    return 0


if __name__ == "__main__":
    sys.exit(main())
