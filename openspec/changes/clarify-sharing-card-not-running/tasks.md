## 1. Line mapping and its tests

- [ ] 1.1 Add the string resource `sharing_stopped` with the exact copy from the `Stopped transport line` scenario to `android/app/src/main/res/values/sharing.xml`; leave `sharing_enabled_unavailable` and `sharing_disabled` unchanged.
- [ ] 1.2 Write `SharingCardTextTest` in `android/app/src/test/java/me/nagaev/veles/sharing/` (Robolectric, `@Config(sdk = [33])`) first: it asserts the expected lines for all 14 (enabled × `SharingAvailability`) combinations (including each availability's own line from the `Availability lines` scenario), that only enabled with `Disabled` yields `[sharing_stopped]`, and that `sharing_stopped` resolves to the exact copy; run it and see it fail (a compile error before 1.3 adds the function counts as failing).
- [ ] 1.3 Add `SharingCardText.kt` with `internal fun sharingCardLines(enabled: Boolean, availability: SharingAvailability): List<Int>` per the design, and make the test pass.

## 2. Card and documentation

- [ ] 2.1 Change `SharingCard.kt` to render one `Text` per id returned by `sharingCardLines(enabled, state)`, replacing the inline `if` and `when`; leave the buttons, the `start` lambda and the `active` logic unchanged.
- [ ] 2.2 Add the exact sentence from the design's Docs decision to `docs/otp-06-transport.md` after "reopen Home and retry."

## 3. Verification

- [ ] 3.1 Run `./gradlew testDebugUnitTest` from `android/` and the Android lint gate (`./gradlew spotlessCheck detekt lintDebug` plus the raw `android.util.Log` guard from the `android / lint` job); fix every failure.
- [ ] 3.2 Run the real-phone check from the design on a real phone; if none is available, stop and ask, never report the change as tested. Build the APK from `android/` with `./gradlew --init-script config/android_keys/fixture-signing.gradle assembleDebug`, compare the new APK's signing-certificate fingerprint with the installed app's before `adb install -r`, and never uninstall or clear data as a workaround. Install the build, enable sharing and reach `Advertising`, reinstall over it with `adb install -r`, read the card (one status line, no "does not share OTPs yet", buttons Enable transport and Stop sharing present) and confirm with `dumpsys activity services` that `SharingService` is absent, press Enable transport and confirm `Advertising` with the service in the foreground, then press the card's Stop sharing button and confirm the plain `Disabled` card; record the commands, device and outputs in `apply.md`.
- [ ] 3.3 Run `python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)` and the checks listed in the `AGENTS.md` of every node it selects, then `openspec validate --all`.
