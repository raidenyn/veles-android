# android/AGENTS.md

The Android app node. `android/` is an independent Gradle build (`settings.gradle.kts` includes
`:app` and `../rust`); the root `./gradlew` forwards common commands. Architecture, flows, packages,
sensitive notifications and testing: `docs/android.md`.

## Signing identities (Android)

Reuse the existing debug fixture identity: `otp23-fixture.keystore` and `fixture-signing.gradle`,
git-ignored under `android/config/android_keys/`; never regenerate keys, uninstall or clear data as a
workaround. Before `adb install -r`, compare the new APK's signing-certificate fingerprint with the
installed one; record only fingerprints read from real output; never borrow debug credentials for
production. One stable key per app identity — the Android and macOS identities are separate (macOS:
`native-bridge/installer/macos/AGENTS.md`). Production signing is a separately reviewed migration; the
fixture build line and backup detail are in `docs/android.md`.

## Build & Test

Run from `android/`; root aliases cannot carry `--tests`.

```bash
./gradlew assembleDebug
./gradlew assembleRelease          # R8-minified; signed only with VELES_KEYSTORE_* set
./gradlew testDebugUnitTest
./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.RegexMessageHandlerTest"
./gradlew connectedDebugAndroidTest
./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.permissions.ui.VelesPermissionsAppTests
```

Clean assembly needs rustup/cargo and NDK r29; Node >= 22 is not needed for the APK alone. The OTP-02
consumer is `:app:protocolJvmTest`, run by `./gradlew protocolCheck` (`protocol/AGENTS.md`). Version name
and code: `docs/releasing.md` (the `androidGitVersion` format).

## Rules

- `.agents/rules/android-lint-before-push.md` — run the lint and raw-`Log` gates before pushing.

> **Run your agent from the repository root.** Scoped rules in `.agents/rules/` load only from there; if you started in this folder, read this folder's rule files listed below by hand.
