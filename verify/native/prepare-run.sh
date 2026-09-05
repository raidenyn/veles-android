#!/usr/bin/env bash
set -euo pipefail

# 0/1/2 contract: 0 = success, 1 = artifact mismatch, 2 = usage/env/identity
# error. A bare `:?` parameter expansion exits 1 on a missing env var, so env
# failures are normalized to exit 2 explicitly.
env_fail() {
  printf '%s\n' "$*" >&2
  exit 2
}

if [ "$#" -ne 4 ]; then
  env_fail 'usage: prepare-run.sh <output.tar> <resolved-commit> <product-dir> <view-dir>'
fi

[ -n "${ImageOS:-}" ] || env_fail 'ImageOS is required'
[ -n "${ImageVersion:-}" ] || env_fail 'ImageVersion is required'
[ -n "${RUNNER_ARCH:-}" ] || env_fail 'RUNNER_ARCH is required'

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
node "$script_dir/create-run.mjs" "$1" "$2" "$ImageOS" "$ImageVersion" "$RUNNER_ARCH" "$3" "$4"
