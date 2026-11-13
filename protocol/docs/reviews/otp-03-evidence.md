# OTP-03 implementation evidence

Branch: `feat/otp-03-spake2-core`, based on `72a31ea`. This document records
implementation evidence, not release-security approval or Gate B/D acceptance.

## Design and boundaries

See [the API contract](../otp-03-api.md) and
[implementation plan](../../superpowers/plans/2026-10-24-otp-03-spake2-core.md).
The core implements RFC 9382 using exact-pinned RustCrypto arithmetic and hash
primitives; it does not assume another SPAKE2 crate implements this profile.
Kotlin and JavaScript perform marshaling and assertions only. All secret
scalars, unmasking, transcript hashing, password reduction, KDF and confirmations
are Rust-owned. No identity store, activation protocol, transport or OTP path
is added.

The eight-handle ceiling is a library resource bound, not permission to open
eight phone pairing windows: OTP-10 must enforce the frozen single-window,
single-in-flight and online-attempt/rate policy before invoking this library.
The API's 300-second lifetime is a maximum; the caller must close executions
at its actual pairing-window deadline and on disconnect/cancel.

### Conformance isolation

Fixed-scalar constructors and alternate identities are `cfg(test)` or enabled
only by the explicit `conformance` Cargo feature. Production `rustJni`,
`rustWasm`, APK assembly and `rustPackage` never enable it. Separate Gradle
tasks write test artifacts under `build/rust/conformance-{wasm,jni,target}`;
none writes them into the app's generated JNI directory or the extension's
local WASM package. Production runtime tests submit a **valid** deterministic
start command and require rejection, rather than testing only an invalid
conformance command.

The conformance-only command 127 supplies role, w, scalar and identities for
the two RFC Appendix B anchors. Command 128 supplies a scalar but uses the
**real validated code/context transform** for the two Veles anchors. Both use
the same exchange/confirm/finish dispatcher as production, not a second PAKE.

## Verification recorded so far

- `cargo test --locked --features conformance`: 21 passing tests, one separately
  invoked benchmark ignored in the regular suite (13 core/API + 8 pre-existing
  fixture tests).
- `./gradlew rustTest rustWasmConformanceTest`: production WASM round trip,
  wrong code, role/context mismatch, invalid-point fixtures, consumed state,
  entropy failure and exact 299999/300000 ms expiry boundary pass; all four
  frozen pairing vectors match pA/pB, cA/cB and Ke through local WASM. Veles
  vectors also exercise code-to-w on that boundary.
- `./gradlew rustLint`: denied-warning Clippy passes on host, WASM and Android.
- `./gradlew assembleDebug assembleDebugAndroidTest testDebugUnitTest`: pass.
- Physical arm64 Android JNI: production round trip and all four frozen
  vectors passed (two RFC anchors, two Veles anchors, real password transform).
  Dedicated full instrumentation run including a real 300-second native-clock
  expiry check: six tests passed in 300.116 seconds. After the review fixes,
  the short instrumentation run also passed (five executed, native expiry
  intentionally skipped; the native clock implementation was unchanged).
- Deterministic malformed-input campaign: 20,000 byte strings with valid API
  version/opcode prefixes, lengths through 4199, plus every truncation of a
  valid start. No panic or unbounded output; malformed input rejected. This is
  a bounded parser-fuzz smoke campaign, **not coverage-guided fuzzing**.
- Host release benchmark after the review fixes: 100 complete A+B exchanges in
  287.610434 ms, mean 2.876107 ms per complete two-party exchange. This is a development-host
  baseline, not representative-device Gate D performance acceptance.

Commands in this environment use `JAVA_HOME="$JAVA_ROOT/zulu21"` and, for
Gradle, `-Dorg.gradle.java.installations.paths="$JAVA_ROOT/zulu21"`; the system
Java installation is a JRE without javac. Direct Cargo commands should set
`CARGO_TARGET_DIR="$PWD/build/rust/target"` from the repository root to keep
generated files under the existing clean contract.

## Reproduction

```bash
./gradlew rustFormat rustLint rustTest rustWasmConformanceTest testDebugUnitTest
homepc ensure
VELES_RUN_EXPIRY=true bash rust/scripts/test-jni-conformance.sh <adb-serial>
# Above run takes at least five minutes; omitting VELES_RUN_EXPIRY skips only
# the native-clock sleep test, not the fixed-vector tests.
homepc session-end
CARGO_TARGET_DIR="$PWD/build/rust/target" cargo test --manifest-path rust/Cargo.toml \
  --release --locked pairing_benchmark_baseline -- --ignored --nocapture
```

## Erasure and integration limitations

