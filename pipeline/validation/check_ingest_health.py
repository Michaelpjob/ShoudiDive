"""Fail CI when the latest ingest run shows a broken or silent source.

Reads ``ingest_health.json`` (written by the orchestrator every run) and
flags, per source:

* ``failed``: the scraper raised this run.
* ``silent``: it returned zero observations for ``max_zero_runs``
  consecutive runs. A scraper that swallows its own errors (or whose
  page format changed) used to read as "ok, 0 observations" forever.
* ``ended``: one of its feeds is retired upstream (e.g. an ERDDAP
  dataset whose time_coverage_end is years old). Red at once, since it
  will not come back on its own.
* ``feed-down``: one of its feeds was not ok for ``max_bad_runs``
  consecutive runs, even though the source as a whole returned data.

Exit 1 on any of these. The workflow commits the new observations
before this step runs, so a red check never blocks data landing.
"""
from __future__ import annotations

import json
from pathlib import Path


DATA_DIR = Path(__file__).resolve().parent / "data"
HEALTH_PATH = DATA_DIR / "ingest_health.json"

# Fallback when a health file predates per-source limits. Mirrors
# ingest._base.DEFAULT_MAX_ZERO_RUNS; not imported so this check runs
# without the scraper dependencies.
DEFAULT_MAX_ZERO_RUNS = 4


def evaluate(health: dict) -> list[dict]:
    """Every failed, silent, ended or down source/feed in one health payload."""
    problems: list[dict] = []
    for src in health.get("sources") or []:
        sid = src.get("source_id") or "unknown"
        limit = int(src.get("max_zero_runs") or DEFAULT_MAX_ZERO_RUNS)
        streak = int(src.get("zero_obs_streak") or 0)
        if src.get("status") == "failed":
            problems.append(_problem(sid, None, "failed", src.get("error") or "scraper raised"))
        elif streak >= limit:
            last = src.get("last_obs_run_utc") or "not since streak tracking began"
            problems.append(_problem(
                sid, None, "silent",
                f"0 observations for {streak} consecutive runs (limit {limit}); "
                f"last run with observations: {last}",
            ))
        for feed in src.get("feeds") or []:
            fid = feed.get("feed_id") or "unknown"
            detail = feed.get("detail") or ""
            if feed.get("status") == "ended":
                problems.append(_problem(sid, fid, "ended", detail or "upstream dataset ended"))
                continue
            f_limit = int(feed.get("max_bad_runs") or DEFAULT_MAX_ZERO_RUNS)
            f_streak = int(feed.get("bad_run_streak") or 0)
            if f_streak >= f_limit:
                problems.append(_problem(
                    sid, fid, "feed-down",
                    f"{feed.get('status')} for {f_streak} consecutive runs (limit {f_limit})"
                    + (f": {detail}" if detail else ""),
                ))
    return problems


def _problem(source_id: str, feed_id: str | None, kind: str, message: str) -> dict:
    return {"source_id": source_id, "feed_id": feed_id, "kind": kind, "message": message}


def _label(p: dict) -> str:
    return f"{p['source_id']}/{p['feed_id']}" if p.get("feed_id") else p["source_id"]


def _print_sources(health: dict) -> None:
    for src in health.get("sources") or []:
        limit = int(src.get("max_zero_runs") or DEFAULT_MAX_ZERO_RUNS)
        print(f"  {src.get('source_id', 'unknown'):28} {src.get('status', '?'):6} "
              f"{src.get('observation_count', 0):>3} obs "
              f"(+{src.get('observations_added', 0)} new)  "
              f"zero-run streak {src.get('zero_obs_streak', '-')}/{limit}")
        for feed in src.get("feeds") or []:
            print(f"      {feed.get('feed_id', 'unknown'):34} {feed.get('status', '?'):6} "
                  f"{feed.get('observation_count', 0):>3} obs  "
                  f"last data {feed.get('last_data_utc') or '-'}")


def main() -> int:
    if not HEALTH_PATH.exists():
        print(f"missing {HEALTH_PATH}")
        return 1
    health = json.loads(HEALTH_PATH.read_text(encoding="utf-8"))
    print(f"ingest health for run {health.get('computed_at')}:")
    _print_sources(health)
    problems = evaluate(health)
    if not problems:
        print(f"ingest health: {len(health.get('sources') or [])} sources ok "
              f"(none failed, ended or silent)")
        return 0
    print(f"ingest health: {len(problems)} problem(s)")
    for p in problems:
        print(f"  - [{p['kind']}] {_label(p)}: {p['message']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
