#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 4 ]; then
  printf '%s\n' 'usage: prepare-run.sh <output.tar> <resolved-commit> <product-dir> <view-dir>' >&2
  exit 2
fi

: "${ImageOS:?ImageOS is required}"
: "${ImageVersion:?ImageVersion is required}"
: "${RUNNER_ARCH:?RUNNER_ARCH is required}"

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
node "$script_dir/create-run.mjs" "$1" "$2" "$ImageOS" "$ImageVersion" "$RUNNER_ARCH" "$3" "$4"
