# NorCal source-discovery notes (pending sources)

Live tracker for the sources in `docs/norcal-vis-validation-sources.md`
that aren't yet wired into the ingest orchestrator. Each entry
captures the *exact* discovery state so the next agent doesn't have
to re-probe.

Probed 2026-05-10.

---

## ✅ Wired

- `cencoos.py` — Monterey Wharf (`mlml_monterey`) + Morro Bay
  (`edu_calpoly_marine_morro`) ERDDAP. Confirmed reachable (HTTP 200,
  ~22 KB ERDDAP index, info CSV verified to include
  `sea_water_turbidity`). Hourly cron with same-day dedup.
- `rcca.py` — Reef Check California 2014-2016 MPA Baseline zip from
  `data.cnra.ca.gov`. 30 MB zip, 30-day disk cache. Schema sniff
  walks every CSV in the archive for a visibility column.

---

## ⚠ Pending — URL or auth blocker

### BAUE trip reports

- Original doc URL: `https://baue.org/reports/trip_reports.php` — **404 (2026-05-10)**.
- Site has moved to Squarespace (homepage at `www.baue.org` returns
  200, header includes `assets.squarespace.com/...`).
- Home page (`https://www.baue.org/`) shows no `href=` containing
  "trip" or "report" — likely behind a nav menu or a member login.
- **Next step**: log in / browse manually to find the actual
  trip-report archive URL. Once found, scraping logic is similar to
  `bdoutdoors.py` (paginated prose with explicit vis numbers).

### ScubaBoard Monterey conditions thread

- URL: `https://scubaboard.com/community/threads/monterey-conditions-lets-keep-it-going.425815/`
  — confirmed HTTP 200, ~146 KB page.
- Content is long-form prose: *"Dove Lover's Point this morning,
  ~20 ft vis, 53° at 30 ft."* Hundreds of posts across 200+ pages.
- **Why not wired now**: needs LLM extraction at scale. The repo
  already has `_llm_extract.py` for this exact pattern (reddit.py
  is the reference impl) — but the ScubaBoard thread is so large
  that running the LLM pass on every page would burn ~$5-15 of
  Anthropic API per full scrape. Need a paginated-with-cache
  approach + a one-shot historical backfill commit so the rolling
  cost stays bounded.
- **Next step**: implement `scubaboard.py` mirroring `reddit.py`
  but with a page cursor (`?page=N`) and `_llm_cache.py` keyed on
  the post permalink. Run the historical backfill once manually,
  commit the JSONL, then let the hourly cron handle the rolling
  delta.

### Beachhopper II Monterey trip log

- URL: `https://beachhopper2.com/` — Squarespace blog format.
- Each post is a trip report with explicit vis in the body.
- Same approach as BAUE once a URL is found, plus LLM extraction
  for the prose.

### Bamboo Reef Monterey blog + Instagram

- URL: `https://www.bambooreef.com/dive-monterey`.
- Reports often go to Instagram (@bambooreef) before the blog —
  Instagram scraping has its own rate-limit + auth pain.
- **Next step**: start with the blog; Instagram is a separate
  problem.

### Aquarius Dive Shop conditions hotline

- Phone-only feed (831-657-1020). Cannot be automated.
- **Next step**: maintainer manually transcribes 4-6 weeks of daily
  vis values into `pipeline/validation/data/norcal_observations_manual.csv`
  during the validation pass; a small parser appends them to the
  JSONL on cron.

---

## ⚠ Pending — agency / research datasets

### California North Coast MPA Baseline Study (data.ca.gov)

- URL: `https://data.ca.gov/dataset/citizen-scientist-monitoring-of-rocky-reefs-and-kelp-forests-california-north-coast-mpa-ba-2016`
- Probing the dataset page reveals the actual downloadable file is
  **`rcca.zip` from `data.cnra.ca.gov`** — i.e. the SAME zip
  already wired in `rcca.py`. The "north coast MPA baseline" page
  on data.ca.gov is a re-listing of the CNRA mirror, not a separate
  dataset.
- **Status**: DUPLICATE of `rcca.py`. No new code needed.

### Reef Check California 2024 annual release

- URL: `https://www.reefcheck.org/data-now-available-from-the-2024-kelp-forest-monitoring-season/`
- The newer (post-2016) annual releases require a manual data-request
  form submission. They're NOT mirrored on the data.cnra.ca.gov
  CKAN.
- **Next step**: maintainer fills the form, the file comes back via
  email, drop it in `pipeline/validation/data/external/` and add a
  small one-shot loader (the schema should match what `rcca.py`
  already handles).

### PISCO

- "Search PISCO data portal California for the current download URL"
  — no stable URL given. Multiple academic mirrors. Punt until the
  Tier 1-3 sources produce enough observations to call PR-NC-4.

### CDFW kelp forest monitoring

- "Some publicly downloadable summary tables, full data on request."
  Same shape as Reef Check 2024 — needs a request. Same plan.

### MBARI M1 mooring

- URL: `https://www.mbari.org/data/m1-mooring-real-time-plots/`
- Chl-a + CTD, but no direct Secchi reading. The doc flags this as
  "useful as a cross-check on chl model inputs rather than direct
  vis output" — i.e. it's an INPUT validator, not a vis-output
  validator. Belongs in a different harness once SST/chl model
  inputs need their own residual scoring; not blocking PR-NC-4.

---

## 🚫 Out of scope

### SBC LTER

- Geography is SoCal-specific (per the doc's own note). Skip.

### Reddit r/scuba

- Already wired via `reddit.py`. CA filtering is generic across the
  whole bbox, so NorCal observations from r/scuba land in the
  observations.jsonl already.

---

## Priority for the next round

1. **ScubaBoard Monterey thread** — by far the highest single-source
   NorCal observation volume. Worth the LLM cost; gate it behind a
   `--once` historical-backfill flag so the recurring cost is bounded.
2. **BAUE** — find the new URL, port the bdoutdoors-style scraper.
3. **Manual Reef Check 2024 request** — gold-standard backstop. Fire
   the form now so the data lands in 2-4 weeks.

Everything else can wait.
