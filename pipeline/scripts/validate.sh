#!/usr/bin/env bash
# pipeline/scripts/validate.sh — automated test pipeline for the data layer.
#
# Mirrors mobile/scripts/validate.sh: each layer prints a banner, fails fast
# on first error, and reports a summary at the end. The pipeline doesn't
# have a JS toolchain so layers are simpler than mobile's seven —
# four layers covering static checks, unit tests, and (optionally) the
# two real-network integration paths PR1 added.
#
# Layers:
#   1. STATIC      python -m py_compile every pipeline/*.py + import smoke
#   2. UNIT        pytest pipeline/tests/test_*.py  (no network)
#   3. FETCH       python pipeline/fetch.py --layer chl  + sidecar assertions
#   4. VISIBILITY  python pipeline/fetch_visibility.py + viz_quality assertions
#
# Usage:
#   pipeline/scripts/validate.sh              # all four layers (network)
#   pipeline/scripts/validate.sh --unit       # layers 1+2 only (no network)
#   pipeline/scripts/validate.sh --full       # alias for default
#   pipeline/scripts/validate.sh --skip-fetch # 1+2+4 (re-use existing fetch
#                                              outputs in public/data)
#
# Exit codes:
#   0   every requested layer passed
#   1   at least one layer failed
#   2   bad invocation (unknown flag, missing python, etc.)

set -u  # don't `set -e`: we want to capture per-layer outcomes and report all

# ----- Argument parsing ----------------------------------------------------

MODE="full"
case "${1:-}" in
  ""|--full) MODE="full" ;;
  --unit) MODE="unit" ;;
  --skip-fetch) MODE="skip-fetch" ;;
  -h|--help)
    sed -n '1,30p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  *)
    echo "validate.sh: unknown flag: $1" >&2
    echo "Try --unit, --full, or --skip-fetch" >&2
    exit 2
    ;;
esac

# ----- Locate repo root ----------------------------------------------------

# This script lives in pipeline/scripts/, so the repo root is two levels up.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

PYTHON="${PYTHON:-python}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "validate.sh: python not on PATH (set \$PYTHON to override)" >&2
  exit 2
fi

# ----- Per-layer status tracking -------------------------------------------

declare -a LAYER_NAMES=()
declare -a LAYER_RESULTS=()

run_layer() {
  local name="$1"; shift
  local cmd="$*"
  echo
  echo "============================================================"
  echo "  $name"
  echo "------------------------------------------------------------"
  echo "  $ $cmd"
  echo "============================================================"
  local start_ts
  start_ts=$(date +%s)
  if eval "$cmd"; then
    local elapsed=$(( $(date +%s) - start_ts ))
    LAYER_NAMES+=("$name")
    LAYER_RESULTS+=("PASS  (${elapsed}s)")
    return 0
  else
    local elapsed=$(( $(date +%s) - start_ts ))
    LAYER_NAMES+=("$name")
    LAYER_RESULTS+=("FAIL  (${elapsed}s)")
    return 1
  fi
}

# ----- Layer 1: static checks ---------------------------------------------

layer_static() {
  # py_compile every Python file under pipeline/ — catches syntax errors
  # without executing imports (so missing optional deps don't fail it).
  # Pipe through xargs so the argv stays under Windows' ARG_MAX limit
  # even on repos with a venv in pipeline/.venv/. Skip __pycache__ and
  # any .venv tree (we're testing OUR code, not vendored deps).
  echo "[static] compile every pipeline/**/*.py"
  find pipeline -name '*.py' \
                -not -path '*/__pycache__/*' \
                -not -path '*/.venv/*' \
                -print0 \
    | xargs -0 -n 50 "$PYTHON" -m py_compile \
    || return 1

  # Import smoke for the two PR1 modules — proves their top-level imports
  # resolve and the new symbols are exposed.
  echo "[static] import smoke (fetch + fetch_visibility)"
  $PYTHON - <<'PY' || return 1
import sys
sys.path.insert(0, 'pipeline')
import fetch
import fetch_visibility
required = {
    'fetch': ('build_age_array', 'encode_age_png', 'build_layer'),
    'fetch_visibility': ('decode_age_png', 'decode_log10_png'),
}
missing = []
for mod_name, names in required.items():
    mod = sys.modules[mod_name]
    for n in names:
        if not hasattr(mod, n):
            missing.append(f"{mod_name}.{n}")
if missing:
    print(f"missing required symbols: {missing}")
    sys.exit(1)
print("import smoke ok")
PY
}

# ----- Layer 2: unit tests -------------------------------------------------

layer_unit() {
  if ! $PYTHON -c "import pytest" 2>/dev/null; then
    echo "[unit] pytest not installed; install with: pip install pytest"
    return 1
  fi
  $PYTHON -m pytest pipeline/tests/test_*.py -v
}

# ----- Layer 3: integration fetch.py ---------------------------------------

layer_fetch() {
  # --layer chl keeps the run scoped to the layer PR1 actually changed,
  # cutting wall time roughly in half vs `--layer all`.
  echo "[fetch] python pipeline/fetch.py --layer chl"
  $PYTHON pipeline/fetch.py --layer chl || return 1
  echo
  echo "[fetch] assert sidecar + manifest"
  $PYTHON -m pipeline.tests.assert_outputs fetch_chl
}

# ----- Layer 4: integration fetch_visibility.py ----------------------------

layer_visibility() {
  echo "[visibility] python pipeline/fetch_visibility.py"
  $PYTHON pipeline/fetch_visibility.py || return 1
  echo
  echo "[visibility] assert quality codes + viz outputs"
  $PYTHON -m pipeline.tests.assert_outputs visibility
}

# ----- Drive the layers ----------------------------------------------------

OVERALL=0

run_layer "Layer 1: STATIC checks" layer_static || OVERALL=1

if [ $OVERALL -eq 0 ]; then
  run_layer "Layer 2: UNIT tests" layer_unit || OVERALL=1
fi

if [ "$MODE" = "full" ] && [ $OVERALL -eq 0 ]; then
  run_layer "Layer 3: FETCH integration" layer_fetch || OVERALL=1
fi

if [ "$MODE" != "unit" ] && [ $OVERALL -eq 0 ]; then
  # skip-fetch mode runs Layer 4 against whatever's already on disk in
  # public/data/. Useful for iterating on fetch_visibility.py without
  # paying the NOAA fetch cost every loop.
  run_layer "Layer 4: VISIBILITY integration" layer_visibility || OVERALL=1
fi

# ----- Summary -------------------------------------------------------------

echo
echo "============================================================"
echo "  validate.sh summary"
echo "============================================================"
for i in "${!LAYER_NAMES[@]}"; do
  printf "  %-40s %s\n" "${LAYER_NAMES[$i]}" "${LAYER_RESULTS[$i]}"
done
echo

if [ $OVERALL -eq 0 ]; then
  echo "  ALL LAYERS PASSED"
else
  echo "  AT LEAST ONE LAYER FAILED"
fi

exit $OVERALL
