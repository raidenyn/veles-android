# Clean submitted-source refresh: cd971f0

This phase uses committed checkpoint
`cd971f0aaba9f3e49e669d63b06405a02f6cd088`. Its source archive SHA-256,
verified on both desktop systems, is
`e34d279b40f1b12a0a45b474c61e32b947a02d1b5135e5fea529f971d9c667cb`.
Earlier dirty-Android and `4e9c685` observations remain unchanged in the parent
directory. No earlier APK, trace, failing run or cost measurement is relabelled.

## Current software gates

`linux-audited-gates.log` captures the actual exported `JAVA_HOME`, Gradle's
launcher and daemon JVM identity, then the required gates in the same shell:

```sh
export JAVA_HOME=/home/dev/.jdk/zulu21
./gradlew --version
./gradlew spotlessCheck detekt lintDebug testDebugUnitTest protocolCheck
```

The launcher is **Azul 21.0.12.1** and the daemon JVM is
`/home/dev/.jdk/zulu21`. The gates returned 0. The normal JVM suite has
**316 tests, 0 failures, 0 errors, 0 skipped**. The old `post-review/jdk.txt`
captured PATH's Ubuntu Java; it does not identify the Gradle runtime and must
not substantiate a Zulu claim.

`ci-log-guards.sh` is byte-for-byte the de-indented guard block in `ci.yml`.
`ci-log-guards-verbatim.log` is its actual `bash -v` execution, including both
grep commands, source text, no matching output, and `guard_exit_code=0`.
The old `post-review/log-guards.log` has only a header and exit marker; it is
retained but is not a saved verbatim script.

Native Windows and macOS `cargo test --locked`,
`cargo clippy --locked --all-targets -- -D warnings`, and
`cargo build --release --locked --bin otp23-host` were rerun against this
archive on the actual systems, successfully. Ignored radio tests are not
counted as passes. Native logs and the Windows command-exit status are retained.

## Physical scope

- Stable Windows Chrome reran the 12 scan/lifecycle cases successfully on the
  new native binary. The existing account is an administrator with a filtered
  medium-integrity token, not a standard-account test.
- The signed macOS final-identifier fixture reran the new cryptographic Apple
  anchor check and launched from Chrome, then scan returned `access_denied`.
  This is Apple Development signing, not CI/distribution provenance or an
  allow/deny/recovery PASS. No TCC reset or bypass was used.
- Both phones installed the new **clean** isolated APK as an update, without
  `-g` and without clearing app data or attempting a permission grant. The APK
  hash read back on each phone matches the Linux-built artifact.
- The 10 Pro still reports both Bluetooth permissions denied and its fixture
  fails `PermissionRequired`. The 6T now reports both permissions granted;
  the grant mechanism was not observed. Its first bounded 10-second attempt
  was stopped before a result, then a fresh hold reached `otp23-ready`.
- A Windows `--phone` diagnostic then discovered one peripheral in CH-05 but
  failed PH-01 because its later scan found no phone within 30 seconds. That
  failure is retained, its cause is not assumed, and it is not a two-phone
  test. Its failure is not relabelled by the later isolated result.
- A subsequent **isolated one-host, one-scan, one-phone** diagnostic passed:
  byte-exact responses at 4, 508, 509, 1016, 1017, 4095 and 4096 bytes, measured
  ATT MTU 517, seven sends and seven replies in the native trace, explicit
  disconnect and shutdown, no surviving host. See
  `windows/isolated-plain-single-phone.json`, its native trace and the exact
  execution snippet. This is supporting evidence, not two-phone concurrency,
  repeated concurrent maxima, M6 or background acceptance. The earlier failure
  was not reproduced under isolation, but its cause remains unproven.
- The 6T fixture was then stopped with its owned `otp23-stop` marker and
  returned `OK (1 test)`; the 10 Pro fixture application was stopped. Temporary
  registrations and scheduled tasks were removed, and both device sessions
  ended. The isolated manifest copy has its username path scrubbed, so it is
  evidence, not a directly installable manifest.

**OTP-24 remains NO-GO.** No new-account test is required by the approved waiver.
The other phone's permission, macOS permission/signing matrix, concurrent
maximum records, failure isolation, all link-active teardown modes, M6 and both
strict background scenarios remain unresolved. No background measurement was
started or interpreted as acceptance.
