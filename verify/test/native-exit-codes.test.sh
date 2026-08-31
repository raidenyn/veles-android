#!/usr/bin/env bash
# Behavior tests for the native offline-package wrapper exit-code contract.
# Exercises verify/native/network-deny-macos.sh and (when pwsh is available)
# verify/native/network-deny-windows.ps1 by executing them with stub commands
# on PATH, asserting the 0/1/2 contract: 0 = match/success, 1 = mismatch,
# 2 = environment/usage/identity/tool/probe failure. No source-regex checks.
#
# macOS wrapper paths covered:
#   - missing required env var (ImageOS/ImageVersion/RUNNER_ARCH) -> exit 2
#   - pre-denial network probe failure -> exit 2
#
# Windows wrapper: pwsh is not installed on this Linux verifier host, so the
# Windows script is exercised only when pwsh is on PATH. When pwsh is absent,
# this script skips the Windows behavior tests and the structural/contract
# assertions in native-environment-contract.test.mjs remain the authoritative
# Windows coverage (documented in the fix report).
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root="$(cd "$script_dir/../.." && pwd)"
macos_script="$repo_root/verify/native/network-deny-macos.sh"
windows_script="$repo_root/verify/native/network-deny-windows.ps1"

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# macOS wrapper: missing required env var must exit 2 (not 1).
# ---------------------------------------------------------------------------
status=$( set +e; bash "$macos_script" >/dev/null 2>&1; echo $? ) # no ImageOS/ImageVersion/RUNNER_ARCH
status=${status##*$'\n'}; status=$(printf '%s' "$status" | tail -1)
[ "$status" -eq 2 ] || fail "macOS wrapper with missing env vars must exit 2, got $status"
# Also confirm none of the three individually: setting only ImageOS still fails
# on ImageVersion.
status=$( ImageOS=macos26 bash "$macos_script" >/dev/null 2>&1; echo $? )
[ "$status" -eq 2 ] || fail "macOS wrapper with only ImageOS must exit 2, got $status"

# ---------------------------------------------------------------------------
# macOS wrapper: pre-denial network probe failure must exit 2.
# ---------------------------------------------------------------------------
run_macos_with_stubs() {
  # $1 = curl exit code (1 simulates a failed pre-denial probe)
  local curl_exit=$1
  local tmp
  tmp=$(mktemp -d)
  mkdir -p "$tmp/bin"
  cat >"$tmp/bin/node" <<NODE
#!/usr/bin/env bash
[ "\$1" = '--version' ] && printf 'v26.8.1\n' && exit 0
exit 0
NODE
  cat >"$tmp/bin/npm" <<NPM
#!/usr/bin/env bash
[ "\$1" = '--version' ] && printf '11.19.0\n' && exit 0
exit 0
NPM
  cat >"$tmp/bin/xcodebuild" <<XC
#!/usr/bin/env bash
printf 'Xcode 26.6\nBuild version 17F113\n'
XC
  cat >"$tmp/bin/xcrun" <<XCR
#!/usr/bin/env bash
exit 0
XCR
  cat >"$tmp/bin/curl" <<CURL
#!/usr/bin/env bash
exit $curl_exit
CURL
  cat >"$tmp/gradlew" <<GRADLE
#!/usr/bin/env bash
exit 0
GRADLE
  chmod +x "$tmp/bin/node" "$tmp/bin/npm" "$tmp/bin/xcodebuild" "$tmp/bin/xcrun" "$tmp/bin/curl" "$tmp/gradlew"
  local rc
  (
    cd "$tmp"
    ImageOS=macos26 ImageVersion=26.6 RUNNER_ARCH=ARM64 DEVELOPER_DIR=/Applications/Xcode_26.6.app \
      PATH="$tmp/bin:$PATH" bash "$macos_script" ./gradlew bridgeBuild >/dev/null 2>&1
  )
  rc=$?
  rm -rf "$tmp"
  return "$rc"
}

status=$( set +e; run_macos_with_stubs 1; echo $? )
[ "$status" -eq 2 ] || fail "macOS wrapper pre-denial probe failure must exit 2, got $status"

# ---------------------------------------------------------------------------
# Windows wrapper: only run behavior tests when pwsh is available.
# ---------------------------------------------------------------------------
if command -v pwsh >/dev/null 2>&1; then
  run_windows_with_stubs() {
    # $1 = exit code the stub node should force for the version check, or 0
    #       to simulate a passing identity gate. The probe failure is simulated
    #       by a stub Invoke-WebRequest substitute is not feasible without
    #       shadowing the cmdlet, so the behavior test focuses on the env-var
    #       gate (which must exit 2 before any tool runs).
    local node_version_out=$1
    local tmp
    tmp=$(mktemp -d)
    mkdir -p "$tmp/bin"
    cat >"$tmp/bin/node" <<NODE
#!/usr/bin/env bash
[ "\$1" = '--version' ] && printf '%s\n' "$node_version_out" && exit 0
exit 0
NODE
    cat >"$tmp/bin/npm" <<NPM
#!/usr/bin/env bash
[ "\$1" = '--version' ] && printf '11.19.0\n' && exit 0
exit 0
NPM
    chmod +x "$tmp/bin/node" "$tmp/bin/npm"
    local rc
    (
      ImageOS=win25 ImageVersion=2025 RUNNER_ARCH=X64 \
        PATH="$tmp/bin:$PATH" pwsh -NoProfile -File "$windows_script" -TauriCachePath "$tmp/.tauri" >/dev/null 2>&1
    )
    rc=$?
    rm -rf "$tmp"
    return "$rc"
  }

  # Missing env var -> exit 2 (no ImageOS).
  status=$( ImageVersion=2025 RUNNER_ARCH=X64 PATH="$PATH" pwsh -NoProfile -File "$windows_script" -TauriCachePath /tmp/.tauri-unused >/dev/null 2>&1; echo $? )
  [ "$status" -eq 2 ] || fail "Windows wrapper with missing ImageOS must exit 2, got $status"

  # Node version drift -> exit 2.
  status=$( set +e; run_windows_with_stubs 'v99.0.0'; echo $? )
  [ "$status" -eq 2 ] || fail "Windows wrapper Node version drift must exit 2, got $status"
else
  printf 'SKIP Windows wrapper behavior tests (pwsh not available on this host); structural assertions in native-environment-contract.test.mjs remain authoritative.\n'
fi

echo 'PASS native wrapper exit-code behavior contract'