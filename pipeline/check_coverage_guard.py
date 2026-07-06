#!/usr/bin/env python
"""Producer-side coverage guard. Run AFTER the fetch steps, BEFORE the commit
step, in the refresh workflows.

A flaky / failing ERDDAP fetch can produce a near-empty layer PNG — chl_1d.png
fell to **9% valid** on 2026-06-16 when both SST sources were down. Committing
that degrades the live layer AND breaks the ``test_no_nan_floods`` gate for
every open PR (CI tests PRs merged-with-main). This guard checks each fragile
primary layer's valid-cell fraction against its floor and, if it came out below
the floor, **restores the last-good PNG from git HEAD** — so a bad fetch keeps
the previous good data on disk instead of publishing garbage. Region-aware via
SHOULDIDIVE_REGION.

A restore also reverts the layer's manifest metadata (its ``1d`` window +
``generated_at``) to HEAD's values. Without that, the manifest advertises
today's dates over yesterday's restored pixels and the frontend confidence
UI — which reads window observation dates — shows "fresh" for data that
isn't. Honest staleness beats optimistic timestamps.

Floors mirror ``pipeline/tests/test_data_integrity.py::LAYER_VALID_FRAC_FLOOR``
(the gate this guard keeps green). Keep the two in sync — a unit test asserts it.

Run:  python pipeline/check_coverage_guard.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

try:
    from pipeline.regions import active_region
except ModuleNotFoundError:
    from regions import active_region

REPO_ROOT = Path(__file__).resolve().parents[1]
REGION = active_region()
REGION_NAME = REGION.name if hasattr(REGION, "name") else "ca"
OUT_DIR = REGION.data_output_dir(REPO_ROOT)

# (artifact -> minimum valid-cell fraction), per region. The 1-day composites
# are the fragile ones (a single bad satellite day empties them); the 2d/3d
# averages and kd490 are robust. Mirror of LAYER_VALID_FRAC_FLOOR.
FLOORS = {
    "ca":       {"sst_1d.png": 0.20, "chl_1d.png": 0.10},
    "pnw":      {"sst_1d.png": 0.20, "chl_1d.png": 0.01},
    "tropical": {"sst_1d.png": 0.30, "chl_1d.png": 0.05},
    "baja":     {"sst_1d.png": 0.20, "chl_1d.png": 0.05},
}

# artifact -> (manifest layer id, window key) whose metadata must revert
# alongside a restored PNG so the manifest never claims fresh dates over
# last-good pixels.
ARTIFACT_MANIFEST_WINDOW = {
    "sst_1d.png": ("sst", "1d"),
    "chl_1d.png": ("chl", "1d"),
}


def merge_restored_window(current: dict, head: dict, layer_id: str, window_key: str) -> bool:
    """Copy `layer_id`'s `window_key` window + generated_at from the HEAD
    manifest into the current one. Pure dict surgery so it's unit-testable;
    returns True if anything changed. Only the restored window reverts —
    sibling windows (2d/3d) keep their fresh metadata since their PNGs
    weren't restored."""
    head_layer = (head.get("layers") or {}).get(layer_id) or {}
    cur_layer = (current.get("layers") or {}).get(layer_id)
    if not head_layer or not isinstance(cur_layer, dict):
        return False
    changed = False
    head_win = (head_layer.get("windows") or {}).get(window_key)
    if isinstance(head_win, dict):
        cur_layer.setdefault("windows", {})[window_key] = head_win
        changed = True
    if head_layer.get("generated_at"):
        cur_layer["generated_at"] = head_layer["generated_at"]
        changed = True
    return changed


def restore_manifest_metadata(artifact: str) -> None:
    mapping = ARTIFACT_MANIFEST_WINDOW.get(artifact)
    manifest_path = OUT_DIR / "manifest.json"
    if mapping is None or not manifest_path.exists():
        return
    layer_id, window_key = mapping
    rel = manifest_path.relative_to(REPO_ROOT).as_posix()
    r = subprocess.run(
        ["git", "show", f"HEAD:{rel}"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"  [guard] {artifact}: manifest metadata NOT reverted "
              f"({r.stderr.strip() or 'no HEAD manifest'})")
        return
    try:
        head_manifest = json.loads(r.stdout)
        current = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"  [guard] {artifact}: manifest metadata NOT reverted (bad JSON: {exc})")
        return
    if merge_restored_window(current, head_manifest, layer_id, window_key):
        manifest_path.write_text(json.dumps(current, indent=2))
        print(f"  [guard] {artifact}: manifest {layer_id}.windows.{window_key} + "
              f"generated_at reverted to HEAD (matches restored pixels)")


def valid_frac(path: Path) -> float:
    """Fraction of cells with data (pixel > 0 = not the no-data sentinel)."""
    im = np.array(Image.open(path))
    if im.ndim == 3:
        chan = im[..., 3] if im.shape[2] == 4 else im[..., 0]
    else:
        chan = im
    return float((chan > 0).mean())


def main() -> int:
    floors = FLOORS.get(REGION_NAME, FLOORS["ca"])
    restored = []
    for artifact, floor in floors.items():
        path = OUT_DIR / artifact
        if not path.exists():
            continue
        vf = valid_frac(path)
        if vf >= floor:
            print(f"  [guard] {artifact}: {vf*100:.1f}% valid (floor {floor*100:.0f}%) — OK")
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        r = subprocess.run(
            ["git", "checkout", "HEAD", "--", rel],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        if r.returncode == 0:
            print(f"  [guard] {artifact}: {vf*100:.1f}% valid < {floor*100:.0f}% floor "
                  f"— RESTORED last-good from HEAD (fetch likely hit a dead source)")
            restore_manifest_metadata(artifact)
            restored.append(artifact)
        else:
            # No HEAD version (first run) or git error — leave it; the gate
            # will flag it loudly, which is the right escalation.
            print(f"  [guard] {artifact}: {vf*100:.1f}% < {floor*100:.0f}% floor but "
                  f"restore failed ({r.stderr.strip() or 'no HEAD version'}) — leaving as-is")
    if restored:
        print(f"Coverage guard restored {len(restored)} degraded layer(s) for "
              f"region={REGION_NAME}: {', '.join(restored)}")
    else:
        print(f"Coverage guard: all checked layers OK for region={REGION_NAME}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
