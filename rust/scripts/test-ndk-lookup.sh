#!/usr/bin/env bash
# Contract test for the Android NDK lookup of the Rust build (design D5, task 10).
#
# Covers:
#   - ANDROID_NDK_HOME wins over $ANDROID_SDK_ROOT/ndk/<version>
#   - $ANDROID_SDK_ROOT/ndk/<version> is used when ANDROID_NDK_HOME is unset
#   - `sdk.dir` of the repository-root local.properties is the last resort
#   - a missing NDK fails with "NDK <version> not found; searched: <locations>"
#   - rustNdkVersion prints exactly the contents of rust/ndk-version
#
# The lookup cases run the real rust/build.gradle.kts in a scratch repository
# that carries the same rust/ layout, so the assertions are about the shipped
# build logic, not a copy of it.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GRADLEW="$ROOT/gradlew"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

for required in rust/build.gradle.kts rust/settings.gradle.kts rust/ndk-version rust/toolchain-tools.toml; do
  [ -f "$ROOT/$required" ] || fail "missing $required (the Rust included build is not implemented yet)"
done

VERSION="$(tr -d '[:space:]' < "$ROOT/rust/ndk-version")"
[ -n "$VERSION" ] || fail "rust/ndk-version is empty"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

scratch="$TMP/repo"
mkdir -p "$scratch/rust"
cp "$ROOT/rust/build.gradle.kts" "$ROOT/rust/settings.gradle.kts" \
  "$ROOT/rust/ndk-version" "$ROOT/rust/toolchain-tools.toml" "$scratch/rust/"

# A fake NDK only has to be a directory for the lookup; the Pkg.Revision check
# belongs to the JNI tasks and is not exercised here.
fake_ndk() {
  mkdir -p "$1"
  printf 'Pkg.Revision = %s\n' "$VERSION" > "$1/source.properties"
}

explicit_ndk="$TMP/ndk-explicit"
sdk_ndk="$TMP/sdk/ndk/$VERSION"
local_sdk="$TMP/sdk-local"
empty_sdk="$TMP/sdk-empty"
fake_ndk "$explicit_ndk"
fake_ndk "$sdk_ndk"
fake_ndk "$local_sdk/ndk/$VERSION"
mkdir -p "$empty_sdk"

# 1. ANDROID_NDK_HOME wins even when the SDK also has the pinned NDK.
out="$(env -u ANDROID_HOME ANDROID_NDK_HOME="$explicit_ndk" ANDROID_SDK_ROOT="$TMP/sdk" \
  bash -c '"$0" -p "$1/rust" --console=plain --no-daemon -q rustNdkDir' "$GRADLEW" "$scratch")" ||
  fail "rustNdkDir failed with ANDROID_NDK_HOME set"
[ "$out" = "$explicit_ndk" ] || fail "ANDROID_NDK_HOME must win; got '$out', want '$explicit_ndk'"

# 2. Without ANDROID_NDK_HOME the SDK's ndk/<version> directory is used.
out="$(env -u ANDROID_HOME -u ANDROID_NDK_HOME ANDROID_SDK_ROOT="$TMP/sdk" bash -c \
  '"$0" -p "$1/rust" --console=plain --no-daemon -q rustNdkDir' "$GRADLEW" "$scratch")" ||
  fail "rustNdkDir failed with ANDROID_SDK_ROOT set"
[ "$out" = "$sdk_ndk" ] || fail "ANDROID_SDK_ROOT/ndk lookup failed; got '$out', want '$sdk_ndk'"

# 3. With neither environment variable, local.properties sdk.dir is the last resort.
printf 'sdk.dir=%s\n' "$local_sdk" > "$scratch/local.properties"
out="$(env -u ANDROID_HOME -u ANDROID_NDK_HOME -u ANDROID_SDK_ROOT bash -c \
  '"$0" -p "$1/rust" --console=plain --no-daemon -q rustNdkDir' "$GRADLEW" "$scratch")" ||
  fail "rustNdkDir failed with local.properties sdk.dir set"
rm -f "$scratch/local.properties"
[ "$out" = "$local_sdk/ndk/$VERSION" ] ||
  fail "local.properties sdk.dir lookup failed; got '$out', want '$local_sdk/ndk/$VERSION'"

# 4. A configured SDK without the pinned NDK fails with the specified message.
set +e
output="$(env -u ANDROID_HOME -u ANDROID_NDK_HOME ANDROID_SDK_ROOT="$empty_sdk" bash -c \
  '"$0" -p "$1/rust" --console=plain --no-daemon -q rustNdkDir' "$GRADLEW" "$scratch" 2>&1)"
status=$?
set -e
[ "$status" -ne 0 ] || fail "rustNdkDir must fail when the pinned NDK is absent"
grep -Fq "NDK $VERSION not found; searched: $empty_sdk/ndk/$VERSION" <<<"$output" ||
  fail "missing the specified failure message; got: $output"

# 5. With no source configured at all, the same message still names the version.
set +e
output="$(env -u ANDROID_HOME -u ANDROID_NDK_HOME -u ANDROID_SDK_ROOT bash -c \
  '"$0" -p "$1/rust" --console=plain --no-daemon -q rustNdkDir' "$GRADLEW" "$scratch" 2>&1)"
status=$?
set -e
[ "$status" -ne 0 ] || fail "rustNdkDir must fail when no NDK source is configured"
grep -Fq "NDK $VERSION not found; searched:" <<<"$output" ||
  fail "missing the specified failure message; got: $output"

# 6. The root-level rustNdkVersion prints exactly rust/ndk-version.
out="$("$GRADLEW" -p "$ROOT" --console=plain --no-daemon -q rustNdkVersion)" ||
  fail "rustNdkVersion failed from the repository root"
[ "$out" = "$VERSION" ] || fail "rustNdkVersion must print '$VERSION'; got '$out'"

echo "PASS: NDK lookup contract (ANDROID_NDK_HOME > SDK ndk/<version> > local.properties, failure message, rustNdkVersion)"
