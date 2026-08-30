#!/usr/bin/env bash
set -euo pipefail

: "${ImageOS:?ImageOS is required}"
: "${ImageVersion:?ImageVersion is required}"
: "${RUNNER_ARCH:?RUNNER_ARCH is required}"
[ "$ImageOS" = 'macos-26' ] || { printf '%s\n' "expected macos-26, got $ImageOS" >&2; exit 2; }
[ "$(node --version)" = 'v26.8.1' ] || { printf '%s\n' 'Node version drift' >&2; exit 2; }
[ "$(npm --version)" = '11.19.0' ] || { printf '%s\n' 'npm version drift' >&2; exit 2; }

export DEVELOPER_DIR=/Applications/Xcode_26.6.app
[ "$(xcodebuild -version | awk '/Build version/ { print $3 }')" = '17F113' ] || { printf '%s\n' 'Xcode version drift' >&2; exit 2; }
xcrun --sdk macosx26.5 --show-sdk-path >/dev/null

./gradlew bridgeBuild
probe='https://github.com/'
curl --fail --silent --show-error --output /dev/null "$probe"
rules=$(mktemp)
printf '%s\n' 'block drop out all' > "$rules"
cleanup() {
  sudo pfctl -d >/dev/null 2>&1 || true
  rm -f "$rules"
}
trap cleanup EXIT

sudo pfctl -f "$rules"
sudo pfctl -E >/dev/null
sudo pfctl -sr | grep -F 'block drop out all' >/dev/null
if curl --fail --silent --show-error --output /dev/null "$probe"; then
  printf '%s\n' 'network probe succeeded after outbound denial' >&2
  exit 1
fi
if [ "$#" -eq 0 ]; then set -- ./gradlew bridgePackage; fi
CARGO_NET_OFFLINE=true "$@"
