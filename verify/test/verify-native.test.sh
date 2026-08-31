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

# --- Wrong argument count exits 2 ---
if bash "$repo_root/verify/verify-native.sh" "$commit" "$run_a" 2>/dev/null; then
  echo 'ERROR: missing argument must exit 2' >&2
  exit 1
else
  [ $? -eq 2 ]
fi

echo 'PASS verify-native.sh two-platform and per-platform layouts'