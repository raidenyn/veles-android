#!/usr/bin/env bash
set -euo pipefail

root="$(mktemp -d)"
trap 'rm -rf "$root"' EXIT
scripts="$root/verify"
bin="$root/bin"
mkdir -p "$scripts" "$bin"
touch "$root/apk.git"
mkdir "$root/native-a" "$root/native-b"
stale="$root/build/verification/SHA256SUMS.toolchains"
create_stale() {
  mkdir -p "$(dirname "$stale")"
  : > "$stale"
}
cp "$(cd "$(dirname "$0")/.." && pwd)/verify-all.sh" "$scripts/verify-all.sh"
chmod +x "$scripts/verify-all.sh"

cat > "$bin/git" <<'EOF'
#!/usr/bin/env bash
case "$1 $2" in
  'rev-parse --verify') printf '%s\n' "${FAKE_COMMIT:-0123456789abcdef0123456789abcdef01234567}" ;;
  'rev-parse HEAD') printf '%s\n' "${FAKE_HEAD:-0123456789abcdef0123456789abcdef01234567}" ;;
  'status --porcelain') printf '%s' "${FAKE_STATUS:-}" ;;
  *) exit 2 ;;
esac
EOF
chmod +x "$bin/git"

for name in verify verify-web verify-rust verify-native verify-supply-chain aggregate-checksums; do
  filename="$name.sh"
  [ "$name" = verify ] && filename=verify.sh
  cat > "$scripts/$filename" <<EOF
#!/usr/bin/env bash
printf '%s|%s\n' '$name' "\$*" >> "\$CALL_LOG"
[ "\${FAIL_COMPONENT:-}" != '$name' ] || exit "\${FAIL_STATUS:-1}"
EOF
  chmod +x "$scripts/$filename"
done

run() {
  PATH="$bin:$PATH" CALL_LOG="$root/calls" "$scripts/verify-all.sh" "$root/apk.git" main "$root/native-a" "$root/native-b"
}

run
expected=$(printf 'verify|%s 0123456789abcdef0123456789abcdef01234567\nverify-web|\nverify-rust|\nverify-native|0123456789abcdef0123456789abcdef01234567 %s %s\nverify-supply-chain|\naggregate-checksums|0123456789abcdef0123456789abcdef01234567' "$root/apk.git" "$root/native-a" "$root/native-b")
[ "$(cat "$root/calls")" = "$expected" ]

rm "$root/calls"
create_stale
if PATH="$bin:$PATH" CALL_LOG="$root/calls" FAKE_STATUS='?? untracked' "$scripts/verify-all.sh" "$root/apk.git" main "$root/native-a" "$root/native-b"; then exit 1; else [ $? -eq 2 ]; fi
[ ! -e "$root/calls" ]
[ ! -e "$stale" ]

create_stale
if PATH="$bin:$PATH" CALL_LOG="$root/calls" FAKE_STATUS=' M verify/verify-all.sh' "$scripts/verify-all.sh" "$root/apk.git" main "$root/native-a" "$root/native-b"; then exit 1; else [ $? -eq 2 ]; fi
[ ! -e "$stale" ]

create_stale
if PATH="$bin:$PATH" CALL_LOG="$root/calls" FAKE_HEAD=ffffffffffffffffffffffffffffffffffffffff "$scripts/verify-all.sh" "$root/apk.git" main "$root/native-a" "$root/native-b"; then exit 1; else [ $? -eq 2 ]; fi
[ ! -e "$stale" ]

rm -f "$root/calls"
if PATH="$bin:$PATH" CALL_LOG="$root/calls" FAIL_COMPONENT=verify-rust FAIL_STATUS=9 "$scripts/verify-all.sh" "$root/apk.git" main "$root/native-a" "$root/native-b"; then exit 1; else [ $? -eq 2 ]; fi
[ "$(cat "$root/calls")" = "$(printf 'verify|%s 0123456789abcdef0123456789abcdef01234567\nverify-web|\nverify-rust|' "$root/apk.git")" ]

rm -f "$root/calls"
if PATH="$bin:$PATH" CALL_LOG="$root/calls" FAIL_COMPONENT=verify-rust FAIL_STATUS=1 "$scripts/verify-all.sh" "$root/apk.git" main "$root/native-a" "$root/native-b"; then exit 1; else [ $? -eq 1 ]; fi

for component in verify verify-web verify-rust verify-native verify-supply-chain; do
  create_stale
  if PATH="$bin:$PATH" CALL_LOG="$root/calls" FAIL_COMPONENT="$component" FAIL_STATUS=1 "$scripts/verify-all.sh" "$root/apk.git" main "$root/native-a" "$root/native-b"; then exit 1; else [ $? -eq 1 ]; fi
  [ ! -e "$stale" ]
done

create_stale
if PATH="$bin:$PATH" CALL_LOG="$root/calls" "$scripts/verify-all.sh" only-three args here; then exit 1; else [ $? -eq 2 ]; fi
[ ! -e "$stale" ]

! grep -Eq 'aggregateChecksums|parseNative|node ' "$scripts/verify-all.sh"

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root="$(cd "$script_dir/../.." && pwd)"
# The documented `npm ci --prefix verify` prerequisite creates
# verify/node_modules/, which would violate verify-all.sh's clean-checkout
# contract. .gitignore MUST ignore it so the contract stays satisfiable
# locally while still rejecting every other untracked file.
grep -q '^/verify/node_modules/$' "$repo_root/.gitignore"

# The native offline-package wrappers (network-deny-macos.sh /
# network-deny-windows.ps1) must honor the 0/1/2 exit contract: environment,
# usage, identity, tool, and probe failures exit 2 (never 1). The behavior
# test executes the scripts with stub commands on PATH and asserts exit codes.
bash "$script_dir/native-exit-codes.test.sh"
