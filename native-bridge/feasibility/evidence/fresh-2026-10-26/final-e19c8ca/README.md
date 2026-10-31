# Final ingress-fix checkpoint: e19c8ca

Application code is frozen at
`e19c8ca555b3275313cd325a12e15791a2cf894d`. This contains the **complete**
fail-closed admission fix: both `postValue` and `postMalformed` use the bounded
disconnect when the callback budget is exhausted. The earlier `cd971f0` runs
remain unchanged and do not stand in for this version.

## Software and artifacts

- `linux-clean-gates.log`: actual exported Zulu `JAVA_HOME`, Gradle launcher and
  daemon identity, the five mandatory gates and `assembleRelease`, exit 0.
- JVM result: **321 tests, 0 failures, 0 errors, 0 skipped**. Five independent
  regressions cover the lower-MTU and short-header saturation cases; restoring
  the silent `postValue` return fails three non-control tests (the mutation
  evidence is in `../ingress-final/`).
- `isolated-build.log`: the debug-only isolated APKs rebuilt successfully from
  the clean commit. The debug APK version is
  `0.0.3-29-e19c8ca-contract_otp23-desktop-alignment` (no dirty suffix).
- The unsigned release APK was rebuilt and inspected: no synthetic fixture,
  unsolicited-push or `OTP23_POC` symbols in any DEX or R8 mapping; no scan or
  location permission and no debuggable flag. Its hash is in `identity.json`.

## Foreground single-phone physical refresh

The new APK was installed as an update (`-r`, no `-g` or data clearing). Its hash
was read back on both phones. The unchanged instrumentation APK retains hash
`09280790…e0d2`.

The exact existing one-host diagnostic snippet from `submitted-cd971f0/` was
rerun with a new output prefix. The Windows native component is unchanged from
`cd971f0` (host SHA-256 `6cf72ed0…19fcda`); the **Android** consumer is the new
`e19c8ca` APK. The foreground diagnostic is supporting single-phone evidence,
never two-phone concurrency, repeated concurrent maxima, M6 or background
acceptance. This healthy-path refresh does not claim to physically inject the
malformed ingress-overload cases; those have the independent regression and
mutation evidence above.

The 6T hold fixture ended gracefully using its owned stop marker and returned
`OK (1 test)`. The owned registration and temporary task were removed. No
personal profile or normal Veles app data was changed.

**OTP-24 remains NO-GO:** the 10 Pro and macOS permission/signing matrix, the
two-phone and failure-isolation matrix, all active-link cleanup modes, M6 and
both strict background scenarios remain unresolved. Only the approved
new-account tests are non-blocking skips.
