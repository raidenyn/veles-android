#!/bin/bash
# Independent shell tests for macos-package-lib.sh (no macOS tools, no signing).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/macos-package-lib.sh"
fail=0; n=0
ok()  { n=$((n+1)); }
bad() { n=$((n+1)); fail=$((fail+1)); echo "FAIL: $1" >&2; }
expect_ok()   { local d="$1"; shift; if "$@" 2>/dev/null; then ok; else bad "$d"; fi; }
expect_fail() { local d="$1"; shift; if "$@" 2>/dev/null; then bad "$d"; else ok; fi; }

# bundle ids
for id in me.nagaev.veles.bridge me.nagaev.veles.otp23.poc a.b A1.b-2; do expect_ok "valid id $id" validate_bundle_id "$id"; done
for id in "" nodots .a a. a..b -a.b a.-b "a-.b" "me nagaev.bridge" 'a.b;c' 'a.b$x' 'a.b/c' "a.b\"c" 'ünï.code'; do
    expect_fail "invalid id '$id'" validate_bundle_id "$id"; done

# identities
expect_fail "ad-hoc identity" reject_adhoc_identity "-"
expect_fail "empty identity" reject_adhoc_identity ""
expect_ok "named identity" reject_adhoc_identity "Apple Development: Name (TEAMID1234)"
expect_ok "hash identity" reject_adhoc_identity "0123456789ABCDEF0123456789ABCDEF01234567"

sig() { # id leaf [signature-line]
    printf 'Executable=/x/MacOS/otp23-host\nIdentifier=%s\nFormat=app bundle with Mach-O thin (arm64)\n%s\nAuthority=%s\nAuthority=Apple Worldwide Developer Relations Certification Authority\nAuthority=Apple Root CA\nTeamIdentifier=TEAMID1234\n' "$1" "${3:-Signature size=4800}" "$2"
}
ID=me.nagaev.veles.bridge
good="$(sig $ID 'Apple Development: Name (TEAMID1234)')"
expect_ok   "correct Apple Development chain" check_signature_authority "$good" $ID
expect_ok   "correct Developer ID chain" check_signature_authority "$(sig $ID 'Developer ID Application: Org (TEAMID1234)')" $ID
# the reproduced defect: dots must not be wildcards
expect_fail "wildcard-dot lookalike id" check_signature_authority "$(sig meXnagaevXvelesXbridge 'Apple Development: N (T)')" $ID
expect_fail "prefix-only id" check_signature_authority "$(sig me.nagaev.veles.bridge.extra 'Apple Development: N (T)')" $ID
expect_fail "suffix-only id" check_signature_authority "$(sig xme.nagaev.veles.bridge 'Apple Development: N (T)')" $ID
expect_fail "historical id when final expected" check_signature_authority "$(sig me.nagaev.veles.otp23.poc 'Apple Development: N (T)')" $ID
expect_fail "ad-hoc signature" check_signature_authority "$(sig $ID 'Apple Development: N (T)' 'Signature=adhoc')" $ID
expect_fail "missing authority" check_signature_authority "$(printf 'Identifier=%s\nSignature size=1\n' $ID)" $ID
expect_fail "self-signed leaf" check_signature_authority "$(sig $ID 'My Local Cert')" $ID
expect_fail "no Apple root" check_signature_authority "$(printf 'Identifier=%s\nAuthority=Apple Development: N (T)\nAuthority=Some Other CA\n' $ID)" $ID
# metacharacters in the expected id are literal too
expect_fail "regex metachar id" check_signature_authority "$(sig meXnagaev 'Apple Development: N (T)')" 'me.*'

# Cryptographic anchor: a mock codesign records its arguments; text checks alone cannot prove trust.
MOCK="$(mktemp -d)"; trap 'rm -rf "$MOCK"' EXIT
printf '#!/bin/bash\necho "$@" > "%s/args"\nexit "${MOCK_STATUS:-0}"\n' "$MOCK" > "$MOCK/codesign"; chmod +x "$MOCK/codesign"
export CODESIGN="$MOCK/codesign"
MOCK_STATUS=0 expect_ok "anchor satisfied" verify_apple_anchor /x/App.app
grep -q -- "--verify --strict -R=anchor apple generic /x/App.app" "$MOCK/args" && ok || bad "anchor requirement arguments: $(cat "$MOCK/args")"
MOCK_STATUS=3 expect_fail "self-signed Apple-looking chain fails the real anchor check" verify_apple_anchor /x/App.app
# a fake chain passes every TEXT check yet must still be rejected by the anchor
fake="$(sig $ID 'Apple Development: Fake (TEAMID1234)')"
check_signature_authority "$fake" $ID 2>/dev/null && ok || bad "fake chain text should pass the text helper"
MOCK_STATUS=3 expect_fail "fake chain rejected by anchor despite passing text" verify_apple_anchor /x/App.app
grep -q 'verify_apple_anchor' "$HERE/macos-package.sh" && ok || bad "packaging script must call verify_apple_anchor"

if [ "$fail" -ne 0 ]; then echo "$fail of $n checks FAILED" >&2; exit 1; fi
echo "macos-package-lib: $n checks passed"
