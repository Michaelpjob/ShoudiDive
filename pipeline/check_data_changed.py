"""Silent-fetcher detector — fails the refresh workflow if today's
output PNGs are byte-identical to what's already committed on this
branch.

Runs AFTER the fetcher steps in refresh-*-data.yml, BEFORE the commit
step. The idea: the fetcher may report success (continue-on-error
swallows timeouts), but if the resulting PNG content didn't change
at all, the fetcher silently failed and is writing back the cached
prior output.

We've hit this exact bug class three times in the last week:
  * 2026-05-15→17: SST/chl/kd490 fetcher timed out at 15min on every
    run, swallowed by continue-on-error. mtime kept advancing because
    `python -m pipeline.fetch` rewrote the PNG header even when the
    underlying data didn't change. Live-cp-manifest caught it 58 hours
    later via the top-level generated_at not moving.
  * 2026-05-20: precip fetcher IndexError on every run, swallowed by
    continue-on-error. PNG byte-identical to prior runs for ~5 days.
  * 2026-05-21: rcca scraper bombing on a malformed CSV. CSV-derived
    PNGs (no per-cell pixels but the watchdog summary) were unchanged
    for ~5 days.

`check_fetch_freshness.py` already catches the FIRST case (mtime).
This script catches the case where mtime updates but the bytes are
identical (cached output served back, or fetcher caught the
exception and wrote NaN/zeros silently).

Output: exit 0 if any of the canonical PNGs changed since the
previous git HEAD, exit 1 if ALL canonical PNGs are byte-identical
(which is the silent-failure signature — real ocean data DOES change
day to day on at least one of these layers).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


# The canonical "if these don't change, something is structurally
# wrong" PNG set. Limited to the layers that this script's caller
# (refresh-*-data.yml) actually re-writes — NOT wind, which lives in
# refresh-*-wind.yml's hourly cycle. Including wind here would false
# positive on every daily run because refresh-*-data leaves the wind
# PNG untouched at whatever the last wind cron wrote.
#
# All three of these (SST, chl, kd490) are written by fetch.py +
# downstream during every refresh-*-data run. At minimum ONE of them
# should change between consecutive daily runs — real ocean
# observations rarely repeat byte-identically across 24 h.
CANONICAL_PNGS_BY_REGION = {
    "ca": [
        "public/data/sst_1d.png",
        "public/data/chl_1d.png",
        "public/data/kd490_1d.png",
    ],
    "pnw": [
        "public/data/pnw/sst_1d.png",
        "public/data/pnw/chl_1d.png",
        "public/data/pnw/kd490_1d.png",
    ],
    "tropical": [
        "public/data/tropical/sst_1d.png",
        "public/data/tropical/chl_1d.png",
        "public/data/tropical/kd490_1d.png",
    ],
}


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _file_hash(path: Path) -> str | None:
    if not path.exists():
        return None
    return _sha256_bytes(path.read_bytes())


def _git_show_hash(rel_path: str) -> str | None:
    """Return the sha256 of <rel_path> at git HEAD. None if the file
    is new (not in HEAD yet) or if git fails."""
    try:
        # `git show HEAD:<path>` streams the file content; we hash it
        # ourselves so we're comparing apples-to-apples with the
        # working-tree _file_hash. `git rev-parse --verify` first to
        # avoid the noisy stderr if the file doesn't exist in HEAD.
        result = subprocess.run(
            ["git", "show", f"HEAD:{rel_path}"],
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        # Git missing on this host — can't compare, treat as new file.
        return None
    if result.returncode != 0:
        return None
    return _sha256_bytes(result.stdout)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--region",
        default=os.environ.get("SHOULDIDIVE_REGION", "ca"),
        choices=sorted(CANONICAL_PNGS_BY_REGION.keys()),
        help="Which region's canonical PNGs to check.",
    )
    args = p.parse_args()

    pngs = CANONICAL_PNGS_BY_REGION[args.region]
    findings: list[str] = []

    any_changed = False
    for rel in pngs:
        path = ROOT / rel
        new_hash = _file_hash(path)
        old_hash = _git_show_hash(rel)
        if new_hash is None:
            findings.append(f"  {rel}: missing on disk (skipped)")
            continue
        if old_hash is None:
            # First commit of this file — treat as a change.
            findings.append(f"  {rel}: new file (not in HEAD yet) — CHANGED")
            any_changed = True
            continue
        if new_hash == old_hash:
            findings.append(f"  {rel}: byte-identical to HEAD ({new_hash[:12]})")
        else:
            findings.append(
                f"  {rel}: HEAD {old_hash[:12]} → now {new_hash[:12]} — CHANGED"
            )
            any_changed = True

    region = args.region
    print(f"[check_data_changed] region={region}, files:")
    for f in findings:
        print(f)

    if not any_changed:
        print(
            f"FAIL: all {len(pngs)} canonical PNGs for region={region} are "
            f"byte-identical to git HEAD. Real ocean data should produce "
            f"at least one different byte across SST + chl + kd490 layers. "
            f"This is the silent-fetcher signature — exactly the bug class "
            f"that's slipped past `continue-on-error: true` multiple times "
            f"in the last week. Check the fetcher logs for swallowed "
            f"exceptions (look for `[error]The action ... has timed out` "
            f"or unexpected `IndexError`/`KeyError` Python tracebacks)."
        )
        return 1

    print(
        f"OK: region={region} canonical PNGs include at least one cell that "
        f"changed since the last commit."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
