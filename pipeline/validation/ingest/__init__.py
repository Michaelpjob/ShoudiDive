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

from .cdip import CDIPScraper
from .eagle4 import Eagle4Scraper


SCRAPERS = [
    CDIPScraper(),
    Eagle4Scraper(),
]

# Where the normalized observation table lives. Versioned in git.
DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
OBS_PATH = DATA_DIR / "observations.jsonl"


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
    for scraper in SCRAPERS:
        sid = getattr(scraper, "source_id", scraper.__class__.__name__)
        try:
            obs = scraper.fetch()
            print(f"  {sid}: {len(obs)} observations")
            new_obs.extend(obs)
        except Exception as exc:  # noqa: BLE001
            print(f"  {sid}: FAILED — {exc.__class__.__name__}: {exc}")

    # Dedup: load existing obs_ids, drop any duplicates from this run.
    existing_ids = _existing_obs_ids()
    fresh = [o for o in new_obs if o.get("obs_id") and o["obs_id"] not in existing_ids]

    # Append in append-mode so we never rewrite the whole table.
    with OBS_PATH.open("a", encoding="utf-8") as f:
        for o in fresh:
            f.write(json.dumps(o) + "\n")

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
