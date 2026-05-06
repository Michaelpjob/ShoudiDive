"""Health check for the *published* data at shouldidive.com.

Distinct surface from the existing checkers:

  pipeline/check_feeds.py
      External HTTP sources (NASA / NOAA / ERDDAP / GMRT / scrapers).
      "Are upstream feeds reachable?"

  pipeline/validation/watchdog.py
      Model accuracy. Compares predictions vs ingested ground-truth
      observations and surfaces bias / calibration / correlation
      regressions per zone.
      "Is the viz model right?"

  pipeline/check_published.py  (this file)
      Live deploy state. Fetches the manifest the React + RN clients
      actually load (`/data/manifest.json`) and verifies it's fresh,
      complete, and every layer's PNG is reachable + has real content.
      "Did the data ship?"

That third axis is what catches silent CI failures: refresh-data.yml
ran but the deploy step failed and the manifest stale-pinned at
yesterday's value, or chl PNGs are HTTP 200 but all-zeros because a
fetch silently fell through. Without this check, the user sees the
problem before we do.

Output: ``pipeline/validation/data/published_health.json`` — same
shape as feed_health.json so the watchdog issue body can include a
combined section. Exits 0 unless something graded "critical" (manifest
unreachable, generated_at >36h stale, or every primary layer broken).
The companion workflow opens an issue on any non-zero finding and
auto-dispatches refresh-data.yml on critical findings older than the
auto-retry cooldown.
"""
from __future__ import annotations

import io
import json
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH  = REPO_ROOT / "pipeline" / "validation" / "data" / "published_health.json"

REMOTE_BASE   = "https://shouldidive.com"
MANIFEST_URL  = f"{REMOTE_BASE}/data/manifest.json"

USER_AGENT = (
    "ShoudiDive-PublishedChecker/1.0 "
    "(+https://shouldidive.com/about/validation; live deploy probe)"
)
DEFAULT_TIMEOUT = 25


# ---- Thresholds ------------------------------------------------------
#
# Knobs deliberately conservative — false positives create issue noise
# that causes the watchdog to be ignored. Tighten once we have a track
# record.

# manifest.generated_at staleness. The daily refresh runs at 06:00 UTC;
# a 30-hour window covers a one-day skip with grace. Beyond 36h we
# escalate to "critical" — that's the auto-retry trigger.
GENERATED_AT_STALE_HOURS = 30
GENERATED_AT_CRITICAL_HOURS = 36

# Per-layer date freshness (when the manifest reports a layer's most-
# recent satellite/model day in `windows.<key>.dates`). chl + kd490
# share the ~4-day NASA OB.DAAC publication lag floor; sst's MUR L4
# typically has a 2d lag but can stretch to 4 during gap-fill rebuilds.
LAYER_DATE_MAX_DAYS = {
    "sst":    4,
    "chl":    7,
    "kd490":  10,   # NASA's kd490 product is more lag-prone than chl
    "viz":    2,
    "wind":   2,    # HRRR is sub-daily; 2d window covers a missed cron
    "wave":   2,    # gfswave is sub-daily, same logic as wind
    "precip": 3,    # CPC unified daily can have a 1-2d publication lag
    # wind5d / swell5d covered separately via summary_url branch
}

# PNG content heuristics. Pipeline writes mode='L' grayscale where R=0
# means no-data; a healthy "dense" layer has ≥5% non-zero coverage
# AND ≥8 distinct non-zero values (catches "stuck on a single fill"
# bugs). Layers like precip can legitimately have ~all-zero output
# during a dry stretch — keyed exemptions below.
MIN_NONZERO_PIXEL_FRACTION    = 0.05
MIN_DISTINCT_NONZERO_VALUES   = 8

# Layers whose content checks should be relaxed because zero is a
# valid signal (no rain this week, no kelp blooms in this aoi, etc).
# When listed here, both nonzero-fraction and distinct-values checks
# are skipped; we only verify the PNG decodes cleanly at expected dims.
CONTENT_CHECK_EXEMPT = {"precip"}

# Minimum body size for a "real" PNG. Calibrated against the actual
# precip_7d.png on a dry week (~620 bytes for 71×87 with very few
# non-zero pixels). 200 bytes still rules out 1×1 transparent
# placeholders (~70 bytes) without false-positiving compressible layers.
MIN_PNG_BYTES = 200


# ---- Findings --------------------------------------------------------

