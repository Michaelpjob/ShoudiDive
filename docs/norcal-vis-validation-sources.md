# NorCal vis-report sources — checklist

For validating the `norcal_*` zone calibration in `PR-NC-1`. Target: ≥20 dated NorCal observations with (lat, lng, depth_ft, observed_secchi_ft); ≥80 % within ±5 ft of `viz_p50_ft` before promoting the new zone defaults.

Sorted from easiest / highest leverage at the top to slow but rigorous at the bottom. Tick each one as you work through it.

---

## Tier 1 — Automated data feeds (best for ongoing validation)

- [ ] **CeNCOOS Monterey Wharf shore station** — turbidity, chl-a, CTD every 15 min since 2012, maintained by Moss Landing Marine Labs. Convert turbidity → Secchi via the standard Holmes/Preisendorfer empirical fit. This is the closest thing to "live ground truth" for Monterey Bay.
  - Portal: https://data.cencoos.org/
  - Effort: Medium — ERDDAP fetch + a small turbidity → Secchi converter.
  - Volume: Continuous since 2012.
  - NorCal coverage: Monterey Bay nearshore only.

- [ ] **CeNCOOS Morro Bay shore station** — same instrument package, near the southern boundary of your new `norcal` zone.
  - https://catalog.data.gov/dataset/cencoos-in-situ-water-quality-monitoring-at-morro-bay
  - Useful as the *control* — if your `norcal` calibration accidentally pulls south of Pt. Sur, Morro Bay residuals will show it.

- [ ] **MBARI M1 mooring (real-time + archive)** — chlorophyll fluorescence + CTD since the early 1990s, near the mouth of Monterey Bay in ~1,000 m. No direct Secchi but excellent chl ground truth for offshore cells — useful as a cross-check on chl model inputs rather than direct vis output.
  - https://www.mbari.org/data/m1-mooring-real-time-plots/
  - Effort: Low for live plots, medium for archive pulls.

---

## Tier 2 — High-signal community trip reports (manual but rich)

- [ ] **BAUE (Bay Area Underwater Explorers) trip reports** — long-running, well-written reports that almost always include explicit vis in feet per site per dive. Includes Cordell Bank, Pt. Lobos, Big Sur, Monterey, Carmel, Sonoma County.
  - https://baue.org/reports/trip_reports.php
  - Effort: Manual extraction per report.
  - Volume: Hundreds of dated reports across the archive.
  - NorCal coverage: Excellent — Monterey through Sonoma.

- [ ] **Beachhopper II Monterey trip log** — Captain MaryJo's charter, posts conditions. Several dives per running week through the season.
  - https://beachhopper2.com/
  - Volume: Weekly during dive season.

- [ ] **Bamboo Reef Monterey dive blog / Instagram (@bambooreef)** — "Best Dive Shop" in Monterey County 10×; their guides scout for the best conditions early morning. Reports go to IG often before anywhere else.
  - https://www.bambooreef.com/dive-monterey
  - https://www.instagram.com/bambooreef/
  - Effort: Scrape IG captions for vis numbers.

- [ ] **Aquarius Dive Shop conditions hotline** — voice updates from local Monterey divers, refreshed daily. Call 831-657-1020 and log the value with a date for 4–6 weeks during validation.
  - https://aquariusdivers.com/conditions
  - Effort: Low (one phone call per day, transcribe into a CSV).

---

## Tier 3 — Open community forums (high volume, scrape-friendly)

- [ ] **ScubaBoard "Monterey conditions" thread** — the canonical NorCal vis log. 200+ pages of dated entries: *"Did Lover's Cove this morning, 25 ft vis, surge calm."* Years deep.
  - https://scubaboard.com/community/threads/monterey-conditions-lets-keep-it-going.425815/
  - Effort: Medium — write an LLM extraction prompt to pull (date, site, vis_ft) tuples out of forum prose.
  - Volume: Thousands of observations across years.

- [ ] **ScubaBoard NorCal subforum** — broader than Monterey; covers Mendocino, Sonoma, SF Bay.
  - https://scubaboard.com/community/forums/norcal.87/

- [ ] **Monterey Scuba Board conditions page** — local community aggregator.
  - https://montereyscubaboard.com/conditions/

- [ ] **California Diver Magazine — Monterey dive conditions** — periodic editorial updates with vis snapshots.
  - https://californiadiver.com/monterey-dive-conditions/

- [ ] **Reddit r/scuba — California threads** — sparser than ScubaBoard but newer divers.
  - https://www.reddit.com/r/scuba/

---

## Tier 4 — Research / agency datasets (rigorous, slow)

- [ ] **Reef Check California (RCCA) kelp forest monitoring** — annual surveys, geocoded, transect-by-transect; vis recorded as part of the dive metadata. ~1,900 surveys since 2006, big NorCal sample, sites from Crescent City to San Diego.
  - https://www.reefcheck.org/kelp-forest-program/
  - Data request: https://www.reefcheck.org/data-now-available-from-the-2024-kelp-forest-monitoring-season/
  - Effort: High — fill the request form, wait for the file, parse the schema.
  - This is the gold-standard dataset for cross-coast validation; worth the wait.

