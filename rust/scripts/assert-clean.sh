#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# OTP-01 sub-project 1d — clean contract.
#
# Root `./gradlew clean` removes every declared generated product, verification,
# SBOM, checksum, and tool output under `build/` plus the two named source-tree
# exceptions (`web-extension/dist` and `web-extension/rust-wasm/pkg`). This
# script asserts none of them survive, so a stale artifact can never be mistaken
# for freshly built evidence.
#
# The two Rust auxiliary CLI caches (`build/rust` and `build/rust-tools`) are
# not product outputs — they are local, gitignored, machine-private caches — but
# the root clean task still removes them, so they are asserted here too.
#
# Gradle recreates `build/reports/problems/` after clean; that is the only known
# exception and is excluded from this assertion.

generated_paths=(
  # Rust JNI/WASM staged package (rustPackage producer).
  build/rust-package
  # Aggregate verification evidence and toolchain manifest.
  build/verification
  # CycloneDX SBOM outputs.
  build/sbom
  # Verification cargo-cyclonedx / cargo-deny install roots.
  build/verify-tools
  # Web-extension deterministic package (npm run package producer).
  build/web-extension
  # Native-bridge deterministic package (bridgePackage producer).
  build/native-bridge
  # Rust build cache and auxiliary CLI install cache (local, gitignored).
  build/rust
  build/rust-tools
  # Web-extension source-tree build output exceptions.
  web-extension/dist
  web-extension/rust-wasm/pkg
  # Native-bridge source-tree build output (Tauri target and dist).
  native-bridge/src-tauri/target
  native-bridge/dist
)

for relative in "${generated_paths[@]}"; do
  if [ -e "$ROOT/$relative" ]; then
    echo "ERROR: generated path survived clean: $relative" >&2
    exit 1
  fi
done

# app/build is allowed to contain reports/problems only after clean; assert no
# generated JNI or APK outputs remain.
for relative in app/build/generated/jniLibs app/build/outputs/apk; do
  if [ -e "$ROOT/$relative" ]; then
    echo "ERROR: generated path survived clean: $relative" >&2
    exit 1
  fi
done