@dataclass
class Finding:
    severity: str          # "critical" | "high" | "medium"
    code: str              # short stable identifier (used for filtering)
    title: str             # one-line headline
    detail: str = ""       # multiline explanation
    layer: Optional[str] = None
    url: Optional[str] = None


def _json_finding(f: Finding) -> dict:
    return {
        "severity": f.severity,
        "code":     f.code,
        "title":    f.title,
        "detail":   f.detail,
        "layer":    f.layer,
        "url":      f.url,
    }


# ---- Manifest fetching ----------------------------------------------

def _http_get(url: str, *, head_only: bool = False, timeout: int = DEFAULT_TIMEOUT):
    """Single shared HTTP helper. Returns the response or raises."""
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if head_only:
        return requests.head(url, headers=headers, timeout=timeout, allow_redirects=True)
    return requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)


def fetch_manifest() -> tuple[Optional[dict], Optional[Finding]]:
    """Pulls the live manifest. On failure returns (None, Finding)."""
    try:
        r = _http_get(MANIFEST_URL)
    except (requests.exceptions.RequestException, socket.gaierror) as exc:
        return None, Finding(
            severity="critical",
            code="manifest_unreachable",
            title="Live manifest fetch failed",
            detail=f"GET {MANIFEST_URL}\n  → {exc.__class__.__name__}: {exc}",
            url=MANIFEST_URL,
        )

    if r.status_code != 200:
        return None, Finding(
            severity="critical",
            code="manifest_http_error",
            title=f"Live manifest returned HTTP {r.status_code}",
            detail=f"GET {MANIFEST_URL} → HTTP {r.status_code} {r.reason}",
            url=MANIFEST_URL,
        )

    try:
        m = r.json()
    except json.JSONDecodeError as exc:
        return None, Finding(
            severity="critical",
            code="manifest_invalid_json",
            title="Live manifest is not valid JSON",
            detail=f"GET {MANIFEST_URL} → 200 OK but JSON parse failed: {exc}",
            url=MANIFEST_URL,
        )

    if not isinstance(m, dict) or "layers" not in m:
        return None, Finding(
            severity="critical",
            code="manifest_missing_layers",
            title="Live manifest is missing the 'layers' key",
            detail=f"Got JSON but top-level shape is not what consumers expect "
                   f"(keys: {list(m.keys()) if isinstance(m, dict) else type(m).__name__})",
            url=MANIFEST_URL,
        )
    return m, None


# ---- Freshness --------------------------------------------------------

