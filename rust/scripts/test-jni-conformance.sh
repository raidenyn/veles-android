#!/usr/bin/env bash
set -euo pipefail
# Run from the repository root after `homepc ensure`. JAVA_HOME must be a JDK.
SERIAL="${1:?usage: bash rust/scripts/test-jni-conformance.sh <adb-serial>}"
PACKAGE=me.nagaev.veles.debug
./gradlew rustJniConformance assembleDebug assembleDebugAndroidTest
ABI="$(adb -s "$SERIAL" shell getprop ro.product.cpu.abi | tr -d '\r')"
case "$ABI" in arm64-v8a|x86_64) ;; *) echo "Unsupported conformance ABI: $ABI" >&2; exit 2 ;; esac
adb -s "$SERIAL" install --no-streaming -r app/build/outputs/apk/debug/app-debug.apk
adb -s "$SERIAL" install --no-streaming -r app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
TEMP=/data/local/tmp/veles-otp03-conformance.so
LIB=/data/user/0/$PACKAGE/code_cache/veles-otp03-conformance.so
cleanup() {
    adb -s "$SERIAL" shell run-as "$PACKAGE" rm -f "$LIB" || true
    adb -s "$SERIAL" shell rm -f "$TEMP" || true
}
trap cleanup EXIT
adb -s "$SERIAL" push "build/rust/conformance-jni/$ABI/libveles_crypto.so" "$TEMP"
adb -s "$SERIAL" shell run-as "$PACKAGE" mkdir -p code_cache
adb -s "$SERIAL" shell run-as "$PACKAGE" cp "$TEMP" "$LIB"
LOG=build/rust/jni-conformance.txt
adb -s "$SERIAL" shell am instrument -w \
    -e velesConformanceLibrary "$LIB" \
    -e velesRunExpiry "${VELES_RUN_EXPIRY:-false}" \
    -e class me.nagaev.veles.crypto.PairingInstrumentedTest,me.nagaev.veles.crypto.VelesCryptoInstrumentedTest \
    "$PACKAGE.test/androidx.test.runner.AndroidJUnitRunner" | tee "$LOG"
# Android's am instrument can exit 0 even when JUnit fails.
grep -q '^OK (' "$LOG"
if grep -Eq 'FAILURES!!!|INSTRUMENTATION_FAILED|Process crashed' "$LOG"; then exit 1; fi
