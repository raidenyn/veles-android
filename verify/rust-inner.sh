#!/usr/bin/env bash
set -euo pipefail

fail() { echo "ERROR: $1" >&2; exit 2; }

# Use a volume-backed GRADLE_USER_HOME so the Gradle wrapper distribution and
# dependencies cached during the online `prepare` run survive into the offline
# `package` run (both containers share the /work volume). The image pre-caches
# the pinned gradle-9.5.0-bin.zip distribution under /opt/gradle-home (set in
# Dockerfile.rust); seed the volume-backed home with that distribution on the
# first prepare so the wrapper never hits services.gradle.org, even offline.
export GRADLE_USER_HOME=/work/gradle-home
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
  ./gradlew --refresh-dependencies rustInstall
  (cd rust && cargo fetch --locked)
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
