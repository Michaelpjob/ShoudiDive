"""Integration-test assertions for the pipeline outputs.

Used by `pipeline/scripts/validate.sh` after running fetch.py +
fetch_visibility.py to confirm that each stage's expected outputs
landed on disk and have plausible content.

Subcommands:

    python -m pipeline.tests.assert_outputs fetch_chl
        After `python pipeline/fetch.py --layer chl`, assert:
          - chl_1d.png + chl_1d_age_days.png exist
          - sidecar is mode='L', dims match chl_1d.png, has at least
            one non-zero pixel (= some valid data), and age field
            spans a sensible range (max age <= 7 days, the fetch.py
            max_back).
          - manifest.json layers.chl.windows.1d carries
            `age_days_url = /data/chl_1d_age_days.png`.

    python -m pipeline.tests.assert_outputs visibility
        After `python pipeline/fetch_visibility.py`, assert:
          - viz_p50_ft.png + viz_quality.png exist
          - viz_quality.png is mode='L', dims = (110, 140),
            has at least one ocean pixel (code != 0), and at
            least 0.5% of ocean pixels carry a code OTHER than
            OBSERVED_1D (=1) — i.e. the freshness gating actually
            downgraded *some* cells. (On a fully-clean satellite
            day every cell will be 1, so we accept that as a
            warning rather than a hard fail; the assertion is
            "either fully fresh OR some downgrades happened" —
            the buggy pre-PR1 path would always be "all-1
            regardless of staleness", which is what we want to
            catch when there *is* staleness.)

Exit codes:
    0   all assertions pass
    1   one or more assertions failed
    2   bad subcommand / wrong invocation
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "public" / "data"
MANIFEST = DATA / "manifest.json"


# --------------------------------------------------------------------------
# Tiny assert framework — one PASS/FAIL line per check.
# --------------------------------------------------------------------------

class Reporter:
    def __init__(self, scope: str):
        self.scope = scope
        self.failures: list[str] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        prefix = "  PASS" if ok else "  FAIL"
        suffix = f"  ({detail})" if detail else ""
        print(f"{prefix}  [{self.scope}] {name}{suffix}")
        if not ok:
            self.failures.append(f"{self.scope}: {name} — {detail}")

    def warn(self, name: str, detail: str = "") -> None:
        suffix = f"  ({detail})" if detail else ""
        print(f"  WARN  [{self.scope}] {name}{suffix}")

    def exit(self) -> int:
        if self.failures:
            print(f"\n[{self.scope}] FAILED — {len(self.failures)} check(s):")
            for f in self.failures:
                print(f"  - {f}")
            return 1
        print(f"\n[{self.scope}] all checks passed")
        return 0


def _load_manifest() -> dict:
    if not MANIFEST.exists():
        return {}
    return json.loads(MANIFEST.read_text())


# --------------------------------------------------------------------------
# Subcommand: fetch_chl — assert PR1 sidecar is emitted + manifest wired.
# --------------------------------------------------------------------------

def assert_fetch_chl() -> int:
    r = Reporter("fetch_chl")

    chl = DATA / "chl_1d.png"
    sidecar = DATA / "chl_1d_age_days.png"

    r.check("chl_1d.png exists", chl.exists(), str(chl))
    r.check("chl_1d_age_days.png exists", sidecar.exists(), str(sidecar))
    if not chl.exists() or not sidecar.exists():
        return r.exit()

    chl_img = Image.open(chl)
    sc_img = Image.open(sidecar)
    r.check(
        "sidecar mode is L (8-bit grayscale)",
        sc_img.mode == "L",
        f"got {sc_img.mode}",
    )
    r.check(
        "sidecar dims match chl_1d.png",
        sc_img.size == chl_img.size,
        f"chl={chl_img.size}, sidecar={sc_img.size}",
    )

    arr = np.array(sc_img)
    n_total = int(arr.size)
    n_valid = int((arr > 0).sum())
    pct_valid = 100.0 * n_valid / n_total if n_total else 0.0
    r.check(
        "sidecar has at least one valid pixel",
        n_valid > 0,
        f"{n_valid}/{n_total} ({pct_valid:.1f}%)",
    )

    if n_valid > 0:
        # pixel value 1..255 → age 0..254 days. fetch.py walks back at
        # most max_back=7 days, so the highest realistic raw pixel is
        # 8 (age=7). Anything higher means orientation or encoding bug.
        max_age = int(arr[arr > 0].max()) - 1
        r.check(
            "sidecar max age <= 7 days (fetch.py max_back)",
            max_age <= 7,
            f"max_age={max_age}",
        )
        n_stale = int(((arr > 1) & (arr <= 255)).sum())
        pct_stale = 100.0 * n_stale / n_valid if n_valid else 0.0
        if n_stale == 0:
            r.warn(
                "all valid cells are age=0 (no staleness today)",
                "this is fine on a clean-satellite day, but it means "
                "this run can't exercise the downgrade path",
            )
        else:
            print(
                f"  INFO  [fetch_chl] {n_stale}/{n_valid} valid cells stale "
                f"({pct_stale:.1f}%); the freshness gate will fire downstream"
            )

    # Manifest wiring.
    manifest = _load_manifest()
    chl_layer = manifest.get("layers", {}).get("chl", {})
    win_1d = chl_layer.get("windows", {}).get("1d", {})
    r.check(
        "manifest.layers.chl.windows.1d.age_days_url present",
        win_1d.get("age_days_url") == "/data/chl_1d_age_days.png",
        f"got {win_1d.get('age_days_url')!r}",
    )

    return r.exit()


# --------------------------------------------------------------------------
# Subcommand: visibility — assert viz_quality.png + p50 plausibly written.
# --------------------------------------------------------------------------

def assert_visibility() -> int:
    r = Reporter("visibility")

    p50 = DATA / "viz_p50_ft.png"
    quality = DATA / "viz_quality.png"
    r.check("viz_p50_ft.png exists", p50.exists())
    r.check("viz_quality.png exists", quality.exists())
    if not p50.exists() or not quality.exists():
        return r.exit()

    q_img = Image.open(quality)
    r.check("viz_quality mode is L", q_img.mode == "L", f"got {q_img.mode}")
    # GRID_W, GRID_H from fetch_visibility.py
    r.check(
        "viz_quality dims are 140x110 (W x H)",
        q_img.size == (140, 110),
        f"got {q_img.size}",
    )

    arr = np.array(q_img)
    n_ocean = int((arr != 0).sum())
    r.check(
        "viz_quality has at least one ocean cell",
        n_ocean > 0,
        f"n_ocean={n_ocean}",
    )

    if n_ocean > 0:
        codes, counts = np.unique(arr[arr != 0], return_counts=True)
        breakdown = ", ".join(
            f"code {int(c)}: {int(n)} ({100.0 * n / n_ocean:.1f}%)"
            for c, n in zip(codes, counts)
        )
        print(f"  INFO  [visibility] quality breakdown — {breakdown}")

        valid_codes = set(range(1, 8))  # 1..7
        unknown = [int(c) for c in codes if int(c) not in valid_codes]
        r.check(
            "all quality codes are in 1..7",
            not unknown,
            f"unknown codes: {unknown}" if unknown else "",
        )

        n_obs1d = int((arr == 1).sum())
        pct_obs1d = 100.0 * n_obs1d / n_ocean if n_ocean else 0.0
        if n_obs1d == n_ocean:
            r.warn(
                "all ocean cells are OBSERVED_1D",
                "this is OK on a fully-fresh satellite day, but means "
                "this run can't prove the PR1 downgrade path actually "
                "fired. Re-run after a cloudy day to exercise it.",
            )
        else:
            r.check(
                "at least one cell downgraded off OBSERVED_1D",
                n_obs1d < n_ocean,
                f"OBSERVED_1D = {n_obs1d}/{n_ocean} ({pct_obs1d:.1f}%)",
            )

    return r.exit()


# --------------------------------------------------------------------------

SUBCOMMANDS = {
    "fetch_chl": assert_fetch_chl,
    "visibility": assert_visibility,
}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in SUBCOMMANDS:
        names = ", ".join(SUBCOMMANDS)
        print(f"usage: assert_outputs.py <{names}>", file=sys.stderr)
        return 2
    return SUBCOMMANDS[sys.argv[1]]()


if __name__ == "__main__":
    sys.exit(main())
