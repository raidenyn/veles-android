#!/usr/bin/env bash
# Regenerate the two REAL base images used by test_correspondence.py with the
# toolchain pinned in native-bridge/installer/rust-toolchain.toml (Rust 1.98.0,
# targets x86_64-pc-windows-msvc and aarch64-apple-darwin), cross-linked on any
# host with rust-lld. No network, no Windows/macOS SDK, no signing is involved.
#
#   native-bridge/installer/scripts/fixtures/make-base-fixtures.sh [OUT_DIR]
#
# The images are written under a fixed relative name from a fixed working
# directory so no absolute host path reaches the PE debug directory. The script
# prints each image's SHA-256; compare with the pinned table in fixtures/PROVENANCE.md
# (test_correspondence.py::test_fixture_digests_match_provenance enforces it).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
installer="$(cd "$here/../.." && pwd)"
out="${1:-$here/correspondence}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$out"
cp "$here/src/pe_min.rs" "$here/src/macho_min.rs" "$work/"

# rustup resolves the pinned 1.98.0 toolchain from native-bridge/installer.
cd "$installer"
RUSTFLAGS= rustc --target x86_64-pc-windows-msvc --edition 2024 --crate-type bin \
  -C panic=abort -C opt-level=s -C debuginfo=0 -C strip=none \
  -C linker=rust-lld -C linker-flavor=lld-link \
  -C link-arg=/ENTRY:mainCRTStartup -C link-arg=/SUBSYSTEM:CONSOLE \
  -C link-arg=/NODEFAULTLIB -C link-arg=/BREPRO -C link-arg=/DEBUG:NONE -C link-arg=/TIMESTAMP:0 \
  "$work/pe_min.rs" -o "$work/veles-fixture-x64.exe"

RUSTFLAGS= SDKROOT=/nonexistent rustc --target aarch64-apple-darwin --edition 2024 --crate-type bin \
  -C panic=abort -C opt-level=s -C debuginfo=0 \
  -C linker=rust-lld -C linker-flavor=ld64.lld \
  -C link-arg=-platform_version -C link-arg=macos -C link-arg=13.0 -C link-arg=13.0 \
  -C link-arg=-undefined -C link-arg=dynamic_lookup \
  "$work/macho_min.rs" -o "$work/veles-fixture-arm64.macho" 2>/dev/null

cp "$work/veles-fixture-x64.exe" "$out/veles-fixture-x64.exe"
cp "$work/veles-fixture-arm64.macho" "$out/veles-fixture-arm64.macho"
sha256sum "$out/veles-fixture-x64.exe" "$out/veles-fixture-arm64.macho"
