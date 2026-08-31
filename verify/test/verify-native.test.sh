#!/usr/bin/env bash
# Exercises verify/verify-native.sh against both supported native run layouts:
#   1. local verify-all.sh two-platform layout (windows/ + macos/ subdirs),
#      emitting to <output>/windows and <output>/macos; and
#   2. CI per-platform layout (single transport tar per run dir), emitting flat.
# Uses verify/native/create-run.mjs to fabricate deterministic transport tars
# from throwaway product/view trees, so no Docker or build is required.
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root="$(cd "$script_dir/../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

commit='0123456789abcdef0123456789abcdef01234567'
identity_windows='{"ImageOS":"Windows","ImageVersion":"2025","RUNNER_ARCH":"X64"}'
identity_macos='{"ImageOS":"macOS","ImageVersion":"26.0","RUNNER_ARCH":"ARM64"}'

# Fabricate a one-platform transport tar in <dst>/<platform>/<name>.tar using
# create-run.mjs, which takes (output, commit, ImageOS, ImageVersion, RUNNER_ARCH,
# product-dir, view-dir).
make_run() {
  local platform=$1 os=$2 image_version=$3 arch=$4 dst=$5
  local product="$tmp/$platform-product"
  local view="$tmp/$platform-view"
  mkdir -p "$product" "$view" "$(dirname "$dst")"
  printf 'artifact-%s' "$platform" >"$product/artifact"
  printf 'host-%s' "$platform" >"$view/host"
  node "$script_dir/../native/create-run.mjs" "$dst" "$commit" "$os" "$image_version" "$arch" "$product" "$view"
}

# --- Layout 1: local two-platform (windows/ and macos/ subdirs in each run) ---
run_a="$tmp/run-a"
run_b="$tmp/run-b"
output="$tmp/verified"

make_run windows Windows 2025 X64 "$run_a/windows/native-windows-run.tar"
make_run windows Windows 2025 X64 "$run_b/windows/native-windows-run.tar"
make_run macos macOS 26.0 ARM64 "$run_a/macos/native-macos-run.tar"
make_run macos macOS 26.0 ARM64 "$run_b/macos/native-macos-run.tar"

VERIFY_NATIVE_OUTPUT="$output" bash "$repo_root/verify/verify-native.sh" "$commit" "$run_a" "$run_b"

# Both platform namespaces must be emitted for the aggregate.
[ -f "$output/windows/product/artifact" ]
[ -f "$output/windows/METADATA.native-bridge.jsonl" ]
[ -f "$output/windows/SHA256SUMS.native-bridge" ]
[ -f "$output/macos/product/artifact" ]
[ -f "$output/macos/METADATA.native-bridge.jsonl" ]
[ -f "$output/macos/SHA256SUMS.native-bridge" ]

# --- Layout 2: CI per-platform (single tar per run dir, flat output) ---
run_a2="$tmp/ci-run-a"
run_b2="$tmp/ci-run-b"
output2="$tmp/ci-verified"
mkdir -p "$run_a2" "$run_b2"
make_run windows Windows 2025 X64 "$run_a2/native-windows-run.tar"
make_run windows Windows 2025 X64 "$run_b2/native-windows-run.tar"

VERIFY_NATIVE_OUTPUT="$output2" bash "$repo_root/verify/verify-native.sh" "$commit" "$run_a2" "$run_b2"

# Flat output, no platform subdirectory (CI uploads this per-platform).
[ -f "$output2/product/artifact" ]
[ -f "$output2/METADATA.native-bridge.jsonl" ]
[ -f "$output2/SHA256SUMS.native-bridge" ]
[ ! -d "$output2/windows" ]
[ ! -d "$output2/macos" ]

# --- Identity mismatch on the two-platform layout exits 2 and emits nothing ---
run_c="$tmp/run-c-mismatch"
mkdir -p "$run_c/windows" "$run_c/macos"
make_run windows Windows 2025 X64 "$run_c/windows/native-windows-run.tar"
# Drift the macOS identity in run-c so macOS comparison fails with exit 2.
make_run macos macOS 99.0 ARM64 "$run_c/macos/native-macos-run.tar"
output3="$tmp/verified-mismatch"
rm -rf "$output3"
if VERIFY_NATIVE_OUTPUT="$output3" bash "$repo_root/verify/verify-native.sh" "$commit" "$run_a" "$run_c"; then
  echo 'ERROR: identity mismatch must exit 2' >&2
  exit 1
