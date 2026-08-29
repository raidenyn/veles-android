#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 3 ]; then
  printf '%s\n' 'usage: verify-native.sh <resolved-commit> <run-a-dir> <run-b-dir>' >&2
  exit 2
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
node "$script_dir/native/compare-runs.mjs" "$1" "$2" "$3" "${VERIFY_NATIVE_OUTPUT:-build/verification/native-bridge}"
