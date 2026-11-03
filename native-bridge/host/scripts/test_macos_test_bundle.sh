#!/bin/bash
# Shell tests for macos-test-bundle.sh: the pure helpers and the whole script against PATH shims
# of uname/security/codesign (no macOS, no signing, no keychain, no real identity involved).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/macos-test-bundle.sh"
. "$SCRIPT"
set +e +o pipefail  # the sourced script sets -e; the checks below expect failures
fail=0; n=0
ok()  { n=$((n+1)); }
bad() { n=$((n+1)); fail=$((fail+1)); echo "FAIL: $1" >&2; }
expect_ok()   { local d="$1"; shift; if "$@" 2>/dev/null; then ok; else bad "$d"; fi; }
expect_fail() { local d="$1"; shift; if "$@" 2>/dev/null; then bad "$d"; else ok; fi; }

ID=me.nagaev.veles.otp23.poc; TEAM=7T39M26Z99; AUTH="Apple Development: yuriy@nagaev.me (ZWYYS8X98S)"
sig() { # id team leaf [signature-line]
    printf 'Executable=/x/MacOS/veles-native-host\nIdentifier=%s\nFormat=app bundle with Mach-O thin (arm64)\n%s\nAuthority=%s\nAuthority=Apple Worldwide Developer Relations Certification Authority\nAuthority=Apple Root CA\nTeamIdentifier=%s\n' "$1" "${4:-Signature size=4800}" "$3" "$2"
}

# --- helpers ---
expect_ok   "approved chain" check_signature_authority "$(sig $ID $TEAM "$AUTH")"
expect_fail "other team" check_signature_authority "$(sig $ID OTHERTEAM1 "$AUTH")"
expect_fail "team prefix only" check_signature_authority "$(sig $ID ${TEAM}9 "$AUTH")"
expect_fail "production identifier is not accepted" check_signature_authority "$(sig me.nagaev.veles.bridge $TEAM "$AUTH")"
expect_fail "dev production identifier" check_signature_authority "$(sig me.nagaev.veles.bridge.dev $TEAM "$AUTH")"
expect_fail "wildcard-dot lookalike id" check_signature_authority "$(sig meXnagaevXvelesXbridge $TEAM "$AUTH")"
expect_fail "dev identifier" check_signature_authority "$(sig $ID.dev $TEAM "$AUTH")"
expect_fail "other Apple Development cert" check_signature_authority "$(sig $ID $TEAM 'Apple Development: other@example.com (AAAAAAAAAA)')"
expect_fail "Developer ID leaf" check_signature_authority "$(sig $ID $TEAM 'Developer ID Application: Org (X)')"
expect_fail "ad-hoc signature" check_signature_authority "$(sig $ID $TEAM "$AUTH" 'Signature=adhoc')"
expect_fail "no Apple root" check_signature_authority "$(printf 'Identifier=%s\nAuthority=%s\nTeamIdentifier=%s\n' $ID "$AUTH" $TEAM)"
expect_fail "anchor check failure" env CODESIGN=/bin/false bash -c ". '$SCRIPT'; verify_apple_anchor /x"

one="   1) AAAA1111AAAA1111AAAA1111AAAA1111AAAA1111 \"$AUTH\"
     1 valid identities found"
[ "$(select_approved_identity "$one" 2>/dev/null)" = "AAAA1111AAAA1111AAAA1111AAAA1111AAAA1111" ] && ok || bad "select exact identity"
expect_fail "no identity" select_approved_identity "     0 valid identities found"
expect_fail "other identity only" select_approved_identity "   1) BBBB \"Apple Development: other@example.com (AAAAAAAAAA)\""
expect_fail "suffix lookalike" select_approved_identity "   1) BBBB \"$AUTH extra\""
expect_fail "two matches" select_approved_identity "   1) AAAA \"$AUTH\"
   2) CCCC \"$AUTH\""

# --- whole script against shims ---
SHIMS="$(mktemp -d)"; LOG="$SHIMS/calls.log"
cat > "$SHIMS/uname" <<'S'
#!/bin/sh
echo Darwin
S
cat > "$SHIMS/security" <<'S'
#!/bin/sh
case "$SHIM_IDENTITIES" in
    none) echo "     0 valid identities found" ;;
    two) printf '   1) AAAA1111 "%s"\n   2) BBBB2222 "%s"\n' "$SHIM_AUTH" "$SHIM_AUTH" ;;
    other) printf '   1) CCCC3333 "Apple Development: other@example.com (AAAAAAAAAA)"\n' ;;
    *) printf '   1) AAAA1111 "%s"\n     1 valid identities found\n' "$SHIM_AUTH" ;;
