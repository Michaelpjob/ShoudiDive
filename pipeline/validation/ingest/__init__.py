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
from .beachcitiescuba import BeachCitiesCubaScraper
from .cdip import CDIPScraper
from .cencoos import CeNCOOSScraper
from .diveviz import DiveVizScraper
from .justgetwet import JustGetWetScraper
from .ndbc import NDBCScraper
from .rcca import RCCAScraper
from .reddit import RedditCAScraper
from .southcoastdivers import SouthCoastDiversScraper
# eagle4 retired 2026-05-09 — domain dead (DNS doesn't resolve from
# GitHub Actions or the user's network). File kept on disk for
# possible future revival.


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
    JustGetWetScraper(),         # SD dive shop, La Jolla / Pt Loma / Coronados, conf 0.85
    BeachCitiesCubaScraper(),    # Laguna Beach dive shop, central-OC kelp coverage,
                                 # conf 0.85. Single spot per page, daily updates.
                                 # 2026-05-09 re-probe of the "JS-rendered" site
                                 # in the previous skip-list — turns out it parses
                                 # cleanly with the same labelled-field pattern
                                 # JustGetWet uses.

    # Tier 2 — prose, LLM-extracted (no-ops gracefully without API key)
    DiveVizScraper(),      # SD + LA + OC dive shop, two-blog feed, conf 0.85
    SouthCoastDiversScraper(),  # Laguna group blog (Rich Parker / Louis
                                # Umphenour). Daily prose posts at custom
                                # PHP endpoint, dated filename timestamps.
                                # Covers Bluebird Canyon + Woods Cove +
                                # live-cam inferences from N/S Laguna —
                                # complements BeachCitiesCuba (Shaw's Cove).
                                # conf 0.80.
    BdOutdoorsScraper(),   # XenForo fishing/spear forum RSS — 4 feeds:
                           # central-CA fishing, SoCal offshore, CA sport,
                           # spearfishing. First central-CA signal in the
                           # roster. Pre-filters posts on spot/viz keywords
                           # before paying for LLM, conf 0.85.
    RedditCAScraper(),     # r/scuba + r/spearfishing CA-keyword search.
                           # Posts only (comments are 10x cost; revisit
                           # once we see the post-only signal). conf 0.80.

    # Tier 1 — NorCal-specific, automated feeds. Added 2026-05-10 to
    # populate the `norcal_*` zone-coefficient validation set behind
    # PR-NC-1. See docs/norcal-vis-validation-sources.md + the
    # _norcal_pending.md sidecar for the discovery audit + the
    # roadmap for the still-pending sources (BAUE, ScubaBoard, etc.).
    CeNCOOSScraper(),      # Monterey Wharf (MLML) + Morro Bay (Cal Poly)
                           # ERDDAP turbidity → Secchi conversion. Daily
                           # mean per station, 7-day rolling lookback.
                           # conf 0.70 (below dive shops — derived, not
                           # eyeballed).
    RCCAScraper(),         # Reef Check California 2014-2016 MPA Baseline
                           # zip from data.cnra.ca.gov. Historical, 30-day
                           # disk cache, NorCal lat-gated (≥36° N). conf 0.90 —
                           # agency dataset with calibrated divers.

    # Eagle4Scraper retired 2026-05-09: eagle4pacific.com fails DNS
    # resolution from both GitHub Actions runners and the user's
    # network. Confirmed dead, not just sandbox-restricted. The
    # scraper file is kept under source control so a future revival
    # (if the shop renames/reopens) can re-import it; it just isn't
    # in the active roster.

    # Sources evaluated and skipped this round (full audit in
    # CANDIDATES.md). Most are link-aggregator pages that point at
    # CDIP buoys (already covered by CDIPScraper) or webcams (need
    # image-analysis pipeline, not text scraping). The handful with
    # actual visibility numbers are wired above.
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

    # Dedup, two layers:
    #   1. obs_id ({source}-{date}-{spot}-{seq}) — same source, same UTC day.
    #   2. exact content (every field but obs_id + timestamp) for NON-sensor
    #      sources — a blog/forum that re-publishes an unchanged post (or a
    #      scraper that re-stamps it with a fresh date) is a duplicate even
    #      though its obs_id differs. Sensor feeds (buoys, turbidity) report the
    #      same value on different days legitimately, so they are NEVER
    #      content-deduped.
    existing_ids = _existing_obs_ids()
    existing_content = _existing_content_keys()
    fresh: list[dict] = []
    seen_content: set[str] = set()
    for o in new_obs:
        oid = o.get("obs_id")
        if not oid or oid in existing_ids:
            continue
        if _is_resample_source(o.get("source")):
            ck = _content_key(o)
            if ck in existing_content or ck in seen_content:
                continue
            seen_content.add(ck)
        fresh.append(o)

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


# --- exact-content dedup for re-scraped prose/forum posts ----------------
# obs_id dedup misses the case where a scraper re-stamps an UNCHANGED post with
# a fresh date each run (one stale blog post became 64 "daily" observations in
# 2026-06). Blog/forum/prose sources re-scrape a post, so byte-identical content
# is a duplicate. Sensor feeds (cdip/ndbc buoys, cencoos turbidity) re-READ the
# same value on different days legitimately, so they are excluded.
_SENSOR_PREFIXES = ("cdip", "ndbc", "cencoos")


def _is_resample_source(source) -> bool:
    return not str(source or "").startswith(_SENSOR_PREFIXES)


def _content_key(o: dict) -> str:
    """Everything that defines the observation EXCEPT when it was scraped."""
    return json.dumps(
        {k: v for k, v in o.items() if k not in ("obs_id", "timestamp_utc")},
        sort_keys=True,
    )


def _existing_content_keys() -> set[str]:
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
            if _is_resample_source(o.get("source")):
                out.add(_content_key(o))
    return out


if __name__ == "__main__":
    new = run_all()
    # Exit non-zero only if NOTHING ingested across all sources —
    # otherwise the workflow's git-commit step has nothing to do
    # but the cron itself wasn't a failure.
    sys.exit(0 if new else 0)
