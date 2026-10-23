#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "ERROR: $1" >&2
  exit 2
}

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(dirname "$script_dir")
aggregate="$repo_root/build/verification/SHA256SUMS.toolchains"
rm -f "$aggregate" || fail "cannot remove stale aggregate"

[ "$#" -eq 4 ] || fail "usage: $0 <apk> <git-ref> <native-run-a-dir> <native-run-b-dir>"
apk=$1
ref=$2
run_a=$3
run_b=$4
[ -f "$apk" ] || fail "APK not found: $apk"
[ -d "$run_a" ] || fail "native run directory not found: $run_a"
[ -d "$run_b" ] || fail "native run directory not found: $run_b"

cd "$repo_root"
resolved=$(git rev-parse --verify "${ref}^{commit}") || fail "cannot resolve git ref: $ref"
head=$(git rev-parse HEAD) || fail "cannot resolve HEAD"
[ "$resolved" = "$head" ] || fail "git ref does not match HEAD"
status=$(git status --porcelain --untracked-files=all) || fail "cannot determine checkout cleanliness"
[ -z "$status" ] || fail "verify-all.sh requires a clean checkout"
summary=$(mktemp) || fail "cannot create component summary"
trap 'rm -f "$summary"' EXIT

run_component() {
  local name=$1
  shift
  if "$@"; then
    printf '%s\n' "$name" >> "$summary"
    printf 'PASS %s\n' "$name"
    return
  else
    local status=$?
  fi
  [ "$status" -eq 1 ] && exit 1
  exit 2
}

run_component android "$script_dir/verify.sh" "$apk" "$resolved"
run_component web "$script_dir/verify-web.sh"
run_component rust "$script_dir/verify-rust.sh"
run_component native "$script_dir/verify-native.sh" "$resolved" "$run_a" "$run_b"
run_component supply-chain "$script_dir/verify-supply-chain.sh"
run_component aggregate "$script_dir/aggregate-checksums.sh" "$resolved"
printf 'VERIFIED: %s\n' "$(paste -sd ' ' "$summary")"
