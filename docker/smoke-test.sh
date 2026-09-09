#!/usr/bin/env bash
#
# Functional check for a freshly built LCModel binary.
#
# Runs the upstream test dataset and asserts on what LCModel actually produced. The exit
# status is deliberately ignored: LCModel is Fortran and its fatal error paths end in a
# bare STOP, which exits 0. Checking $? would pass a binary that failed on every spectrum.
#
# Usage: docker/smoke-test.sh /path/to/lcmodel [upstream-ref]

set -euo pipefail

BINARY="${1:?usage: smoke-test.sh /path/to/lcmodel [upstream-ref]}"
REF="${2:-c9d95ff9b1fd2e7e8b256088ba1f5cf4df5fb207}"
RAW="https://raw.githubusercontent.com/schorschinho/LCModel/${REF}/test_lcm"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

echo "==> fetching upstream test data (ref ${REF:0:12})"
for f in 3t.basis control.file data.raw; do
    curl -fsSL --retry 3 -o "$work/$f" "$RAW/$f"
done

# Upstream's control file writes only PostScript. Ask for a .coord as well: it is the
# machine-readable output the wrapper actually parses, and the one worth asserting on.
# (awk rather than sed: BSD sed on macOS does not understand "\n" in a replacement.)
awk '/^\$END/ { print "lcoord=9"; print "filcoo=\047out.coord\047" } { print }' \
    "$work/control.file" > "$work/control.tmp" && mv "$work/control.tmp" "$work/control.file"

echo "==> running LCModel"
output="$(cd "$work" && "$(cd "$(dirname "$BINARY")" && pwd)/$(basename "$BINARY")" \
          < control.file 2>&1)" || true
echo "$output"

echo "==> checking results"
fail() { echo "SMOKE TEST FAILED: $1" >&2; exit 1; }

grep -q 'FATAL ERROR' <<<"$output" && fail "LCModel reported a fatal error"
[ -s "$work/out.ps" ]    || fail "no PostScript output produced"
[ -s "$work/out.coord" ] || fail "no .coord output produced"
grep -qE 'NAA|Cr\+PCr' "$work/out.coord" \
    || fail ".coord contains no recognisable metabolite names"

# Concentrations should be finite numbers, not NaN/Inf from a miscompiled build.
grep -qiE 'nan|infinity' "$work/out.coord" && fail ".coord contains NaN/Inf concentrations"

echo "==> concentrations (first few):"
sed -n '/Conc\./,+6p' "$work/out.coord" || true

echo "SMOKE TEST PASSED"