Owned Rust scalars, password intermediates, transcript, Ke and expected MACs
use zeroizing storage; the shared point is explicitly cleared. RustCrypto
SHA/HMAC/HKDF internal state, compiler temporaries and JVM/JS copies cannot
promise complete memory erasure. Callers must erase their copies and explicitly
close abandoned handles: idle pruning is command-driven, with no autonomous
background timer. WASM checks the JS-owned typed array length before copying,
then owns and clears the bounded copied input. The API emits Ke once for the
future enrollment schedule; Ke must never be used directly as a traffic key.

The WASM evidence runs the generated web-target module under Node Web Crypto;
it does not claim a physical Chrome extension pairing session. The core is
packaged as the existing local WASM artifact; wiring a connector belongs to
OTP-07/OTP-10. Bluetooth, first-time enrollment activation, representative
Chrome/device benchmarks and external cryptographic review remain their
respective later tasks. No claim that the RFC's stable release gates passed.

## Dependency hashes

All direct dependencies are exact-pinned; `rust/Cargo.lock` pins the complete
transitive closure. `sha256sum` on cached crates matched these lockfile hashes:

| Crate | Version | Archive SHA-256 |
|---|---|---|
| p256 | 0.13.2 | `c9863ad85fa8f4460f9c48cb909d38a0d689dba1f6f6988a5e3e0d31071bcd4b` |
| sha2 | 0.10.9 | `a7507d819769d01a365ab707794a4084392c824f54a7a6a7862f8c3d0892b283` |
| hkdf | 0.12.4 | `7b5f8eb2ad728638ea2c7d47a21db23b7b58a72ed6a38256b8a1849f15fbbdf7` |
| hmac | 0.12.1 | `6c49c37c09c17a53d937dfbb742eb3a961d65a994e6bcdcf37e7399d0cc8ab5e` |
| zeroize | 1.8.2 | `b97154e67e32c85465826e8bcc1c59429aaaf107c1e4a9e53c8d8ccd5eff88d0` |
| getrandom | 0.2.17 | `ff2abc00be7fca6ebc474524697ae276ad847ad0a6b3faa4bcb027e9a4614ad0` |
| js-sys | 0.3.104 | `0e0c1080212aad755ea003d18543e8768dd432c48819efd73a7bf1e39b7a5a3a` |

## Final verification and review

Independent implementation review inspected the full tracked/untracked diff
and independently reran host tests and Clippy. It found two merge-blocking
boundary issues. Both were reproduced with failing runtime regressions before
fixing them:

1. **Pre-copy WASM bound:** an 8 MiB rejected input grew linear memory by about
   8 MiB. The export now takes `js_sys::Uint8Array` by reference, checks length
   first and copies a bounded prefix on oversize. `rustWasmMemoryBoundTest`
   requires at most two WASM pages of allocator growth for this call.
2. **Rust-owned secret response erasure:** the finish payload and FFI response
   were ordinary Vec allocations. Payloads and FFI responses now use zeroizing
   ownership; WASM copies to a JS-owned array before dropping the Rust buffer.
   The JNI oversized prefix is zeroizing too. `rustWasmResponseErasureTest`
   observes actual Ke response allocations and checks their tail after drop
   (excluding allocator free-list metadata). This test failed before the fix
   and passed after it; it is not a claim that every dependency/compiler copy
   can be scrubbed. No second review was substituted for regression evidence.

Final aggregate command passed:

```bash
./gradlew rustFormat rustLint rustTest rustWasmConformanceTest rustJniConformance \
  testDebugUnitTest assembleDebug assembleDebugAndroidTest assembleRelease protocolCheck
```

`protocolCheck` included 159 Node contract tests with freshly regenerated,
independently cross-checked crypto evidence plus Kotlin, Rust and both
TypeScript fixture consumers. `assembleRelease` includes R8 and vital lint.
The existing npm install emitted audit findings for the unchanged extension
dependency graph; dependency upgrades were not part of this cryptographic core.

`rustPackage` also passed, staging the production JNI/WASM outputs with
checksums. `rust/scripts/verify-apk-jni.sh` passed against both assembled APKs.
`nm -D --defined-only` on the production arm64 library showed exactly
`VelesCrypto_pairingCommand` and the existing `VelesCrypto_reverseBytes` JNI
exports, with no conformance-class export. Temporary conformance libraries on
the phone were removed and the home-PC session was ended.

The reviewer set aside later OTP-10 integration, autonomous pruning beyond the
documented caller lifecycle, dependency/compiler erasure limits, coverage-guided
fuzzing beyond the accurately labeled smoke campaign, pending verifier results,
and later external/physical-browser/performance release gates. These boundaries
are retained; the previously pending verifier results are recorded above.
There were no deferred minor findings. The initial review's merge-blocking
verdict predates the two verified fixes; this evidence does not assert a second
independent approval or external cryptographic audit.
