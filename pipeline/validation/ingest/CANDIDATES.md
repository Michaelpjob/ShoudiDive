# Dive-shop ingest audit — full source list

Comprehensive audit of every URL on the user-supplied source manifest
(`outputs/dive_visibility_sources_2026-05-09.json`, 50+ entries
covering Monterey → Coronado Islands). Each row has been individually
probed; the verdict column says whether we already cover it, scrape
it now, plan to scrape it, or rejected it (with the reason).

**Update cadence:** the JSON manifest is the source of truth for
what _exists_ on the public web. This file is the source of truth for
what we _do about each one_. Re-audit annually (CMS changes,
webcam-only shops adding text reports, defunct shops re-opening).

---

## Active scrapers — viz signal flowing today

| Source | Region | Type | URL | Confidence | Notes |
|--------|--------|------|-----|:---------:|-------|
| **Just Get Wet** | San Diego | dive shop, daily | https://justgetwet.com/blogs/dive-reports-and-conditions | 0.85 | Labelled-field prose, regex-extractable. Highest-volume secchi source today. |
| **DiveViz** (2 blogs) | LA-OC + SD | aggregator, daily | https://diveviz.com/blogs/{daily-dive-report,la-and-oc-dive-conditions} | 0.85 | Prose, LLM-extracted. The `san-diego-dive-conditions` blog (line 152 of source manifest) is dormant since 2019 — content moved to `daily-dive-report`. |
| **Beach Cities Scuba** | Laguna | dive shop, daily | https://beachcitiescuba.com/pages/current-conditions | 0.85 | Single-spot-per-page (Shaw's Cove default) with labelled fields. Added 2026-05-09. |
| **South Coast Divers** | Laguna | dive shop, daily | https://southcoastdivers.com/blog | 0.80 | Daily prose blog (Rich Parker / Louis Umphenour). LLM-extracted. Custom timestamped-filename URL pattern. Added 2026-05-09. Complements Beach Cities Scuba — covers Bluebird Canyon + Woods Cove + N/S Laguna live-cam inferences. |
| **BdOutdoors** (4 RSS feeds) | CA-wide | forum, daily | https://www.bdoutdoors.com/forums/forum/.../index.rss | 0.85 | Spear / fishing forum. Pre-filters for viz keywords before LLM call. |
| **Reddit r/scuba + r/spearfishing** | CA-wide | forum, irregular | https://www.reddit.com/r/{scuba,spearfishing}/.rss | 0.80 | LLM-extracted. Geo subs (r/sandiego etc) excluded — noise too high. |
| **CDIP buoys** (6) | CA coast | buoy, live | https://cdip.ucsd.edu/data_access/justdar.cdip | 0.95 | Hs + SST. **No secchi.** Useful for SST validation only. |
| **NDBC buoys** (6) | CA coast | buoy, live | https://www.ndbc.noaa.gov/data/realtime2/ | 0.95 | Hs + SST. **No secchi.** Same as CDIP — fills central + LA-county zones. |

---

## Aggregator-only (no own viz signal — REJECTED)

These sites are link/widget aggregators that re-display data we
already pull from primary sources. Scraping them would duplicate
buoy data already in `observations.jsonl` from CDIPScraper /
NDBCScraper.

