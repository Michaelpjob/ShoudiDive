#!/usr/bin/env bash
#
# Mobile-app validation suite. Runs every check that's possible in
# bash on Windows / Linux / macOS — i.e. every check that DOES NOT
# require booting a simulator or a real device. The goal is to catch
# every class of failure we hit during the initial bootstrap loop:
#
#   - SDK / package version drift
#   - Project config issues (missing entry point, peer-deps, scheme)
#   - Babel transform errors / missing imports / bundle compile fails
#   - Data-layer regressions (manifest fetch, URL resolution, subscribers)
#
# Run it after every non-trivial mobile/ change BEFORE pushing. If
# every layer passes, the bundle is in a deployable state and the
# remaining unknown is native-render behaviour (which you only see on
# a device). When something fails, the fix is local and we don't waste
# anyone's time as the QA loop.
#
# Layers (each runs only if the previous passes):
#
#   1. expo install --check  — package alignment with installed SDK
#   2. expo-doctor           — full project-config health
#   3. expo export ios       — iOS Hermes bundle compile
#   4. expo export web       — web fallback bundle compile (so I can
#                              render the app myself in a browser)
#   5. jest                  — JS unit tests for the data layer
#
# What this canNOT catch (intentional gap, requires a Mac+simulator
# or a real device):
#
#   - Skia overlay actually painting on top of a MapView
#   - react-native-maps tile fetch / region-change behaviour
#   - Touch / gesture interactions
#   - Animation performance under real conditions
#
# Those go into a Detox or Maestro suite later.

set -euo pipefail

# Run from the mobile/ root regardless of where the user invoked us.
cd "$(dirname "$0")/.."

echo ""
echo "▶ Layer 1/5 — expo install --check (SDK alignment)"
echo "──────────────────────────────────────────────────"
npx expo install --check

echo ""
echo "▶ Layer 2/5 — expo-doctor (project-wide health)"
echo "──────────────────────────────────────────────────"
# expo-doctor exits non-zero on any warning; we tolerate warnings
# but fail on critical issues. The "|| true" lets warnings through
# while critical-issue grep below catches the real failures.
DOCTOR_OUT=$(npx --yes expo-doctor 2>&1 || true)
echo "$DOCTOR_OUT"
if echo "$DOCTOR_OUT" | grep -qiE "✖|critical|fatal|error"; then
  # Soft-fail on cosmetic issues, hard-fail on real ones. Real ones
  # contain the words above; warnings use a check-mark / note style.
  echo ""
  echo "✖ expo-doctor reports critical issue(s) above. Fix before continuing."
  exit 1
fi

echo ""
echo "▶ Layer 3/5 — expo export (iOS Hermes bundle smoke test)"
echo "──────────────────────────────────────────────────"
# Wipe any prior bundle so we never accept a cached "success" from
# stale bytecode. The --clear flag also resets Metro's transform
# cache, which is what catches Babel-config regressions.
rm -rf dist .expo/cache 2>/dev/null || true
npx expo export --platform ios --output-dir dist --clear

echo ""
echo "▶ Layer 4/5 — expo export web (web fallback compiles)"
echo "──────────────────────────────────────────────────"
# The web target uses platform-specific shims (e.g. MapScreen.web.jsx
# instead of MapScreen.jsx) so I can render the app in a browser and
# validate layout / chip taps / state without booting Expo Go on a
# device. If the web bundle stops compiling, that loop breaks and
# I'm forced back to "ask the user to scan a QR code" — exactly the
# pattern we're trying to eliminate. So we hard-fail when web breaks.
rm -rf dist 2>/dev/null || true
npx expo export --platform web --output-dir dist

echo ""
echo "▶ Layer 5/5 — jest (data-layer unit tests)"
echo "──────────────────────────────────────────────────"
npx jest --silent --colors

# Bundle artefacts are only useful for inspection; clean so they
# don't get committed by accident or confuse subsequent runs.
rm -rf dist 2>/dev/null || true

echo ""
echo "──────────────────────────────────────────────────"
echo "✓ All 5 validation layers passed."
echo "  Mobile bundle is in a deployable state."
echo "──────────────────────────────────────────────────"
