#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
mkdir -p build/verification/supply-chain

npm ci --ignore-scripts --prefix web-extension
npm ci --ignore-scripts --prefix native-bridge
verify/generate-sboms.sh
node verify/verify-sboms.mjs
node verify/check-npm-licenses.mjs
node verify/check-npm-install-scripts.mjs
node verify/check-cargo-build-scripts.mjs
node verify/check-remote-code.mjs
