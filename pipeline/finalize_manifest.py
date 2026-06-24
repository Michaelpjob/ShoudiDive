"""Reconcile each manifest layer's grid dims to the actual PNG it renders.

WHY THIS EXISTS
---------------
The per-region refresh workflows (refresh-ca-data.yml, refresh-baja-data.yml,
...) commit manifest.json + PNGs straight to main, bypassing the PR gate. A
partial / timed-out refresh can update the PNGs without the manifest's grid
metadata (or vice versa), leaving the two disagreeing — e.g. SST switched to
the 586x511 MUR source but the manifest still claimed the 234x206 NOAA-blended
grid. That tripped `test_manifest_grid_dims_match_png_dims` and turned the dev
gate red on main, blocking every open PR until it was reconciled by hand.

This tool reads each layer's actual PNG — following window urls AND
`summary_url`, which the dims test does NOT, so it also catches sst7d/sst5d —
and sets the manifest grid to match.

  * reconcile (default): rewrite the manifest grid to the real PNG dims.
  * --check: report mismatches and exit 1 (use as a CI / cron guard).

It only ever changes grid width/height to the real PNG dimensions, so it can
only make the manifest MORE correct. Missing PNGs are skipped and it never
raises on bad data, so it is safe to run before a refresh commits.

Run:
  python -m pipeline.finalize_manifest                 # reconcile all regions
  python -m pipeline.finalize_manifest --region ca
  python -m pipeline.finalize_manifest --check         # report-only, exit 1 if desynced
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "public" / "data"

# region -> manifest path. ca is the top-level manifest; the others nest under
# public/data/<region>/ (see the region-data-ownership convention).
REGIONS = {
    "ca": DATA_DIR / "manifest.json",
    "baja": DATA_DIR / "baja" / "manifest.json",
    "pnw": DATA_DIR / "pnw" / "manifest.json",
    "tropical": DATA_DIR / "tropical" / "manifest.json",
}


def _png_dims(path: Path):
    """(width, height) of a PNG, or None if it can't be read."""
    from PIL import Image
    try:
        with Image.open(path) as im:
            return im.size
    except Exception:
        return None


def _region_base(region: str) -> Path:
    return DATA_DIR if region == "ca" else DATA_DIR / region


def _resolve(url, region: str) -> Path | None:
    """Map a manifest url (/data/... or /data/<region>/...) to an existing file."""
    if not isinstance(url, str) or url.startswith("http"):
        return None
    p = url.lstrip("/")
    if p.startswith("data/"):
        p = p[len("data/"):]
    if region != "ca" and p.startswith(f"{region}/"):
        p = p[len(region) + 1:]
    cand = _region_base(region) / p
    return cand if cand.exists() else None


def _layer_png(info: dict, region: str) -> Path | None:
    """One representative PNG for a layer — window urls first, then summary_url."""
    for win in (info.get("windows") or {}).values():
        if not isinstance(win, dict):
            continue
        for field in ("url", "speed_url", "uv_url", "wave_url"):
            p = _resolve(win.get(field), region)
            if p:
                return p
    # Layers like sst7d / sst5d carry no window PNG — they point at a summary
    # JSON that lists the real PNGs. The dims test can't follow this; we can.
    summary = _resolve(info.get("summary_url"), region)
    if summary:
        try:
            txt = summary.read_text(encoding="utf-8")
        except Exception:
            return None
        for ref in re.findall(r"[\w./-]+\.png", txt):
            p = _resolve(ref, region)
            if p:
                return p
    return None


def reconcile_region(region: str, manifest_path: Path, check_only: bool) -> list[str]:
    """Reconcile one region's manifest. Returns a list of change descriptions."""
    if not manifest_path.exists():
        return []
    try:
        orig = manifest_path.read_text(encoding="utf-8")
        m = json.loads(orig)
    except Exception as exc:
        print(f"  {region}: cannot read manifest ({exc})")
        return []

    changes: list[str] = []
    for lid, info in (m.get("layers") or {}).items():
        if not isinstance(info, dict):
            continue
        grid = info.get("grid") or {}
        try:
            ew, eh = int(grid["width"]), int(grid["height"])
        except (KeyError, TypeError, ValueError):
            continue
        png = _layer_png(info, region)
        if png is None:
            continue
        dims = _png_dims(png)
        if dims is None:
            continue
        aw, ah = dims
        if (aw, ah) != (ew, eh):
            changes.append(f"{region}/{lid}: {ew}x{eh} -> {aw}x{ah} ({png.name})")
            if not check_only:
                info["grid"]["width"], info["grid"]["height"] = aw, ah

    if changes and not check_only:
        out = json.dumps(m, indent=2)
        if orig.endswith("\n"):
            out += "\n"  # preserve the file's trailing-newline state -> minimal diff
        manifest_path.write_text(out, encoding="utf-8")
    return changes


def main() -> None:
    ap = argparse.ArgumentParser(description="Reconcile manifest grid dims to the real PNGs.")
    ap.add_argument("--region", default="all", choices=[*REGIONS, "all"])
    ap.add_argument("--check", action="store_true",
                    help="report mismatches without writing; exit 1 if any layer is desynced")
    args = ap.parse_args()

    regions = list(REGIONS) if args.region == "all" else [args.region]
    all_changes: list[str] = []
    for r in regions:
        changes = reconcile_region(r, REGIONS[r], args.check)
        for c in changes:
            print(("  MISMATCH " if args.check else "  FIX ") + c)
        all_changes += changes

    verb = "mismatched" if args.check else "reconciled"
    print(f"finalize_manifest: {len(all_changes)} layer(s) {verb}")
    if args.check and all_changes:
        sys.exit(1)


if __name__ == "__main__":
    main()
