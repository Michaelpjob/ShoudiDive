# QUESTIONS — non-blocking, for the human (never waited on)

> Working artifact per the PRD (§7). Answer whenever convenient;
> defaults proceed as noted.

**Q1 — Cloudflare Bot Fight Mode (optional now).** The live probes now
ride real Chrome, so monitoring is independent of the zone's bot
settings. If you want bare-fetch clients (curl, scripts, the RN app's
direct fetches) to stop being intermittently 403'd from datacenter
IPs, check Cloudflare → Security → Bots and consider turning Bot Fight
Mode off for shouldidive.com (it offers little for a public static-data
site, and it's what was 403ing the old probes). The local wrangler
token can't read zone settings, so I couldn't confirm/change it myself.
Default: leave as-is; monitoring no longer cares.

**Q2 — NASA OB.DAAC outage (no action; awareness).**
oceandata.sci.gsfc.nasa.gov has been unreachable (globally, not just
from runners) since ~2026-06-09. chl freshness rides MODIS NRT via
that host; kd490 is 13.6 d stale (10 d threshold). With the circuit
breaker the refresh now degrades gracefully and the health-check issue
will carry the red feed line until NASA recovers. Nothing for us to
fix; if it persists >1 week, consider a fallback chl source (ERDDAP
NOAA S-NPP VIIRS) as a small follow-up.

**Q3 — #102 (kelp admin beds, 3/7 tasks done).** Recommendation:
close-and-re-cut. The branch is 5+ weeks stale against main, the
prior session's handoff already recommended closing it as superseded
by canopy work, and PRD requirement 14 re-scopes kelp rendering around
spot bundles (admin beds + observed canopy as distinct styles). I'll
re-cut the usable CDFW-admin-beds pipeline piece onto a fresh branch
as part of Z14 rather than rebasing the old branch. Object if you'd
rather I finish #102 in place.

**Q4 — R2 go/no-go (Group R, blocks requirements 18–20 only).** When
you're ready: create the R2 bucket + scoped API token per
`docs/kelp-roadmap.md` PR-K4-1 (~$1–5/mo). Everything in Groups S/T/Z
ships without it.

**Q5 — Mobile codebase archival (`mobile/` vs `flutter_app/`).** PRD
§6 says you pick. No urgency; both stay CI-green meanwhile.

**WQ1 — Water column: BETA→GA calibration threshold (ask-first, parked).**
PRD §6 reserves the "below-cliff estimate is trustworthy enough to
drop the BETA tag" decision for you. Proposal to react to when C4
lands: GA when, over 30 days on the pilot spots, |modeled − observed
cliff| ≤ 8 ft for ≥70% of glider/diver observations AND the below-
cliff vis class (Good/Fair/Poor) matches ≥60%. Nothing needed now —
the flag ships BETA regardless.

**WQ2 — Water column: spot-registry duplication.** The pipeline
sidecar hardcodes the CA saved-spots list that mirrors
`src/lib/mapData.js` (WC-D6). Worth unifying into one JSON registry
both sides read when the smooth-zoom spot fan-out (Z17 registry) lands
— same registry can serve both PRDs. No action needed from you unless
you object to the temporary duplication.

**WQ3 — Water column V6 dependency on #139.** The diver-report
submission/display reuse (V6) and the diver-report half of the C4
calibration set stay blocked until the other agent's field-reports
engine (#139) merges. Glider/pier calibration proceeds without it.

**Q6 — GitHub vulnerability alerts.** Push output shows "8
vulnerabilities on the default branch (1 critical, 6 moderate, 1
low)" — beyond the three mobile dependabot PRs in the queue. Worth a
look at github.com/Michaelpjob/ShoudiDive/security/dependabot after
the queue clears; happy to take a pass at the remaining ones next
session if you want.
