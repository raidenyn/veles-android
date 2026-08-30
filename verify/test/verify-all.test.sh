#!/usr/bin/env bash
set -euo pipefail

root="$(mktemp -d)"
trap 'rm -rf "$root"' EXIT
scripts="$root/verify"
bin="$root/bin"
mkdir -p "$scripts" "$bin"
touch "$root/apk.git"
mkdir "$root/native-a" "$root/native-b"
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
if PATH="$bin:$PATH" CALL_LOG="$root/calls" FAKE_STATUS='?? untracked' "$scripts/verify-all.sh" "$root/apk.git" main "$root/native-a" "$root/native-b"; then exit 1; else [ $? -eq 2 ]; fi
[ ! -e "$root/calls" ]

if PATH="$bin:$PATH" CALL_LOG="$root/calls" FAKE_STATUS=' M verify/verify-all.sh' "$scripts/verify-all.sh" "$root/apk.git" main "$root/native-a" "$root/native-b"; then exit 1; else [ $? -eq 2 ]; fi

if PATH="$bin:$PATH" CALL_LOG="$root/calls" FAKE_HEAD=ffffffffffffffffffffffffffffffffffffffff "$scripts/verify-all.sh" "$root/apk.git" main "$root/native-a" "$root/native-b"; then exit 1; else [ $? -eq 2 ]; fi

rm -f "$root/calls"
if PATH="$bin:$PATH" CALL_LOG="$root/calls" FAIL_COMPONENT=verify-rust FAIL_STATUS=9 "$scripts/verify-all.sh" "$root/apk.git" main "$root/native-a" "$root/native-b"; then exit 1; else [ $? -eq 2 ]; fi
[ "$(cat "$root/calls")" = "$(printf 'verify|%s 0123456789abcdef0123456789abcdef01234567\nverify-web|\nverify-rust|' "$root/apk.git")" ]

rm -f "$root/calls"
if PATH="$bin:$PATH" CALL_LOG="$root/calls" FAIL_COMPONENT=verify-rust FAIL_STATUS=1 "$scripts/verify-all.sh" "$root/apk.git" main "$root/native-a" "$root/native-b"; then exit 1; else [ $? -eq 1 ]; fi

if PATH="$bin:$PATH" CALL_LOG="$root/calls" "$scripts/verify-all.sh" only-three args here; then exit 1; else [ $? -eq 2 ]; fi

! grep -Eq 'aggregateChecksums|parseNative|SHA256SUMS|node ' "$scripts/verify-all.sh"