| Source | URL | Why rejected |
|--------|-----|--------------|
| Aquarius Dive Shop (Monterey) | https://aquariusdivers.com/conditions | "Wave Models" + "Forecasts" + "Web Cams" sections only — links out to NOAA/CDIP/NDBC. **Same buoy data we already pull.** |
| Diver Dan's | https://www.diverdans.com/local-diving/monterey-conditions/ | Pure navigation hub — links to NDBC, CDIP, Windy, webcams. No own data. |
| Pacific Wilderness | https://pacificwilderness.com/?page_id=676 | Aggregator: webcams + Magic Seaweed + LA County beach advisories. |
| Heff.net | https://www.heff.net/scuba/features/cdc.shtml | Links + a phone number for SD Lifeguards. No web data. |
| L.A. Scuba Diving | https://lascubadiving.com/workshop/divereport.html | Links to wave models + webcams hosted elsewhere. |
| South Coast Divers /conds.shtml | http://www.southcoastdivers.com/conds.shtml | Different URL than `/blog` — this one is the aggregator page. **The /blog URL is what we scrape.** |
| Spectre Boat /weather | https://www.spectreboat.com/weather | Links out to vizfinder.com. (Spectre is a charter, not a shop.) |
| 22nd Street Sportfishing | https://www.22ndstreet.com/fishreports.php | Reachable, but reports are catch-focused with no viz mentions. |
| Davey's Locker | https://www.daveyslocker.com/news/ | Whale watching + fishing focus, no viz signal. |
| Channel Islands Dive Adventures /northern-channel-islands/ | https://channelislandsdiveadventures.com/.../northern-channel-islands/ | Static informational copy ("vis rarely below 10 ft, average 40 ft"). No dated trip reports at the linked /blog/ or /category/dive-vacation-reports/ paths (both 404). |
| Waterhorse Charters | https://www.waterhorsecharters.com/{wreck-alley,coronado-islands}/ | Customer testimonials only. No dated trip reports with numbers. |
| Monterey Scuba Board | https://montereyscubaboard.com/conditions/ | Educational guide page, not a forum. No dated posts. |
| Ocean Safari Scuba /islands | https://www.oceansafariscuba.com/islands | Trip albums + events, no condition reports. |
| LagunaPages | http://www.lagunapages.com/beach/conditions.asp | Returned HTTP 500. Likely abandoned. |
| DiveCenter.com SoCal | https://divecenter.com/dive-conditions/southern-california-... | Aggregator — irregular updates. |
| Open Water Data (Shaw's/Diver's Cove) | https://www.openwaterdata.com/site/laguna-beach-shaws-cove-divers-cove | Live structured data — but the **structured fields are AIR data** (air temp, wind, humidity, precipitation). Water data shows "No relevant data". Useful for the existing wind/swell pipeline if we expand inputs; not for viz. |

---

## Dormant / camera-down (REVISIT QUARTERLY)

| Source | URL | State | Action |
|--------|-----|-------|--------|
| **DivePros SD** | https://www.diveprosd.com/ | Format is structured ("10–13 ft" graded "C") but the underlying Scripps Pier underwater camera is "currently down for technical issues" since mid-April 2026. Last report April 18. | Revisit monthly. When camera comes back, build a scraper modeled on Beach Cities Scuba (labelled-field prose). High-value: it's a structured La Jolla viz forecast that updates daily. |
| Truth Aquatics (Vision/Conception) | https://truthaquatics.com/ | DNS-fails from both GitHub Actions and user's network as of 2026-05-09. The Conception fire (Sep 2019) ended the company; the website is gone. | **Defunct. Do not retry.** |

---

## Webcam-driven (FUTURE PROJECT — image-analysis pipeline)

Multiple shops link or host underwater/surface cams. Building a
visibility-from-image inference pipeline would unlock these
simultaneously. Sketch:

- Pull cam still every hour
- Run a small CNN (or vision-LLM) trained on (still, secchi-from-text-report) pairs
- Emit synthetic "obs" tagged source=webcam, conf 0.65 (lower than human reports)

Sources that would feed it:

- Spanglers' Scuba (Monterey live cams at multiple sites)
- Spanglers' Whaler's Cove (Point Lobos)
- USPS Ventura aggregated cams
- Pacific Wilderness cam list (Cabrillo Beach, Isthmus Reef, Casino Point)
- Beach Cities Scuba's S/N Laguna cams (referenced by South Coast Divers)
- DivePros SD's Scripps Pier cam (when restored)

This is a separate body of work — defer until the text-scraper
roster has hit its asymptote.

---

## Partnership ASK (highest signal-to-engineering-effort ratio)

Per the original handoff: peer forecasters are friendly small teams.
Reaching out is a 30-min email; scraping their JS-rendered SPAs is
days of work for inferior data.

