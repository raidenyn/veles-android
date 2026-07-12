#!/usr/bin/env bash
# Runs INSIDE the verify container. Rebuilds <tag-or-ref> from source.
# Modes (at least one required):
#   - /apk/released.apk mounted -> compare released vs rebuilt (verify mode)
#   - /out directory mounted    -> copy rebuilt unsigned APK there (audit mode)
set -euo pipefail

REF="${1:?usage: verify-inner.sh <tag-or-ref>}"
RELEASED=/apk/released.apk
REPO_URL="${VELES_REPO_URL:-https://github.com/raidenyn/veles-android}"

if [ ! -f "$RELEASED" ] && [ ! -d /out ]; then
  echo "ERROR: mount an APK at $RELEASED (verify) and/or a directory at /out (audit)." >&2
  exit 2
fi

echo "==> Cloning $REPO_URL at $REF (full history so androidgitversion resolves)"
# Trust local mounts regardless of host UID (container runs as root); the
# https clone path is unaffected by this setting.
git config --global --add safe.directory '*'
git clone --quiet "$REPO_URL" /build/src
cd /build/src
git checkout --quiet "$REF"

echo "==> Building unsigned release APK (no VELES_KEYSTORE_* in this environment)"
./gradlew --no-daemon assembleRelease

REBUILT=app/build/outputs/apk/release/app-release-unsigned.apk
if [ ! -f "$REBUILT" ]; then
  echo "ERROR: $REBUILT not found after build." >&2
  exit 1
fi

if [ -d /out ]; then
  cp "$REBUILT" /out/
  echo "==> Copied rebuilt APK to /out"
fi

if [ -f "$RELEASED" ]; then
  echo "==> SHA-256 of released and rebuilt APKs:"
  sha256sum "$RELEASED" "$REBUILT"
  if cmp -s "$RELEASED" "$REBUILT"; then
    echo "VERIFIED: released APK is byte-identical to the rebuild (unsigned release)."
  elif apksigcopier compare "$RELEASED" --unsigned "$REBUILT"; then
    echo "VERIFIED: released APK matches the rebuild after signature stripping."
  else
    echo "MISMATCH: released APK does NOT correspond to source at $REF." >&2
    exit 1
  fi
fi