else
  [ $? -eq 2 ]
fi
# The windows comparison would succeed before macOS fails, but a component
# never emits a success manifest after failure: NOTHING may be published.
[ ! -e "$output3/windows" ]
[ ! -e "$output3/macos" ]
[ ! -d "$output3" ]

# --- True content mismatch (same identity, different bytes) exits 1 ---
# Two runs with identical runner identity but differing product bytes is a real
# artifact mismatch (exit 1), not an environment error (exit 2). The mismatch
# exit code is authoritative and must not be promoted to 2 by the publication
# guards. Nothing is published.
run_d="$tmp/run-d-mismatch-bytes"
run_e="$tmp/run-e-mismatch-bytes"
mkdir -p "$run_d/windows" "$run_d/macos" "$run_e/windows" "$run_e/macos"
# Windows: same identity, DIFFERENT product content between run-d and run-e.
make_run windows Windows 2025 X64 "$run_d/windows/native-windows-run.tar"
product_e="$tmp/windows-product-e"
view_e="$tmp/windows-view-e"
mkdir -p "$product_e" "$view_e"
printf 'artifact-windows-DIFFERENT' >"$product_e/artifact"
printf 'host-windows' >"$view_e/host"
node "$script_dir/../native/create-run.mjs" "$run_e/windows/native-windows-run.tar" "$commit" Windows 2025 X64 "$product_e" "$view_e"
# macOS: identical between run-d and run-e (so the mismatch is windows-only).
make_run macos macOS 26.0 ARM64 "$run_d/macos/native-macos-run.tar"
make_run macos macOS 26.0 ARM64 "$run_e/macos/native-macos-run.tar"
output4="$tmp/verified-bytes-mismatch"
rm -rf "$output4"
if bash "$repo_root/verify/verify-native.sh" "$commit" "$run_d" "$run_e" 2>/dev/null; then
  echo 'ERROR: content mismatch must exit 1' >&2
  exit 1
else
  status=$?
  [ "$status" -eq 1 ] || { echo "ERROR: content mismatch must exit 1, got $status" >&2; exit 1; }
fi
# A mismatch never publishes anything.
[ ! -e "$output4/windows" ]
[ ! -e "$output4/macos" ]
[ ! -d "$output4" ]

# --- Wrong argument count exits 2 ---
if bash "$repo_root/verify/verify-native.sh" "$commit" "$run_a" 2>/dev/null; then
  echo 'ERROR: missing argument must exit 2' >&2
  exit 1
else
  [ $? -eq 2 ]
fi

# --- Publication failure is atomic and exits 2 with nothing published ---
# Both platform comparisons succeed, but staging (and thus publication) cannot
# proceed because the output's parent directory is read-only. The component must
# exit 2 (environment/output error), never 1, and must not leave any partial
# output (no windows-published-while-macos-failed, no leftover staging dirs).
readonly_parent="$tmp/readonly-parent"
mkdir -p "$readonly_parent"
chmod 0500 "$readonly_parent"
unwritable_output="$readonly_parent/native-bridge"
if VERIFY_NATIVE_OUTPUT="$unwritable_output" bash "$repo_root/verify/verify-native.sh" "$commit" "$run_a" "$run_b" 2>/dev/null; then
  echo 'ERROR: publication failure must exit 2' >&2
  chmod 0700 "$readonly_parent"
  exit 1
else
  status=$?
  chmod 0700 "$readonly_parent"
  [ "$status" -eq 2 ]
fi
# Nothing was published at the unwritable output location and no staging
# directories were left behind in its (now-writable) parent.
[ ! -e "$unwritable_output" ]
staging_leftover=$(find "$readonly_parent" -maxdepth 1 -name '.verify-native-*' 2>/dev/null | head -1)
[ -z "$staging_leftover" ]

