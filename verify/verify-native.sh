#!/usr/bin/env bash
# Compare two native-bridge runs of one platform (CI per-platform layout) or
# two runs each of both platforms (local verify-all.sh layout), and emit the
# verified tree(s) for aggregation.
#
# Synopsis:
#   verify-native.sh <resolved-commit> <run-a-dir> <run-b-dir>
#
# CI per-platform layout (each run dir contains exactly one transport tar for
# ONE platform, produced by build-native-<platform>.yml):
#   <run-a-dir>/<platform>-<platform>-run.tar[.sha256]
#   <run-b-dir>/<platform>-<platform>-run.tar[.sha256]
#   -> verified tree emitted flat under $VERIFY_NATIVE_OUTPUT (default
#      build/verification/native-bridge/), uploaded per-platform.
#
# Local verify-all.sh two-platform layout (each run dir contains both platforms
# as named subdirectories, matching the binding synopsis in
# docs/superpowers/specs/2026-08-28-otp-01-1d-verification-supply-chain-design.md):
#   <run-a-dir>/windows/native-windows-run.tar[.sha256]
#   <run-a-dir>/macos/native-macos-run.tar[.sha256]
#   <run-b-dir>/windows/native-windows-run.tar[.sha256]
#   <run-b-dir>/macos/native-macos-run.tar[.sha256]
#   -> verified trees emitted under $VERIFY_NATIVE_OUTPUT/windows and
#      $VERIFY_NATIVE_OUTPUT/macos, the layout aggregate-checksums.mjs consumes.
set -euo pipefail

if [ "$#" -ne 3 ]; then
  printf '%s\n' 'usage: verify-native.sh <resolved-commit> <run-a-dir> <run-b-dir>' >&2
  exit 2
fi

resolved_commit=$1
run_a=$2
run_b=$3
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
output="${VERIFY_NATIVE_OUTPUT:-build/verification/native-bridge}"

# Local verify-all.sh two-platform layout: each run dir carries both windows/
# and macos/ subdirectories. Compare each platform independently and emit to
# <output>/windows and <output>/macos so aggregate-checksums.mjs finds both
# required namespaces.
if [ -d "$run_a/windows" ] && [ -d "$run_a/macos" ] && [ -d "$run_b/windows" ] && [ -d "$run_b/macos" ]; then
  for platform in windows macos; do
    node "$script_dir/native/compare-runs.mjs" "$resolved_commit" "$run_a/$platform" "$run_b/$platform" "$output/$platform"
  done
  exit 0
fi

# CI per-platform layout: each run dir contains exactly one transport tar for a
# single platform; emit the verified tree flat under $output, which the calling
# workflow uploads per-platform.
node "$script_dir/native/compare-runs.mjs" "$resolved_commit" "$run_a" "$run_b" "$output"