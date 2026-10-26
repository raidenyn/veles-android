#!/bin/bash
# Pure validation helpers for macos-package.sh (sourced; no signing, no macOS tools needed).

# Reverse-DNS bundle identifier: at least two dot-separated labels, [A-Za-z0-9-] only.
validate_bundle_id() {
    # Bracket ranges follow the caller's locale collation (on macOS [A-Z] can match accented letters), so
    # pin the ASCII/C locale for this function only. Function-local, Bash 3.2 compatible, validation unchanged.
    local LC_ALL=C
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

# Cryptographic trust anchor: the text checks below can be satisfied by a self-signed certificate whose
# name merely looks like Apple's. This runs the real requirement check, which only passes when the
# signature chains to the genuine Apple root ("$CODESIGN" is overridable only so tests can mock it).
verify_apple_anchor() {
    "${CODESIGN:-codesign}" --verify --strict -R='anchor apple generic' "$1"
}

# $1 = `codesign -dvv` output (a TEXT check of identity and class; it cannot establish trust by itself,
# see verify_apple_anchor), $2 = expected bundle identifier.
# Requires: not ad-hoc, Identifier matches, an Apple Development / Developer ID Application leaf
# authority and an Apple root in the chain. Prints the reason on failure.
check_signature_authority() {
    local text="$1" expected="$2"
    if printf '%s\n' "$text" | grep -q 'Signature=adhoc'; then
        echo "signature is ad-hoc" >&2; return 1
    fi
    # Literal whole-line match: the identifier contains dots, which a regex would treat as wildcards.
    if ! printf '%s\n' "$text" | grep -qxF -- "Identifier=$expected"; then
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