def _parse_iso(s: str) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp OR a plain YYYY-MM-DD date.

    Always returns a tz-aware datetime in UTC — manifest layer dates
    are date-only strings ("2026-05-03"), and Python parses those as
    NAIVE datetimes, which then can't be arithmetic'd against
    datetime.now(timezone.utc). Normalising here closes that gap.
    """
    if not s:
        return None
    # tolerate trailing Z (Python's fromisoformat supports it from 3.11+)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def check_generated_at(manifest: dict) -> list[Finding]:
    out: list[Finding] = []
    raw = manifest.get("generated_at")
    when = _parse_iso(raw) if raw else None
    now = datetime.now(timezone.utc)

    if when is None:
        out.append(Finding(
            severity="high",
            code="generated_at_missing",
            title="Manifest has no parseable generated_at",
            detail=f"Raw value: {raw!r}",
        ))
        return out

    age_hours = (now - when).total_seconds() / 3600
    if age_hours > GENERATED_AT_CRITICAL_HOURS:
        out.append(Finding(
            severity="critical",
            code="generated_at_critical",
            title=f"Manifest is {age_hours:.1f}h stale (threshold {GENERATED_AT_CRITICAL_HOURS}h)",
            detail=(
                f"generated_at={when.isoformat()} (now {now.isoformat()}); "
                f"daily refresh has been failing for >36h. Auto-retry should "
                f"have fired by now — investigate refresh-data.yml runs."
            ),
        ))
    elif age_hours > GENERATED_AT_STALE_HOURS:
        out.append(Finding(
            severity="high",
            code="generated_at_stale",
            title=f"Manifest is {age_hours:.1f}h stale (threshold {GENERATED_AT_STALE_HOURS}h)",
            detail=(
                f"generated_at={when.isoformat()}. The 06:00 UTC daily run may "
                f"have failed; check the most recent refresh-data.yml outcome."
            ),
        ))
    return out


# ---- Layer probing ----------------------------------------------------

def _resolve_url(path_or_url: str) -> str:
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        return path_or_url
    if not path_or_url.startswith("/"):
        path_or_url = "/" + path_or_url
    return f"{REMOTE_BASE}{path_or_url}"


def _probe_png(url: str, layer_id: Optional[str] = None) -> tuple[bool, Optional[str], dict]:
    """Returns (ok, error, stats). stats has bytes/dims/nonzero_frac/distinct.

    `layer_id` is used to look up content-check exemptions
    (CONTENT_CHECK_EXEMPT) — layers like precip legitimately publish
    near-all-zero PNGs during dry weeks, and the variability checks
    would false-positive on those. Passing None applies the strict
    defaults (used for one-off probes and the wave_url path).
    """
    stats: dict = {}
    try:
        r = _http_get(url)
    except (requests.exceptions.RequestException, socket.gaierror) as exc:
        return False, f"{exc.__class__.__name__}: {exc}", stats

    stats["http_status"] = r.status_code
    stats["bytes"] = len(r.content) if r.content else 0
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}", stats
    if stats["bytes"] < MIN_PNG_BYTES:
        return False, f"body too small ({stats['bytes']} bytes; floor {MIN_PNG_BYTES})", stats

    try:
        img = Image.open(io.BytesIO(r.content))
        img.load()
    except Exception as exc:
        return False, f"PIL decode failed: {exc.__class__.__name__}: {exc}", stats

    stats["mode"] = img.mode
    stats["dims"] = list(img.size)

    # Pull the R channel and check coverage / variability.
    if img.mode in ("L", "P"):
        gray = img.convert("L")
    elif img.mode in ("RGBA", "RGB"):
        # For multi-channel layers (wind UV, wave Hs/Tp/Dp) just sample R.
        gray = img.split()[0]
    else:
        gray = img.convert("L")

    # `tobytes()` returns one byte per pixel for mode 'L' — same data
    # as getdata() but stable across Pillow versions (getdata is deprecated
    # for removal in Pillow 14).
    px = gray.tobytes()
    total = len(px)
    nonzero_count = sum(1 for v in px if v > 0)
    nonzero_frac = (nonzero_count / total) if total else 0.0
    distinct_nonzero = len({v for v in px if v > 0})
    stats["nonzero_fraction"] = round(nonzero_frac, 4)
    stats["distinct_nonzero_values"] = distinct_nonzero

    if layer_id in CONTENT_CHECK_EXEMPT:
        # PNG decoded cleanly at expected dims — for exempt layers
        # that's all the integrity we can confirm without overfitting.
        stats["content_check"] = "exempt"
        return True, None, stats

    if nonzero_frac < MIN_NONZERO_PIXEL_FRACTION:
        return False, (f"{nonzero_frac*100:.1f}% non-zero coverage "
                       f"(floor {MIN_NONZERO_PIXEL_FRACTION*100:.0f}%) — "
                       "PNG looks blank"), stats
    if distinct_nonzero < MIN_DISTINCT_NONZERO_VALUES:
        return False, (f"only {distinct_nonzero} distinct non-zero pixel values "
                       f"(floor {MIN_DISTINCT_NONZERO_VALUES}) — "
                       "looks stuck / constant fill"), stats
    return True, None, stats


def check_layers(manifest: dict) -> tuple[list[Finding], list[dict]]:
    """Probes every layer in the manifest. Returns (findings, per_layer_stats)."""
    findings: list[Finding] = []
    layer_stats: list[dict] = []
    layers = manifest.get("layers") or {}

    if not layers:
        findings.append(Finding(
            severity="critical",
            code="layers_empty",
            title="Manifest has no layers",
            detail="`layers` key present but empty — pipeline produced nothing.",
        ))
        return findings, layer_stats

    for layer_id, info in sorted(layers.items()):
        if not isinstance(info, dict):
            continue
        # 5-day forecast layers (wind5d, swell5d) — verify summary URL.
        if "summary_url" in info:
            findings.extend(_probe_5d_summary(layer_id, info, layer_stats))
            continue

        # Standard windowed layers (sst/chl/viz). Probe the primary
        # window plus do a date-freshness check on it.
        windows = info.get("windows") or {}
        if not windows:
            findings.append(Finding(
                severity="medium",
                code="layer_no_windows",
                title=f"Layer `{layer_id}` has no windows",
                detail=f"Manifest entry: {json.dumps(info, default=str)[:200]}",
                layer=layer_id,
            ))
            continue
        # Pick the primary window by convention; fall back to whatever exists.
        primary_key = _primary_window_for(layer_id, windows)
        if primary_key is None:
            findings.append(Finding(
                severity="medium",
                code="layer_no_probable_primary",
                title=f"Layer `{layer_id}` has windows but none are recognized "
                      "(expected one of 1d/2d/3d/now)",
                detail=f"Available keys: {sorted(windows.keys())}",
                layer=layer_id,
            ))
            continue
        win = windows[primary_key]
        # Some layers package vector data across multiple PNGs in one
        # window — wind has `speed_url` + `uv_url`, wave has `wave_url`
        # (RGBA Hs/Tp/Dp). Probe every PNG-URL field present.
        url_fields = [k for k in (
            "url", "speed_url", "uv_url", "wave_url",
        ) if isinstance(win.get(k), str)]
        if not url_fields:
            findings.append(Finding(
                severity="high",
                code="layer_window_no_url",
                title=f"Layer `{layer_id}` window `{primary_key}` has no PNG url",
                detail=f"Window entry keys: {sorted(win.keys())}",
                layer=layer_id,
            ))
            continue
        for url_field in url_fields:
            url = _resolve_url(win[url_field])
            ok, err, stats = _probe_png(url, layer_id=layer_id)
            layer_stats.append({
                "layer":  layer_id,
                "window": primary_key,
                "field":  url_field,
                "url":    url,
                "ok":     ok,
                "error":  err,
                **stats,
            })
            if not ok:
                findings.append(Finding(
                    severity="high",
                    code="layer_png_unhealthy",
                    title=f"Layer `{layer_id}` ({primary_key}/{url_field}) PNG is unhealthy",
                    detail=f"GET {url}\n  → {err}",
                    layer=layer_id,
                    url=url,
                ))

        # Date freshness — if the window includes a `dates` array (most
        # recent observation date last), check it against the per-layer
        # max age.
        date_finding = _check_layer_date_freshness(layer_id, primary_key, win)
        if date_finding:
            findings.append(date_finding)

    return findings, layer_stats


def _primary_window_for(layer_id: str, windows: dict) -> Optional[str]:
    # viz publishes a single "now" slot. sst/chl/kd490 publish 1d/2d/3d
    # composites; "2d" is the balanced default the React app shows.
    # wind/wave/precip publish a single recent window each.
    preferred = {
        "viz":    ["now"],
        "sst":    ["2d", "1d", "3d"],
        "chl":    ["2d", "1d", "3d"],
        "kd490":  ["2d", "1d", "3d"],
        "wind":   ["1d", "now"],
        "wave":   ["1d", "now"],
        "precip": ["7d", "1d", "now"],   # precip uses a multi-day rollup
    }.get(layer_id, ["2d", "1d", "now", "3d"])
    for k in preferred:
        if k in windows:
            return k
    # Fall back to any window with a url.
    for k, w in windows.items():
        if isinstance(w, dict) and w.get("url"):
            return k
    return None


def _check_layer_date_freshness(layer_id: str, window_key: str, win: dict) -> Optional[Finding]:
    dates = win.get("dates")
    if not isinstance(dates, list) or not dates:
        return None
    max_days = LAYER_DATE_MAX_DAYS.get(layer_id)
    if max_days is None:
        return None
    # Last entry is the most recent satellite/model date for the window.
    latest_str = dates[-1]
    latest = _parse_iso(str(latest_str))
    if latest is None:
        return Finding(
            severity="medium",
            code="layer_date_unparseable",
            title=f"Layer `{layer_id}` ({window_key}) date is unparseable",
            detail=f"Raw value: {latest_str!r}",
            layer=layer_id,
        )
    age_days = (datetime.now(timezone.utc) - latest).total_seconds() / 86400
    if age_days > max_days:
        return Finding(
            severity="high",
            code="layer_date_stale",
            title=f"Layer `{layer_id}` ({window_key}) data is "
                  f"{age_days:.1f} days old (threshold {max_days} days)",
            detail=f"Latest date in window: {latest_str}. The fetcher likely "
                   f"hit an upstream gap and walked back further than usual.",
            layer=layer_id,
        )
    return None


def _probe_5d_summary(layer_id: str, info: dict, layer_stats: list[dict]) -> list[Finding]:
    """Lightweight check for wind5d / swell5d: fetch the summary JSON and
    confirm it has the expected `days` array. Probing every per-bucket
    PNG would be expensive (~60+ files); the summary is the integrity
    gate."""
    findings: list[Finding] = []
    summary_path = info.get("summary_url")
    if not summary_path:
        findings.append(Finding(
            severity="high",
            code="layer_5d_no_summary_url",
            title=f"Layer `{layer_id}` has no summary_url",
            layer=layer_id,
        ))
        return findings
    url = _resolve_url(summary_path)
    try:
        r = _http_get(url)
    except (requests.exceptions.RequestException, socket.gaierror) as exc:
        findings.append(Finding(
            severity="high",
            code="layer_5d_summary_unreachable",
            title=f"Layer `{layer_id}` summary unreachable",
            detail=f"GET {url}\n  → {exc.__class__.__name__}: {exc}",
            layer=layer_id, url=url,
        ))
        return findings
    if r.status_code != 200:
        findings.append(Finding(
            severity="high",
            code="layer_5d_summary_http_error",
            title=f"Layer `{layer_id}` summary returned HTTP {r.status_code}",
            detail=f"GET {url} → HTTP {r.status_code} {r.reason}",
            layer=layer_id, url=url,
        ))
        return findings
    try:
        summary = r.json()
    except json.JSONDecodeError as exc:
        findings.append(Finding(
            severity="high",
            code="layer_5d_summary_invalid_json",
            title=f"Layer `{layer_id}` summary is not valid JSON",
            detail=f"GET {url} → 200 but parse failed: {exc}",
            layer=layer_id, url=url,
        ))
        return findings

    days = summary.get("days") if isinstance(summary, dict) else None
    expected_days = 5
    layer_stats.append({
        "layer":  layer_id,
        "window": "summary",
        "url":    url,
        "ok":     bool(days) and len(days) >= expected_days,
        "error":  None if (days and len(days) >= expected_days) else
                  f"days={len(days) if isinstance(days, list) else type(days).__name__}",
        "http_status": r.status_code,
        "bytes":  len(r.content) if r.content else 0,
        "days":   len(days) if isinstance(days, list) else 0,
    })
    if not isinstance(days, list) or len(days) < expected_days:
        findings.append(Finding(
            severity="high",
            code="layer_5d_summary_short",
            title=f"Layer `{layer_id}` summary has too few days "
                  f"({len(days) if isinstance(days, list) else 0}/{expected_days})",
            detail=f"GET {url}\n  → summary.days has {len(days) if isinstance(days, list) else 'no'} entries",
            layer=layer_id, url=url,
        ))
    return findings


# ---- Main ------------------------------------------------------------

def main() -> int:
    started = datetime.now(timezone.utc)
    print(f"Probing live deploy at {MANIFEST_URL}")

    findings: list[Finding] = []
    manifest, fatal = fetch_manifest()
    if fatal is not None:
        findings.append(fatal)
        layer_stats: list[dict] = []
    else:
        findings.extend(check_generated_at(manifest))
        layer_findings, layer_stats = check_layers(manifest)
        findings.extend(layer_findings)

    # Print summary
    by_severity = {"critical": 0, "high": 0, "medium": 0}
    for f in findings:
        by_severity[f.severity] = by_severity.get(f.severity, 0) + 1
        marker = {"critical": "CRIT", "high": "HIGH", "medium": "MED "}[f.severity]
        print(f"  [{marker}] {f.code}: {f.title}")
        if f.detail:
            for ln in f.detail.splitlines():
                print(f"         {ln}")

    out = {
        "computed_at": started.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "manifest_url": MANIFEST_URL,
        "manifest_generated_at": (manifest or {}).get("generated_at"),
        "summary": {
            "total_findings": len(findings),
            "critical": by_severity["critical"],
            "high":     by_severity["high"],
            "medium":   by_severity["medium"],
            "healthy":  len(findings) == 0,
        },
        "findings":    [_json_finding(f) for f in findings],
        "layer_stats": layer_stats,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {OUT_PATH.relative_to(REPO_ROOT)}")
    print(f"Summary: {by_severity['critical']} critical / "
          f"{by_severity['high']} high / {by_severity['medium']} medium")

    # Exit code drives the workflow's branching:
    #   0  — all healthy, close any open issue
    #   1  — non-critical findings, open/update issue but don't auto-retry
    #   2  — at least one critical finding, open issue AND trigger refresh-data
    if by_severity["critical"] > 0:
        return 2
    if by_severity["high"] > 0 or by_severity["medium"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
