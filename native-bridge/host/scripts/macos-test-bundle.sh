#!/bin/bash
# Embed the freshly built production-source `veles-native-host` into the EXISTING approved OTP-23
# macOS test identity and sign it with that machine's existing Apple Development certificate, so
# the Task 7 CoreBluetooth adapter can be exercised under the TCC identity the earlier physical
# runs used. This is a TEST VEHICLE: it is NOT the production bundle and says nothing about the
# production identity (CI credentials, notarization, migration) -- that needs a separately
# planned migration and revalidation.
#
# The ONE approved identity:
#     bundle identifier  me.nagaev.veles.otp23.poc   (docs/desktop-ble-poc.md: the approved PoC
#                                                    identity; `me.nagaev.veles.bridge` is the
#                                                    DIFFERENT production identity and needs a
#                                                    separately planned migration/revalidation)
#     certificate        Apple Development: yuriy@nagaev.me (ZWYYS8X98S)
#     TeamIdentifier     7T39M26Z99
# The certificate name and team are copied from real recorded `codesign -dvv` output
# (native-bridge/feasibility/evidence/investigations-2026-10-27/macos/signed-bundle-readback-2026-10-27.txt,
# post-grant-2026-10-26/strict-mac-20261027/f2-macos-host-bundle-codesign.txt) -- the machine's one
# existing Apple Development identity the PoC is documented to be signed with. No recorded output
# shows the PoC identifier itself under it, so if the certificate is not found on the Mac the
# script STOPS (BLOCKED) rather than choosing anything else. There is no option to choose another
# identifier, team or certificate.
#
# Hard rules (CLAUDE.md "Stable Signing Identities"):
#   * the certificate is selected read-only from `security find-identity` by its EXACT recorded
#     name, and only if exactly one matches; none or several => BLOCKED, never a fallback;
#   * ad-hoc signing is impossible (the identity is always that named certificate);
#   * no key generation/export, no keychain or ACL changes, no TCC reset, no installer;
#   * run from the Aqua (logged-in GUI) session; over SSH `codesign` may fail with
#     errSecInternalComponent -- that is reported, never "fixed" by changing credentials.
#
# Output is NONDESTRUCTIVE and ATOMIC: everything is built and verified in a fresh, uniquely named
# staging directory created by this script inside <out>; the result is published by rename only
# after every check passed. An existing app or identity record in <out> is never touched or
# replaced (the script refuses; move it away yourself). On any failure only this run's own staging
# directory is removed, so no stale or half-built evidence can remain.
#
# Published: <out>/VelesOtp23PoC.app (Contents/MacOS/veles-native-host) and
# <out>/VelesOtp23PoC.identity.txt with the source executable hash and the post-signing
# executable identity recorded SEPARATELY (codesign rewrites the Mach-O, so the hashes differ by
# design; every value is read from real tool output at run time, none is inferred).
#
# Usage:
#   scripts/macos-test-bundle.sh [-b target/release/veles-native-host] [-o target/macos-test-bundle]
set -euo pipefail

APPROVED_BUNDLE_ID="me.nagaev.veles.otp23.poc"
APPROVED_TEAM_ID="7T39M26Z99"
APPROVED_AUTHORITY="Apple Development: yuriy@nagaev.me (ZWYYS8X98S)"
APP_NAME="VelesOtp23PoC.app"
RECORD_NAME="VelesOtp23PoC.identity.txt"
EXEC_NAME="veles-native-host"

# --- pure helpers (sourced by the shell tests; no macOS tools needed) ---------------------------

# Cryptographic trust anchor: text checks can be satisfied by a self-signed certificate whose name
# merely looks like Apple's; this runs the real requirement check ($CODESIGN overridable only so
# tests can mock it).
verify_apple_anchor() {
    "${CODESIGN:-codesign}" --verify --strict -R='anchor apple generic' "$1"
}

# $1 = `security find-identity -v -p codesigning` output. Prints the SHA-1 of the ONE identity
# whose quoted name is exactly the approved authority; fails if none or more than one matches.
select_approved_identity() {
    local hashes count
    hashes="$(printf '%s\n' "$1" | awk -v want="\"$APPROVED_AUTHORITY\"" \
        '{ n = index($0, "\""); if (n > 0 && substr($0, n) == want) print $2 }')"
    count="$(printf '%s' "$hashes" | grep -c . || true)"
    if [ "$count" != "1" ]; then
        echo "BLOCKED: expected exactly one existing '$APPROVED_AUTHORITY' signing identity, found $count. No fallback, no key creation." >&2
        return 1
    fi
    printf '%s\n' "$hashes"
}

