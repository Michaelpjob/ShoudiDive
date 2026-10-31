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
| 1 | `EARTHDATA_TOKEN` expired 2026-08-23; NASA chl (sources 1-3) dead; chl map blank in the app since then | open | Michael | Generate a new token at urs.earthdata.nasa.gov, then run `bash scripts/rotate-earthdata-token.sh` (validates, sets the secret, dispatches the CA refresh) | [#444](https://github.com/Michaelpjob/ShoudiDive/pull/444) |
| 2 | Timelines opened on the oldest day of the window (sst5d anchor, 3 days back) instead of today | fix-ready | Michael | Merge [#443](https://github.com/Michaelpjob/ShoudiDive/pull/443) (verified on dev 2026-10-30) | [#443](https://github.com/Michaelpjob/ShoudiDive/pull/443) |
| 3 | Token expiry was invisible: refresh green, feed probe green, manifest healthy-looking | fix-ready | Michael | Merge [#444](https://github.com/Michaelpjob/ShoudiDive/pull/444) (login-page detection, authenticated feed probe, data-health findings) | [#444](https://github.com/Michaelpjob/ShoudiDive/pull/444) |
| 4 | `dev-checks` red on `dev` all of 2026-10-30 (`tests/reefLocations.test.js`, 13 failures); auto `deploy-dev` blocked, previews dispatched by hand | open | agent | Diff the reef-location data files between `main` and `dev`; fix the drifted side | run 41136424417 |
| 5 | `osm_overpass_main` feed probe red (HTTP 502) in the 2026-10-30 18Z health check | watching | agent | Re-check on the next health run; static feed, no user-facing effect seen | run 41103952478 |

## Recurring deadlines

| What | Cadence | Last done | Next due | How |
|------|---------|-----------|----------|-----|
| `EARTHDATA_TOKEN` (NASA Earthdata Login user token, powers chl sources 1-3) | every 60 days (token lifetime) | 2026-06-23 (expired 2026-08-23) | now, then 60 days after the next rotation | `bash scripts/rotate-earthdata-token.sh`; the script prints the next due date |
| `COPERNICUSMARINE_SERVICE_*` (CMEMS GlobColour chl backstop) | no known expiry | 2026-06-19 | n/a | `gh secret set` if CMEMS ever starts rejecting |
| GitHub PAT used by workflows (`BOT_PUSH_TOKEN`) | depends on how it was created | unknown | verify in repo settings | repo Settings, Secrets and variables |

## Log

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
- **Status:** open (item 4).

### Backfill from session notes (before this log existed)

- **2026-10-20:** Navy closures frozen at Jun 29 (scisland.org moved to `assets/js/data.js`; GitHub runners get 403). Fixed in PR #436 plus the `shouldidive-relay` Worker. Viz age-sentinel bleed (999 no-data averaged into coastal ages) fixed in PR #437.
- **2026-08-06:** CoastWatch retired the 2 km DINEOF kd490/chl ids; kd490 froze 33 days at 2026-07-04. PR #347 re-pointed to the 9 km ids. SQ-DINEOF publishes about 10 to 11 days behind, so kd490 age never reaches 0.
- **2026-07-05:** NASA OB.DAAC file search started requiring `sensor_id` + `dtid` (silent "No Results Found"). PR #293.
- **2026-06-23:** NASA unreachable from GitHub runners over IPv6. PR #248 forces IPv4. Token rotated the same day (this is the token that expired on 2026-08-23).
- **2026-06-19:** NASA file search 422 after an API migration. PR #214.
- **2026-06-16:** Partial chl fetch committed a 9%-valid `chl_1d.png`. PR #179 coverage guard plus fail-fast ERDDAP budget.
