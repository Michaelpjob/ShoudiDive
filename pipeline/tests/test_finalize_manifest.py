"""Unit tests for pipeline/finalize_manifest.py.

finalize_manifest is the recurrence guard for the 2026-06-20 stale-manifest
incident: fetch.py wrote a fresh SST PNG + summary.json (234x206 @ June-20)
but its end-of-main() manifest write never ran, so manifest.layers.sst stayed
@ June-19 (grid 47x42-era). These tests pin the reconcile + verify behaviour
hermetically — a throwaway data dir built per-test, no network, no touching
the real public/data/.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import finalize_manifest as fm  # noqa: E402
from PIL import Image  # noqa: E402


def _write_png(path: Path, w: int, h: int) -> None:
    """8-bit grayscale PNG of size (w, h) — matches the encoder's mode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.zeros((h, w), dtype=np.uint8), mode="L").save(path)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Repoint finalize_manifest at a throwaway CA-style data dir."""
    monkeypatch.setattr(fm, "OUT_DIR", tmp_path)
    monkeypatch.setattr(fm, "SLUG", "ca")
    monkeypatch.setattr(fm, "MANIFEST_PATH", tmp_path / "manifest.json")
    return tmp_path


def test_reconcile_fixes_stale_grid_and_freshens(data_dir):
    # PNG + summary are FRESH (234x206 @ June-20); manifest is STALE
    # (47x42 @ June-19) — the exact 2026-06-20 divergence.
    _write_png(data_dir / "sst_1d.png", 234, 206)
    (data_dir / "sst").mkdir()
    (data_dir / "sst" / "summary.json").write_text(json.dumps(
        {"generated_at": "2026-06-20T08:53:30Z", "grid": {"width": 234, "height": 206}}
    ))
    manifest = {
        "generated_at": "2026-06-19T15:52:08Z",
        "layers": {
            "sst": {
                "grid": {"width": 47, "height": 42},
                "generated_at": "2026-06-19T15:50:19Z",
                "history_summary_url": "/data/sst/summary.json",
                "windows": {"1d": {"url": "/data/sst_1d.png"}},
            }
        },
    }
    changed = fm.reconcile(manifest)

    assert manifest["layers"]["sst"]["grid"] == {"width": 234, "height": 206}
    assert manifest["layers"]["sst"]["generated_at"] == "2026-06-20T08:53:30Z"
    # Top-level bumped to be at least as fresh as SST.
    assert manifest["generated_at"] == "2026-06-20T08:53:30Z"
    assert any("grid" in c for c in changed)
    # The reconciled manifest now satisfies the integrity gates.
    assert fm.verify(manifest) == []


def test_top_level_anchors_on_sst_not_wind(data_dir):
    # Wind is HOURLY and legitimately newer than the daily top-level; the
    # freshness contract anchors on SST, so wind must NOT drag top-level up.
    _write_png(data_dir / "sst_1d.png", 10, 10)
    _write_png(data_dir / "wind_1d.png", 10, 10)
    manifest = {
        "generated_at": "2026-06-19T00:00:00Z",
        "layers": {
            "sst": {
                "grid": {"width": 10, "height": 10},
                "generated_at": "2026-06-20T08:00:00Z",
                "windows": {"1d": {"url": "/data/sst_1d.png"}},
            },
            "wind": {
                "grid": {"width": 10, "height": 10},
                "generated_at": "2026-06-20T22:00:00Z",  # newer than SST
                "windows": {"1d": {"url": "/data/wind_1d.png"}},
            },
        },
    }
    fm.reconcile(manifest)
    assert manifest["generated_at"] == "2026-06-20T08:00:00Z"  # == SST, not wind
    assert fm.verify(manifest) == []


