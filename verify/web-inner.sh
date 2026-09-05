#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "ERROR: $1" >&2
  exit 2
}

assert_pins() {
  [ "$(node --version)" = "v26.8.1" ] || fail "Node version drift"
  [ "$(npm --version)" = "11.19.0" ] || fail "npm version drift"
}

prepare() {
  assert_pins
  rm -rf /work/src
  mkdir -p /work/src
  cp -a /source/. /work/src/
  cd /work/src/web-extension
  npm ci --ignore-scripts
  npm run format:check
  npm run lint
  npm run typecheck
  npm test
  npm run build
  npm run test:bundle
}

package_reference() {
  assert_pins
  cd /work/src/web-extension
  npm run package
  mkdir -p /out
  for path in veles-extension-0.1.0.zip veles-extension-0.1.0.zip.sha256 SHA256SUMS; do
    [ -f "/work/src/build/web-extension/$path" ] || fail "missing reference artifact: $path"
    cp "/work/src/build/web-extension/$path" "/out/$path"
  done
}

case "${1:-}" in
  prepare) prepare ;;
  package) package_reference ;;
  *) fail "usage: web-inner.sh <prepare|package>" ;;
esac
