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
#
# Atomic publication (two-platform path). Both platform comparisons are run into
# a sibling staging directory created in the SAME parent directory as the final
# output (so a single rename is the only on-disk mutation at the destination).
# Only after both comparisons succeed is the complete, fully-staged tree renamed
# into place. A publication failure rolls any prior output back. Environment and
# rollback/cleanup failures exit 2 (never 1); cleanup is best-effort and never
# overrides an explicit exit code.
set -euo pipefail

# Exit 2 on an environment/usage error. Centralised so failures classify
# consistently even under `set -e` (raw exits would otherwise propagate 1).
env_fail() {
  printf '%s\n' "$*" >&2
  exit 2
}

if [ "$#" -ne 3 ]; then
  env_fail 'usage: verify-native.sh <resolved-commit> <run-a-dir> <run-b-dir>'
fi

resolved_commit=$1
run_a=$2
run_b=$3
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
output="${VERIFY_NATIVE_OUTPUT:-build/verification/native-bridge}"
output_parent=$(dirname "$output")

# Local verify-all.sh two-platform layout: each run dir carries both windows/
# and macos/ subdirectories. Compare each platform independently into a staging
# area and publish both verified trees as one atomic operation only after BOTH
# comparisons succeed, so a component never emits a success manifest after a
# failure (spec: a component never emits a success manifest after failure).
if [ -d "$run_a/windows" ] && [ -d "$run_a/macos" ] && [ -d "$run_b/windows" ] && [ -d "$run_b/macos" ]; then
  # Both the comparison stage and the publish stage live in the output's parent
  # directory (same filesystem as the final output), so the final publication is
  # a single atomic rename. Creating the stages in the system temp dir (e.g.
  # /tmp) would put them on a different filesystem than $output, turning the
  # final `mv` into a non-atomic copy-and-delete.
  if ! mkdir -p "$output_parent"; then
    env_fail "ERROR: failed to create output parent directory $output_parent"
  fi

  if ! stage=$(mktemp -d "$output_parent/.verify-native-stage.XXXXXX"); then
    env_fail 'ERROR: failed to create comparison staging directory'
  fi

  # Track every sibling we may need to roll back / clean up. `result` records
  # the authoritative exit code: an explicit env_fail sets it to 2 and wins over
  # any cleanup failure; success leaves it at 0.
  publish_stage=""
  new_output=""
  old_output=""
  result=0
  cleanup() {
    # Best-effort cleanup only; a cleanup failure must never override an
    # explicit exit code already recorded in `result`.
    [ -n "$stage" ] && rm -rf "$stage" 2>/dev/null || true
    [ -n "$publish_stage" ] && rm -rf "$publish_stage" 2>/dev/null || true
    [ -n "$new_output" ] && rm -rf "$new_output" 2>/dev/null || true
    # `old_output` is only non-empty mid-rollback; on a successful publish it is
    # removed explicitly before the trap runs. Best-effort removal here covers
    # the rare path where a publish failed and the rollback also failed.
    [ -n "$old_output" ] && rm -rf "$old_output" 2>/dev/null || true
    exit "$result"
  }
  trap cleanup EXIT

  for platform in windows macos; do
    # Propagate compare-runs.mjs' own exit code: 0 = match, 1 = mismatch
    # (authoritative, must NOT be promoted to 2), 2 = environment error. A
    # comparison failure never publishes (the trap cleans the stage), and its
    # exit code is preserved exactly. We capture the status explicitly because
    # `if ! cmd; then $?` yields the negated (0) status, not the original.
    set +e
    node "$script_dir/native/compare-runs.mjs" \
      "$resolved_commit" "$run_a/$platform" "$run_b/$platform" "$stage/$platform"
    cmp_status=$?
    set -e
    if [ "$cmp_status" -ne 0 ]; then
      result=$cmp_status
      exit "$cmp_status"
    fi
  done

  # Both comparisons succeeded. Build a complete publish tree as a sibling of
  # the final output (same parent -> same filesystem -> the final rename is
  # atomic). A staging-move failure is an environment/output error (exit 2).
  if ! publish_stage=$(mktemp -d "$output_parent/.verify-native-publish.XXXXXX"); then
    result=2
    env_fail 'ERROR: failed to create publish staging directory'
  fi
  if ! mv "$stage/windows" "$publish_stage/windows"; then
    result=2
    env_fail 'ERROR: failed to stage windows output'
  fi
  if ! mv "$stage/macos" "$publish_stage/macos"; then
    result=2
    env_fail 'ERROR: failed to stage macos output'
  fi
  # $stage is now empty; leave its cleanup to the trap.

  # Atomically swap the complete staged tree into place. Move any existing
  # output aside to a sibling staging name (same parent -> atomic rename), then
  # rename the staged tree into $output. If the rename fails, restore the old
  # output from its sibling staging name so the destination is never left
  # partial/empty. On success, remove the old output.
  if [ -e "$output" ]; then
    old_output="${output}.old.$$"
    if ! mv "$output" "$old_output"; then
      result=2
      env_fail "ERROR: failed to move aside existing output at $output"
    fi
  fi
  new_output="$output"
  if ! mv "$publish_stage" "$new_output"; then
    result=2
    printf '%s\n' "ERROR: failed to publish native output to $output" >&2
    publish_stage=""  # the failed mv left it in place; trap will not double-remove
    if [ -n "$old_output" ]; then
      if ! mv "$old_output" "$new_output"; then
        printf '%s\n' "ERROR: could not restore previous output at $output" >&2
        old_output=""  # restore failed; do not let the trap remove the leftover
      else
        old_output=""
      fi
    fi
    exit 2
  fi
  # Successful publish: the staged tree is now at $output; clear the staging
  # names so the trap does not remove them, and drop the old output.
  publish_stage=""
  new_output=""
  if [ -n "$old_output" ]; then
    rm -rf "$old_output" 2>/dev/null || true
    old_output=""
  fi
  exit 0
fi

# CI per-platform layout: each run dir contains exactly one transport tar for a
# single platform; emit the verified tree flat under $output, which the calling
# workflow uploads per-platform.
node "$script_dir/native/compare-runs.mjs" "$resolved_commit" "$run_a" "$run_b" "$output"