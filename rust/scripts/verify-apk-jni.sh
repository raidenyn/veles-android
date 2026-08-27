#!/usr/bin/env bash
set -euo pipefail

APK="${1:?usage: $0 <apk>}"
if [ ! -f "$APK" ]; then
  echo "ERROR: APK not found: $APK" >&2
  exit 2
fi

JAR="${JAVA_HOME:?JAVA_HOME must point to JDK 21}/bin/jar"
mapfile -t ACTUAL_ABIS < <("$JAR" tf "$APK" | awk -F/ '$1 == "lib" {print $2}' | sort -u)
EXPECTED_ABIS=(arm64-v8a armeabi-v7a x86_64)
if ! diff -u <(printf '%s\n' "${EXPECTED_ABIS[@]}") <(printf '%s\n' "${ACTUAL_ABIS[@]}"); then
  echo "ERROR: APK native ABI set differs from the approved set." >&2
  exit 1
fi

mapfile -t ACTUAL_VELES < <("$JAR" tf "$APK" | awk '/^lib\/[^/]+\/libveles_crypto\.so$/ {print}' | sort)
EXPECTED_VELES=(
  lib/arm64-v8a/libveles_crypto.so
  lib/armeabi-v7a/libveles_crypto.so
  lib/x86_64/libveles_crypto.so
)
if ! diff -u <(printf '%s\n' "${EXPECTED_VELES[@]}") <(printf '%s\n' "${ACTUAL_VELES[@]}"); then
  echo "ERROR: APK Veles JNI entry set differs from the approved set." >&2
  exit 1
fi
