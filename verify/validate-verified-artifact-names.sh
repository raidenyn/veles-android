#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -eq 0 ]; then
  printf '%s\n' 'usage: validate-verified-artifact-names.sh <artifact-name>...' >&2
  exit 2
fi

for name in "$@"; do
  case "$name" in
    unverified-*)
      printf 'unverified artifact is not accepted: %s\n' "$name" >&2
      exit 2
      ;;
    verified-android|verified-web-extension|rust-jni-wasm|verified-native-windows|verified-native-macos)
      ;;
    *)
      printf 'unexpected verified artifact: %s\n' "$name" >&2
      exit 2
      ;;
  esac
done
