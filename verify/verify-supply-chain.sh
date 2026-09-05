#!/usr/bin/env bash
# Run the full supply-chain verification: SBOM generation + validation, license
# policy, npm install-script audit, cargo build-script scan, and remote-code
# scan. Reports land under build/sbom/ and build/verification/supply-chain/.
#
# Exit codes (binding 0/1/2 contract):
#   0 - every check passed
#   1 - an artifact/policy check mismatched (propagated from the node checkers)
#   2 - environment/pin failure: wrong Node/npm, missing tool, missing
#       manifest, or build/dependency-acquisition failure
set -euo pipefail

# Environment and pin failures (wrong Node/npm, missing npm, missing lockfile,
# failed dependency install, failed SBOM generation) must surface as exit 2,
# not the raw 1 that `set -e` would propagate from a bare `test`/`command`.
env_fail() {
  echo "ERROR: $1" >&2
  exit 2
}

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
mkdir -p build/verification/supply-chain

node_bin="$(command -v node)" || env_fail "node not found on PATH (requires Node 26.8.1)"
npm_bin="$(dirname "$node_bin")/npm"
[ -x "$npm_bin" ] || env_fail "npm not found alongside node at $node_bin (requires npm 11.19.0)"
[ "$(node --version)" = "v26.8.1" ] || env_fail "Node 26.8.1 required, found $(node --version)"
[ "$(npm --version)" = "11.19.0" ] || env_fail "npm 11.19.0 required, found $(npm --version)"

# Dependency acquisition and SBOM generation are environment/infrastructure
# steps; a failure here is an environment error (exit 2), not a policy mismatch.
npm ci --ignore-scripts --prefix web-extension || env_fail "web-extension npm ci failed"
npm ci --ignore-scripts --prefix native-bridge || env_fail "native-bridge npm ci failed"
verify/generate-sboms.sh || env_fail "SBOM generation failed (are cargo-cyclonedx/cargo-deny provisioned? see ./gradlew verifyCargoCyclonedx installCargoDeny)"

# The node checkers classify their own exit codes (1 = mismatch, 2 = error) and
# propagate them directly; do not re-map them.
node verify/verify-sboms.mjs
node verify/check-npm-licenses.mjs
node verify/check-npm-install-scripts.mjs
node verify/check-cargo-build-scripts.mjs
node verify/check-remote-code.mjs