# Fleet Tracks: where the SoCal sportfishing fleet stops, from public AIS

Standalone tool at `/fleet/` (bundle in `public/fleet/`, same pattern as the
Kelp Paddy Finder in `public/paddies/`). This directory is the model that
builds `public/fleet/data.json`.

## Pipeline

```
fetch_ais.py   NOAA OCM daily AIS (csv.zst, national)  ->  $FLEET_DATA_DIR/socal/<date>.parquet
roster.py      berth discovery + landing fleet lists    ->  data/roster.json   (committed)
catch.py       landing dock counts, one page per day    ->  $FLEET_DATA_DIR/counts.parquet
model.py       positions -> trips -> stops -> H3 zones -> the three answers
build_site.py  model.run() + counts join                ->  public/fleet/data.json
```

`$FLEET_DATA_DIR` defaults to a sibling `ShoudiDive-fleet-data/` next to the
checkout; the raw AIS is tens of GB and never enters git.

```bash
python fleet-model/fetch_ais.py --start 2025-06-01 --end 2025-10-31 --workers 4
python fleet-model/roster.py            # needs $FLEET_DATA_DIR/roster_web.json (fleet lists)
python fleet-model/catch.py --start 2025-06-01 --end 2025-10-31
python fleet-model/build_site.py public/fleet
```

## Data

* **AIS**: USCG Nationwide AIS via NOAA Office for Coastal Management,
  `https://noaaocm.blob.core.windows.net/ais/csv2/csv<year>/ais-<date>.csv.zst`.
  One position per vessel per minute, US EEZ only, terrestrial receivers only,
  published about two months in arrears (2026 is available through June as of
  2026-10-05). CC0. Columns: mmsi, base_date_time (UTC), lon, lat, sog, cog,
  heading, vessel_name, imo, call_sign, vessel_type, status, length, width,
  draft, transceiver.
* **Fleet lists**: `roster_web.json` scraped from the landings' own fleet pages
  and sportfishingreport.com per-boat pages (name, landing, length, capacity,
  trip types, MMSI when a public AIS directory had it).
* **Dock counts**: sportfishingreport.com `/landings/<slug>.php?month=&year=&day=`
  (one table per landing-day: boat, trip type, anglers, species counts). Its
  robots.txt allows crawling; we fetch at one request per second and cache
  every page. Facts, credited in the UI.

## Method

**Roster.** A hull is accepted when its AIS name matches a fleet-list row and
it either sleeps at that landing's berth (01:00-04:00 local, SOG < 0.5 kt,
within 600 m) or at least visits the dock (within 1.5 km) and looks like a
party boat (passenger/fishing type, or a 14-45 m pleasure-coded hull); or
when the fleet list carries its MMSI. One hull per fleet-list row. Everything
else that merely looks the part (Island Packers ferries, whale-watch and dive
boats, tugs named INDEPENDENCE) is written to `candidates` for review and
`data/roster_overrides.json` can force accept/drop/rename by MMSI.

**Trips.** In harbor = within 2.5 km of any landing dock (bay, channel, bait
receiver). A trip is a maximal out-of-harbor run lasting >= 1.5 h and reaching
>= 3 km from the dock. AIS gaps do not end a trip: coverage = positions /
elapsed minutes is carried on every trip, because shore receivers fade at
roughly 30-50 nm and the long-range fleet is mostly seen leaving and returning.

**Stops.** Drift/anchor = run at SOG < 2 kt for >= 10 min. Troll = run at
3.5-8.5 kt for >= 20 min whose net displacement / path length < 0.7 (a transit
is a straight line). Runs break at a > 15 min gap. Nothing inside the harbor
ring is a stop; the UI additionally hides stops within 6 km of a dock (bait
grounds, the kelp edge) unless asked.

**Zones.** H3 resolution 7 (about 1.4 km across, 5 km^2) for scoring and
display; resolution 8 (0.5 km) for the "same spot" test. Per cell: fishing
minutes, distinct trips, distinct boats, stops, trolling minutes;
convergence = a local day with >= 3 fleet boats in one cell.

**The three questions.**
1. Return rate: share of trip N+1's fishing minutes in res-8 cells fished on
   trip N (same spot), and within a one-ring of them (same area).
2. Home zone: share of a boat's season in its top cell, and the number of
   cells needed to cover half its fishing time.
3. Convergence: share of a boat's fishing days that had >= 3 fleet boats in its
   cell.

**Catch join.** The dock's posted count for (landing, boat, return date) is
attached to the trip; fish kept per angler is spread over the trip's cells
weighted by stop minutes. Multi-day trips get one count for all their stops,
which is the honest limit of the public data.

## Known limits

* Terrestrial AIS only: nothing past ~50 nm, nothing in Mexican waters. The
  Rockpile / Hidden Bank / Alijos end of the fleet's season is invisible
  without a satellite feed (Spire or similar, paid).
* Carriage is required at >= 65 ft or > 150 passengers; the six-pack and
  small open-party boats are not on AIS unless fitted voluntarily, and some
  65 ft boats broadcast only intermittently. `roster.json.unmatched_fleet_list`
  is the honest list of fleet boats never seen.
* Convergence is partly social (radio, radar, watching each other on AIS).
  Several boats in one cell is evidence the fleet thinks fish are there, not
  independent evidence of fish.
