#!/usr/bin/env bash
set -euo pipefail

fail() { echo "ERROR: $1" >&2; exit 2; }

# The prepare and package containers share only /work. Keep both dependency
# homes there so their online caches remain available under network denial.
export GRADLE_USER_HOME=/work/gradle-home
export CARGO_HOME=/work/cargo-home
seed_gradle_distribution() {
  local seed_dir=/opt/gradle-home/wrapper/dists
  local dest_dir="$GRADLE_USER_HOME/wrapper/dists"
  if [ -d "$seed_dir" ]; then
    mkdir -p "$dest_dir"
    cp -a "$seed_dir/." "$dest_dir/" 2>/dev/null || true
  fi
}

assert_pins() {
  java -version 2>&1 | grep -q '21.0.12' || fail "JDK version drift"
  [ "$(sdkmanager --version)" = "12.0" ] || fail "Android SDK helper version drift"
  grep -Eq '^Pkg\.Revision[[:space:]]*=[[:space:]]*29\.0\.14206865$' "$ANDROID_HOME/ndk/29.0.14206865/source.properties" || fail "NDK version drift"
  [ "$(rustc --version | cut -d' ' -f2)" = "1.98.0" ] || fail "Rust version drift"
  [ "$(node --version)" = "v26.8.1" ] || fail "Node version drift"
  [ "$(npm --version)" = "11.19.0" ] || fail "npm version drift"
}

prepare() {
  assert_pins
  rm -rf /work/src
  mkdir -p /work/src
  cp -a /source/. /work/src/
  cd /work/src
  seed_gradle_distribution
  (cd rust && cargo fetch --locked)
  ./gradlew --refresh-dependencies rustInstall
}

package_reference() {
  assert_pins
  cd /work/src
  CARGO_NET_OFFLINE=true ./gradlew --offline rustPackage
  mkdir -p /out
  cp -a build/rust-package/. /out/
}

case "${1:-}" in
  prepare) prepare ;;
  package) package_reference ;;
  *) fail "usage: rust-inner.sh <prepare|package>" ;;
esac
