#!/usr/bin/env bash
# Rotate EARTHDATA_TOKEN (NASA Earthdata Login user token, 60-day lifetime).
#
# The token powers the three NASA OB.DAAC chl sources in pipeline/chl_blend.py.
# When it expires, OB.DAAC answers every download with the Earthdata login
# page (HTML, HTTP 200), the blend quietly falls back to gap-fill, and the
# app's observed-only chl view goes blank (see docs/OPS-LOG.md, 2026-10-30).
#
# What this does, in order:
#   1. reads the new token (prompt, --from-file, or $EARTHDATA_TOKEN) and
#      strips stray whitespace / CR / LF (a trailing newline makes NASA 401);
#   2. VALIDATES it against NASA: finds the newest AQUA NRT chl file via the
#      file_search API, downloads it with the bearer token, and checks the
#      first bytes are a netCDF (HDF5) signature, not a login page;
#   3. sets the GitHub secret with `gh secret set EARTHDATA_TOKEN --body`
#      (no stdin, so no newline gotcha);
#   4. dispatches refresh-ca-data.yml on main so chl comes back now instead
#      of at the next cron;
#   5. prints the date the new token dies (today + 60 days).
#
# Usage:
#   bash scripts/rotate-earthdata-token.sh                # prompts for the token
#   bash scripts/rotate-earthdata-token.sh --from-file p  # token in a file
#   bash scripts/rotate-earthdata-token.sh --check-only   # validate, change nothing
#   bash scripts/rotate-earthdata-token.sh --no-dispatch  # set the secret only
#
# Get a token: https://urs.earthdata.nasa.gov/profile  ->  "Generate Token".
# (Earthdata allows at most two live user tokens; revoke the old one there.)
# The token is never printed by this script.

set -euo pipefail

REPO="${REPO:-Michaelpjob/ShoudiDive}"
SEARCH_URL="https://oceandata.sci.gsfc.nasa.gov/api/file_search"
GETFILE_URL="https://oceandata.sci.gsfc.nasa.gov/ob/getfile"
TOKEN_LIFETIME_DAYS=60

check_only=0
dispatch=1
from_file=""
while [ $# -gt 0 ]; do
  case "$1" in
    --check-only) check_only=1 ;;
    --no-dispatch) dispatch=0 ;;
    --from-file) shift; from_file="${1:-}" ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

say() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

for tool in curl gh od date; do
  command -v "$tool" >/dev/null 2>&1 || die "$tool is required on PATH"
done

# ---- 1. read the token ----------------------------------------------------
token=""
if [ -n "$from_file" ]; then
  [ -r "$from_file" ] || die "cannot read $from_file"
  token="$(cat "$from_file")"
elif [ -n "${EARTHDATA_TOKEN:-}" ]; then
  token="$EARTHDATA_TOKEN"
  say "Using the token from \$EARTHDATA_TOKEN."
else
  printf 'Paste the new Earthdata token (input is hidden), then Enter: '
  IFS= read -rs token
  printf '\n'
fi
token="$(printf '%s' "$token" | tr -d '\r\n[:space:]')"
[ -n "$token" ] || die "no token given"
if [ "${#token}" -lt 60 ]; then
  die "that is only ${#token} characters; an Earthdata user token is a long JWT (starts with eyJ...)"
fi
case "$token" in
  eyJ*) ;;
  *) say "WARNING: token does not start with 'eyJ' (Earthdata user tokens are JWTs). Validating anyway." ;;
esac

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

