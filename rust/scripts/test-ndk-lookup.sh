#!/usr/bin/env bash
# Contract test for the Android NDK lookup of the Rust build (design D5, task 10).
#
# Covers the documented order:
#   1. ANDROID_NDK_HOME
#   2. $ANDROID_SDK_ROOT/ndk/<version>
#   3. $ANDROID_HOME/ndk/<version>
#   4. `sdk.dir` of the repository-root local.properties
#   5. `sdk.dir` of android/local.properties
# plus:
#   - a missing NDK fails with "NDK <version> not found; searched: <locations>", listing
#     every candidate in that order
#   - with no source configured at all, the message names every supported source
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
mkdir -p "$scratch/rust" "$scratch/android"
cp "$ROOT/rust/build.gradle.kts" "$ROOT/rust/settings.gradle.kts" \
  "$ROOT/rust/ndk-version" "$ROOT/rust/toolchain-tools.toml" "$scratch/rust/"

# A fake NDK only has to be a directory for the lookup; the Pkg.Revision check
# belongs to the JNI tasks and is not exercised here.
fake_ndk() {
  mkdir -p "$1"
  printf 'Pkg.Revision = %s\n' "$VERSION" > "$1/source.properties"
}

# run_lookup runs rustNdkDir without any ambient Android SDK/NDK variable, then
# adds whatever the caller passes as KEY=VALUE arguments.
run_lookup() {
  env -u ANDROID_HOME -u ANDROID_NDK_HOME -u ANDROID_SDK_ROOT "$@" bash -c \
    '"$0" -p "$1/rust" --console=plain --no-daemon -q rustNdkDir' "$GRADLEW" "$scratch"
}

explicit_ndk="$TMP/ndk-explicit"
sdk_ndk="$TMP/sdk/ndk/$VERSION"
home_ndk="$TMP/sdk-home/ndk/$VERSION"
local_sdk="$TMP/sdk-local"
android_local_sdk="$TMP/sdk-android-local"
empty_sdk="$TMP/sdk-empty"
empty_home="$TMP/sdk-home-empty"
empty_local="$TMP/sdk-local-empty"
empty_android_local="$TMP/sdk-android-local-empty"
fake_ndk "$explicit_ndk"
fake_ndk "$sdk_ndk"
fake_ndk "$home_ndk"
fake_ndk "$local_sdk/ndk/$VERSION"
fake_ndk "$android_local_sdk/ndk/$VERSION"
mkdir -p "$empty_sdk" "$empty_home" "$empty_local" "$empty_android_local"

# 1. ANDROID_NDK_HOME wins even when the SDKs also have the pinned NDK.
out="$(run_lookup "ANDROID_NDK_HOME=$explicit_ndk" "ANDROID_SDK_ROOT=$TMP/sdk" \
  "ANDROID_HOME=$TMP/sdk-home")" ||
  fail "rustNdkDir failed with ANDROID_NDK_HOME set"
[ "$out" = "$explicit_ndk" ] || fail "ANDROID_NDK_HOME must win; got '$out', want '$explicit_ndk'"

# 2. Without ANDROID_NDK_HOME, $ANDROID_SDK_ROOT/ndk/<version> wins over ANDROID_HOME.
out="$(run_lookup "ANDROID_SDK_ROOT=$TMP/sdk" "ANDROID_HOME=$TMP/sdk-home")" ||
  fail "rustNdkDir failed with ANDROID_SDK_ROOT set"
[ "$out" = "$sdk_ndk" ] || fail "ANDROID_SDK_ROOT/ndk lookup failed; got '$out', want '$sdk_ndk'"

# 3. Without ANDROID_NDK_HOME and ANDROID_SDK_ROOT, $ANDROID_HOME/ndk/<version> is used.
out="$(run_lookup "ANDROID_HOME=$TMP/sdk-home")" ||
  fail "rustNdkDir failed with ANDROID_HOME set"
[ "$out" = "$home_ndk" ] || fail "ANDROID_HOME/ndk lookup failed; got '$out', want '$home_ndk'"

# 4. With no environment variable, the repository-root local.properties sdk.dir wins
#    over android/local.properties.
printf 'sdk.dir=%s\n' "$local_sdk" > "$scratch/local.properties"
printf 'sdk.dir=%s\n' "$android_local_sdk" > "$scratch/android/local.properties"
out="$(run_lookup)" || fail "rustNdkDir failed with a repository-root local.properties sdk.dir"
[ "$out" = "$local_sdk/ndk/$VERSION" ] ||
  fail "repository-root local.properties sdk.dir lookup failed; got '$out', want '$local_sdk/ndk/$VERSION'"

# 5. android/local.properties is the last resort: the file the Android build reads.
rm -f "$scratch/local.properties"
out="$(run_lookup)" || fail "rustNdkDir failed with android/local.properties sdk.dir set"
[ "$out" = "$android_local_sdk/ndk/$VERSION" ] ||
  fail "android/local.properties sdk.dir lookup failed; got '$out', want '$android_local_sdk/ndk/$VERSION'"

# 6. With all sources configured but none carrying the pinned NDK, the failure lists
#    every candidate in the documented order.
printf 'sdk.dir=%s\n' "$empty_local" > "$scratch/local.properties"
printf 'sdk.dir=%s\n' "$empty_android_local" > "$scratch/android/local.properties"
set +e
output="$(run_lookup "ANDROID_SDK_ROOT=$empty_sdk" "ANDROID_HOME=$empty_home" 2>&1)"
status=$?
set -e
[ "$status" -ne 0 ] || fail "rustNdkDir must fail when the pinned NDK is absent"
grep -Fq "NDK $VERSION not found; searched: $empty_sdk/ndk/$VERSION, $empty_home/ndk/$VERSION, $empty_local/ndk/$VERSION, $empty_android_local/ndk/$VERSION" <<<"$output" ||
  fail "missing the ordered failure message; got: $output"
rm -f "$scratch/local.properties" "$scratch/android/local.properties"

# 7. With no source configured at all, the same message still names the version and
#    every supported source.
set +e
output="$(run_lookup 2>&1)"
status=$?
set -e
[ "$status" -ne 0 ] || fail "rustNdkDir must fail when no NDK source is configured"
grep -Fq "NDK $VERSION not found; searched:" <<<"$output" ||
  fail "missing the specified failure message; got: $output"
grep -Fq "ANDROID_NDK_HOME, ANDROID_SDK_ROOT, ANDROID_HOME, repository-root local.properties sdk.dir or android/local.properties sdk.dir" <<<"$output" ||
  fail "the empty fallback must name every supported source; got: $output"

# 8. The root-level rustNdkVersion prints exactly rust/ndk-version.
out="$("$GRADLEW" -p "$ROOT" --console=plain --no-daemon -q rustNdkVersion)" ||
  fail "rustNdkVersion failed from the repository root"
[ "$out" = "$VERSION" ] || fail "rustNdkVersion must print '$VERSION'; got '$out'"

echo "PASS: NDK lookup contract (ANDROID_NDK_HOME > ANDROID_SDK_ROOT > ANDROID_HOME > local.properties > android/local.properties, failure message, rustNdkVersion)"
