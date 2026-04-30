#!/usr/bin/env bash
# Smoke test for ShoudiDive live data endpoints.
# Hardcoded layer/window pairs the Flutter app actually consumes.
# Asserts HTTP 200, non-zero content, expected content-type.

set -u

BASE_URL="https://shouldidive.com"
PASS=0
FAIL=0
FAILURES=()

# (label, path, min_bytes, expected_content_type_substring)
TESTS=(
  "manifest|/data/manifest.json|500|application/json"
  "land geojson|/data/land.geojson|10000|application/"
  "mpa geojson|/data/mpa-boundaries.geojson|10000|application/"
  "sst 1d|/data/sst_1d.png|2000|image/png"
  "sst 2d|/data/sst_2d.png|2000|image/png"
  "sst 3d|/data/sst_3d.png|2000|image/png"
  "chl 1d|/data/chl_1d.png|500|image/png"
  "chl 2d|/data/chl_2d.png|500|image/png"
  "chl 3d|/data/chl_3d.png|500|image/png"
  "viz p50|/data/viz_p50_ft.png|500|image/png"
  "viz p10|/data/viz_p10_ft.png|500|image/png"
  "viz p90|/data/viz_p90_ft.png|500|image/png"
  "viz quality|/data/viz_quality.png|100|image/png"
  "wind speed now|/data/wind_speed_now.png|500|image/png"
  "wind speed +6h|/data/wind_speed_p6h.png|500|image/png"
  "wind speed +24h|/data/wind_speed_p24h.png|500|image/png"
  "wind speed +72h|/data/wind_speed_p72h.png|500|image/png"
  "wind uv now|/data/wind_uv_now.png|500|image/png"
  "wind uv +6h|/data/wind_uv_p6h.png|500|image/png"
  "wind uv +24h|/data/wind_uv_p24h.png|500|image/png"
  "wind uv +72h|/data/wind_uv_p72h.png|500|image/png"
  "wave now|/data/wave_now.png|500|image/png"
  "wave max 3d|/data/wave_max_3d.png|500|image/png"
  "precip 7d|/data/precip_7d.png|500|image/png"
)

probe() {
  local label="$1"
  local path="$2"
  local min_bytes="$3"
  local expect_ct="$4"
  local url="${BASE_URL}${path}"

  local headers
  headers=$(curl -sI -L --max-time 15 "$url" 2>&1) || {
    FAIL=$((FAIL+1)); FAILURES+=("$label: curl error"); printf "FAIL %-22s curl error\n" "$label"; return
  }

  local status
  status=$(printf '%s\n' "$headers" | head -n 1 | awk '{print $2}')
  local ct
  ct=$(printf '%s\n' "$headers" | grep -i '^content-type:' | head -n 1 | tr -d '\r' | awk '{print $2}')
  local cl
  cl=$(printf '%s\n' "$headers" | grep -i '^content-length:' | head -n 1 | tr -d '\r' | awk '{print $2}')

  if [[ "$status" != "200" ]]; then
    FAIL=$((FAIL+1)); FAILURES+=("$label: HTTP $status")
    printf "FAIL %-22s HTTP %s\n" "$label" "$status"
    return
  fi
  if [[ -n "$cl" && "$cl" -lt "$min_bytes" ]]; then
    FAIL=$((FAIL+1)); FAILURES+=("$label: only ${cl}B (< ${min_bytes})")
    printf "FAIL %-22s only %sB (expected ≥ %s)\n" "$label" "$cl" "$min_bytes"
    return
  fi
  if [[ -n "$expect_ct" && "$ct" != *"$expect_ct"* ]]; then
    FAIL=$((FAIL+1)); FAILURES+=("$label: content-type=$ct")
    printf "FAIL %-22s ct=%s\n" "$label" "$ct"
    return
  fi
  PASS=$((PASS+1))
  printf "PASS %-22s %sB %s\n" "$label" "${cl:-?}" "${ct:-?}"
}

printf "%-5s %-22s %s\n" "stat" "asset" "details"
printf "%-5s %-22s %s\n" "----" "----------------------" "-----------------------"
for spec in "${TESTS[@]}"; do
  IFS='|' read -r label path min_bytes expect_ct <<< "$spec"
  probe "$label" "$path" "$min_bytes" "$expect_ct"
done

echo
echo "----"
echo "Result: $PASS passed, $FAIL failed (of $((PASS+FAIL)) checks)"
if (( FAIL > 0 )); then
  echo "Failures:"
  for f in "${FAILURES[@]}"; do echo "  - $f"; done
  exit 1
fi
exit 0