| Target | URL | What to ask for |
|--------|-----|-----------------|
| **VizFinder** | https://www.vizfinder.com/ | Daily forecast JSON or RSS. Goes into `peer_forecasts.jsonl` (NOT `observations.jsonl`) so score.py computes 3-way agreement (their forecast vs. ours vs. dive-shop ground truth). |
| **SpearFactor** | https://spearfactor.com/ | Same. Spearfishing-specific, narrower geography. |
| **DiveViz** (full-feed API) | (already partnered de-facto via blog scrape) | Ask if they'd expose a structured JSON endpoint that gives us all SoCal regions in one fetch — would cut polite-rate-limit time + LLM token cost ~5x. |

Email template lives below in this file.

---

## Forum / community (low signal density, defer)

| Source | URL | Why deferred |
|--------|-----|--------------|
| Monterey County Dive Reports (Facebook Group) | https://www.facebook.com/groups/montereycountydivereports/ | Highly active per the source manifest, but Facebook auth-walls + actively breaks scrapers. Not worth the maintenance burden. |
| ScubaBoard regional forums | https://scubaboard.com/community/forums/california.50/ | Probed 2026-05-09: SoCal subforum is currently 100% Chamber Day fundraiser admin chatter. The "SoCal Dive Site Reviews" subforum is historical reviews (newest from Jul 2025). Watch for activity to come back. |
| Monterey Scuba Board "/conditions/" | https://montereyscubaboard.com/conditions/ | Static educational page, not a forum. (Domain name misleading.) |
| DAN Alert Diver | https://dan.org/alert-diver/article/big-sur/ | Magazine — irregular long-form articles, not a regular condition feed. |
| OpenDiveSites.org | https://opendivesites.org/Point_Lobos | Wiki-style, infrequent updates. |
| California Diver Magazine | https://californiadiver.com/monterey-dive-conditions/ | Magazine — irregular. |
| California Diving News | https://cadivingnews.com/dive-spots/los-coronados-islands-lobster-shack/ | Magazine — single articles per spot, not a feed. |

---

## Probe ritual for new candidates

For any URL not yet on this list:

```bash
# From a normal terminal (not the dev sandbox — it allowlists hosts).
curl -sL -A "Mozilla/5.0" https://CANDIDATE/ -o /tmp/probe.html
echo "size: $(wc -c </tmp/probe.html) bytes"
grep -iE "vis(ibility)?|viz|water clarity|[0-9]{1,2}\s*(ft|feet)" /tmp/probe.html | head -10
```

Verdict tree:

1. **Size < 500 bytes**: 404, parked domain, or bot-blocker. Skip.
2. **Size > 10 KB, but no viz keywords in HTML**: aggregator. Skip.
3. **Size > 10 KB, viz keywords match a STATIC range** ("vis 10-50 ft"): it's site reference info, not a current report. Skip.
4. **viz keywords match in a DATED CONTEXT** ("Saturday 5/8/26... viz around 10 ft"): viable. Note format:
   - Labelled fields ("Vis: X-Y ft") → regex scraper, model on Just Get Wet / Beach Cities Scuba
   - Prose ("clean water at 8-10 feet today") → LLM scraper, model on DiveViz / South Coast Divers
5. **Live structured data feed (JSON/CSV)**: likely high-value. Investigate API endpoints.

Paste the matching lines + URL pattern back to me; I'll wire a
scraper same-day if it's regex-extractable, next-day if LLM.

---

## Partnership outreach email template

> **Subject:** ShouldIDive.com — open-source CA-coast viz forecaster — partnership ask
>
> Hi <name>,
>
> I'm building **shouldidive.com** — a free CA-coast water-clarity
> forecaster. Live now at sst/swell/wind/viz layers across
> 31.8°–37.6°N. Source pipeline is open
> (github.com/Michaelpjob/ShoudiDive).
>
> I'd love to ingest your daily forecasts as a peer signal alongside
> the buoy + dive-shop ground truth I'm already collecting. The point
> is **not** to copy your work — it's to compute a 3-way agreement
> metric (your forecast, my forecast, dive-shop reports) so I can
> calibrate my model and surface where forecasters disagree.
>
> Anything I display would credit you with a clickable link back.
> If you have an RSS, JSON, or even an emailable daily summary, I
> can wire it into the validation pipeline within a day. Happy to
> chat if there's a way to make this useful for both of us.
>
> — Michael
