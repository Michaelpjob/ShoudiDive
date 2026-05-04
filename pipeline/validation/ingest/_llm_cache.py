"""Per-post LLM-call cache, shared by every prose scraper.

The hourly ingest cron sees the same posts across many runs (e.g. a
Reddit thread sits in r/scuba's newest-25 for ~2 days; a BD Outdoors
thread sits at the top of the SoCal RSS feed for ~4 days). Without
caching, every cron tick LLM-extracts EVERY visible post, paying for
the same Haiku call 24-96 times before dedup at the obs_id layer
discards the duplicate observation.

This cache short-circuits that — once a post has been LLM'd, we
record its key and skip the LLM round-trip on subsequent runs. The
cache is JSON on disk in ``pipeline/.cache/llm_seen_posts.json``,
TTL 7 days (after which we re-LLM in case the post body grew or got
edited). Persisted via the ingest workflow's standard CI-disk path —
gitignored, ephemeral. If the cache file disappears (CI runner
recycle), the worst case is one cycle of "re-LLM everything" before
the cache rebuilds; cost is bounded by the per-run MAX_ITEMS caps.

Usage in a scraper:

    from . import _llm_cache
    cache = _llm_cache.load()
    if _llm_cache.seen(cache, key):
        return []                        # already extracted, skip LLM
    extracted = _llm_extract.extract_from_prose(body)
    _llm_cache.mark(cache, key)
    _llm_cache.save(cache)               # call once per fetch() at the end
"""
from __future__ import annotations

import json
import pathlib
import time
from typing import Iterable


CACHE_DIR = pathlib.Path(__file__).resolve().parents[2] / ".cache"
CACHE_PATH = CACHE_DIR / "llm_seen_posts.json"

# 7 days. After this we re-LLM in case the post body was edited or
# extended (XenForo posts can be edited; Reddit selftext rarely changes
# but can). Short enough to re-process if the cron stops for a while
# and the cache becomes irrelevant; long enough to amortize cost across
# the natural 2-4 day window a post stays at the top of a feed.
TTL_SECONDS = 7 * 24 * 3600


def load() -> dict:
    """Load the cache from disk. Returns {} if missing or unreadable."""
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Corrupt cache — start fresh rather than crash. Worst case is
        # one re-LLM cycle before the new cache rebuilds.
        return {}


def save(cache: dict) -> None:
    """Persist the cache, creating the directory if needed. Best-effort —
    failures are logged but don't break the orchestrator."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        CACHE_PATH.write_text(json.dumps(cache, separators=(",", ":")), encoding="utf-8")
    except OSError as exc:
        print(f"  llm-cache: save failed — {exc.__class__.__name__}: {exc}")


def seen(cache: dict, key: str) -> bool:
    """True iff `key` was LLM'd within the last TTL_SECONDS. Stale entries
    are treated as "not seen" so they get re-LLM'd."""
    if not key:
        return False
    ts = cache.get(key)
    if not isinstance(ts, (int, float)):
        return False
    return (time.time() - ts) < TTL_SECONDS


def mark(cache: dict, key: str) -> None:
    """Mark `key` as just-LLM'd. Call AFTER the LLM call succeeds (or
    after _llm_extract returns) so a network failure doesn't poison
    the cache."""
    if key:
        cache[key] = int(time.time())


def prune(cache: dict) -> dict:
    """Drop entries older than TTL_SECONDS. Keeps the cache file small;
    call once per fetch() before saving."""
    cutoff = time.time() - TTL_SECONDS
    return {k: v for k, v in cache.items() if isinstance(v, (int, float)) and v >= cutoff}


def __keys_for_test(items: Iterable[str]) -> list[str]:
    """Tiny helper for tests / interactive checks — not used in prod."""
    return [k for k in items if k]
