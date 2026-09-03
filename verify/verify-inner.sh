#!/usr/bin/env bash
# Runs INSIDE the verify container. Rebuilds <tag-or-ref> from source.
# Modes (at least one required):
#   - /apk/released.apk mounted -> compare released vs rebuilt (verify mode)
#   - /out directory mounted    -> copy rebuilt unsigned APK there (audit mode;
#     optional VELES_OUTPUT_UID and VELES_OUTPUT_GID restore host ownership)
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
# Clone/checkout failures are environment errors (exit 2), not artifact
# mismatch (exit 1). git would otherwise propagate raw statuses (e.g. 128).
if ! git clone --quiet "$REPO_URL" /build/src; then
  echo "ERROR: cannot clone $REPO_URL" >&2
  exit 2
fi
cd /build/src
if ! git checkout --quiet "$REF"; then
  echo "ERROR: cannot checkout $REF" >&2
  exit 2
fi

echo "==> Building unsigned release APK (no VELES_KEYSTORE_* in this environment)"
# A build failure prevents comparison and is an environment/build error (exit 2),
# not an artifact mismatch (exit 1).
if ! ./gradlew --no-daemon assembleRelease; then
  echo "ERROR: release APK build failed; cannot compare." >&2
  exit 2
fi

REBUILT=app/build/outputs/apk/release/app-release-unsigned.apk
if [ ! -f "$REBUILT" ]; then
  echo "ERROR: $REBUILT not found after build." >&2
  exit 2
fi

if [ -d /out ]; then
  cp "$REBUILT" /out/
  /build/src/rust/scripts/verify-apk-jni.sh /out/app-release-unsigned.apk
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

# Ownership is an output-boundary concern, so it cannot preempt an artifact
# mismatch. Supplying neither variable preserves standalone audit mode.
VELES_OUTPUT_UID="${VELES_OUTPUT_UID:-}"
VELES_OUTPUT_GID="${VELES_OUTPUT_GID:-}"
if [ -d /out ] && { [ -n "$VELES_OUTPUT_UID" ] || [ -n "$VELES_OUTPUT_GID" ]; }; then
  if [[ ! "$VELES_OUTPUT_UID" =~ ^[0-9]+$ ]] || [[ ! "$VELES_OUTPUT_GID" =~ ^[0-9]+$ ]]; then
    echo "ERROR: output ownership must be numeric host UID:GID." >&2
    exit 2
  fi
  if ! chown "$VELES_OUTPUT_UID:$VELES_OUTPUT_GID" /out/app-release-unsigned.apk; then
    echo "ERROR: cannot return rebuilt APK with host ownership." >&2
    exit 2
  fi
fi