# $1 = `codesign -dvv` output. Requires: not ad-hoc, literal Identifier and TeamIdentifier of the
# approved identity, the FIRST authority being exactly the approved certificate, and an Apple
# root in the chain. Prints the reason on failure.
check_signature_authority() {
    local text="$1" leaf
    if printf '%s\n' "$text" | grep -q 'Signature=adhoc'; then
        echo "signature is ad-hoc" >&2; return 1
    fi
    if ! printf '%s\n' "$text" | grep -qxF -- "Identifier=$APPROVED_BUNDLE_ID"; then
        echo "signed identifier is not the approved $APPROVED_BUNDLE_ID" >&2; return 1
    fi
    if ! printf '%s\n' "$text" | grep -qxF -- "TeamIdentifier=$APPROVED_TEAM_ID"; then
        echo "TeamIdentifier is not the approved team" >&2; return 1
    fi
    leaf="$(printf '%s\n' "$text" | awk '/^Authority=/ {sub(/^Authority=/, ""); print; exit}')"
    if [ "$leaf" != "$APPROVED_AUTHORITY" ]; then
        echo "leaf authority is not the approved certificate" >&2; return 1
    fi
    if ! printf '%s\n' "$text" | grep -q '^Authority=Apple Root CA'; then
        echo "no Apple root authority in the certificate chain" >&2; return 1
    fi
    return 0
}

# A SHA-256 hex digest (first field only); shasum on macOS, sha256sum elsewhere (tests).
sha256_of() {
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        sha256sum "$1" | awk '{print $1}'
    fi
}

# --- main -----------------------------------------------------------------------------------------

STAGING=""
cleanup_staging() {
    # Only the unique directory this run created (mktemp -d under <out>); the name is re-checked.
    case "$STAGING" in
        */.staging.??????*) if [ -d "$STAGING" ]; then rm -rf "$STAGING"; fi ;;
    esac
    return 0
}

main() {
    local script_dir root bin out identity_hash
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    root="$(dirname "$script_dir")"
    bin="$root/target/release/$EXEC_NAME"
    out="$root/target/macos-test-bundle"

    case "$(uname -s)" in
        Darwin) ;;
        *)
            echo "error: macos-test-bundle.sh only runs on macOS (it needs codesign and the existing Apple Development identity)" >&2
            return 2
            ;;
    esac

    while [ $# -gt 0 ]; do
        case "$1" in
            -b|--binary) bin="${2:?missing binary}"; shift 2 ;;
            -o|--out)    out="${2:?missing out dir}"; shift 2 ;;
            -h|--help)   echo "usage: macos-test-bundle.sh [-b HOST_BIN] [-o OUT_DIR]   (identity is fixed; see the header)"; return 0 ;;
            *)           echo "error: unknown argument $1 (the identity cannot be chosen)" >&2; return 1 ;;
        esac
    done

    if [ ! -f "$bin" ]; then
        echo "error: host binary not found at $bin; build it first:" >&2
        echo "  cd '$root' && cargo build --locked --release --bin $EXEC_NAME" >&2
        return 1
    fi
    mkdir -p "$out"
    # Includes the legacy record name of the earlier script version: an old `identity.txt` could
    # be mistaken for evidence of this run, so it is refused (never deleted or overwritten).
    if [ -e "$out/$APP_NAME" ] || [ -e "$out/$RECORD_NAME" ] || [ -e "$out/identity.txt" ]; then
        echo "error: $out already contains $APP_NAME, $RECORD_NAME or a legacy identity.txt; refusing to touch it. Move it away first (it may be approved evidence)." >&2
        return 1
    fi
    identity_hash="$(select_approved_identity "$(security find-identity -v -p codesigning 2>/dev/null)")" || return 1

    STAGING="$(mktemp -d "$out/.staging.XXXXXX")"
    trap cleanup_staging EXIT
    local app="$STAGING/$APP_NAME" source_sha
    source_sha="$(sha256_of "$bin")"
    mkdir -p "$app/Contents/MacOS"
    cp -f "$bin" "$app/Contents/MacOS/$EXEC_NAME"
    chmod 755 "$app/Contents/MacOS/$EXEC_NAME"

    cat > "$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key>
    <string>$APPROVED_BUNDLE_ID</string>
    <key>CFBundleName</key>
    <string>VelesOtp23PoC</string>
    <key>CFBundleExecutable</key>
    <string>$EXEC_NAME</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleShortVersionString</key>
    <string>0.1.0</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>CFBundleMinimumOSVersion</key>
    <string>13.0</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSBluetoothAlwaysUsageDescription</key>
    <string>The Veles test host connects to nearby Android test peripherals to exchange synthetic measurement records only.</string>
    <key>NSBluetoothPeripheralUsageDescription</key>
    <string>The Veles test host connects to nearby Android test peripherals to exchange synthetic measurement records only.</string>
