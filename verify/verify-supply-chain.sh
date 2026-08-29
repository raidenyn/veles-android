#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
mkdir -p build/verification/supply-chain

node_bin="$(command -v node)"
npm_bin="$(dirname "$node_bin")/npm"
test -x "$npm_bin"
test "$("$node_bin" --version)" = "v26.8.1"
test "$("$npm_bin" --version)" = "11.19.0"
"$npm_bin" ci --ignore-scripts --prefix web-extension
"$npm_bin" ci --ignore-scripts --prefix native-bridge
verify/generate-sboms.sh
node verify/verify-sboms.mjs
node verify/check-npm-licenses.mjs
node verify/check-npm-install-scripts.mjs
node verify/check-cargo-build-scripts.mjs
node verify/check-remote-code.mjs
