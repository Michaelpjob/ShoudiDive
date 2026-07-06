"""Generate the ShoudiDive "Kelp Paddy Finder" tool bundle from a fresh model
run and drop it into ShoudiDive's public/paddies/ (a standalone tool, served
OUTSIDE the main map overlay).

    python build_site.py [TARGET_DIR]

TARGET_DIR defaults to $PADDIES_TARGET_DIR, then a sibling ShoudiDive checkout,
then the older sibling sd-kelp-paddies checkout.
The output (index.html, app.js, paddies.css, leaflet.*, data.json) is
CSP-clean and uses ShoudiDive's own /data/land.geojson as the basemap, so it
drops into Cloudflare Pages with no CSP changes. Re-run to refresh the data
snapshot; wiring it into the daily pipeline is the follow-up.
"""
import os
import sys
import time

import beds
import dashboard
import reference
import sitegen

def _default_target():
    here = os.path.dirname(os.path.abspath(__file__))
    env_target = os.environ.get("PADDIES_TARGET_DIR")
    if env_target:
        return env_target
    for repo in ("ShoudiDive", "sd-kelp-paddies"):
        candidate = os.path.join(here, "..", repo, "public", "paddies")
        if os.path.isdir(os.path.dirname(candidate)):
            return candidate
    return os.path.join(here, "..", "ShoudiDive", "public", "paddies")


def main():
    t0 = time.time()
    target = sys.argv[1] if len(sys.argv) > 1 else _default_target()
    target = os.path.abspath(target)
    data, _overview, _zones = dashboard.build()
    # Guard the foundation: the model MUST seed from the real Landsat canopy.
    # beds.py silently drops to coarse hand-placed beds (mostly mainland points)
    # if the canopy extract can't be read — which sheds paddies from the coast
    # and ships "kelp on land" data with no red check. Fail loud in CI
    # (PADDIES_REQUIRE_REAL_KELP) rather than committing the degraded fallback.
    print(f"  kelp source: {beds.KELP_SOURCE} ({len(beds.SCB_BEDS)} seed cells)")
    if beds.KELP_SOURCE.startswith("hand-fallback"):
        msg = (f"kelp seeding fell back to hand beds ({beds.KELP_SOURCE}); "
               "bundle paddies-model/data/landsat_kelp_ca.nc")
        if os.environ.get("PADDIES_REQUIRE_REAL_KELP"):
            raise SystemExit(f"FATAL: {msg}")
        print(f"  WARNING: {msg}")
    # Guard the land mask too: if land.geojson can't be found, the mask is
    # empty -> the green likelihood field + drift cones are NOT clipped to
    # water and nothing beaches, so the tool ships "kelp on land" (the exact
    # symptom on a Linux CI runner where the old hard-coded Windows path
    # failed). Fail loud rather than publish a degraded snapshot.
    cov = data.get("model_meta", {}).get("landmask_cov", 0.0)
    print(f"  land mask coverage: {cov*100:.1f}% of bbox")
    if not cov or cov <= 0:
        msg = ("land mask empty (land.geojson not found) — green field + cones "
               "won't clip to water and nothing beaches; check PADDIES_LOCAL_DATA "
               "or public/data/land.geojson")
        if os.environ.get("PADDIES_REQUIRE_REAL_KELP"):
            raise SystemExit(f"FATAL: {msg}")
        print(f"  WARNING: {msg}")
    # Bake the static reference overlays (dive spots / banks / harbors) into the
    # bundle. repo_root = the sd-kelp-paddies root holding public/data/spots.
    reference.apply(data, os.path.dirname(os.path.dirname(target)))
    out = sitegen.write_bundle(data, target)
    files = sorted(os.listdir(out))
    total = sum(os.path.getsize(os.path.join(out, f)) for f in files)
    print(f"  wrote site bundle -> {out}")
    print(f"  {len(files)} files, {total/1024:.0f} KB total: {', '.join(files)}")
    print(f"  done in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
