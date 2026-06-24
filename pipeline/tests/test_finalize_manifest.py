"""Unit tests for finalize_manifest — manifest<->PNG grid reconciliation."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # pipeline/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import json  # noqa: E402

from PIL import Image  # noqa: E402

import finalize_manifest as fm  # noqa: E402


def _write_manifest(tmp_path, layer):
    mpath = tmp_path / "manifest.json"
    mpath.write_text(json.dumps({"layers": {"sst": layer}}, indent=2), encoding="utf-8")
    return mpath


def test_reconcile_fixes_window_layer(tmp_path, monkeypatch):
    monkeypatch.setattr(fm, "DATA_DIR", tmp_path)
    Image.new("L", (40, 30)).save(tmp_path / "layer.png")
    mpath = _write_manifest(tmp_path, {
        "grid": {"width": 10, "height": 10},
        "windows": {"now": {"url": "/data/layer.png"}},
    })
    changes = fm.reconcile_region("ca", mpath, check_only=False)
    assert len(changes) == 1
    assert json.loads(mpath.read_text())["layers"]["sst"]["grid"] == {"width": 40, "height": 30}


def test_reconcile_follows_summary_url(tmp_path, monkeypatch):
    # sst7d / sst5d style: the layer declares a grid but the PNG is reached via
    # summary_url. The dims test can't follow this; finalize can.
    monkeypatch.setattr(fm, "DATA_DIR", tmp_path)
    Image.new("L", (586, 511)).save(tmp_path / "hist.png")
    (tmp_path / "summary.json").write_text(json.dumps({"frames": ["/data/hist.png"]}), encoding="utf-8")
    mpath = _write_manifest(tmp_path, {
        "grid": {"width": 234, "height": 206},
        "summary_url": "/data/summary.json",
    })
    changes = fm.reconcile_region("ca", mpath, check_only=False)
    assert len(changes) == 1
    assert json.loads(mpath.read_text())["layers"]["sst"]["grid"] == {"width": 586, "height": 511}


def test_check_only_reports_without_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(fm, "DATA_DIR", tmp_path)
    Image.new("L", (40, 30)).save(tmp_path / "layer.png")
    mpath = _write_manifest(tmp_path, {
        "grid": {"width": 10, "height": 10},
        "windows": {"now": {"url": "/data/layer.png"}},
    })
    before = mpath.read_text()
    changes = fm.reconcile_region("ca", mpath, check_only=True)
    assert len(changes) == 1            # reported
    assert mpath.read_text() == before  # but not written


def test_consistent_manifest_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(fm, "DATA_DIR", tmp_path)
    Image.new("L", (40, 30)).save(tmp_path / "layer.png")
    mpath = _write_manifest(tmp_path, {
        "grid": {"width": 40, "height": 30},
        "windows": {"now": {"url": "/data/layer.png"}},
    })
    before = mpath.read_text()
    assert fm.reconcile_region("ca", mpath, check_only=False) == []
    assert mpath.read_text() == before


def test_missing_png_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(fm, "DATA_DIR", tmp_path)
    mpath = _write_manifest(tmp_path, {
        "grid": {"width": 10, "height": 10},
        "windows": {"now": {"url": "/data/nope.png"}},
    })
    assert fm.reconcile_region("ca", mpath, check_only=False) == []  # no PNG -> skip, no crash
