#!/usr/bin/env bash
set -euo pipefail

# Environment/usage/identity failures exit 2 (never 1). A raw nonzero exit under
# `set -e` would otherwise propagate the underlying command's status (e.g. 1 for
# a Gradle build failure), so every acquisition/build failure is normalized to
# exit 2 via explicit guards. Artifact mismatches are the caller's
# responsibility (byte comparison), not this wrapper's.
env_fail() {
  printf '%s\n' "$*" >&2
  exit 2
}

[ -n "${ImageOS:-}" ] || env_fail 'ImageOS is required'
[ -n "${ImageVersion:-}" ] || env_fail 'ImageVersion is required'
[ -n "${RUNNER_ARCH:-}" ] || env_fail 'RUNNER_ARCH is required'
[ "$ImageOS" = 'macos26' ] || env_fail "expected macos26, got $ImageOS"
[ "$(node --version)" = 'v26.8.1' ] || env_fail 'Node version drift'
[ "$(npm --version)" = '11.19.0' ] || env_fail 'npm version drift'

export DEVELOPER_DIR=/Applications/Xcode_26.6.app
[ "$(xcodebuild -version | awk '/Build version/ { print $3 }')" = '17F113' ] || env_fail 'Xcode version drift'
xcrun --sdk macosx26.5 --show-sdk-path >/dev/null || env_fail 'macOS SDK not found'

./gradlew bridgeBuild || env_fail 'bridgeBuild failed'
probe='https://github.com/'
curl --fail --silent --show-error --output /dev/null "$probe" || env_fail 'pre-denial network probe failed'
command -v sandbox-exec >/dev/null || env_fail 'sandbox-exec is required'
profile=$(mktemp)
cleanup() {
  rm -f "$profile"
}
trap cleanup EXIT

# Sandbox profile: deny all outbound network, then re-allow loopback ONLY.
# SBPL is last-match-wins, so the loopback allow MUST follow the deny.
# Gradle 9 ALWAYS forks a single-use daemon process even under --no-daemon,
# and that daemon communicates with the client over loopback TCP; a blanket
# `(deny network-outbound)` blocks that socket and the build fails with
# 'Could not connect to the Gradle daemon'. SBPL accepts `localhost` (not a
# numeric address) with `:*` for every loopback TCP port. Allowing it at both
# local and remote ends lets the daemon control socket work while real outbound
# (e.g. curl https://github.com/) stays denied, so the pre/post network probes
# still behave identically.
printf '%s\n' \
  '(version 1)' \
  '(allow default)' \
  '(deny network-outbound)' \
  '(allow network-outbound (local ip "localhost:*") (remote ip "localhost:*"))' \
  > "$profile"
sandbox-exec -f "$profile" /usr/bin/true || env_fail 'sandbox-exec self-test failed'
if sandbox-exec -f "$profile" curl --fail --silent --show-error --output /dev/null "$probe"; then
  env_fail 'network probe succeeded after outbound denial'
fi
if [ "$#" -eq 0 ]; then set -- ./gradlew --no-daemon bridgePackage; fi
# --no-daemon is kept for parity with the Windows wrapper and to minimize
# daemon overhead, but it is no longer required for sandbox correctness:
# Gradle 9 forks a single-use daemon even with --no-daemon, and that daemon
# uses a loopback control socket which the profile above now allows. The
# Windows wrapper does not use --no-daemon: Windows firewall outbound rules
# do not block loopback, so gradlew.bat's daemon works under the Windows
# deny rule. This asymmetry is intentional and documented; the contract
# test treats the two wrappers' default package commands separately for
# that reason.
sandbox-exec -f "$profile" env CARGO_NET_OFFLINE=true "$@" || env_fail 'sandboxed package command failed'
