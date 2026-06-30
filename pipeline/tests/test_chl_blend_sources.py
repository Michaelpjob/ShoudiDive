"""Source-roster + provenance-labeling tests for pipeline/chl_blend.py.

chl is built by a multi-source BLENDER (build_blended_chl), NOT fetch.py's
build_layer — so its fallback wiring + source labeling live here, separate
from the SST/kd490 path. (A closed PR once added a chl fallback to
LAYERS["chl"].fallbacks and it was a silent no-op for exactly this reason.)

These tests pin:
  * the source roster (ids + priorities unique; a no-auth, GitHub-reachable
    last-resort source is flagged fallback=True; primaries are not);
  * the ERDDAP URL builder wires `variable=` + the altitude [0] index for the
    raw-VIIRS dataset (dims time/altitude/lat/lon) — a wrong var/dim yields no
    frames silently;
  * build_blended_chl's provenance: the source owning the most 1d cells is
    written to manifest `source`, and `source_fallback` is set IFF that
    dominant source is a fallback. src/lib/confidence.js reads source_fallback
    to drop the score + surface "via <source> (primary unavailable)".

Network-free: every fetcher is stubbed and http_get is monkeypatched.

Run:
    python -m pytest pipeline/tests/test_chl_blend_sources.py -v
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest  # noqa: F401  (fixtures: tmp_path, monkeypatch)

# Mirror the other pipeline tests: import the top-level script as a bare module.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import chl_blend as cb  # noqa: E402


# ---- Source roster ------------------------------------------------------

def test_sources_import_and_ids_unique():
    assert isinstance(cb.CHL_SOURCES, list) and len(cb.CHL_SOURCES) > 0
    ids = [s.id for s in cb.CHL_SOURCES]
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate chl source ids: {sorted(dupes)}"


def test_priorities_unique():
    # Roster is declared best-first; priority ints must be unique so the
    # blend tie-break (lower priority int wins a cell) is deterministic.
    prios = [s.priority for s in cb.CHL_SOURCES]
    assert len(set(prios)) == len(prios), f"duplicate priorities: {prios}"


def test_has_noauth_last_resort_fallback():
    """For the 2026-06 double outage (NASA primaries need EARTHDATA *and*
    coastwatch.noaa.gov DINEOFs were down) chl needs a no-auth, last-resort
    source so the layer can stay live-but-coarse instead of freezing."""
    fbs = [s for s in cb.CHL_SOURCES if s.fallback]
    assert fbs, "no fallback chl source flagged"
    noauth = [s for s in fbs if not s.requires_earthdata]
    assert noauth, "the fallback chl source must be no-auth (no EARTHDATA)"
    # A fallback must be a genuine last resort — the highest priority int.
    assert max(s.priority for s in cb.CHL_SOURCES) in {s.priority for s in fbs}


def test_primaries_not_flagged_fallback():
    # The NASA (≥3) + DINEOF (≥2) primaries must NOT carry the fallback flag —
    # that flag drops user-facing confidence and would mislabel good data.
    primaries = [s for s in cb.CHL_SOURCES if not s.fallback]
    assert len(primaries) >= 4
    assert all(not s.fallback for s in primaries)


# ---- ERDDAP URL builder (variable + altitude wiring) --------------------

def test_erddap_fetcher_wires_variable_and_altitude(tmp_path, monkeypatch):
    """The raw-VIIRS dataset serves `chla` in a (time, altitude, lat, lon)
    shape — the builder must request ?chla...[0]... (the altitude index),
    not the DINEOF default chlor_a. Captures the URL without any network."""
    captured = {}

    def fake_get(url, **kw):
        captured["url"] = url
        return None  # short-circuit: we only want the URL it built

    monkeypatch.setattr(cb, "http_get", fake_get)
    monkeypatch.setattr(cb, "CACHE_DIR", tmp_path)  # don't touch the real cache

    fetcher = cb._make_noaa_erddap_fetcher(
        "https://upwell.pfeg.noaa.gov/erddap/griddap",
        "erdVHNchla1day", has_altitude=True, variable="chla")
    assert fetcher(date(2026, 6, 16)) is None  # http_get returned None
    url = captured["url"]
    assert "upwell.pfeg.noaa.gov" in url
    assert "erdVHNchla1day.nc?chla" in url
    assert ")][0][(" in url, f"altitude [0] index not wired between time/lat: {url}"


def test_erddap_fetcher_default_variable_is_chlor_a(tmp_path, monkeypatch):
    """The DINEOF primaries rely on the default variable (chlor_a) — guard
    against a future refactor flipping the default to chla."""
    captured = {}
    monkeypatch.setattr(cb, "http_get",
                        lambda url, **kw: captured.__setitem__("url", url) or None)
    monkeypatch.setattr(cb, "CACHE_DIR", tmp_path)
    cb._make_noaa_erddap_fetcher("https://h/erddap/griddap", "ds")(date(2026, 6, 16))
    assert ".nc?chlor_a" in captured["url"]


# ---- Provenance labeling (build_blended_chl) ----------------------------

def _const_fetcher(value):
    """Fetcher returning a full canonical grid of `value`. np.nan => a source
    that yields no valid frame (so it's dropped, like an unavailable primary)."""
    def f(d):
        return np.full((cb.OUT_H, cb.OUT_W), value, dtype=np.float32)
    return f


def _stub_blend_env(monkeypatch, tmp_path, sources):
    monkeypatch.setattr(cb, "CHL_SOURCES", sources)
    monkeypatch.setattr(cb, "OUT_DIR", tmp_path)          # PNG sidecars -> tmp
    monkeypatch.setattr(cb, "_load_land_mask", lambda w, h, *a, **k: None)
    monkeypatch.delenv("EARTHDATA_TOKEN", raising=False)


def test_blend_flags_source_fallback_when_fallback_dominates(tmp_path, monkeypatch):
    # Primary unavailable (all-NaN -> dropped), so the raw fallback owns the
    # blend. The manifest must say so and flag source_fallback.
    primary = cb.ChlSource(id="primary", label="Primary (gap-filled)",
                           priority=1, max_back=3, fetcher=_const_fetcher(np.nan))
    fb = cb.ChlSource(id="fb", label="Raw VIIRS fallback", priority=6,
                      max_back=3, fetcher=_const_fetcher(0.5), fallback=True)
    _stub_blend_env(monkeypatch, tmp_path, [primary, fb])

    ml = cb.build_blended_chl(date(2026, 6, 16))
    assert ml is not None
    assert ml["source"] == "Raw VIIRS fallback"
    assert ml.get("source_fallback") is True


def test_blend_no_fallback_flag_when_primary_dominates(tmp_path, monkeypatch):
    # Both serve, but the primary (lower priority int) owns every cell, so the
    # blend is primary-quality — no source_fallback, no confidence penalty.
    primary = cb.ChlSource(id="primary", label="Primary (gap-filled)",
                           priority=1, max_back=3, fetcher=_const_fetcher(0.3))
    fb = cb.ChlSource(id="fb", label="Raw VIIRS fallback", priority=6,
                      max_back=3, fetcher=_const_fetcher(0.5), fallback=True)
    _stub_blend_env(monkeypatch, tmp_path, [primary, fb])

    ml = cb.build_blended_chl(date(2026, 6, 16))
    assert ml is not None
    assert ml["source"] == "Primary (gap-filled)"
    assert "source_fallback" not in ml


def test_blend_returns_none_when_no_source_has_data(tmp_path, monkeypatch):
    dead = cb.ChlSource(id="dead", label="Dead", priority=1, max_back=3,
                        fetcher=_const_fetcher(np.nan))
    _stub_blend_env(monkeypatch, tmp_path, [dead])
    assert cb.build_blended_chl(date(2026, 6, 16)) is None
