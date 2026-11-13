# OTP-04: Android installation identity and trust storage

## Implementation

`app/src/main/java/me/nagaev/veles/trust/` contains:

- `AndroidIdentityKeyStore`: a P-256 ECDSA Android Keystore key under
  `veles.installation.identity.v1`. Only public SEC1 bytes and signatures are
  returned. `sign` returns JCA DER; the authenticated session codec must perform
  the frozen protocol's low-S P1363 normalization before wire use.
- `TrustDatabase` / `RoomTrustStorage`: a separate transactional database at
  `Context.noBackupFilesDir/installation-trust.db`. Android excludes this directory
  from both Auto Backup and device transfer, independently of include/exclude XML.
  SQLite journal/WAL files live in that same excluded directory. The signing key
  is never stored in the database or backed up. No destructive migration fallback
  is enabled.
- `InstallationTrustRepository`: injectable identity, storage, clocks, and activation
  verifier interfaces. Records store installation IDs, validated uncompressed P-256
  public keys, UTF-8-bounded labels, client type, canonical enrollment transcript
  hash, pair fingerprint, state, creation/state-change/last-seen metadata, boot continuity, and
  monotonic creation readings. All record changes are atomic.

The phone-side lifecycle is `PENDING` → `COMMITTED_WAITING_ACK` → `ACTIVE`.
`activePeers` never returns an incomplete record. Commit and acknowledgement
proofs must pass the caller-supplied authenticated session verifier; no default
verifier exists. Repeated valid stages are idempotent, while skipping a stage
rejects. OTP-09/OTP-10 supply the actual session-bound MAC verification and
authenticated identity-record ingestion; this task does not implement PAKE,
transport, or the enrollment flow.

Incomplete records expire at exactly 86,400,000 elapsed milliseconds, on monotonic
clock rollback, or on boot-token mismatch. Android's `BOOT_COUNT` provides
cross-process reboot detection. A random 256-bit continuity token is persisted
atomically and rotated whenever the boot count changes; unavailable continuity fails closed. Fully active
records survive reboots. Duplicate peer IDs cannot replace trust; explicit
revocation is required first.

An existing database with a missing/different key rejects. A surviving key with
missing installation metadata also rejects. App-data clearing removes both, so a
subsequent fresh installation generates a new random ID/key and no peer trust.
Restored peer metadata cannot authorize peers without the original Keystore key.
Storage errors expose a fixed exception without parser payloads or chained causes;
this package does not log identifiers, payloads, keys, or proofs.

Safe-log evidence includes the corruption exception assertion and a manual source
scan of this package for `Log`, `VelesLog`, `println`, and `printStackTrace` calls.
Repeat this scan when modifying the package; only fixed cause-free errors may
cross the storage/Keystore boundary.

## Verification

Final verification on 2026-10-24: the complete `testDebugUnitTest` suite,
`detekt`, `lintDebug`, `assembleDebug`, and `assembleDebugAndroidTest` passed.
Lint reported warnings but no errors. All five `AndroidTrustTest` instrumentation
tests passed on a physical Android 15 device after the review fixes. The focused
JVM repository suite contains eight tests. Code review and focused re-review
resolved the transition-timestamp and random-continuity-token findings.

JVM tests cover activation ordering, proof rejection, idempotency, duplicate IDs,
key loss, pending expiry/boot changes, active survival, selective revocation,
invalid curve points and UTF-8 label bounds.

Physical Android 15 instrumentation verifies non-exportable private-key bytes,
256-bit Keystore properties, real signing/verification, stable identity across
repository/database reopen, database placement in the backup-excluded directory,
pending persistence, corruption-safe errors, missing keys, and missing metadata.
The backup test checks the Android exclusion boundary (the actual database path);
it does not claim an OEM backup/restore or device-to-device transfer was executed.

Commands (use a complete Java 21 JDK):

```sh
./gradlew testDebugUnitTest detekt
./gradlew assembleDebug assembleDebugAndroidTest
adb -s <serial> install --no-streaming -r app/build/outputs/apk/debug/app-debug.apk
adb -s <serial> install --no-streaming -r app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
adb -s <serial> shell am instrument -w -r -e class me.nagaev.veles.trust.AndroidTrustTest me.nagaev.veles.debug.test/androidx.test.runner.AndroidJUnitRunner
```

Push installation is used because streamed installation stalled over the remote
ADB connection. An unrelated running emulator was unauthorized; instrumentation
was run explicitly on the physical device instead.