- [ ] **California North Coast MPA Baseline Study (2014–2016)** — open dataset, citizen-scientist + Reef Check monitoring of rocky reefs and kelp forests.
  - https://data.ca.gov/dataset/citizen-scientist-monitoring-of-rocky-reefs-and-kelp-forests-california-north-coast-mpa-ba-2016
  - NorCal-specific (Mendocino, Humboldt, Del Norte).

- [ ] **SBC LTER (Santa Barbara Coastal Long-Term Ecological Research)** — listed for completeness but the geography is SoCal-specific, not NorCal. Skip unless you want a SoCal control for your validation harness.
  - https://data.cnra.ca.gov/dataset/sbc-lter-darwin-core-archive-kelp-forest-reef-fish-abundance

- [ ] **PISCO Partnership for Coastal Oceans monitoring** — academic kelp-forest dive surveys, multiple NorCal sites; vis recorded per dive. Public data via OBIS / their site.
  - Search "PISCO data portal California" for the current download URL — the data lives at multiple mirrors.

- [ ] **CDFW kelp forest monitoring** — California Department of Fish and Wildlife; some publicly downloadable summary tables, full data on request.

---

## Tier 5 — One-off / domain-expert pings

- [ ] **DM Beachhopper II Captain MaryJo** with an explicit ask: "I'm calibrating a vis-prediction model; would you be willing to send me your trip log for the season with dated vis observations?" Operators are usually happy to help if you'll send back the predictions for their next month.
- [ ] **DM BAUE board** — same ask. They keep way more detailed dive logs than what's in the public trip reports.
- [ ] **Cold Water Divers of Sonoma / Mendocino** — local clubs that dive Salt Point, Stillwater Cove, Van Damme. Email or DM via their FB groups.
- [ ] **Monterey Bay Aquarium dive program** — they log conditions twice daily for their working dive team. Highly accurate, almost certainly requires a research-data-use request.

---

## How to use this list

1. **Spin up a CSV** with columns: `date_utc, lat, lng, depth_ft, source, observed_secchi_ft, notes`.
2. **Work top-down**. Tier 1 + Tier 2 (≈ 4 sources) should give you enough volume to run the first validation pass within a day. Tier 3 (forum scrapes) is where you'll find the long-tail sample you need to lock in the calibration. Tier 4 is the rigorous backstop.
3. **Save to `pipeline/validation/data/norcal_observations.csv`**. Have `pipeline/validation/norcal_residuals.py` (the harness specced in `PR-NC-1`) read it.
4. **Tick the boxes as you pull**. When you've got ≥20 observations spanning at least three of the five NorCal cell-types (Monterey nearshore, Big Sur nearshore, SF Bay nearshore, Farallons islands, Pioneer/Davidson offshore), you have enough to call the calibration.

For the long tail, the most valuable single source is probably the ScubaBoard Monterey thread — it's already structured as "date · location · vis" in prose form, and an LLM extraction prompt can turn it into a CSV in an afternoon. Start there if you only do one Tier 3.

---

## Sources

- [Reef Check California 2024 data release](https://www.reefcheck.org/data-now-available-from-the-2024-kelp-forest-monitoring-season/)
- [Reef Check Kelp Forest Program](https://www.reefcheck.org/kelp-forest-program/)
- [California North Coast MPA Baseline dataset](https://data.ca.gov/dataset/citizen-scientist-monitoring-of-rocky-reefs-and-kelp-forests-california-north-coast-mpa-ba-2016)
- [BAUE trip reports](https://baue.org/reports/trip_reports.php)
- [Beachhopper II Monterey](https://beachhopper2.com/)
- [Bamboo Reef Monterey](https://www.bambooreef.com/dive-monterey)
- [Aquarius Dive Shop conditions](https://aquariusdivers.com/conditions)
- [California Diver Monterey conditions](https://californiadiver.com/monterey-dive-conditions/)
- [ScubaBoard Monterey conditions thread](https://scubaboard.com/community/threads/monterey-conditions-lets-keep-it-going.425815/)
- [ScubaBoard NorCal subforum](https://scubaboard.com/community/forums/norcal.87/)
- [Monterey Scuba Board conditions](https://montereyscubaboard.com/conditions/)
- [MBARI M1 mooring](https://www.mbari.org/data/m1-mooring-real-time-plots/)
- [CeNCOOS data portal](https://data.cencoos.org/)
- [CeNCOOS Morro Bay water quality](https://catalog.data.gov/dataset/cencoos-in-situ-water-quality-monitoring-at-morro-bay)
- [CeNCOOS overview at MBARI](https://www.mbari.org/data/cencoos/)
