#!/bin/bash
# Pure validation helpers for macos-package.sh (sourced; no signing, no macOS tools needed).

# Reverse-DNS bundle identifier: at least two dot-separated labels, [A-Za-z0-9-] only.
validate_bundle_id() {
    case "$1" in
        ''|*[!A-Za-z0-9.-]*|.*|*.|*..*|-*|*-.*|*.-*) return 1 ;;
    esac
    case "$1" in *.*) return 0 ;; *) return 1 ;; esac
}

# Explicit ad-hoc ('-') or empty identities are never acceptable.
reject_adhoc_identity() {
    case "$1" in
        '-'|'') return 1 ;;
    esac
    return 0
}

# $1 = `codesign -dvv` output, $2 = expected bundle identifier.
# Requires: not ad-hoc, Identifier matches, an Apple Development / Developer ID Application leaf
# authority and an Apple root in the chain. Prints the reason on failure.
check_signature_authority() {
    local text="$1" expected="$2"
    if printf '%s\n' "$text" | grep -q 'Signature=adhoc'; then
        echo "signature is ad-hoc" >&2; return 1
    fi
    if ! printf '%s\n' "$text" | grep -qx "Identifier=$expected"; then
        echo "signed identifier does not match $expected" >&2; return 1
    fi
    local leaf
    leaf="$(printf '%s\n' "$text" | awk -F= '/^Authority=/ {print $2; exit}')"
    case "$leaf" in
        "Apple Development: "*|"Developer ID Application: "*) ;;
        *) echo "leaf authority '$leaf' is not an Apple Development or Developer ID Application certificate" >&2; return 1 ;;
    esac
    if ! printf '%s\n' "$text" | grep -q '^Authority=Apple Root CA'; then
        echo "no Apple root authority in the certificate chain" >&2; return 1
    fi
    return 0
}
