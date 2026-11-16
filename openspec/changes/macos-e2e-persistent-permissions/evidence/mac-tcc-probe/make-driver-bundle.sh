#!/bin/zsh
# GUI-session bundle build: main executable is the protocol driver, the real production host is a
# resource. The bundle keeps the canonical test identity, so the driver is the responsible process
# for TCC and the child host request is attributed to the same granted identity.
set -euo pipefail

DRIVER=$1
HOST=$2
TEMPLATE_APP=$3
OUT=$4

APPROVED_AUTHORITY="Apple Development: yuriy@nagaev.me (ZWYYS8X98S)"
APP="$OUT/VelesOtp23PoC.app"
[[ -f $DRIVER && -f $HOST ]] || { echo "missing driver or host" >&2; exit 1; }
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp -f "$DRIVER" "$APP/Contents/MacOS/veles-tcc-driver"
cp -f "$HOST" "$APP/Contents/Resources/veles-native-host"
chmod 755 "$APP/Contents/MacOS/veles-tcc-driver" "$APP/Contents/Resources/veles-native-host"
cp -f "$TEMPLATE_APP/Contents/Info.plist" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleExecutable veles-tcc-driver" "$APP/Contents/Info.plist"

MATCHES=$(printf '%s\n' "$(security find-identity -v -p codesigning 2>/dev/null)" |
    awk -v want="\"$APPROVED_AUTHORITY\"" '{ n = index($0, "\""); if (n > 0 && substr($0, n) == want) print $2 }')
COUNT=$(printf '%s' "$MATCHES" | grep -c . || true)
[[ $COUNT == 1 ]] || { echo "BLOCKED: expected exactly one '$APPROVED_AUTHORITY', found $COUNT" >&2; exit 1; }

codesign --force --sign "$MATCHES" "$APP"
codesign --verify --strict "$APP"
echo "published: $APP"
codesign -dvvv "$APP" 2>&1 | egrep '^(Identifier|CDHash=|Signature|TeamIdentifier|Authority)=' || true
codesign -d -r- "$APP" 2>&1 | tail -n +2