# --- Early staging failure (read-only parent) survives without exercising rollback ---
# The output's parent is read-only, so the initial comparison staging `mktemp`
# (verify-native.sh:68) fails BEFORE any pre-existing output is moved aside. The
# component exits 2 and must not leave a partial output. This proves survival on
# an early-staging failure; the genuine rollback branch is exercised below.
readonly_parent="$tmp/readonly-parent"
mkdir -p "$readonly_parent/native-bridge/windows/product" \
         "$readonly_parent/native-bridge/macos/product"
printf 'prior-windows' >"$readonly_parent/native-bridge/windows/product/artifact"
printf 'prior-macos' >"$readonly_parent/native-bridge/macos/product/artifact"
chmod 0500 "$readonly_parent"
readonly_output="$readonly_parent/native-bridge"
if VERIFY_NATIVE_OUTPUT="$readonly_output" bash "$repo_root/verify/verify-native.sh" "$commit" "$run_a" "$run_b" 2>/dev/null; then
  echo 'ERROR: early staging failure must exit 2' >&2
  chmod 0700 "$readonly_parent"
  exit 1
else
  status=$?
  chmod 0700 "$readonly_parent"
  [ "$status" -eq 2 ]
fi
# The pre-existing output was untouched (no rollback ran; nothing was published).
[ -f "$readonly_output/windows/product/artifact" ]
[ -f "$readonly_output/macos/product/artifact" ]
[ "$(cat "$readonly_output/windows/product/artifact")" = 'prior-windows' ]
[ "$(cat "$readonly_output/macos/product/artifact")" = 'prior-macos' ]
# No sibling staging/backup directories were left behind in the parent.
readonly_leftover=$(find "$readonly_parent" -maxdepth 1 -name '.verify-native-*' -o -maxdepth 1 -name '*.old.*' 2>/dev/null | head -1)
[ -z "$readonly_leftover" ]
rm -rf "$readonly_parent"

# --- Rollback genuinely restores a pre-existing output after a failed publish ---
# Both platform comparisons succeed, the staged tree is built, and the pre-existing
# output is moved aside (verify-native.sh:132-138). The FINAL publish rename
# (verify-native.sh:140, `mv publish_stage $output`) is then forced to fail, so the
# rollback branch (verify-native.sh:144-151) runs and must restore the prior output
# from its sibling staging name. The script exits 2, and the restored prior output
# MUST survive script exit (the EXIT trap at verify-native.sh:84 must not delete it).
#
# Mechanism: a shim `mv` on PATH that delegates to the real `mv` for every call
# EXCEPT the publish rename (arg1 basename begins with `.verify-native-publish.` and
# arg2 is the target output), which it fails. The restore rename (arg1 basename
# `*.old.$$`) and the comparison/staging renames are allowed through unchanged, so
# the failure is isolated to the publish step AFTER the pre-existing output has been
# moved aside — exactly the rollback branch.
rollback_parent="$tmp/rollback-parent"
mkdir -p "$rollback_parent/native-bridge/windows/product" \
         "$rollback_parent/native-bridge/macos/product"
printf 'prior-windows' >"$rollback_parent/native-bridge/windows/product/artifact"
printf 'prior-macos' >"$rollback_parent/native-bridge/macos/product/artifact"
rollback_output="$rollback_parent/native-bridge"
rollback_fakebin="$tmp/rollback-fakebin"
mkdir -p "$rollback_fakebin"
cat >"$rollback_fakebin/mv" <<'ROLLBACK_MV'
#!/usr/bin/env bash
# Shim mv: fail the publish rename only (arg1 is the publish stage, arg2 is the
# final output). All other renames (comparison staging, move-aside, restore) are
# delegated to the real mv so the failure is isolated to the publish step.
real=/usr/bin/mv
bname=$(basename -- "$1")
case "$bname" in
  .verify-native-publish.*)
    if [ "$2" = "$ROLLBACK_BLOCK_OUTPUT" ]; then exit 1; fi
    ;;
