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
# and macos/ subdirectories. Compare each platform independently into a staging
# area and publish both verified trees as one atomic operation only after BOTH
# comparisons succeed, so a component never emits a success manifest after a
# failure (spec: a component never emits a success manifest after failure).
if [ -d "$run_a/windows" ] && [ -d "$run_a/macos" ] && [ -d "$run_b/windows" ] && [ -d "$run_b/macos" ]; then
  stage=$(mktemp -d)
  cleanup_stage() { rm -rf "$stage" "$publish_stage" "$old_output"; }
  publish_stage=""
  old_output=""
  trap cleanup_stage EXIT
  for platform in windows macos; do
    node "$script_dir/native/compare-runs.mjs" "$resolved_commit" "$run_a/$platform" "$run_b/$platform" "$stage/$platform"
  done
  # Both comparisons succeeded. Publish both platform trees atomically: stage a
  # complete output tree, then a single rename into the final location. A
  # publication failure must roll back any partial output and exit 2
  # (environment/output error), never 1, and never leave a partial publish.
  publish_stage=$(mktemp -d)
  if ! mv "$stage/windows" "$publish_stage/windows"; then
    echo "ERROR: failed to stage windows output" >&2
    exit 2
  fi
  if ! mv "$stage/macos" "$publish_stage/macos"; then
    echo "ERROR: failed to stage macos output" >&2
    exit 2
  fi
  mkdir -p "$(dirname "$output")"
  # Swap: move any existing output aside, rename the complete staged tree into
  # place, then remove the old output. If the rename fails, restore the old
  # output so we never publish a partial tree.
  if [ -e "$output" ]; then
    old_output="${output}.old.$$"
    if ! mv "$output" "$old_output"; then
      echo "ERROR: failed to move aside existing output at $output" >&2
      exit 2
    fi
  fi
  if ! mv "$publish_stage" "$output"; then
    echo "ERROR: failed to publish native output to $output" >&2
    if [ -n "$old_output" ] && ! mv "$old_output" "$output"; then
      echo "ERROR: could not restore previous output at $output" >&2
    fi
    exit 2
  fi
  rm -rf "$old_output"
  exit 0
fi

# CI per-platform layout: each run dir contains exactly one transport tar for a
# single platform; emit the verified tree flat under $output, which the calling
# workflow uploads per-platform.
node "$script_dir/native/compare-runs.mjs" "$resolved_commit" "$run_a" "$run_b" "$output"