# ---- 2. validate against NASA --------------------------------------------
say "Looking up the newest AQUA MODIS NRT chl file..."
filename=""
for back in 2 3 4 5 6 7 8 9 10 11; do
  d="$(date -u -d "-${back} days" +%F 2>/dev/null || date -u -v-"${back}"d +%F)"
  body="$(curl -sS -m 40 -H "Authorization: Bearer $token" --get "$SEARCH_URL" \
    --data-urlencode 'search=AQUA_MODIS*L3m.DAY.CHL.chlor_a.4km.NRT*' \
    --data-urlencode "sdate=$d" --data-urlencode "edate=$d" \
    --data-urlencode 'dtype=L3m' --data-urlencode 'results_as_file=1' \
    --data-urlencode 'sensor_id=7' --data-urlencode 'dtid=1055' || true)"
  filename="$(printf '%s\n' "$body" | tr -d '\r' \
    | grep -E '^AQUA_MODIS\.[0-9]{8}\.L3m\.DAY\.CHL\.chlor_a\.4km\.NRT\.nc$' | head -1 || true)"
  if [ -n "$filename" ]; then
    say "  found $filename ($d)"
    break
  fi
done
[ -n "$filename" ] || die "NASA file_search listed no AQUA NRT chl file for the last 11 days; cannot validate. Try again later or check https://oceandata.sci.gsfc.nasa.gov/api/file_search by hand."

say "Downloading it with the new token (this is the real auth test)..."
out="$tmpdir/probe.nc"
meta="$(curl -sS -L -m 180 -H "Authorization: Bearer $token" -o "$out" \
  -w '%{http_code} %{url_effective} %{content_type}' "$GETFILE_URL/$filename" || true)"
code="${meta%% *}"
rest="${meta#* }"
final_url="${rest%% *}"
ctype="${rest#* }"
size=0
[ -f "$out" ] && size="$(wc -c < "$out" | tr -d ' ')"
magic="$(od -An -tx1 -N4 "$out" 2>/dev/null | tr -d ' \n')"

case "$final_url" in
  *urs.earthdata.nasa.gov*)
    die "NASA redirected to the Earthdata login page (HTTP $code). The token was REJECTED. Nothing changed. Generate a fresh token at https://urs.earthdata.nasa.gov/profile and run this again." ;;
esac
if [ "$code" != "200" ]; then
  die "unexpected HTTP $code from $GETFILE_URL/$filename (content-type ${ctype:-?}, $size bytes). Nothing changed."
fi
case "$magic" in
  89484446|434446*) ;;  # \x89HDF (netCDF-4) or CDF (classic netCDF)
  *) die "download is not a netCDF (first bytes: ${magic:-none}, content-type ${ctype:-?}, $size bytes). Looks like an auth failure. Nothing changed." ;;
esac
say "  OK: $size bytes of netCDF came back. The token works."

if [ "$check_only" -eq 1 ]; then
  say "--check-only: not touching the secret."
  exit 0
fi

# ---- 3. set the secret ----------------------------------------------------
say "Setting repo secret EARTHDATA_TOKEN on $REPO..."
gh secret set EARTHDATA_TOKEN --repo "$REPO" --body "$token"
gh secret list --repo "$REPO" | grep -E '^EARTHDATA_TOKEN' || die "secret not visible after set"

# ---- 4. kick a refresh ----------------------------------------------------
if [ "$dispatch" -eq 1 ]; then
  say "Dispatching refresh-ca-data.yml on main (Baja picks the token up on its next cron)..."
  gh workflow run refresh-ca-data.yml --repo "$REPO" -f branch=main
  sleep 5
  run_id="$(gh run list --repo "$REPO" --workflow=refresh-ca-data.yml --limit 1 --json databaseId --jq '.[0].databaseId' || true)"
  if [ -n "$run_id" ]; then
    say "  run $run_id started. Watch it with:"
    say "    gh run watch $run_id --repo $REPO"
    say "  Success looks like '[aqua_modis_nrt] N frames' in the 'Fetch latest ocean data' step and"
    say "  'sources used [1, 2, 3, ...]' on the blended lines. chl paints again after the deploy."
  fi
fi

# ---- 5. the clock ---------------------------------------------------------
due="$(date -u -d "+${TOKEN_LIFETIME_DAYS} days" +%F 2>/dev/null || date -u -v+"${TOKEN_LIFETIME_DAYS}"d +%F)"
say ""
say "Done. This token expires on $due (Earthdata tokens live $TOKEN_LIFETIME_DAYS days)."
say "Add that date to docs/OPS-LOG.md 'Recurring deadlines' and your calendar."
