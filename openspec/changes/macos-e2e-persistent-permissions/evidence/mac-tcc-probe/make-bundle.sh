#!/bin/zsh
# Builds one probe/real .app carrying the canonical macOS test identity and signs it with the
# one approved Apple Development certificate. Run inside the logged-in GUI session (Keychain).
#
#   make-bundle.sh BINARY TAG OUT_ROOT [APP_NAME EXEC_NAME BUNDLE_ID]
#
# Prints the published app path, then codesign -dvvv and the designated requirement.
set -euo pipefail

BINARY=$1
TAG=$2
OUT_ROOT=$3
APP_NAME=${4:-VelesOtp23PoC.app}
EXEC_NAME=${5:-veles-native-host}
BUNDLE_ID=${6:-me.nagaev.veles.otp23.poc}

APPROVED_AUTHORITY="Apple Development: yuriy@nagaev.me (ZWYYS8X98S)"
APPROVED_TEAM_ID="7T39M26Z99"

[[ -f $BINARY ]] || { echo "error: binary not found: $BINARY" >&2; exit 1; }
mkdir -p "$OUT_ROOT"
APP="$OUT_ROOT/$APP_NAME"
STAGE="$OUT_ROOT/.stage-$TAG-$$"
rm -rf "$APP" "$STAGE"
mkdir -p "$STAGE/Contents/MacOS"
cp -f "$BINARY" "$STAGE/Contents/MacOS/$EXEC_NAME"
chmod 755 "$STAGE/Contents/MacOS/$EXEC_NAME"

cat > "$STAGE/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key>
    <string>$BUNDLE_ID</string>
    <key>CFBundleName</key>
    <string>${APP_NAME%.app}</string>
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

IDENTITY_HASH=""
MATCHES=$(printf '%s\n' "$(security find-identity -v -p codesigning 2>/dev/null)" |
    awk -v want="\"$APPROVED_AUTHORITY\"" '{ n = index($0, "\""); if (n > 0 && substr($0, n) == want) print $2 }')
COUNT=$(printf '%s' "$MATCHES" | grep -c . || true)
[[ $COUNT == 1 ]] || { echo "BLOCKED: expected exactly one '$APPROVED_AUTHORITY', found $COUNT" >&2; exit 1; }
IDENTITY_HASH=$MATCHES

mv "$STAGE" "$APP"
codesign --force --sign "$IDENTITY_HASH" "$APP"
codesign --verify --strict "$APP"
codesign --verify --strict -R='anchor apple generic' "$APP"

echo "published: $APP"
echo "exec_sha256: $(shasum -a 256 "$APP/Contents/MacOS/$EXEC_NAME" | awk '{print $1}')"
echo "--- codesign -dvvv"
codesign -dvvv "$APP" 2>&1 | egrep '^(Identifier|Format|CDHash=|Signature|TeamIdentifier|Authority)=' || true
echo "--- designated requirement"
codesign -d -r- "$APP" 2>&1 | tail -n +2
