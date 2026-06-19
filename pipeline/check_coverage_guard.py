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

Floors mirror ``pipeline/tests/test_data_integrity.py::LAYER_VALID_FRAC_FLOOR``
(the gate this guard keeps green). Keep the two in sync — a unit test asserts it.

Run:  python pipeline/check_coverage_guard.py
"""
from __future__ import annotations

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