esac
S
cat > "$SHIMS/codesign" <<'S'
#!/bin/sh
echo "codesign $*" >> "$SHIM_LOG"
case "$1" in
    --force) [ "$SHIM_MODE" = sign_fail ] && exit 1; exit 0 ;;
    --verify) if [ "$SHIM_MODE" = verify_fail ]; then exit 1; fi
              if [ "$SHIM_MODE" = verify_fail_after_publish ] && [ -n "${SHIM_PUBLISHED:-}" ] && [ -d "$SHIM_PUBLISHED" ]; then exit 1; fi
              exit 0 ;;
    -dvv) team=7T39M26Z99
          [ "$SHIM_MODE" = wrong_team ] && team=OTHERTEAM1
          printf 'Identifier=me.nagaev.veles.otp23.poc\nFormat=app bundle\nAuthority=%s\nAuthority=Apple Worldwide Developer Relations Certification Authority\nAuthority=Apple Root CA\nTeamIdentifier=%s\n' "$SHIM_AUTH" "$team" >&2 ;;
    -dv) echo "CDHash=0123456789abcdef" >&2
         # Real `codesign -dv --verbose=4` keeps writing after the CDHash line. Emit far more than a
         # pipe buffer (64 KiB) so a consumer that exits early at CDHash= makes this producer take
         # SIGPIPE (exit 141) -- a deterministic regression for the pipefail failure seen on macOS.
         i=0; while [ "$i" -lt 4000 ]; do echo "Hash choices=sha256 padding-line-$i-0123456789abcdef0123456789abcdef" >&2; i=$((i+1)); done
         # Publication races: something appears in the output dir after the preflight.
         case "$SHIM_RACE" in
             app) mkdir -p "$SHIM_OUT/VelesOtp23PoC.app"; echo racer > "$SHIM_OUT/VelesOtp23PoC.app/marker" ;;
             record) echo racer > "$SHIM_OUT/VelesOtp23PoC.identity.txt" ;;
         esac ;;
esac
exit 0
S
chmod +x "$SHIMS"/uname "$SHIMS"/security "$SHIMS"/codesign
export SHIM_AUTH="$AUTH" SHIM_LOG="$LOG"
BIN="$SHIMS/host-bin"; printf 'host-binary-contents' > "$BIN"

run() { # mode identities out [extra args...]
    local mode="$1" ids="$2" out="$3"; shift 3
    SHIM_RACE="${SHIM_RACE:-}" SHIM_OUT="$out" SHIM_MODE="$mode" SHIM_IDENTITIES="$ids" SHIM_PUBLISHED="$out/VelesOtp23PoC.app" PATH="$SHIMS:$PATH" bash "$SCRIPT" -b "$BIN" -o "$out" "$@" >"$SHIMS/stdout" 2>"$SHIMS/stderr"
}
leftovers() { ls -A "$1" | grep -c '^\.staging\.' || true; }

# success: published app + record, staging gone, signed with the selected hash, never ad-hoc
OUT="$SHIMS/o1"; : > "$LOG"
if run good one "$OUT"; then ok; else bad "success run exit"; fi
[ -d "$OUT/VelesOtp23PoC.app/Contents/MacOS" ] && ok || bad "app published"
[ "$(leftovers "$OUT")" = 0 ] && ok || bad "no staging left after success"
grep -q "^post_signing_executable_sha256: " "$OUT/VelesOtp23PoC.identity.txt" && grep -q "^source_executable_sha256: " "$OUT/VelesOtp23PoC.identity.txt" && ok || bad "record has separate hashes"
grep -q "production_identity: UNVALIDATED" "$OUT/VelesOtp23PoC.identity.txt" && ok || bad "record labelled unvalidated"
grep -q -- "--sign AAAA1111 " "$LOG" && ! grep -q -- "--sign - " "$LOG" && ok || bad "signed with the selected identity only"
grep -q "<string>me.nagaev.veles.otp23.poc</string>" "$OUT/VelesOtp23PoC.app/Contents/Info.plist" && ! grep -q "me.nagaev.veles.bridge" "$OUT/VelesOtp23PoC.app/Contents/Info.plist" && ok || bad "approved PoC identifier (not production) in plist"

# refuses to touch an existing app or a stale record (nothing deleted, nothing changed)
OUT="$SHIMS/o2"; mkdir -p "$OUT/VelesOtp23PoC.app"; echo keep > "$OUT/VelesOtp23PoC.app/marker"
if run good one "$OUT"; then bad "existing app must be refused"; else ok; fi
[ "$(cat "$OUT/VelesOtp23PoC.app/marker")" = keep ] && ok || bad "existing app untouched"
OUT="$SHIMS/o3"; mkdir -p "$OUT"; echo stale > "$OUT/VelesOtp23PoC.identity.txt"
if run good one "$OUT"; then bad "stale record must be refused"; else ok; fi
[ "$(cat "$OUT/VelesOtp23PoC.identity.txt")" = stale ] && [ ! -e "$OUT/VelesOtp23PoC.app" ] && ok || bad "stale record untouched, nothing published"

# legacy record from the earlier script version is refused, untouched
OUT="$SHIMS/o4"; mkdir -p "$OUT"; echo old-evidence > "$OUT/identity.txt"
if run good one "$OUT"; then bad "legacy identity.txt must be refused"; else ok; fi
[ "$(cat "$OUT/identity.txt")" = old-evidence ] && [ ! -e "$OUT/VelesOtp23PoC.app" ] && [ ! -e "$OUT/VelesOtp23PoC.identity.txt" ] && [ "$(leftovers "$OUT")" = 0 ] && ok || bad "legacy identity.txt untouched, nothing published"

