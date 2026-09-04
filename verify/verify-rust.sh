#!/usr/bin/env bash
# Compare the current clean checkout's rust-jni-wasm package with a Docker reference build.
set -euo pipefail

fail() { echo "ERROR: $1" >&2; exit 2; }
[ $# -eq 0 ] || fail "usage: $0"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || fail "cannot determine script directory"
REPO_ROOT="$(dirname "$SCRIPT_DIR")" || fail "cannot determine repository root"
DOCKER_BIN="${DOCKER_BIN:-docker}"
GRADLE_BIN="${GRADLE_BIN:-$REPO_ROOT/gradlew}"
RUST_PACKAGE_DIR="${RUST_PACKAGE_DIR:-$REPO_ROOT/build/rust-package}"
ID="$$-${RANDOM}"
IMAGE="veles-verify-rust-${ID}"
VOLUME="veles-verify-rust-work-${ID}"
REFERENCE_DIR="$(mktemp -d)" || fail "cannot create reference directory"

cleanup() {
  "$DOCKER_BIN" volume rm -f "$VOLUME" >/dev/null 2>&1 || true
  "$DOCKER_BIN" image rm -f "$IMAGE" >/dev/null 2>&1 || true
  rm -rf "$REFERENCE_DIR"
}
trap cleanup EXIT

checkout_status="$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all)" || fail "cannot determine checkout cleanliness"
[ -z "$checkout_status" ] || fail "verify-rust.sh requires a clean checkout"

"$GRADLE_BIN" rustPackage || fail "candidate Rust package failed"
if node "$SCRIPT_DIR/verify-rust.mjs" --validate "$RUST_PACKAGE_DIR"; then :; else
  status=$?
  [ "$status" -eq 1 ] && exit 1
  fail "candidate artifact validation failed"
fi

"$DOCKER_BIN" build -t "$IMAGE" -f "$SCRIPT_DIR/Dockerfile.rust" "$REPO_ROOT" || fail "failed to build Rust reference image"
"$DOCKER_BIN" volume create "$VOLUME" >/dev/null || fail "failed to create reference work volume"
"$DOCKER_BIN" run --rm -v "$REPO_ROOT:/source:ro" -v "$VOLUME:/work" "$IMAGE" prepare || fail "reference preparation failed"
"$DOCKER_BIN" run --rm --network=none -e "VELES_OUTPUT_UID=$(id -u)" -e "VELES_OUTPUT_GID=$(id -g)" -v "$VOLUME:/work" -v "$REFERENCE_DIR:/out" "$IMAGE" package || fail "offline Rust reference package failed"

if node "$SCRIPT_DIR/verify-rust.mjs" "$RUST_PACKAGE_DIR" "$REFERENCE_DIR"; then exit 0; else
  status=$?
  [ "$status" -eq 1 ] && exit 1
  fail "artifact comparison failed"
fi