esac
exec "$real" "$@"
ROLLBACK_MV
chmod +x "$rollback_fakebin/mv"
if ROLLBACK_BLOCK_OUTPUT="$rollback_output" \
     VERIFY_NATIVE_OUTPUT="$rollback_output" \
     PATH="$rollback_fakebin:$PATH" \
     bash "$repo_root/verify/verify-native.sh" "$commit" "$run_a" "$run_b" 2>/dev/null; then
  echo 'ERROR: rollback publication failure must exit 2' >&2
  rm -rf "$rollback_parent" "$rollback_fakebin"
  exit 1
else
  status=$?
  [ "$status" -eq 2 ] || { echo "ERROR: rollback must exit 2, got $status" >&2; rm -rf "$rollback_parent" "$rollback_fakebin"; exit 1; }
fi
# The pre-existing output was restored and MUST STILL EXIST after the script has
# exited. This is the regression guard: the EXIT trap must not `rm -rf` the restored
# prior output (the defect deleted it because `new_output` still pointed at $output
# after rollback succeeded).
[ -f "$rollback_output/windows/product/artifact" ] || { echo 'ERROR: restored prior windows output missing after script exit' >&2; rm -rf "$rollback_parent" "$rollback_fakebin"; exit 1; }
[ -f "$rollback_output/macos/product/artifact" ] || { echo 'ERROR: restored prior macos output missing after script exit' >&2; rm -rf "$rollback_parent" "$rollback_fakebin"; exit 1; }
[ "$(cat "$rollback_output/windows/product/artifact")" = 'prior-windows' ]
[ "$(cat "$rollback_output/macos/product/artifact")" = 'prior-macos' ]
# No sibling staging/backup directories were left behind in the parent.
rollback_leftover=$(find "$rollback_parent" -maxdepth 1 -name '.verify-native-*' -o -maxdepth 1 -name '*.old.*' 2>/dev/null | head -1)
[ -z "$rollback_leftover" ]
rm -rf "$rollback_parent" "$rollback_fakebin"

# --- Staging is created on the output's filesystem (same parent as output) ---
# The comparison and publish staging directories must be siblings of the final
# output (same parent -> same filesystem), so the final publication is a single
# atomic rename. With the old mktemp-in-/tmp approach the stages would live in
# the system temp dir (potentially a different filesystem than the output), and
# the final `mv` would degrade to a non-atomic copy-and-delete.
#
# This assertion makes TMPDIR unusable (a nonexistent path) and then runs a
# successful publish. `mktemp -d <template>` with an absolute template ignores
# TMPDIR, so the new same-parent-staging verifier creates its stages in the
# output's parent and succeeds. The old `mktemp -d` (no template) honored
# TMPDIR, so it would fail to create its stages and exit nonzero — this
# assertion would fail with the old approach.
struct_parent="$tmp/struct-parent"
rm -rf "$struct_parent"
mkdir -p "$struct_parent"
struct_output="$struct_parent/native-bridge"
# Point TMPDIR at a nonexistent path so any verifier that stages via a bare
# `mktemp -d` (honoring TMPDIR) fails; a same-parent-staging verifier uses an
# absolute template and is unaffected.
struct_tmpdir="$tmp/nonexistent-tmpdir"
if TMPDIR="$struct_tmpdir" VERIFY_NATIVE_OUTPUT="$struct_output" \
     bash "$repo_root/verify/verify-native.sh" "$commit" "$run_a" "$run_b" 2>&1; then
  :
else
  echo 'ERROR: same-parent staging publish must succeed even when TMPDIR is unusable' >&2
  rm -rf "$struct_parent"
  exit 1
fi
# Both platforms were published.
[ -f "$struct_output/windows/product/artifact" ]
[ -f "$struct_output/macos/product/artifact" ]
# No staging directories were left behind in the output's parent.
struct_leftover=$(find "$struct_parent" -maxdepth 1 -name '.verify-native-*' 2>/dev/null | head -1)
[ -z "$struct_leftover" ]
rm -rf "$struct_parent"

echo 'PASS verify-native.sh two-platform and per-platform layouts'