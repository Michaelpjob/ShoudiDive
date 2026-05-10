"""Pin fetch.py's LAYERS dict to the LayerSpec contract.

fetch.py's LAYERS dict used to repeat range/scale/unit values inline,
making it possible for one of the 12 fetcher scripts to drift from
the contract in pipeline/lib/layer_spec.py without anyone noticing
until a user reported wrong colors.

Now `fetch.py` imports `LAYER_SPECS` and merges encoder-specific config
on top via the `_layer_config()` helper. This test pins that wiring:
the published values (range, scale, unit) inside fetch.py's LAYERS
dict must equal the values in LAYER_SPECS.

Run:
    python -m pytest pipeline/tests/test_fetch_layer_spec_consistency.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# fetch.py uses `from pipeline.lib.layer_spec import LAYER_SPECS`,
# which requires the REPO root on sys.path. The other tests in this
# directory put pipeline/ on sys.path directly because they import
# `from fetch import ...` (the top-level scripts as bare modules).
# We do BOTH so this test file works whichever way pytest is launched.
ROOT = Path(__file__).resolve().parents[1]   # pipeline/
REPO_ROOT = ROOT.parent                      # repo root
for p in (str(REPO_ROOT), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from lib.layer_spec import LAYER_SPECS       # noqa: E402

# Importing fetch has side effects (it parses argv when run as a script
# through __main__, but as a module-level import it just defines the
# LAYERS dict + helper functions). The xarray + pillow deps are listed
# in pipeline/requirements.txt and installed by the dev-checks
# pipeline-tests job.
import fetch                                 # noqa: E402


@pytest.mark.parametrize("name", ["sst", "chl", "kd490"])
def test_fetch_layer_range_matches_spec(name):
    """fetch.py's published `range` for each layer must equal the
    LayerSpec contract's range. Drift here = encoder writes one
    range, decoder reads another, colors silently wrong."""
    encoder = fetch.LAYERS[name]
    spec = LAYER_SPECS[name]
    assert tuple(encoder["range"]) == tuple(spec.range), (
        f"fetch.py LAYERS[{name!r}].range = {encoder['range']!r} but "
        f"LayerSpec for {name!r} has range = {spec.range!r}. "
        f"Update one or the other so the pipeline encoder and "
        f"the frontend decoder agree."
    )


@pytest.mark.parametrize("name", ["sst", "chl", "kd490"])
def test_fetch_layer_scale_matches_spec(name):
    encoder = fetch.LAYERS[name]
    spec = LAYER_SPECS[name]
    assert encoder["scale"] == spec.scale, (
        f"fetch.py LAYERS[{name!r}].scale = {encoder['scale']!r} but "
        f"LayerSpec for {name!r} has scale = {spec.scale!r}"
    )


@pytest.mark.parametrize("name", ["sst", "chl", "kd490"])
def test_fetch_layer_unit_matches_spec(name):
    encoder = fetch.LAYERS[name]
    spec = LAYER_SPECS[name]
    assert encoder["unit"] == spec.unit, (
        f"fetch.py LAYERS[{name!r}].unit = {encoder['unit']!r} but "
        f"LayerSpec for {name!r} has unit = {spec.unit!r}"
    )


def test_fetch_layer_config_helper_rejects_specs_without_range():
    """The _layer_config helper should fail loudly if a future LayerSpec
    entry has range=None — that's a configuration error for any layer
    going through fetch.py (which only encodes scalar PNGs)."""
    # `viz` and `wave` have range=None in LAYER_SPECS by design.
    with pytest.raises(ValueError, match="no range"):
        fetch._layer_config("viz", {"dataset": "x"})
    with pytest.raises(ValueError, match="no range"):
        fetch._layer_config("wave", {"dataset": "x"})
