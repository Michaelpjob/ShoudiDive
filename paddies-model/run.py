"""Kelp-paddy FINDER prototype — full lifecycle to a positioning layer.

    python run.py [live|swell|warm] ["Launch Name"]

Outputs -> out/<scenario>/ : index.html (interactive map with ranked
hotspots), drift_map.png (annotated proof), density.png, hotspots.json,
floating/beached/sunk/beds/tracks.geojson, meta.json.
"""
import os
import sys
import time

import config
import beds as beds_mod
import forcing as forcing_mod
import detachment as detach_mod
import drift as drift_mod
import findability as find_mod
import hotspots as hot_mod
import render as render_mod
from landmask import LandMask


def main():
    scenario = sys.argv[1] if len(sys.argv) > 1 else config.SCENARIO
    launch_name = sys.argv[2] if len(sys.argv) > 2 else config.DEFAULT_LAUNCH
    launch_ll = config.LAUNCHES.get(launch_name, config.LAUNCHES[config.DEFAULT_LAUNCH])
    config.OUT_DIR = os.path.join("out", scenario)
    t0 = time.time()
    print(f"== Kelp-paddy FINDER (SCB) | scenario={scenario} | launch={launch_name} ==")

    forcing = forcing_mod.build_forcing(scenario)
    landmask = LandMask()
    now_h = forcing.now_hours()

    detach = detach_mod.compute(forcing, beds_mod.SCB_BEDS, now_h, config.RELEASE_AGES_DAYS)
    print(f"  INPUT abundance {detach['index']}/100 ({detach['band']}) | "
          f"peak Hs={detach['peak_hs_m']}m SST={detach['peak_sst_c']}C | driver={detach['dominant']}")

    result = drift_mod.run_drift(forcing, landmask, detach)
    m = result["meta"]
    print(f"  FATE  afloat={m['n_floating']} ({int(m['frac_floating']*100)}%)  "
          f"beached={m['n_beached']} ({int(m['frac_beached']*100)}%)  "
          f"sunk={m['n_sunk']} ({int(m['frac_sunk']*100)}%)  "
          f"| float_amount={m['float_amount']} -> ~{m['est_floating_paddies']:,} paddies")

    dens = find_mod.build(result["floating"], landmask)
    hotspots = hot_mod.extract(dens, landmask, launch_ll)
    print(f"  findability peak={dens['peak']:.2f} (ref {dens['ref']}) | {len(hotspots)} hotspots:")
    for h in hotspots:
        print(f"    #{h['rank']} {h['distance_nm']}nm {h['compass']} "
              f"({h['lat']},{h['lng']}) str={h['strength']} reach={h['reachable']}")

    render_mod.write_all(result, dens, hotspots, launch_name, launch_ll, config.OUT_DIR)
    print(f"  wrote {config.OUT_DIR}/ | done in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
