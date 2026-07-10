#!/usr/bin/env bash
# Verify a released Veles APK against its source tag, using only Docker.
#
#   verify/verify.sh <path-to-released-apk> <tag>
#
# Rebuilds <tag> from source inside a pinned reference environment and
# compares the result with the released APK (signature-stripped via
# apksigcopier). Exit 0 = verified.
#
# Run this script from a checkout of the SAME tag you are verifying, so the
# reference environment pins match that release (see docs/reproducible-builds.md).
#
# VELES_REPO_URL overrides the source repo: an https URL, or a local
# directory (useful pre-merge; it is mounted read-only into the container).
set -euo pipefail

if [ $# -ne 2 ]; then
  echo "usage: $0 <path-to-released-apk> <tag>" >&2
  exit 2
fi
APK="$1"
TAG="$2"
if [ ! -f "$APK" ]; then
  echo "ERROR: no such file: $APK" >&2
  exit 2
fi
APK="$(realpath "$APK")"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

# Pin-consistency check: Dockerfile JDK must match .java-version.
JAVA_VERSION="$(tr -d '[:space:]' < "$REPO_ROOT/.java-version")"
JAVA_VERSION_RE="${JAVA_VERSION//./\\.}"
if ! grep -Eq "eclipse-temurin:${JAVA_VERSION_RE}_" "$SCRIPT_DIR/Dockerfile"; then
  echo "ERROR: verify/Dockerfile base image does not match .java-version ($JAVA_VERSION)." >&2
  echo "       Update them together — see docs/reproducible-builds.md." >&2
  exit 2
fi

echo "==> Building reference environment image (this can take several minutes)"
docker build -t veles-verify "$SCRIPT_DIR" || { echo "ERROR: failed to build reference image." >&2; exit 2; }

DOCKER_ARGS=(--rm -v "$APK":/apk/released.apk:ro)
if [ -n "${VELES_REPO_URL:-}" ] && [ -d "$VELES_REPO_URL" ]; then
  DOCKER_ARGS+=(-v "$(realpath "$VELES_REPO_URL")":/host-repo:ro -e VELES_REPO_URL=/host-repo)
elif [ -n "${VELES_REPO_URL:-}" ]; then
  DOCKER_ARGS+=(-e VELES_REPO_URL)
fi

echo "==> Rebuilding $TAG and comparing against $APK"
docker run "${DOCKER_ARGS[@]}" veles-verify "$TAG"
