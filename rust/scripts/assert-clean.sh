#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Gradle recreates build/reports/problems/ after clean; that is the only
# known exception and is excluded from this assertion.
for relative in build/rust build/rust-tools web-extension/dist web-extension/rust-wasm/pkg; do
  if [ -e "$ROOT/$relative" ]; then
    echo "ERROR: generated path survived clean: $relative" >&2
    exit 1
  fi
done
# app/build is allowed to contain reports/problems only after clean; assert
# no generated JNI or APK outputs remain.
for relative in app/build/generated/jniLibs app/build/outputs/apk; do
  if [ -e "$ROOT/$relative" ]; then
    echo "ERROR: generated path survived clean: $relative" >&2
    exit 1
  fi
done
