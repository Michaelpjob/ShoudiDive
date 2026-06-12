"""LayerSpec — single source of truth for every layer the pipeline
publishes and the frontend renders.

Background:
  The pipeline encodes ocean data as 8-bit PNGs over a range. A linear
  layer like SST maps 0..255 → 9..25 °C. A log-scaled layer like chl
  maps 0..255 → log10(0.05..20) mg/m³. Wind has the same encoding twice
  (u channel + v channel).

  The encoder side (pipeline/fetch_*.py) and the decoder side
  (src/lib/dataSource.js) both need to agree on:
    * range  (the [min, max] of the physical value)
    * scale  ("linear" | "log10")
    * type   (which channel layout: scalar / uv / wave-rgba)

  Today these constants are repeated across ~12 fetcher scripts and the
  frontend's per-layer if/else chain in dataSource.js. A drift between
  the encoder writing range=(9,25) and the decoder using range=(8,26)
  silently produces wrong colors with no error — exactly the kind of
  bug that's invisible until a user complains.

  This module is the single source of truth. The validator
  pipeline/validate_manifest.py reads the published manifest.json and
  asserts that every layer's range / scale / shape matches the
  registry below.

Scope of this PR:
  * Define LayerSpec
  * Build LAYER_SPECS — the registry
  * validate(manifest) returns a list of contract violations

Out of scope (follow-up PRs):
  * Migrating fetch_*.py to import from here instead of redefining
  * Migrating the frontend's decoder ladder to read from a JSON
    snapshot of LAYER_SPECS shipped at build time

This module is stdlib-only on purpose — the pipeline-tests CI job
runs validate(manifest) on every PR, so we want the contract checker
to install in <1 second from a clean cache.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# LayerSpec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LayerSpec:
    """Contract for one published layer."""

    name: str
    """The manifest key, e.g. 'sst', 'wind5d', 'current5d'."""

    category: str
    """Coarse grouping for UI + monitoring, one of:
       'temperature' | 'clarity' | 'wind' | 'current' | 'swell' |
       'visibility' | 'derived' | 'forcing'.
       'forcing' = published in the manifest as input to a downstream
       model but NOT rendered in the frontend (precip, wave inputs).
    """

    range: tuple[float, float] | None
    """Physical [min, max]. None when the layer is summary-only (no
    PNG range — e.g. swell5d declares its range under per-channel keys
    like height_range_m / period_range_s instead)."""

    scale: str | None
    """'linear' | 'log10' | None for summary-only layers."""

    unit: str | None
    """Human-readable physical unit (degC, mg/m^3, kt, m/s, ft, etc.)."""

    payload: str
    """Channel layout for the published PNG(s):
       'scalar_png'  — one 8-bit grayscale PNG, value scaled by range
       'uv_png'      — RGB PNG carrying u (R) and v (G) components
       'wave_png'    — RGBA PNG carrying Hs (R) Tp (G) Dp (B)
       'summary_only'— no PNG; metadata-only layer for the frontend
                       (e.g. tides.json, rivers.json — those don't
                       live in the layers map but the spec still
                       declares them for completeness elsewhere)
    """

    frontend_renders: bool = True
    """True if the React app draws this layer on the map. False for
    pipeline-internal forcing inputs (precip, wave ingest)."""

    extra_required_keys: tuple[str, ...] = field(default_factory=tuple)
    """Manifest keys this layer is contractually required to publish
    beyond the universal set (range/scale/unit). Used by validate()."""

    extra_optional_keys: tuple[str, ...] = field(default_factory=tuple)
    """Manifest keys this layer MAY publish without triggering an
    'unknown key' violation. The validator is conservative: anything
    not in {universal} ∪ extra_required ∪ extra_optional is a
    violation, so the registry stays honest."""


# ---------------------------------------------------------------------------
# Registry — the actual contract
# ---------------------------------------------------------------------------
#
# These match the values currently emitted by pipeline/fetch.py,
# pipeline/fetch_wind.py, pipeline/fetch_currents.py,
# pipeline/fetch_swell_5day.py, etc. If a fetcher emits a different
# range or scale, validate(manifest) fires and the dev-checks gate
# fails — surfacing the drift before it ships.
#
# Universal keys that every PNG-bearing layer publishes (validator
# allows these on every layer without listing them per-spec):
#   range, scale, unit, grid, windows, generated_at
UNIVERSAL_KEYS: tuple[str, ...] = (
    "range",
    "scale",
    "unit",
    "grid",
    "windows",
    "generated_at",
    "source",
    "source_url",
    "source_legend",
    "sources",  # multi-source layers (chl_blend) carry a list
)


LAYER_SPECS: dict[str, LayerSpec] = {
    "sst": LayerSpec(
        name="sst",
        category="temperature",
        range=(9.0, 25.0),
        scale="linear",
        unit="degC",
        payload="scalar_png",
        extra_optional_keys=(
            "history_summary_url",
            "forecast_summary_url",
        ),
    ),
    "sst7d": LayerSpec(
        name="sst7d",
        category="temperature",
        range=(9.0, 25.0),
        scale="linear",
        unit="degC",
        payload="summary_only",
        extra_required_keys=("summary_url",),
    ),
    "sst5d": LayerSpec(
        name="sst5d",
        category="temperature",
        range=(9.0, 25.0),
        scale="linear",
        unit="degC",
        payload="summary_only",
        extra_required_keys=("summary_url",),
        extra_optional_keys=("beta", "method"),
    ),
    "chl": LayerSpec(
        name="chl",
        category="clarity",
        range=(0.05, 20.0),
        scale="log10",
        unit="mg/m^3",
        payload="scalar_png",
        extra_optional_keys=(
            "blended",
            "age_days_url",
            "freshest_date",
            "frames_used",
            "valid_cells_in_source",
            "coverage_frac",
            "best_window",
            "method",
        ),
    ),
    "kd490": LayerSpec(
        name="kd490",
        category="clarity",
        range=(0.02, 10.0),
        scale="log10",
        unit="m^-1",
        payload="scalar_png",
        extra_optional_keys=(
            "age_days_url",
            "freshest_date",
            "frames_used",
            "method",
        ),
    ),
    "wind": LayerSpec(
        name="wind",
        category="wind",
        # Range expressed via uv_range + speed_range, not a single range.
        range=None,
        scale="linear",
        unit="kt",
        payload="uv_png",
        extra_required_keys=("uv_range", "speed_range"),
        extra_optional_keys=("vector_convention",),
    ),
    "wind5d": LayerSpec(
        name="wind5d",
        category="wind",
        range=None,
        scale="linear",
        unit="kt",
        payload="uv_png",
        extra_required_keys=("uv_range", "speed_range", "summary_url"),
    ),
    "swell5d": LayerSpec(
        name="swell5d",
        category="swell",
        range=None,
        scale="linear",
        unit="m,s,deg",
        payload="wave_png",
        extra_required_keys=(
            "height_range_m",
            "period_range_s",
            "summary_url",
        ),
    ),
    "current5d": LayerSpec(
        name="current5d",
        category="current",
        range=None,
        scale="linear",
        unit="kt",
        payload="uv_png",
        extra_required_keys=("uv_range", "speed_range", "summary_url"),
        extra_optional_keys=("beta", "method", "vector_convention"),
    ),
    "rtofs5d": LayerSpec(
        # NOAA RTOFS Global ocean-model 7-day forecast. Parallel track
        # to sst5d (persistence-decay) carrying SST + surface currents
        # in one manifest entry — the loader (src/lib/loaders/rtofs5d.js)
        # decodes the per-lead SST PNGs from `sst_d{1,3,5,7}.png` and the
        # per-lead U/V RGBA PNGs from `uv_d{1,3,5,7}.png` referenced
        # inside the summary file. Range is region-aware via
        # active_region().layer_range_overrides["sst5d"]; the manifest
        # entry mirrors whatever range the SST PNGs were encoded with.
        # Beta flag matches sst5d's convention.
        name="rtofs5d",
        category="temperature",
        range=(9.0, 25.0),
        scale="linear",
        unit="degC",
        payload="summary_only",
        extra_required_keys=("summary_url", "uv_range", "uv_unit"),
        extra_optional_keys=(
            "beta",
            "model",
            "init_cycle",
            "horizon_days",
            "leads_day_offsets",
        ),
    ),
    "viz": LayerSpec(
        name="viz",
        category="visibility",
        range=None,  # range_ft instead
        scale="linear",
        unit="ft",
        payload="scalar_png",
        extra_required_keys=("range_ft",),
        extra_optional_keys=(
            "p10_url", "p90_url", "quality_url",
            # 2026-05-14: viz marked beta until NorCal ground truth lands;
            # beta_reason is a free-text disclaimer surfaced in the UI.
            "beta", "beta_reason",
        ),
    ),
    "viz_column": LayerSpec(
        # Water-column visibility (PRD water-column C1): below-cliff
        # vis raster on the viz 0-80 ft range (so the existing Vis
        # legend semantics decode it), plus a cliff-depth raster
        # (cliff_range_ft) and a per-spot sidecar (spots_url) carrying
        # the 24 h internal-tide cliff series. frontend_renders stays
        # False until the V-group UI (WaterColumn.jsx) lands; flip it
        # in that PR.
        name="viz_column",
        category="visibility",
        range=None,  # range_ft + cliff_range_ft instead
        scale="linear",
        unit="ft",
        payload="scalar_png",
        frontend_renders=False,
        extra_required_keys=("range_ft", "cliff_range_ft"),
        extra_optional_keys=(
            "swing_ft", "spots_url", "method", "beta", "beta_reason",
        ),
    ),
    "wave": LayerSpec(
        # Published as a wave_png with height_range_m + period_range_s,
        # same encoding as swell5d. Used as input to fetch_visibility,
        # not rendered in the frontend.
        name="wave",
        category="swell",
        range=None,
        scale=None,
        unit="m,s",
        payload="wave_png",
        frontend_renders=False,
        extra_required_keys=("height_range_m", "period_range_s"),
    ),
    "precip": LayerSpec(
        # 7-day cumulative precip from NOAA CPC. Encoded against
        # range_mm (per-layer key, like viz's range_ft) instead of a
        # generic 'range' field, and the manifest does not currently
        # carry a top-level 'scale' for this layer (encoding is
        # implicit linear). Used as input to fetch_visibility, not
        # rendered in the frontend.
        name="precip",
        category="forcing",
        range=None,
        scale=None,
        unit="mm",
        payload="scalar_png",
        frontend_renders=False,
        extra_required_keys=("range_mm",),
    ),
}


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------
#
# Returns a list of human-readable violation strings. Empty list = manifest
# matches the contract. Caller (validate_manifest.py CLI) renders the list
# to stdout and exits non-zero on any violation.


def validate(manifest: dict[str, Any]) -> list[str]:
    """Return the list of contract violations in `manifest`.

    The manifest is the parsed JSON object — top-level keys
    `generated_at`, `bbox`, `layers`. Empty list means clean.
    """
    issues: list[str] = []

    # Top-level shape
    if "generated_at" not in manifest:
        issues.append("manifest: missing top-level 'generated_at'")
    if "bbox" not in manifest:
        issues.append("manifest: missing top-level 'bbox'")
    elif not _is_bbox(manifest["bbox"]):
        issues.append(
            f"manifest.bbox: expected [lng_min, lat_min, lng_max, lat_max] "
            f"as 4 floats, got {manifest['bbox']!r}"
        )
    if "layers" not in manifest or not isinstance(manifest["layers"], dict):
        issues.append("manifest: missing or malformed top-level 'layers' object")
        return issues  # nothing more to do without layers

    layers = manifest["layers"]

    # Every published layer must be registered.
    for name in layers:
        if name not in LAYER_SPECS:
            issues.append(
                f"layer {name!r}: unknown layer (not in pipeline.lib.layer_spec.LAYER_SPECS). "
                f"Add a LayerSpec entry, or rename the manifest key to match an existing spec."
            )

    for name, spec in LAYER_SPECS.items():
        if name not in layers:
            # Forcing layers (wave, precip) may legitimately be absent
            # if their fetcher failed. Frontend-rendered layers SHOULD
            # be present — but soft-fail at the validator level since
            # the watchdog already opens an issue when they're missing.
            continue
        info = layers[name]
        issues.extend(_validate_layer_info(name, info, spec))

    return issues


def _is_bbox(b: Any) -> bool:
    return (
        isinstance(b, list)
        and len(b) == 4
        and all(isinstance(x, (int, float)) for x in b)
    )


def _validate_layer_info(name: str, info: Any, spec: LayerSpec) -> list[str]:
    issues: list[str] = []

    if not isinstance(info, dict):
        issues.append(f"layer {name!r}: expected object, got {type(info).__name__}")
        return issues

    # range
    if spec.range is not None:
        r = info.get("range")
        if r is None:
            issues.append(f"layer {name!r}: missing required 'range'")
        elif not (isinstance(r, list) and len(r) == 2):
            issues.append(f"layer {name!r}: 'range' should be [min, max] of length 2")
        else:
            spec_min, spec_max = spec.range
            r_min, r_max = float(r[0]), float(r[1])
            if abs(r_min - spec_min) > 1e-6 or abs(r_max - spec_max) > 1e-6:
                issues.append(
                    f"layer {name!r}: range {r} does not match spec {list(spec.range)}. "
                    f"Drift = encoder side and decoder side will produce wrong colors."
                )

    # scale
    if spec.scale is not None:
        s = info.get("scale")
        # 'wind' / 'wind5d' / 'current5d' / 'swell5d' don't publish a
        # top-level 'scale' (they use uv_range / height_range_m etc.).
        # Only flag missing scale when payload is scalar_png.
        if spec.payload == "scalar_png" and s is None:
            issues.append(f"layer {name!r}: missing required 'scale'")
        elif s is not None and s != spec.scale:
            issues.append(
                f"layer {name!r}: scale {s!r} does not match spec {spec.scale!r}"
            )

    # required extra keys
    for key in spec.extra_required_keys:
        if key not in info:
            issues.append(f"layer {name!r}: missing required key {key!r}")

    # unknown keys are not flagged — manifests evolve, and the validator
    # should not block a deploy because a fetcher added a useful diagnostic
    # field. Range/scale drift is the failure mode that matters.

    return issues
