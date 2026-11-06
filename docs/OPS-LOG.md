# Operations log

One place for what broke, what was done about it, what is still open, and the
deadlines that recur. Append-only, newest entry first. Agents: add an entry
whenever you diagnose a data outage, ship a fix for one, or learn a deadline.
Keep each entry to the facts you verified (log lines, run ids, dates), and
name the owner action when one exists. Status words: `open`, `fix-ready`
(PR open, not merged), `fixed`, `watching`.

## Open items

| # | Item | Status | Owner | Next action | Links |
|---|------|--------|-------|-------------|-------|
| 1 | `EARTHDATA_TOKEN` expired 2026-08-23; NASA chl (sources 1-3) dead; chl map blank in the app since then | fixed 2026-11-01 | Michael | Rotated 21:32Z; refresh run 41352491458 logged 3 frames from each NASA sensor; live overlay paints 246 direct cells at 21:44Z. Next rotation due 2026-12-31 | [#444](https://github.com/Michaelpjob/ShoudiDive/pull/444) |
| 6 | Data PNGs reached browsers up to 4 h late: the zone's Cloudflare "Browser Cache TTL" (default 4 hours) raised `/data/*.png` from the 300 s set in `public/_headers` to `max-age=14400`; JSON was unaffected (not edge-cached) | fixed 2026-11-02 | Michael | Zone setting changed to "Respect Existing Headers" (`browser_cache_ttl` = 0) at 03:35Z through the Cloudflare API; live `/data/chl_1d.png` and `sst_1d.png` now answer `max-age=300`. Nothing to merge | observed 2026-11-01 21:41Z |
| 2 | Timelines opened on the oldest day of the window (sst5d anchor, 3 days back) instead of today | fix-ready | Michael | Merge [#443](https://github.com/Michaelpjob/ShoudiDive/pull/443) (verified on dev 2026-10-30) | [#443](https://github.com/Michaelpjob/ShoudiDive/pull/443) |
| 3 | Token expiry was invisible: refresh green, feed probe green, manifest healthy-looking | fix-ready | Michael | Merge [#444](https://github.com/Michaelpjob/ShoudiDive/pull/444) (login-page detection, authenticated feed probe, data-health findings) | [#444](https://github.com/Michaelpjob/ShoudiDive/pull/444) |
| 4 | `dev-checks` red on `dev` all of 2026-10-30 (`tests/reefLocations.test.js`, 13 failures); auto `deploy-dev` blocked, previews dispatched by hand | fixed 2026-11-01 | agent | [#446](https://github.com/Michaelpjob/ShoudiDive/pull/446) (sync-dev keeps the hand-curated layers) and [#448](https://github.com/Michaelpjob/ShoudiDive/pull/448) (reef locations re-cut) merged 20:47Z and 21:02Z; `dev-checks` green on `dev` on every run since 20:52Z (run 41349070294 onward) | run 41136424417 |
| 5 | `osm_overpass_main` feed probe red (HTTP 502) in the 2026-10-30 18Z health check | watching | agent | Re-check on the next health run; static feed, no user-facing effect seen | run 41103952478 |

## Recurring deadlines

| What | Cadence | Last done | Next due | How |
|------|---------|-----------|----------|-----|
| `EARTHDATA_TOKEN` (NASA Earthdata Login user token, powers chl sources 1-3) | every 60 days (token lifetime) | 2026-11-01 (previous 2026-06-23, expired 2026-08-23) | 2026-12-31 16:15 EST (calendar reminder 2026-12-26) | `bash scripts/rotate-earthdata-token.sh`; the script prints the next due date |
| `COPERNICUSMARINE_SERVICE_*` (CMEMS GlobColour chl backstop) | no known expiry | 2026-06-19 | n/a | `gh secret set` if CMEMS ever starts rejecting |
| GitHub PAT used by workflows (`BOT_PUSH_TOKEN`) | depends on how it was created | unknown | verify in repo settings | repo Settings, Secrets and variables |

## Log

### 2026-11-02, Browser Cache TTL now respects origin headers (item 6)

- The zone `shouldidive.com` lives in the `Michael.job.gb@gmail.com` Cloudflare account (Free plan), not in the litus.com accounts that Michael's Chrome is signed in to, so the dashboard route was a dead end; the `CLOUDFLARE_API_TOKEN` the local wrangler uses has zone-settings access, and `PATCH /zones/<zone>/settings/browser_cache_ttl {"value": 0}` succeeded at 03:35:10Z (previous value 14400).
- **Verified live** right after: `/data/chl_1d.png`, `sst_1d.png` and `chl_1d.sources.png` answer `Cache-Control: public, max-age=300, must-revalidate`; `/apple-touch-icon.png` keeps its 86400, so origin values are honoured in both directions. A browser now picks up a refreshed layer within 5 minutes of the deploy instead of up to 4 hours.
- Noted, not changed: `/data/manifest.json` carries two `Cache-Control` values (`max-age=60` from its own rule plus `max-age=300` from `/data/*.json`, because Pages merges every matching `_headers` rule). Harmless today because the clients fetch the manifest with `cache: "no-cache"`; tidy up if that rule is ever touched.

### 2026-11-01, Earthdata token rotated

- Michael generated a new token (expires 2026-12-31 16:15 EST) and ran `scripts/rotate-earthdata-token.sh`. First paste was refused with the login page although "OB.DAAC Flask getfile" is an approved application in the Earthdata profile; a re-copy from the token page worked (bad paste). Validation downloaded a 7.6 MB AQUA NRT netCDF; secret set 21:32Z; `refresh-ca-data` run 41352491458 dispatched on main.
- The script now diagnoses a failed download by asking the Earthdata Login user API whether the token itself is live (401 = bad paste or revoked token; 200 = OB.DAAC not authorized for the account).
- Note for next time: `bash` is not on the PowerShell PATH on the dev box; launch with `& "C:\Program Files\Git\bin\bash.exe" <script>`.
- **Verified:** run 41352491458 (success, 21:38Z) logged `[aqua_modis_nrt] 3 frames, ages: 1, 3, 4d`, `[snpp_viirs_nrt] 3 frames`, `[s3a_olci_nrt] 3 frames`, `blended 1d: ... sources used [1, 3, 4, 5, 6]`. Live manifest 21:34:51Z: aqua owns 179 cells (age 1 d), s3a 67. Decoded sidecars: 246 cells pass the observed-only gate (direct source, age 1-3 d), all offshore (nearest at 118.1 W off San Diego; coast cloud-blank today). The live overlay image carries exactly 246 opaque pixels at 21:44Z.
- **Found on the way (item 6):** for ~5 minutes after the deploy the edge still served the previous PNGs (origin propagation), which is self-healing; the lasting problem is the zone's Browser Cache TTL rewriting data PNGs to `max-age=14400`, so a browser that loaded the site keeps old layers for up to 4 h after every refresh.

### 2026-10-30, chl blank since 2026-08-23: expired Earthdata token

- **Symptom:** chl layer renders nothing on shouldidive.com; saved spots read "no data". Noticed by Michael ("what happened to our chloro reporting").
- **Evidence:** `refresh-ca-data` logs. 2026-08-22 18Z: `[aqua_modis_nrt] 3 frames, ages: 1, 2, 3d` (and SNPP, S3A). 2026-08-23 06Z and every run since: `nasa-dl AQUA_MODIS.<date>.L3m.DAY.CHL.chlor_a.4km.NRT.nc: HTTP 200, 31083 bytes (probably auth-failed)` for every sensor, then `no valid frames in last 7d`; `blended 1d: ... sources used [4, 5, 6]`. `gh secret list` shows `EARTHDATA_TOKEN` updated 2026-06-23. Earthdata Login user tokens are valid for 60 days.
- **Why it was invisible:** the blend fell back to DINEOF + GlobColour gap-fill, which the app hides by design (observed-only view, PR #287); raw VIIRS (7) never wins a cell because the gap-free products cover everything; the manifest still read 60% coverage at 1 day old; `check_feeds` probes the NASA search endpoint, which needs no token.
- **Fix:** owner regenerates the token (item 1). PR #444 makes the next expiry loud: `::warning` on the run, `nasa_auth_failed` on the manifest, `nasa_obdaac_getfile` feed probe, `chl_nasa_auth_failed` and `chl_no_direct_observation` findings that open the data-health issue.
- **Status:** open until the token is rotated; alerting fix-ready.

### 2026-10-30, app opened on the first day of the window, not today

- **Symptom:** on load the SST forecast playhead and legend said "Oct 27" on Oct 30; wind, swell and current opened on pre-dawn.
- **Cause:** `src/hooks/useTimelineSelections.js` only replaced a selection when it had no data. The placeholder selections (`f0`, `day 0`) always have data, so the today-default in the `default*Selection` helpers never ran on first load. The pipeline side was already right (sst5d ships -3..+3 days with `default_slot: f3`).
- **Fix:** PR #443: first-load initialisation in the hook, `todaySelection` / `findCurrentBucket` in `src/lib/today.js` (current local hour), "Today" label on the SST strip. Verified on dev.shouldidive.pages.dev.
- **Status:** fix-ready.

### 2026-10-30, dev previews need a manual dispatch

- `dev-checks` failed on every `dev` push that day in `web-tests` (`tests/reefLocations.test.js`, 13 failures) while the same tests pass on `main` and on PRs. `deploy-dev` only auto-runs after green checks, so previews were dispatched with `gh workflow run deploy-dev.yml --ref dev`.
- **Status:** fixed 2026-11-01 by #446 + #448 (item 4); `dev-checks` green on `dev` since 20:52Z.

### Backfill from session notes (before this log existed)

- **2026-10-20:** Navy closures frozen at Jun 29 (scisland.org moved to `assets/js/data.js`; GitHub runners get 403). Fixed in PR #436 plus the `shouldidive-relay` Worker. Viz age-sentinel bleed (999 no-data averaged into coastal ages) fixed in PR #437.
- **2026-08-06:** CoastWatch retired the 2 km DINEOF kd490/chl ids; kd490 froze 33 days at 2026-07-04. PR #347 re-pointed to the 9 km ids. SQ-DINEOF publishes about 10 to 11 days behind, so kd490 age never reaches 0.
- **2026-07-05:** NASA OB.DAAC file search started requiring `sensor_id` + `dtid` (silent "No Results Found"). PR #293.
- **2026-06-23:** NASA unreachable from GitHub runners over IPv6. PR #248 forces IPv4. Token rotated the same day (this is the token that expired on 2026-08-23).
- **2026-06-19:** NASA file search 422 after an API migration. PR #214.
- **2026-06-16:** Partial chl fetch committed a 9%-valid `chl_1d.png`. PR #179 coverage guard plus fail-fast ERDDAP budget.
