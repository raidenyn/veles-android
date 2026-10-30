#!/bin/bash
# Unregister the macOS OTP-23 PoC Native Messaging host: removes (or restores the backup of)
# ONLY me.nagaev.veles.otp23.plain.json beside the user's Chrome NativeMessagingHosts directory.
# It never touches other hosts, the PoC app bundle under target/macos-poc (delete it yourself
# if unwanted), or Bluetooth/TCC grants.
#
# Usage: ./scripts/macos-unregister.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 not found on PATH (macOS provides one via the Command Line Tools: xcode-select --install)" >&2
    exit 1
fi
exec python3 "$SCRIPT_DIR/macos_poc.py" unregister "$@"