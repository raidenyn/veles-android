# macOS TCC verification harness (evidence only)

These files are the exact harness used for the 2026-11-16 experiment. They are evidence, not
production tooling: they are not installed, not referenced by the app, and carry only the canonical
test identity already documented in `native-bridge/installer/macos/AGENTS.md`.

Prerequisites: macOS with the Xcode command-line tools (`swiftc`), an unlocked login keychain holding
`Apple Development: yuriy@nagaev.me (ZWYYS8X98S)`, the canonical app built by
`native-bridge/host/scripts/macos-test-bundle.sh`, and a logged-in GUI session (signing over SSH can
fail with `errSecInternalComponent`).

## Driver route (used for the real host)

`driver.swift` is a small app that LaunchServices starts. It spawns the real
`veles-native-host` from inside the signed bundle, drives `hello → start_scan → stop_scan →
shutdown`, and writes the host's framed responses to a JSON file. The driver bundle keeps the
canonical identifier and certificate, so it is the responsible process for the child's Bluetooth
request.

```bash
HERE=<this directory>
APP=<output>/VelesOtp23PoC.app          # from macos-test-bundle.sh
OUT=<a new directory>
mkdir -p "$OUT/driver-out"
xcrun swiftc -O -o "$OUT/driver" "$HERE/driver.swift"
zsh "$HERE/make-driver-bundle.sh" "$OUT/driver" "$APP/Contents/MacOS/veles-native-host" "$APP" \
  "$OUT/driver-bundle"
open -n --stdout "$OUT/driver-out/stdout.log" --stderr "$OUT/driver-out/stderr.log" \
  "$OUT/driver-bundle/VelesOtp23PoC.app" \
  --args "$APP/Contents/MacOS/veles-native-host" "$OUT/driver-out/result.json" \
  "chrome-extension://abcdefghijklmnopabcdefghijklmnop/"
```

The origin must match the host's build-time allowlist: pass the default placeholder above, or the
exact `VELES_ALLOWED_ORIGINS` value the binary was built with. The result JSON contains the
transcript; `scan_state` with `status: started` means CoreBluetooth was authorized.

Capture the TCC window for the run with the predicate in
`.agents/rules/macos-test-app-tcc.md`, and judge coverage, prompt and grant-write events as the rule
describes.

## Probe route (optional, fresh grant only)

`probe.swift` reads `CBManager.authorization` before and after creating a `CBCentralManager` and
writes a small JSON result. `make-bundle.sh` builds and signs it under the canonical identifier. Use
it only to create or inspect a fresh grant; it is not a preflight requirement.

```bash
xcrun swiftc -O -o probe-v1 probe.swift
zsh make-bundle.sh ./probe-v1 v1 "$PWD/out" VelesOtp23PoC.app veles-tcc-probe \
  me.nagaev.veles.otp23.poc
open -n --stdout "$PWD/out/stdout.log" --stderr "$PWD/out/stderr.log" \
  "$PWD/out/VelesOtp23PoC.app" --args "$PWD/out/grant.json" 600
```

## Direct-SSH negative control (never a verification route)

`scan_probe.py` executes the host directly from an SSH session and drives the same protocol. It is
the negative control: TCC attributes the request to `sshd-keygen-wrapper`, so the host reports
`scan_failed` with `stage: permission_pending` even when the grant exists. Do not use this route to
claim reuse.

```bash
python3 scan_probe.py "<app>/Contents/MacOS/veles-native-host"
```
