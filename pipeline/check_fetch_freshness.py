"""Post-fetch sentinel — exits non-zero if a fetcher silently produced no
fresh output.

Used by the `refresh-*-data.yml` workflows right after the ocean-fetch
step. That step carries `continue-on-error: true` so a `timeout-minutes`
hit is otherwise swallowed; this sentinel turns that swallowed timeout
back into a workflow-level failure that lights up the cron inbox.

Pattern: check the on-disk mtime of a canonical PNG that the fetcher
*must* have written. If the file is missing or its mtime is older than
the threshold, the fetcher didn't actually run successfully — fail.

Why mtime, not the manifest:
  * The manifest's `generated_at` gets bumped by the LAST fetcher to
    run, even if earlier fetchers timed out. So the manifest can look
    fresh while individual layers are silently frozen.
  * The PNG mtime reflects whether THIS fetcher wrote anything in the
    current workflow run — which is exactly what we want to gate on.

Resolves the region from `SHOULDIDIVE_REGION` so the same script works
in all four refresh-*-data.yml workflows without hard-coded paths.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


# Canonical sentinel PNG per layer family. Picked because:
#   * sst_1d.png — present in every region, always written by fetch.py
#   * chl_1d.png — present in every region, always written by fetch.py
#   * kd490_1d.png — same
LAYER_SENTINEL = {
    "sst": "sst_1d.png",
    "chl": "chl_1d.png",
    "kd490": "kd490_1d.png",
}


def region_data_dir() -> Path:
    """Resolve the region's data directory.

    CA lives at public/data/ (no slug). PNW + tropical nest under
    public/data/<region>/. Matches the data_output_dir convention
    used by the rest of the pipeline.
    """
    region = os.environ.get("SHOULDIDIVE_REGION", "ca")
    if region == "ca":
        return ROOT / "public" / "data"
    return ROOT / "public" / "data" / region


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--layer",
        required=True,
        choices=sorted(LAYER_SENTINEL.keys()),
        help="Layer family whose sentinel PNG to check.",
    )
    p.add_argument(
        "--max-age-minutes",
        type=int,
        default=30,
        help=(
            "Fail if the sentinel PNG's mtime is older than this many "
            "minutes (default 30). Should be larger than the healthy "
            "fetcher runtime and smaller than its timeout-minutes."
        ),
    )
    args = p.parse_args()

    sentinel = region_data_dir() / LAYER_SENTINEL[args.layer]
    region = os.environ.get("SHOULDIDIVE_REGION", "ca")

    if not sentinel.exists():
        print(f"FAIL: sentinel PNG missing: {sentinel.relative_to(ROOT)}")
        print(f"      Layer={args.layer} region={region}")
        print(f"      The fetcher didn't run at all, OR the workflow's")
        print(f"      checkout step landed on a commit that predates the layer.")
        return 1

    age_seconds = time.time() - sentinel.stat().st_mtime
    age_minutes = age_seconds / 60.0

    if age_minutes > args.max_age_minutes:
        print(
            f"FAIL: {sentinel.relative_to(ROOT)} mtime is {age_minutes:.1f} "
            f"min old (threshold {args.max_age_minutes} min)."
        )
        print(f"      Layer={args.layer} region={region}")
        print(f"      The previous fetch step almost certainly hit `timeout-minutes`")
        print(f"      and `continue-on-error: true` swallowed it. Check the run log")
        print(f"      for `##[error]The action ... has timed out` in the step that")
        print(f"      writes {LAYER_SENTINEL[args.layer]}.")
        return 1

    print(
        f"OK: {sentinel.relative_to(ROOT)} refreshed "
        f"{age_minutes:.1f} min ago (layer={args.layer} region={region})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
