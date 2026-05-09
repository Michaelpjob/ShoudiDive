# Candidate dive-shop scrapers — manual probe queue

Each entry below is a dive shop / charter / forum that **was on the
original handoff list** but couldn't be validated from the dev
sandbox (sandbox network restrictions returned `ECONNREFUSED` or
`404` even when the URLs work in a normal browser). Once a human
confirms the URL is reachable + has the expected content, copy the
matching scraper template and add it to the roster in
`__init__.py`.

The probe ritual for each candidate is the same:

```bash
# 1. Curl the candidate URL with a real browser User-Agent.
curl -A "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36" \
  "https://CANDIDATE/dive-reports" | head -200

# 2. If you see actual HTML (not "<div id=\"app\"></div>" or 403),
#    save a chunk to a file and grep for visibility numbers:
curl -A "..." "URL" -o /tmp/probe.html
grep -iE "vis(ibility)?|viz|[0-9]{1,2}\s*(ft|feet|')" /tmp/probe.html | head

# 3. If the regex finds quantitative viz lines, the source is
#    Tier 1 / regex-extractable — clone justgetwet.py or
#    beachcitiescuba.py as a template.

# 4. If only prose ("water cleared up nicely" without a number),
#    it's Tier 2 / LLM-only — clone diveviz.py as a template
#    and let _llm_extract.py do the structuring.

# 5. If the body is empty / <div id="root"></div>, the site is JS-
#    rendered. Either:
#      a) inspect the network panel for a JSON API endpoint the SPA
#         calls (often /api/posts.json or .json on a Shopify route);
#         scrape that JSON directly — much cleaner than rendering.
#      b) Fall back to Puppeteer (already a devDep). See the
#         _puppeteer_base.py stub TODO at the bottom of this file.
```

## SoCal dive shops (highest priority — fills viz gaps)

| Candidate | URL | Region | Notes |
|-----------|-----|--------|-------|
| **Anacapa Divers** | https://anacapadivers.com/ | Channel Islands (Oxnard) | Day-boat to Anacapa + Santa Cruz. Currently no Channel Islands scraper. |
| **Truth Aquatics** | https://truthaquatics.com/ | Channel Islands (Santa Barbara) | Liveaboard fleet (Vision/Conception). Posts trip reports. |
| **Peace Divers** | https://www.peacedivers.com/ | Channel Islands (Ventura) | Day boat. |
| **Dive Ventura** | https://diveventura.com/ | Ventura | Local boat fleet. |
| **Searcher Charters** | https://searcherboat.com/ | Long Beach offshore | Same family as Spectre, may share a CMS. |
| **Dudedivers / Bottom Scratchers** | https://www.dudedivers.com/ | LB / OC | Trip reports historically. |
| **Aquarius Divers** | https://aquariusdivers.com/conditions | SD | Already noted as link-farm to CDIP/buoy widgets. **Skip.** |
| **22nd Street Sportfishing** | https://www.22ndstreet.com/fishreports.php | LA Harbor | Static HTML, but reports are catch-focused. **Probably skip** — no viz signal. |
| **Davey's Locker** | https://www.daveyslocker.com/news/ | Newport Beach | Whale watching + fishing. **Probably skip** — no viz signal. |

## Peer forecasters (partnership > scraping)

Per the original handoff: "reach out before scraping — these are
friendly small teams." If they share a JSON or RSS endpoint, write
a `PeerForecasterScraper` that emits to `peer_forecasts.jsonl`
(NOT `observations.jsonl`) so `score.py` computes 3-way agreement
without conflating peer forecasts with ground-truth.

| Candidate | URL | Notes |
|-----------|-----|-------|
| **vizfinder.com** | https://vizfinder.com/ | SoCal viz forecaster. JS-rendered SPA. |
| **spearfactor.com** | https://spearfactor.com/ | Spearfishing-focused viz. |
| **Surfline** | https://surfline.com/ | Has a JSON API; viz reports are an enterprise feature, may need a partnership ask. |

Email template suggestion:

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
> Anything I display would credit you with a clickable link back. If
> you have an RSS, JSON, or even an emailable daily summary, I can
> wire it into the validation pipeline within a day. Happy to chat
> if there's a way to make this useful for both of us.
>
> — Michael

## Community sources

| Candidate | URL | Notes |
|-----------|-----|-------|
| **scubaboard.com SoCal forum** | https://www.scubaboard.com/community/forums/socal.88/ | **Skip.** Probed 2026-05-09 — current threads are Chamber Day fundraiser admin, not condition reports. |
| **scubaboard.com SoCal Dive Site Reviews** | https://www.scubaboard.com/community/forums/socal-dive-site-reviews.1120/ | **Skip.** Newest thread is Jul 2025; content is historical reviews, not daily reports. |
| **Spearboard CA section** | https://www.spearboard.com/ | Probe needed — forum browsing was blocked from the sandbox. Likely RSS feed available like bdoutdoors. |
| **r/sandiego, r/orangecounty, r/santabarbara** | reddit.com | **Skip per existing reddit.py comment** — geo-subs are 99% non-diving. r/scuba + r/spearfishing with CA-keyword filter is the right shape, already implemented. |

## Stretch — not on the original list but worth considering

| Candidate | Why |
|-----------|-----|
| **Strava public freedive activities** | Many freedivers log dives on Strava with conditions in the description. Strava has an API; the geo-bbox filter would scope to CA. |
| **NOAA SECOORA / IOOS web cams** | Already used as a buoy proxy by the cdip.py scraper. Worth probing whether the underlying pier-cam metadata exposes a viz proxy. |
| **dive shop email list scrape** | If a shop emails a daily report, set up a dedicated inbox and parse the From: + body. Lower ops cost than scraping but requires shop opt-in. |

## Implementation TODO

If/when we need to scrape JS-rendered SPAs, drop a
`pipeline/validation/ingest/_puppeteer_base.py` that subprocess-spawns
a small Node script using the puppeteer devDep we already have.
Sketch:

```python
# _puppeteer_base.py
import json, subprocess, pathlib

NODE_RENDERER = pathlib.Path(__file__).resolve().parent / "_render.mjs"

def render_url(url: str, wait_selector: str | None = None, timeout_s: int = 30) -> str:
    args = ["node", str(NODE_RENDERER), url]
    if wait_selector:
        args += ["--wait", wait_selector]
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout_s)
    r.check_returncode()
    return r.stdout
```

```javascript
// _render.mjs
import puppeteer from "puppeteer";
const [,, url, ...rest] = process.argv;
const browser = await puppeteer.launch({ headless: "new" });
const page = await browser.newPage();
await page.setUserAgent("ShoudiDive-Validator/1.0 (+https://shouldidive.com/about/validation)");
await page.goto(url, { waitUntil: "networkidle2", timeout: 25000 });
const waitIdx = rest.indexOf("--wait");
if (waitIdx >= 0) await page.waitForSelector(rest[waitIdx + 1], { timeout: 10000 });
process.stdout.write(await page.content());
await browser.close();
```

Cost: ~150MB Chromium per cron run (puppeteer ships its own). The
existing `web-smoke` + `cp-visual-paint` jobs already pay this cost,
so adding it to `ingest-ground-truth.yml` is incremental, not new.

Defer this until at least 2 of the JS-only candidates above are
confirmed to have actual viz signal — no point paying the puppeteer
boot cost for sources that turn out to be link farms.
