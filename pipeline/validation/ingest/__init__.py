"""Ingest orchestrator: run every scraper, append unique obs to JSONL.

Runs from CI on an hourly cron. Per-scraper failures are isolated —
one broken source must never take down the rest. Output appends to
``pipeline/validation/data/observations.jsonl``; the file is git-
versioned (small, ~7 MB/year) so historical observations survive the
ephemeral CI disk.

Dedup keys on ``obs_id``, which is ``{source}-{date}-{spot}-{seq}``
so re-runs on the same UTC day don't double-write.
"""
from __future__ import annotations

import json
import pathlib
import sys
from datetime import datetime, timezone

from .bdoutdoors import BdOutdoorsScraper
from .cdip import CDIPScraper
from .diveviz import DiveVizScraper
from .eagle4 import Eagle4Scraper
from .justgetwet import JustGetWetScraper
from .ndbc import NDBCScraper
from .reddit import RedditCAScraper


# Scraper roster for the hourly cron. Order doesn't matter — each
# scraper's failures are isolated by the orchestrator. To add a
# source: drop a file in this directory subclassing ``BaseScraper``,
# then append it here.
SCRAPERS = [
    # Tier 1 — structured data, highest confidence
    CDIPScraper(),         # 6 CDIP buoys: Hs + SST,  conf 0.95
    NDBCScraper(),         # 6 NDBC buoys (federal): adds Cape San Martin /
                           # Santa Maria / Santa Monica Basin / Tanner Bank /
                           # San Clemente Basin / Monterey, conf 0.95.
                           # Fills central-coast + LA-County zones where CDIP
                           # has no nearby station.

    # Tier 1 — labelled prose, regex-extractable, no LLM dependency
    JustGetWetScraper(),   # SD dive shop, La Jolla / Pt Loma / Coronados, conf 0.85

    # Tier 2 — prose, LLM-extracted (no-ops gracefully without API key)
    DiveVizScraper(),      # SD + LA + OC dive shop, two-blog feed, conf 0.85
    BdOutdoorsScraper(),   # XenForo fishing/spear forum RSS — 4 feeds:
                           # central-CA fishing, SoCal offshore, CA sport,
                           # spearfishing. First central-CA signal in the
                           # roster. Pre-filters posts on spot/viz keywords
                           # before paying for LLM, conf 0.85.
    RedditCAScraper(),     # r/scuba + r/spearfishing CA-keyword search.
                           # Posts only (comments are 10x cost; revisit
                           # once we see the post-only signal). conf 0.80.

    # Tier 1/2 — best-effort URL probe; URL in handoff didn't resolve
    Eagle4Scraper(),       # Channel Islands dive shop, conf 0.85

    # Sources evaluated and skipped this round:
    #   aquariusdivers.com/conditions    — link farm to CDIP/buoy widgets, no own data
    #   beachcitiescuba.com/pages/...    — JS-rendered, BS4 sees empty body
    #   spectreboat.com/weather          — affiliate widget linking to vizfinder.com
    #   22ndstreet / H&M / Davey's       — JS-rendered SPAs (no RSS endpoint found)
    #   vizfinder.com / spearfactor.com  — peer forecasters (Tier 1.5),
    #     JS-rendered SPAs. Per the handoff: "reach out before scraping —
    #     these are friendly small teams." Better as a partnership / API
    #     ask than a scrape. When an RSS or JSON endpoint exists, drop in
    #     a PeerForecasterScraper that writes to a SEPARATE
    #     peer_forecasts.jsonl (NOT observations.jsonl) so score.py can
    #     compute the three-way agreement metric documented in
    #     01-architecture.md without conflating peer forecasts with
    #     ground-truth.
    # Re-evaluate quarterly; a working URL or a static fallback would
    # let any of these slot into the same scraper pattern.
]

# Where the normalized observation table lives. Versioned in git.
DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
OBS_PATH = DATA_DIR / "observations.jsonl"
HEALTH_PATH = DATA_DIR / "ingest_health.json"


def run_all() -> list[dict]:
    """Run every scraper, dedupe by obs_id, append new rows to JSONL.

    Returns the list of NEW observations added in this run (not the
    full table) so callers can log a count without re-reading the
    whole file.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    started = datetime.now(timezone.utc)
    print(f"ingest run start: {started.isoformat(timespec='seconds')}")
    print(f"  out: {OBS_PATH}")

    new_obs: list[dict] = []
    source_status: list[dict] = []
    for scraper in SCRAPERS:
        sid = getattr(scraper, "source_id", scraper.__class__.__name__)
        try:
            obs = scraper.fetch()
            print(f"  {sid}: {len(obs)} observations")
            new_obs.extend(obs)
            source_status.append({
                "source_id": sid,
                "status": "ok",
                "observation_count": len(obs),
                "error": None,
            })
        except Exception as exc:  # noqa: BLE001
            source_status.append({
                "source_id": sid,
                "status": "failed",
                "observation_count": 0,
                "error": f"{exc.__class__.__name__}: {exc}",
            })
            print(f"  {sid}: FAILED — {exc.__class__.__name__}: {exc}")

    # Dedup: load existing obs_ids, drop any duplicates from this run.
    existing_ids = _existing_obs_ids()
    fresh = [o for o in new_obs if o.get("obs_id") and o["obs_id"] not in existing_ids]

    # Append in append-mode so we never rewrite the whole table.
    with OBS_PATH.open("a", encoding="utf-8") as f:
        for o in fresh:
            f.write(json.dumps(o) + "\n")

    health = {
        "computed_at": started.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "total_sources": len(source_status),
        "ok": sum(1 for s in source_status if s["status"] == "ok"),
        "failed": sum(1 for s in source_status if s["status"] == "failed"),
        "observations_fetched": len(new_obs),
        "observations_added": len(fresh),
        "sources": source_status,
    }
    HEALTH_PATH.write_text(json.dumps(health, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {HEALTH_PATH}")

    print(f"ingest run done: {len(fresh)} new (of {len(new_obs)} fetched)")
    return fresh


def _existing_obs_ids() -> set[str]:
    if not OBS_PATH.exists():
        return set()
    out: set[str] = set()
    with OBS_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            obs_id = o.get("obs_id")
            if obs_id:
                out.add(obs_id)
    return out


if __name__ == "__main__":
    new = run_all()
    # Exit non-zero only if NOTHING ingested across all sources —
    # otherwise the workflow's git-commit step has nothing to do
    # but the cron itself wasn't a failure.
    sys.exit(0 if new else 0)
