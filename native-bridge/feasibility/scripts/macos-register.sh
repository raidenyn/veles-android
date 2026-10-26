#!/bin/bash
# Register the packaged macOS OTP-23 PoC Native Messaging host for the CURRENT USER only.
# Writes ~/Library/Application Support/Google/Chrome/NativeMessagingHosts/me.nagaev.veles.otp23.plain.json
# pointing inside VelesOtp23PoC.app with the exact allowed origin. No admin rights, nothing
# machine-wide, no service/autostart, no TCC changes. Refuses to overwrite an unrelated
# manifest. Remove again with macos-unregister.sh.
#
# Usage: ./scripts/macos-register.sh [--app PATH]   (before: ./scripts/macos-package.sh)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 not found on PATH (macOS provides one via the Command Line Tools: xcode-select --install)" >&2
    exit 1
fi
exec python3 "$SCRIPT_DIR/macos_poc.py" register "$@"