"""Unit tests for pipeline/lib/layer_spec.py.

The LayerSpec contract is the source of truth for what each layer
publishes. These tests pin the contract: registry consistency,
validator behavior on synthetic manifests, and (most importantly)
that the CURRENTLY-COMMITTED public/data/manifest.json passes the
contract. Any future PR that ships a manifest violating the contract
will fail this test in CI before reaching production.

Run:
    python -m pytest pipeline/tests/test_layer_spec.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Match the sys.path pattern used by the other tests in this directory:
# they treat pipeline/ as the source root rather than a subpackage.
# See pipeline/tests/test_freshness.py for the pattern.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.layer_spec import (  # noqa: E402
    LAYER_SPECS,
    LayerSpec,
    UNIVERSAL_KEYS,
    validate,
)

REPO_ROOT = ROOT.parent  # pipeline/.. = repo root
COMMITTED_MANIFEST = REPO_ROOT / "public" / "data" / "manifest.json"


# ---------------------------------------------------------------------------
# Registry consistency
# ---------------------------------------------------------------------------


def test_registry_keys_match_layer_names():
    for key, spec in LAYER_SPECS.items():
        assert spec.name == key, (
            f"LAYER_SPECS key {key!r} doesn't match its LayerSpec.name {spec.name!r}"
        )


def test_every_spec_has_a_known_category():
    known = {
        "temperature",
        "clarity",
        "wind",
        "current",
        "swell",
        "visibility",
        "derived",
        "forcing",
    }
    for name, spec in LAYER_SPECS.items():
        assert spec.category in known, (
            f"layer {name!r} has unknown category {spec.category!r}; "
            f"add it to the test's `known` set if it's intentional"
        )


def test_every_spec_has_a_known_payload():
    known = {"scalar_png", "uv_png", "wave_png", "summary_only"}
    for name, spec in LAYER_SPECS.items():
        assert spec.payload in known, (
            f"layer {name!r} has unknown payload {spec.payload!r}"
        )


def test_scalar_png_layers_with_range_have_scale():
    # If a layer uses scalar PNG encoding AND declares a range, it must
    # also declare a scale — otherwise the decoder can't unmap pixel
    # values back to physical units.
    for name, spec in LAYER_SPECS.items():
        if spec.payload == "scalar_png" and spec.range is not None:
            assert spec.scale is not None, (
                f"layer {name!r} is scalar_png with a range but no scale"
            )


def test_universal_keys_does_not_overlap_with_extras():
    for name, spec in LAYER_SPECS.items():
        for key in spec.extra_required_keys + spec.extra_optional_keys:
            assert key not in UNIVERSAL_KEYS, (
                f"layer {name!r}: key {key!r} appears in both UNIVERSAL_KEYS "
                f"and the layer's extras — pick one"
            )


# ---------------------------------------------------------------------------
# Validator behavior — synthetic manifests
# ---------------------------------------------------------------------------


def _minimal_valid_manifest():
    return {
        "generated_at": "2026-05-09T12:00:00Z",
        "bbox": [-124.0, 32.4, -117.0, 37.6],
        "layers": {
            "sst": {
                "range": [9.0, 25.0],
                "scale": "linear",
                "unit": "degC",
                "windows": {},
            },
        },
    }


def test_validator_passes_a_minimal_well_formed_manifest():
    issues = validate(_minimal_valid_manifest())
    assert issues == [], f"expected no issues, got {issues}"


def test_validator_flags_missing_top_level_keys():
    m = {}
    issues = validate(m)
    joined = " ".join(issues)
    assert "generated_at" in joined
    assert "bbox" in joined
    assert "layers" in joined


def test_validator_flags_malformed_bbox():
    m = _minimal_valid_manifest()
    m["bbox"] = [1.0, 2.0]  # only 2 elements
    issues = validate(m)
    assert any("bbox" in i for i in issues), issues


def test_validator_flags_unknown_layer():
    m = _minimal_valid_manifest()
    m["layers"]["mystery_layer"] = {"range": [0, 1], "scale": "linear"}
    issues = validate(m)
    assert any("mystery_layer" in i and "unknown" in i for i in issues), issues


def test_validator_flags_range_drift():
    m = _minimal_valid_manifest()
    m["layers"]["sst"]["range"] = [10.0, 26.0]  # drifted from spec (9, 25)
    issues = validate(m)
    assert any("range" in i for i in issues), issues


def test_validator_flags_scale_drift():
    m = _minimal_valid_manifest()
    m["layers"]["sst"]["scale"] = "log10"  # spec says linear
    issues = validate(m)
    assert any("scale" in i for i in issues), issues


def test_validator_flags_missing_required_extra_key():
    # wind layer's spec requires uv_range + speed_range. A wind layer
    # without uv_range should fail.
    m = _minimal_valid_manifest()
    m["layers"]["wind"] = {
        "scale": "linear",
        "unit": "kt",
        "speed_range": [0, 50],
        # uv_range intentionally missing
    }
    issues = validate(m)
    assert any(
        "wind" in i and "uv_range" in i for i in issues
    ), f"expected a wind/uv_range complaint, got {issues}"


# ---------------------------------------------------------------------------
# The committed manifest must match the contract
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not COMMITTED_MANIFEST.exists(),
    reason="public/data/manifest.json is not committed in this checkout",
)
def test_committed_manifest_matches_contract():
    manifest = json.loads(COMMITTED_MANIFEST.read_text(encoding="utf-8"))
    issues = validate(manifest)
    assert issues == [], (
        "Committed manifest does not match LayerSpec contract:\n"
        + "\n".join(f"  - {i}" for i in issues)
    )