def test_top_level_never_moves_backwards(data_dir):
    # SST older than an already-fresh top-level: keep the newer top-level.
    _write_png(data_dir / "sst_1d.png", 10, 10)
    manifest = {
        "generated_at": "2026-06-20T20:00:00Z",
        "layers": {"sst": {
            "grid": {"width": 10, "height": 10},
            "generated_at": "2026-06-20T08:00:00Z",
            "windows": {"1d": {"url": "/data/sst_1d.png"}},
        }},
    }
    fm.reconcile(manifest)
    assert manifest["generated_at"] == "2026-06-20T20:00:00Z"


def test_verify_catches_grid_mismatch(data_dir):
    _write_png(data_dir / "sst_1d.png", 234, 206)
    manifest = {
        "generated_at": "2026-06-20T08:53:30Z",
        "layers": {"sst": {
            "grid": {"width": 47, "height": 42},  # disagrees with the PNG
            "generated_at": "2026-06-20T08:53:30Z",
            "windows": {"1d": {"url": "/data/sst_1d.png"}},
        }},
    }
    problems = fm.verify(manifest)
    assert problems and "grid" in problems[0]


def test_verify_catches_stale_top_level(data_dir):
    _write_png(data_dir / "sst_1d.png", 10, 10)
    manifest = {
        "generated_at": "2026-06-19T00:00:00Z",   # >5 min behind SST
        "layers": {"sst": {
            "grid": {"width": 10, "height": 10},
            "generated_at": "2026-06-20T08:53:30Z",
            "windows": {"1d": {"url": "/data/sst_1d.png"}},
        }},
    }
    assert any("behind SST" in p for p in fm.verify(manifest))


def test_main_check_only_never_writes(data_dir):
    _write_png(data_dir / "sst_1d.png", 47, 42)  # matches the manifest grid
    mpath = data_dir / "manifest.json"
    mpath.write_text(json.dumps({
        "generated_at": "2026-06-20T08:53:30Z",
        "layers": {"sst": {
            "grid": {"width": 47, "height": 42},
            "generated_at": "2026-06-20T08:53:30Z",
            "windows": {"1d": {"url": "/data/sst_1d.png"}},
        }},
    }))
    before = mpath.read_text()
    assert fm.main(["--check-only"]) == 0
    assert mpath.read_text() == before  # untouched


def test_main_check_only_fails_on_divergence(data_dir):
    _write_png(data_dir / "sst_1d.png", 234, 206)
    (data_dir / "manifest.json").write_text(json.dumps({
        "generated_at": "2026-06-20T08:53:30Z",
        "layers": {"sst": {
            "grid": {"width": 47, "height": 42},  # diverged from the PNG
            "generated_at": "2026-06-20T08:53:30Z",
            "windows": {"1d": {"url": "/data/sst_1d.png"}},
        }},
    }))
    assert fm.main(["--check-only"]) == 1  # fail-loud → cron skips deploy


def test_main_reconcile_persists_and_then_verifies(data_dir):
    # End-to-end: stale on disk -> main() writes the fix -> --check-only green.
    _write_png(data_dir / "sst_1d.png", 234, 206)
    (data_dir / "sst").mkdir()
    (data_dir / "sst" / "summary.json").write_text(json.dumps(
        {"generated_at": "2026-06-20T08:53:30Z"}
    ))
    mpath = data_dir / "manifest.json"
    mpath.write_text(json.dumps({
        "generated_at": "2026-06-19T15:52:08Z",
        "layers": {"sst": {
            "grid": {"width": 47, "height": 42},
            "generated_at": "2026-06-19T15:50:19Z",
            "history_summary_url": "/data/sst/summary.json",
            "windows": {"1d": {"url": "/data/sst_1d.png"}},
        }},
    }))
    assert fm.main([]) == 0
    persisted = json.loads(mpath.read_text())
    assert persisted["layers"]["sst"]["grid"] == {"width": 234, "height": 206}
    assert persisted["generated_at"] == "2026-06-20T08:53:30Z"
    assert fm.main(["--check-only"]) == 0