# publication race on the app: a directory appears after preflight -> never replaced, ours set aside
OUT="$SHIMS/r1"; mkdir -p "$OUT"
if SHIM_RACE=app run good one "$OUT"; then bad "app race must fail"; else ok; fi
[ "$(cat "$OUT/VelesOtp23PoC.app/marker")" = racer ] && [ ! -e "$OUT/VelesOtp23PoC.identity.txt" ] && ok || bad "app race: racer untouched, no record"
ls -A "$OUT" | grep -q '^\.race-nested\.' && [ ! -e "$OUT/VelesOtp23PoC.app/VelesOtp23PoC.app" ] && ok || bad "app race: our bundle set aside, not nested"
[ "$(leftovers "$OUT")" = 0 ] && ok || bad "app race left staging"
# publication race on the record: a record appears after preflight -> not overwritten, bundle set aside, no misleading success
OUT="$SHIMS/r2"; mkdir -p "$OUT"
if SHIM_RACE=record run good one "$OUT"; then bad "record race must fail"; else ok; fi
[ "$(cat "$OUT/VelesOtp23PoC.identity.txt")" = racer ] && [ ! -e "$OUT/VelesOtp23PoC.app" ] && ls -A "$OUT" | grep -q '^\.unrecorded\.' && ok || bad "record race: racer untouched, bundle set aside"
grep -q "^packaged:" "$SHIMS/stderr" && bad "record race printed success" || ok
[ "$(leftovers "$OUT")" = 0 ] && ok || bad "record race left staging"

# failures publish nothing, leave no staging and no record, and never disturb a sibling app
for mode in sign_fail verify_fail wrong_team; do
    OUT="$SHIMS/f-$mode"; mkdir -p "$OUT/sibling.app"; echo keep > "$OUT/sibling.app/m"
    if run "$mode" one "$OUT"; then bad "$mode must fail"; else ok; fi
    [ ! -e "$OUT/VelesOtp23PoC.app" ] && [ ! -e "$OUT/VelesOtp23PoC.identity.txt" ] && ok || bad "$mode published something"
    [ "$(leftovers "$OUT")" = 0 ] && ok || bad "$mode left staging"
    [ "$(cat "$OUT/sibling.app/m")" = keep ] && ok || bad "$mode touched a sibling"
done

# post-publish verification failure: set aside (not deleted), no record
OUT="$SHIMS/f-post"; mkdir -p "$OUT"
if run verify_fail_after_publish one "$OUT"; then bad "post-publish verify failure must fail"; else ok; fi
[ ! -e "$OUT/VelesOtp23PoC.app" ] && [ ! -e "$OUT/VelesOtp23PoC.identity.txt" ] && ls -A "$OUT" | grep -q '^\.failed-verification\.' && ok || bad "post-publish failure handling"

# missing / duplicate / foreign identity: BLOCKED, nothing created beyond the empty out dir
for ids in none two other; do
    OUT="$SHIMS/b-$ids"
    if run good "$ids" "$OUT"; then bad "$ids identities must block"; else ok; fi
    [ -z "$(ls -A "$OUT")" ] && ok || bad "$ids left files"
    grep -q "BLOCKED" "$SHIMS/stderr" && ok || bad "$ids reports BLOCKED"
done
# no way to choose another identity
OUT="$SHIMS/b-arg"
if run good one "$OUT" -i "Apple Development: x (Y)"; then bad "-i must not exist"; else ok; fi
if run good one "$OUT" --bundle-id me.nagaev.veles.otp23.poc; then bad "--bundle-id must not exist"; else ok; fi
# not macOS: exit 2
printf '#!/bin/sh\necho Linux\n' > "$SHIMS/uname"
SHIM_MODE=good SHIM_IDENTITIES=one PATH="$SHIMS:$PATH" bash "$SCRIPT" -b "$BIN" -o "$SHIMS/o9" >/dev/null 2>&1; [ $? -eq 2 ] && ok || bad "non-macOS exit code"

# vendored bluest patch is pinned: the recorded hashes match the files
VENDOR="$HERE/../vendor/bluest"
while read -r hash path; do
    [ "$(sha256_of "$VENDOR/$path")" = "$hash" ] && ok || bad "vendored $path does not match VELES-PATCH.md"
    grep -q "$hash" "$VENDOR/VELES-PATCH.md" && ok || bad "VELES-PATCH.md lacks the hash of $path"
done <<'H'
0d88ea8bbb86c41325bc27990d9c81811881141b9d5a04ce2d40efeb47d5122c src/corebluetooth/characteristic.rs
b561908bdfc075c61732f41207a0bc42812cbaef723408674ddbbc72c54c0e5d src/lib.rs
H

rm -rf "$SHIMS"
echo "macos-test-bundle tests: $n checks, $fail failed"
[ "$fail" -eq 0 ]
