#!/usr/bin/env bash
# Compare the current clean checkout's web package with a Docker reference build.
set -euo pipefail

fail() {
  echo "ERROR: $1" >&2
  exit 2
}

if [ $# -ne 0 ]; then
  fail "usage: $0"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || fail "cannot determine script directory"
REPO_ROOT="$(dirname "$SCRIPT_DIR")" || fail "cannot determine repository root"
DOCKER_BIN="${DOCKER_BIN:-docker}"
ID="$$-${RANDOM}"
IMAGE="veles-verify-web-${ID}"
VOLUME="veles-verify-web-work-${ID}"
REFERENCE_DIR="$(mktemp -d)" || fail "cannot create reference directory"

cleanup() {
  "$DOCKER_BIN" volume rm -f "$VOLUME" >/dev/null 2>&1 || true
  "$DOCKER_BIN" image rm -f "$IMAGE" >/dev/null 2>&1 || true
  rm -rf "$REFERENCE_DIR"
}
trap cleanup EXIT

if ! checkout_status="$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all)"; then
  fail "cannot determine checkout cleanliness"
fi
if [ -n "$checkout_status" ]; then
  fail "verify-web.sh requires a clean checkout"
fi

cd "$REPO_ROOT/web-extension" || fail "cannot enter web-extension directory"
npm ci --ignore-scripts || fail "candidate npm install failed"
npm run format:check || fail "candidate format check failed"
npm run lint || fail "candidate lint failed"
npm run typecheck || fail "candidate typecheck failed"
npm test || fail "candidate tests failed"
npm run build || fail "candidate build failed"
npm run test:bundle || fail "candidate bundle test failed"
npm run package || fail "candidate package failed"
node "$SCRIPT_DIR/verify-web.mjs" --validate "$REPO_ROOT/build/web-extension" || fail "candidate artifact validation failed"

"$DOCKER_BIN" build -t "$IMAGE" -f "$SCRIPT_DIR/Dockerfile.web" "$REPO_ROOT" || fail "failed to build web reference image"
"$DOCKER_BIN" volume create "$VOLUME" >/dev/null || fail "failed to create reference work volume"
"$DOCKER_BIN" run --rm -v "$REPO_ROOT:/source:ro" -v "$VOLUME:/work" "$IMAGE" prepare || fail "reference preparation failed"
"$DOCKER_BIN" run --rm --network=none -v "$VOLUME:/work" -v "$REFERENCE_DIR:/out" "$IMAGE" package || fail "offline reference package failed"

if node "$SCRIPT_DIR/verify-web.mjs" "$REPO_ROOT/build/web-extension" "$REFERENCE_DIR"; then
  exit 0
else
  status=$?
  if [ "$status" -eq 1 ]; then
    exit 1
  fi
  fail "artifact comparison failed"
fi
