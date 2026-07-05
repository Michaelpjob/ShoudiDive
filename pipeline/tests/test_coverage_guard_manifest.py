"""Unit tests for check_coverage_guard.merge_restored_window.

When the coverage guard restores a degraded PNG from HEAD, the manifest
metadata for that layer's window must revert with it — otherwise the
manifest advertises today's observation dates over yesterday's restored
pixels and the frontend confidence UI reads the layer as fresh. These
tests pin the dict-surgery helper: restored window reverts, sibling
windows keep their fresh metadata, and missing/malformed entries are a
no-op rather than a crash.

Run:
    python -m pytest pipeline/tests/test_coverage_guard_manifest.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from check_coverage_guard import merge_restored_window  # noqa: E402


def _manifests():
    head = {
        "layers": {
            "sst": {
                "generated_at": "2026-07-03T08:00:00Z",
                "windows": {
                    "1d": {"url": "/data/sst_1d.png", "date": "2026-07-02"},
                    "3d": {"url": "/data/sst_3d.png", "date": "2026-07-01"},
                },
            },
        },
    }
    current = {
        "layers": {
            "sst": {
                "generated_at": "2026-07-04T08:00:00Z",
                "windows": {
                    "1d": {"url": "/data/sst_1d.png", "date": "2026-07-04"},
                    "3d": {"url": "/data/sst_3d.png", "date": "2026-07-03"},
                },
            },
        },
    }
    return head, current


def test_restored_window_and_generated_at_revert_to_head():
    head, current = _manifests()
    assert merge_restored_window(current, head, "sst", "1d") is True
    sst = current["layers"]["sst"]
    assert sst["windows"]["1d"]["date"] == "2026-07-02"
    assert sst["generated_at"] == "2026-07-03T08:00:00Z"


def test_sibling_windows_keep_fresh_metadata():
    head, current = _manifests()
    merge_restored_window(current, head, "sst", "1d")
    # 3d PNG wasn't restored, so its fresh date must survive.
    assert current["layers"]["sst"]["windows"]["3d"]["date"] == "2026-07-03"


def test_missing_layer_in_head_is_noop():
    head, current = _manifests()
    del head["layers"]["sst"]
    before = current["layers"]["sst"]["windows"]["1d"]["date"]
    assert merge_restored_window(current, head, "sst", "1d") is False
    assert current["layers"]["sst"]["windows"]["1d"]["date"] == before


def test_missing_layer_in_current_is_noop():
    head, current = _manifests()
    del current["layers"]["sst"]
    assert merge_restored_window(current, head, "sst", "1d") is False


def test_empty_manifests_do_not_crash():
    assert merge_restored_window({}, {}, "sst", "1d") is False
