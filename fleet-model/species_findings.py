"""Plain-text findings from public/fleet-species/data.json: for each species,
when it shows up in the posted counts, and where AIS stops and report spots
put it in each season. Used for the write-up; the page shows the same data.

    python fleet-model/species_findings.py
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
D = json.load(open(os.path.join(HERE, "..", "public", "fleet-species", "data.json"), encoding="utf-8"))
SEASONS = [("Spring (Mar-May)", [3, 4, 5]), ("Summer (Jun-Aug)", [6, 7, 8]), ("Fall (Sep-Nov)", [9, 10, 11]), ("Winter (Dec-Feb)", [12, 1, 2])]


def near(h):
    c = D["cells"][h]
    return f"near {c['near'][0]} ({c['near'][1]:.0f} nm)" if c.get("near") else f"{c['c'][0]:.2f},{c['c'][1]:.2f}"


def main():
    print(f"counts {D['meta']['counts_window']}, AIS {D['meta']['ais_window']}, matched {D['meta']['n_matched']}, reports {D['meta'].get('n_reports')}")
    for cat in D["cats"]:
        k = cat["key"]
        cal = D["calendar"][k]
        by_month = {}
        for w, (n, f) in zip(D["weeks"], cal):
            by_month.setdefault(w[:7], [0, 0])
            by_month[w[:7]][0] += n
            by_month[w[:7]][1] += f
        peak = sorted(by_month.items(), key=lambda x: -x[1][0])[:4]
        s = D["summary"][k]
        print(f"\n=== {cat['label']}: {s['posted_trips']} posted trips, {s['fish']} fish | AIS-placed {s['located_trips']} | report mentions {s.get('report_mentions')} (at a charted spot {s.get('report_located')})")
        print("  busiest months:", ", ".join(f"{m} {v[0]} trips/{v[1]} fish" for m, v in peak))
        for name, months in SEASONS:
            on = lambda mo: int(mo[5:7]) in months  # noqa: E731
            cells = {}
            for h, mm in D["grid"].get(k, {}).items():
                pos = sum(v[0] for mo, v in mm.items() if on(mo))
                tot = sum(v[0] for mo, v in D["effort"].get(h, {}).items() if on(mo))
                if pos:
                    cells[h] = (pos, tot)
            spots = {}
            for n, e in D["rspots"].items():
                pos = sum(v for mo, v in e["sp"].get(k, {}).items() if on(mo))
                tot = sum(v for mo, v in e["tot"].items() if on(mo))
                if pos:
                    spots[n] = (pos, tot)
            if not cells and not spots:
                continue
            tc = sorted(cells.items(), key=lambda x: -x[1][0])[:4]
            ts = sorted(spots.items(), key=lambda x: -x[1][0])[:5]
            print(f"  {name}:")
            if tc:
                print("    AIS:", "; ".join(f"{near(h)} {p}/{t} trips" for h, (p, t) in tc))
            if ts:
                print("    reports:", "; ".join(f"{n} {p}/{t}" for n, (p, t) in ts))


if __name__ == "__main__":
    main()
