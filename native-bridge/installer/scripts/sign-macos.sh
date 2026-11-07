#!/usr/bin/env bash
# OTP-25 Task 13: credential-handling shim for the macOS signing stages.
#
#   sign-macos.sh --stage nested|outer --inputs DIR --output DIR --scratch DIR --reports DIR \
#                 --descriptor FILE --boundary FILE --commit SHA --recipe-digest HEX
#
# Everything that matters lives in release_gate.py (authorization, credential-presence check,
# input provenance, ephemeral keychain, signing, notarization, stapling, assessment, cleanup in a
# `finally`). This shim only selects the interpreter and forwards the arguments. Credentials are
# REFERENCES in the environment (VELES_MAC_*, VELES_NOTARY_*); they are never expanded here, never
# echoed and never placed on this script's command line. There is no unsigned fallback and no
# option that skips a check. It compiles nothing and publishes nothing.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
gate="$script_dir/release_gate.py"
[ -f "$gate" ] || { echo "sign-macos: release_gate.py not found" >&2; exit 2; }

python="$(command -v python3)" || { echo "sign-macos: python3 is required" >&2; exit 2; }

# -I: ignore PYTHON* variables and the current directory, so nothing in the working tree can shadow a module.
exec "$python" -I "$gate" sign --platform macos "$@"
