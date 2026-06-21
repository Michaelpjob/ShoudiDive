#!/usr/bin/env python3
"""Reconcile public/data/manifest.json with the artifacts actually on disk.

WHY THIS EXISTS
---------------
fetch.py writes each layer's PNG + per-layer summary.json *incrementally*
inside build_layer(), but writes the consolidated manifest.json exactly
once — at the very end of main(), after every layer has been built. If a
later layer raises, or the job hits its (75-min) timeout on an ERDDAP-slow
day (the fetch step is continue-on-error), main() never reaches that final
manifest write. The PNGs + summaries on disk are then FRESH while
manifest.json still describes the PREVIOUS run:

  * each per-layer ``grid`` no longer matches its PNG dimensions — the
    frontend's bilinear sampler trusts ``grid`` blindly, so a stale grid
    silently stretches / mis-projects the layer with no visible error; and
  * the top-level ``generated_at`` is stale, so the freshness UI
    under-reports how current the map is.

This actually shipped on 2026-06-20: sst_1d.png + sst/summary.json advanced
to 234x206 @ 2026-06-20T08:53Z, but manifest.layers.sst stayed @ 2026-06-19
(grid 47x42-era) because that run's main() never finalized the manifest.
The coverage guard restoring a last-good PNG of different dimensions is a
second way the grid can drift from the manifest within a single run.

WHAT IT DOES
------------
Re-derives each layer's ``grid`` from the actual PNG, and each layer's
``generated_at`` from its summary.json sidecar (the authoritative
fetch-time stamp build_layer writes *before* the manifest), then bumps the
top-level ``generated_at`` to be at least as fresh as SST. The freshness
contract anchors on SST — wind has its own HOURLY refresh and is
legitimately newer than the daily top-level, so it is excluded. On-disk
artifacts are the source of truth; the manifest is the thing that drifts.

It runs as a SEPARATE workflow step (always(), after all fetchers) so it
survives a crashed / timed-out fetch.py — the exact failure mode a finalize
baked into fetch.py's own main() cannot cover.

  python pipeline/finalize_manifest.py                # reconcile + write
  python pipeline/finalize_manifest.py --check-only   # assert, never write

``--check-only`` re-runs the post-conditions WITHOUT writing: grid == PNG
for every layer, and top-level >= SST (5-minute grace). It is wired in as a
fail-loud pre-commit / pre-deploy gate so the daily cron — which commits
straight to main with no PR review — can't silently ship a manifest that
diverges from its PNGs. It mirrors the two assertions in
pipeline/tests/test_data_integrity.py (test_manifest_grid_dims_match_png_dims
and test_top_level_at_least_as_fresh_as_sst) that today run ONLY on PRs.

Region-agnostic: resolves the active region from $SHOULDIDIVE_REGION exactly
like fetch.py, so it covers ca / baja / pnw / tropical uniformly.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image

# Dual-import to match fetch.py: `python pipeline/finalize_manifest.py`
# (sys.path[0] = pipeline/) falls to the second arm, while
# `python -m pipeline.finalize_manifest` (sys.path[0] = repo root) takes the
# first. The cron uses the script-style invocation.
try:
    from pipeline.regions import active_region
except ModuleNotFoundError:
    from regions import active_region

ROOT = Path(__file__).resolve().parents[1]
_REGION = active_region()
SLUG = _REGION.data_dir_slug
OUT_DIR = _REGION.data_output_dir(ROOT)
MANIFEST_PATH = OUT_DIR / "manifest.json"

# Matches the grace window in test_top_level_at_least_as_fresh_as_sst: the
# per-layer finalize and the top-level bump can land on opposite sides of a
# second boundary.
FRESHNESS_GRACE = timedelta(minutes=5)


def _rel_to_outdir(url: str) -> Path | None:
    """Resolve a manifest ``/data/...`` URL to a file under OUT_DIR.

    Mirrors the URL->path logic in test_data_integrity.py exactly, so this
    finalize and the PR gate always agree on which file backs a layer.
    """
    if not isinstance(url, str) or not url or url.startswith("http"):
        return None
    p = url.lstrip("/")
    if p.startswith("data/"):
        p = p[len("data/"):]
    if SLUG != "ca" and p.startswith(f"{SLUG}/"):
        p = p[len(SLUG) + 1:]
    return OUT_DIR / p


def _primary_png(info: dict) -> Path | None:
    """First on-disk window PNG for a layer (any window / url field)."""
    for win in (info.get("windows") or {}).values():
        if not isinstance(win, dict):
            continue
        for field in ("url", "speed_url", "uv_url", "wave_url"):
            cand = _rel_to_outdir(win.get(field))
            if cand is not None and cand.exists():
                return cand
    return None


def _summary_gen_at(layer_id: str, info: dict) -> str | None:
    """generated_at from a layer's summary.json sidecar (its fetch-time stamp).

    build_layer writes summary.json *before* the manifest, so on a partial
    refresh the sidecar carries the true fresh timestamp while the manifest
    entry is stale.
    """
    cand = _rel_to_outdir(info.get("summary_url") or info.get("history_summary_url") or "")
    if cand is None or not cand.exists():
        cand = OUT_DIR / layer_id / "summary.json"
    if not cand.exists():
        return None
    try:
        return json.loads(cand.read_text()).get("generated_at")
    except Exception:
        return None


def _parse_ts(raw) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _png_dims(png: Path) -> tuple[int, int] | None:
    try:
        with Image.open(png) as im:
            return int(im.size[0]), int(im.size[1])
    except Exception as exc:  # corrupt / truncated PNG
        print(f"[finalize] WARN cannot read {png.name}: {exc}", file=sys.stderr)
        return None


def reconcile(manifest: dict) -> list[str]:
    """Re-sync per-layer grid + generated_at + top-level. Returns a change log."""
    layers = manifest.get("layers") or {}
    changed: list[str] = []
    for layer_id, info in layers.items():
        if not isinstance(info, dict):
            continue
        png = _primary_png(info)
        if png is not None:
            dims = _png_dims(png)
            if dims is not None:
                w, h = dims
                grid = info.get("grid") or {}
                if grid.get("width") != w or grid.get("height") != h:
                    info["grid"] = {"width": w, "height": h}
                    changed.append(
                        f"{layer_id}.grid {grid.get('width')}x{grid.get('height')} -> {w}x{h}"
                    )
        gen = _summary_gen_at(layer_id, info)
        if gen and info.get("generated_at") != gen:
            old = info.get("generated_at")
            info["generated_at"] = gen
            changed.append(f"{layer_id}.generated_at {old} -> {gen}")

    # Top-level anchors on SST (wind is excluded — hourly cadence). Never
    # moves backwards: max of the existing top-level and the SST stamp.
    sst_ts = _parse_ts((layers.get("sst") or {}).get("generated_at"))
    top_ts = _parse_ts(manifest.get("generated_at"))
    target = max([t for t in (sst_ts, top_ts) if t is not None], default=None)
    if target is not None:
        new_top = _iso(target)
        if manifest.get("generated_at") != new_top:
            old = manifest.get("generated_at")
            manifest["generated_at"] = new_top
            changed.append(f"generated_at {old} -> {new_top}")
    return changed


def verify(manifest: dict) -> list[str]:
    """Post-conditions the data_integrity gates enforce: grid == PNG for every
    layer, and top-level generated_at >= SST (within the grace window)."""
    problems: list[str] = []
    layers = manifest.get("layers") or {}
    for layer_id, info in layers.items():
        if not isinstance(info, dict):
            continue
        grid = info.get("grid") or {}
        try:
            ew, eh = int(grid["width"]), int(grid["height"])
        except (KeyError, TypeError, ValueError):
            continue  # no grid claim, nothing to validate
        png = _primary_png(info)
        if png is None:
            continue  # no backing PNG; covered by the URL-existence test
        dims = _png_dims(png)
        if dims is None:
            problems.append(f"{layer_id}: cannot read backing PNG {png.name}")
            continue
        if dims != (ew, eh):
            problems.append(
                f"{layer_id}: manifest grid {ew}x{eh} != {png.name} {dims[0]}x{dims[1]}"
            )

    sst_ts = _parse_ts((layers.get("sst") or {}).get("generated_at"))
    top_ts = _parse_ts(manifest.get("generated_at"))
    if sst_ts is not None and top_ts is not None and top_ts + FRESHNESS_GRACE < sst_ts:
        skew = (sst_ts - top_ts).total_seconds() / 60
        problems.append(
            f"top-level generated_at {_iso(top_ts)} is {skew:.1f} min behind "
            f"SST {_iso(sst_ts)} — the finalize step that bumps top-level "
            f"didn't run"
        )
    return problems


def _write_atomic(path: Path, text: str) -> None:
    # fetch.py writes json.dumps(indent=2) with NO trailing newline; match it
    # so a finalize never churns a spurious byte. Temp-then-replace so a crash
    # mid-write can't leave a truncated manifest.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Reconcile manifest.json with on-disk artifacts.")
    ap.add_argument(
        "--check-only",
        action="store_true",
        help="Assert grid==PNG + top-level>=SST without writing (fail-loud gate).",
    )
    args = ap.parse_args(argv)

    if not MANIFEST_PATH.exists():
        # Nothing committed yet (e.g. a brand-new region). Not an error.
        print(f"[finalize] no manifest at {MANIFEST_PATH}; nothing to do (region={SLUG})")
        return 0

    try:
        manifest = json.loads(MANIFEST_PATH.read_text())
    except Exception as exc:
        print(f"[finalize] FATAL: cannot parse {MANIFEST_PATH}: {exc}", file=sys.stderr)
        return 1

    if args.check_only:
        problems = verify(manifest)
        if problems:
            print(
                f"[finalize] CHECK FAILED for region={SLUG} ({len(problems)} problem(s)):",
                file=sys.stderr,
            )
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            return 1
        print(f"[finalize] check OK: manifest grid + freshness consistent (region={SLUG})")
        return 0

    changed = reconcile(manifest)
    if changed:
        _write_atomic(MANIFEST_PATH, json.dumps(manifest, indent=2))
        print(f"[finalize] reconciled manifest ({len(changed)} change(s), region={SLUG}):")
        for c in changed:
            print(f"  - {c}")
    else:
        print(f"[finalize] manifest already matches on-disk artifacts (region={SLUG})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
