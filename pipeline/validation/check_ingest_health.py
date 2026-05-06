"""Fail CI when the latest ingest run has scraper failures."""
from __future__ import annotations

import json
from pathlib import Path


DATA_DIR = Path(__file__).resolve().parent / "data"
HEALTH_PATH = DATA_DIR / "ingest_health.json"


def main() -> int:
    if not HEALTH_PATH.exists():
        print(f"missing {HEALTH_PATH}")
        return 1
    health = json.loads(HEALTH_PATH.read_text(encoding="utf-8"))
    failed = [s for s in health.get("sources", []) if s.get("status") == "failed"]
    if not failed:
        print("ingest health: all scrapers ok")
        return 0
    print(f"ingest health: {len(failed)} scraper(s) failed")
    for src in failed:
        print(f"  - {src.get('source_id')}: {src.get('error')}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
