#!/bin/bash
# Package the built plain OTP-23 host into a minimal macOS app bundle and code-sign it with an
# existing Apple Development identity. No ad-hoc signing (it would make macOS ask for the
# Bluetooth permission again after every rebuild), no keychain changes, no installer.
#
# Output: <root>/target/macos-poc/VelesOtp23PoC.app  (Contents/MacOS/otp23-host)
#
# Usage:
#   ./scripts/macos-package.sh                       # auto-detect a valid Apple Development identity
#   ./scripts/macos-package.sh -i "Apple Development: NAME (TEAMID)"
#   ./scripts/macos-package.sh -b target/release/otp23-host -o target/macos-poc
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
DEFAULT_BIN="$ROOT/target/release/otp23-host"
APP_ROOT="$ROOT/target/macos-poc"
APP_NAME="VelesOtp23PoC.app"
IDENTITY=""

case "$(uname -s)" in
    Darwin) ;;
    *)
        echo "error: macos-package.sh only runs on macOS (it needs codesign and the Apple Development identity)" >&2
        exit 2
        ;;
esac

while [ $# -gt 0 ]; do
    case "$1" in
        -i|--identity) IDENTITY="$2"; shift 2 ;;
        -b|--binary)   DEFAULT_BIN="$2"; shift 2 ;;
        -o|--out)      APP_ROOT="$2"; shift 2 ;;
        -h|--help)     echo "usage: macos-package.sh [-i IDENTITY] [-b HOST_BIN] [-o OUT_DIR]"; exit 0 ;;
        *)             echo "error: unknown argument $1" >&2; exit 1 ;;
    esac
done

if [ ! -f "$DEFAULT_BIN" ]; then
    echo "error: host binary not found at $DEFAULT_BIN; build it first:" >&2
    echo "  cd '$ROOT' && cargo build --release --bin otp23-host" >&2
    exit 1
fi

APP="$APP_ROOT/$APP_NAME"
rm -rf "$APP"                      # only the PoC's own bundle path: no broad deletion
mkdir -p "$APP/Contents/MacOS"
cp -f "$DEFAULT_BIN" "$APP/Contents/MacOS/otp23-host"
chmod 755 "$APP/Contents/MacOS/otp23-host"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key>
    <string>me.nagaev.veles.otp23.poc</string>
    <key>CFBundleName</key>
    <string>VelesOtp23PoC</string>
    <key>CFBundleExecutable</key>
    <string>otp23-host</string>
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
    <string>The Veles OTP-23 feasibility host connects to two nearby Android test peripherals to exchange synthetic measurement records only.</string>
    <key>NSBluetoothPeripheralUsageDescription</key>
    <string>The Veles OTP-23 feasibility host connects to two nearby Android test peripherals to exchange synthetic measurement records only.</string>
</dict>
</plist>
PLIST

if [ -z "$IDENTITY" ]; then
    IDENTITY="$(security find-identity -v -p codesigning 2>/dev/null \
        | awk '/"Apple Development/ {print $2; exit}')" || true
    if [ -z "$IDENTITY" ]; then
        echo "error: no valid 'Apple Development' codesigning identity found. Pass one explicitly:" >&2
        echo "  ./scripts/macos-package.sh -i 'Apple Development: Name (TEAMID)'" >&2
        echo "Ad-hoc signing is rejected on purpose; see the macOS section of README.md." >&2
        exit 1
    fi
fi

codesign --force --sign "$IDENTITY" "$APP"
codesign --verify --strict "$APP"

{
    echo "packaged: $APP"
    codesign -dv "$APP" 2>&1 | sed -n '1,3p'
    echo "next:"
    echo "  ./scripts/macos-register.sh"
    echo "  python3 scripts/macos_poc.py --out evidence/macos-poc.json"
} >&2