</dict>
</plist>
PLIST

    codesign --force --sign "$identity_hash" "$app" || { echo "error: codesign failed; nothing published" >&2; return 1; }
    codesign --verify --strict "$app" || { echo "error: strict signature verification failed; nothing published" >&2; return 1; }
    verify_apple_anchor "$app" || { echo "error: signature does not satisfy 'anchor apple generic'; nothing published" >&2; return 1; }
    local dvv signed_sha cdhash dv
    dvv="$(codesign -dvv "$app" 2>&1)"
    check_signature_authority "$dvv" || { echo "error: signed identity is not the approved test identity; nothing published" >&2; return 1; }

    # Source and post-signing identities are recorded separately, from real output only.
    signed_sha="$(sha256_of "$app/Contents/MacOS/$EXEC_NAME")"
    # Capture the FULL output first: an early-exiting awk on a pipe would SIGPIPE codesign (exit 141
    # under pipefail). The parse then reads a here-string, so no producer is left writing to a closed pipe.
    dv="$(codesign -dv --verbose=4 "$app" 2>&1)" || { echo "error: codesign -dv failed; nothing published" >&2; return 1; }
    cdhash="$(awk -F= '/^CDHash=/ {print $2; exit}' <<<"$dv")"
    {
        echo "test_vehicle: true"
        echo "production_identity: UNVALIDATED (this is the approved OTP-23 test identity, not the production bundle)"
        echo "bundle: $out/$APP_NAME"
        echo "source_executable_sha256: $source_sha"
        echo "post_signing_executable_sha256: $signed_sha"
        echo "cdhash: $cdhash"
        printf '%s\n' "$dvv" | grep -E '^(Identifier|TeamIdentifier|Authority)=' || true
    } > "$STAGING/$RECORD_NAME"

    # Publish by rename (same filesystem), bundle first and the record last, so a record exists
    # only for a bundle that passed every check. Nothing existing is ever replaced or deleted:
    # `mv -n` refuses to overwrite, a directory that appeared at the destination meanwhile would
    # make `mv` nest our bundle inside it (detected and set aside), and the record is linked with
    # `ln`, which fails if the name exists.
    local run_id
    run_id="$(basename "$STAGING")"
    if ! mv -n "$app" "$out/$APP_NAME" || [ -e "$app" ]; then
        echo "error: could not publish the bundle (a path appeared in $out); nothing was replaced" >&2
        return 1
    fi
    if [ -e "$out/$APP_NAME/$APP_NAME" ]; then
        mv -n "$out/$APP_NAME/$APP_NAME" "$out/.race-nested.$run_id"
        echo "error: $APP_NAME appeared in $out during the run; our bundle was set aside as .race-nested.$run_id, the existing path is untouched, no record written" >&2
        return 1
    fi
    if ! codesign --verify --strict "$out/$APP_NAME"; then
        # Never delete a published path: set it aside under a name that cannot be mistaken for
        # evidence, and publish no record.
        mv -n "$out/$APP_NAME" "$out/.failed-verification.$run_id"
        echo "error: the published bundle failed strict verification; moved aside, no record written" >&2
        return 1
    fi
    if ! ln "$STAGING/$RECORD_NAME" "$out/$RECORD_NAME" 2>/dev/null || ! cmp -s "$STAGING/$RECORD_NAME" "$out/$RECORD_NAME"; then
        mv -n "$out/$APP_NAME" "$out/.unrecorded.$run_id"
        echo "error: could not publish the identity record (a path appeared in $out); the bundle was set aside as .unrecorded.$run_id and nothing existing was replaced" >&2
        return 1
    fi
    echo "packaged: $out/$APP_NAME" >&2
    cat "$out/$RECORD_NAME" >&2
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
