#!/bin/bash
# OTP-23 M-11 DEV packaging (private stage; parent executes ONLY after review approval).
# Packages an EXISTING raw unsigned host binary into a DISTINCT developer bundle:
#   target-dev/VelesOtp23Dev.app  (bundle id me.nagaev.veles.bridge.dev, CFBundleName VelesOtp23Dev)
# NO codesign is run: the binary keeps its automatic linker ad-hoc signature, which this script only
# RECORDS (read-only `codesign -dvv`). No Apple key, no ad-hoc substitution for the approved fixture,
# nothing outside this stage's own target-dev dir is touched. Refuses to overwrite an existing bundle.
#
# Usage: ./package-dev.sh <raw-otp23-host>            # verifies its sha256 == the pinned counterpart
# Output prints a JSON provenance record (binary sha, codesign -dvv text, bundle path, plist fields).

set -euo pipefail
STAGE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXPECTED_SHA="c36894dca9d47717410cf833ca4f181894d1144e48bc0f301232a2be307864ea"

RAW="${1:-$STAGE/source-raw/otp23-host}"
OUT="$STAGE/target-dev/VelesOtp23Dev.app"

need_jq() { command -v python3 >/dev/null 2>&1; }
if ! need_jq; then echo "error: python3 required" >&2; exit 1; fi

if [ ! -f "$RAW" ]; then
    echo "error: raw host binary not found at $RAW" >&2
    exit 1
fi
ACTUAL_SHA="$(shasum -a 256 "$RAW" | awk '{print $1}')"
if [ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]; then
    echo "error: raw binary sha $ACTUAL_SHA != pinned same-source counterpart $EXPECTED_SHA" >&2
    exit 1
fi
if [ -e "$OUT" ]; then
    echo "error: $OUT already exists — unique M-11 artifact, never silently overwritten" >&2
    exit 1
fi

mkdir -p "$STAGE/target-dev"
APP="$OUT"
mkdir -p "$APP/Contents/MacOS"
cp -f "$RAW" "$APP/Contents/MacOS/otp23-host"
chmod 755 "$APP/Contents/MacOS/otp23-host"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key>
    <string>me.nagaev.veles.bridge.dev</string>
    <key>CFBundleName</key>
    <string>VelesOtp23Dev</string>
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

# RECORD-ONLY signature readback: the automatic linker ad-hoc signature is recorded truthfully and
# no signing command exists in this script.
SIG_RAW="$(codesign -dvv "$RAW" 2>&1 | head -8 || true)"
SIG_DEV="$(codesign -dvv "$APP" 2>&1 | head -8 || true)"

python3 - "$RAW" "$APP" "$ACTUAL_SHA" <<'PYEOF'
import hashlib, json, pathlib, sys
raw, app, raw_sha = sys.argv[1], sys.argv[2], sys.argv[3]
def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()
print(json.dumps({
    "what": "OTP-23 M-11 dev bundle provenance (recorded, NOT signed)",
    "raw_binary": raw, "raw_binary_sha256": raw_sha,
    "bundle": app,
    "bundle_binary_sha256": sha(pathlib.Path(app) / "Contents/MacOS/otp23-host"),
    "info_plist": {"CFBundleIdentifier": "me.nagaev.veles.bridge.dev",
                   "CFBundleName": "VelesOtp23Dev",
                   "CFBundleExecutable": "otp23-host",
                   "usage_descriptions": "verbatim copies of the approved fixture's strings"},
    "codesign_commands_run": [],   # none: no sign, no replace, no re-sign
    "signature_policy": "automatic linker ad-hoc signature recorded truthfully; no Apple "
                        "Development codesign; never applied to the approved fixture",
    "expected_raw_sha256": "c36894dca9d47717410cf833ca4f181894d1144e48bc0f301232a2be307864ea",
}, indent=2))
PYEOF

echo "--- codesign -dvv RAW (record-only) ---"
printf '%s\n' "$SIG_RAW"
echo "--- codesign -dvv DEV bundle (record-only) ---"
printf '%s\n' "$SIG_DEV"