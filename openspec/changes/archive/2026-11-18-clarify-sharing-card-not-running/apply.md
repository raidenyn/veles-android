# Apply Receipt

**Change**: `clarify-sharing-card-not-running`
**Iteration**: `1`
**Applied at**: `2026-11-18 09:55`
**Executor**: `executing-plans` (inline in the session; one read-only reviewer subagent for the implementation, see below)

---

## Workspace

- **Worktree**: the session worktree `/home/dev/.t3/worktrees/veles-android/t3-16a329fe-c9c1782ab8095f0e` (already isolated; no second worktree was created)
- **Branch**: `chore/openspec-ff-change`

---

## Commits

- **Range**: `5776cdf..HEAD` (the scaffold commit, two implementation commits and this receipt's commit)
- **Count**: `4`

---

## Tasks

- **Completed**: `8 of 8` checkboxes in tasks.md flipped to `- [x]`
- **Remaining**: none

---

## Evidence

### Unit tests and lint (tasks 1.2, 1.3, 3.1)

- Red first: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.sharing.SharingCardTextTest"` failed to compile on `sharingCardLines` and `R.string.sharing_stopped`.
- Green: the same command passed, 7 tests, 0 failures; then the full `./gradlew testDebugUnitTest` passed.
- `./gradlew spotlessCheck detekt lintDebug` passed (no lint finding in the changed files); the raw `android.util.Log` guard printed nothing.
- Environment note: the container's `/usr/lib/jvm` has only a JRE, so the build ran with `JAVA_HOME=/home/dev/.java/jdk-21.0.13+9`.

### Real-phone check on a OnePlus 6T (task 3.2)

- **Device**: OnePlus 6T (`ONEPLUS A6013`), Android 15, adb serial `2be02e41`; package `me.nagaev.veles.otp23` (the fixture identity).
- **Build and signing**: `./gradlew --init-script config/android_keys/fixture-signing.gradle assembleDebug` from `android/`, with the git-ignored fixture key copied (never committed) from the main checkout; the key files were byte-identical to the originals (`cmp`). Before installing, `apksigner verify --print-certs` gave the same signer certificate SHA-256 for the installed APK and the new APK: `9ef3104b934a37b111726ff91c7cb5ec5affc0e8b6a90742ed0bc55501969df9`. The app was never uninstalled and its data never cleared.
- **Install**: the APK was pushed once to `/data/local/tmp` (the adb link is slow) and installed with `pm install -r`, which printed `Success`.
- **After the first `-r`** (the preference was still on from an earlier run): `dumpsys activity services me.nagaev.veles.otp23` listed only `NotificationListener` (0 `SharingService` records). The card showed the title and one status line: "Sharing is on, but the Bluetooth service is not running. This can happen after an app update, a restart or the system stopping the app. Tap Enable transport to start it again, or Stop sharing to turn it off.", with the buttons Enable transport and Stop sharing; no "does not share OTPs yet".
- **Enable transport**: the card read "Available over Bluetooth. OTP sharing is not enabled." (`Advertising`) with the Stop sharing button; `dumpsys` showed `SharingService` with `isForeground=true foregroundId=204 types=0x00000010` (connectedDevice).
- **Reinstall while advertising** (`pm install -r` again, `Success`): 0 `SharingService` records; after reopening the app the card showed the same single stopped line and both buttons.
- **The stopped card's Stop sharing text button**: the card changed to "Disabled. This transport does not share OTPs yet." with Enable transport. After `am force-stop` and reopening the app, the stopped line did not return (plain `Disabled` card).
- **Main Stop sharing button**: Enable transport reached `Advertising` again (1 foreground service); pressing Stop sharing gave the plain `Disabled` card and 0 `SharingService` records.
- The pushed APK was removed from the phone afterwards.
- Not run: the OnePlus 10 Pro (also attached) and a long-lived-session scenario; the change touches only the card's text lines.

### Repository checks (task 3.3)

- `python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)` selected `android`, `protocol` and `docs`.
- `android`: the unit tests and the lint gate above. `protocol`: `./gradlew rustHostJni protocolCheck` passed. `docs`: `python3 docs/checks/check_agent_files.py --root .` exited 0 and `python3 -m unittest discover -s docs/checks -p 'test_*.py'` passed (112 tests).
- `openspec validate --all`: 28 passed, 0 failed.

---

### Implementation review

One read-only reviewer subagent (Opus) reviewed `fff30b6` and `09194b5` against the spec, design and the original card logic: all 14 combinations match, the strings and the docs sentence are exact, the tests do not use a clock; verdict approve. Two low findings, both accepted:

- `SharingCardText.kt` repeats the `active` check that `SharingCard.kt` uses for the buttons; a new running availability would need both updated. Accepted: the enum is small and the `when` in the mapping is exhaustive; a shared `isActive` can be a later cleanup.
- No Compose UI test covers the card's wiring. Accepted: the real-phone check above reads the rendered card in every state the change touches.

---

## Next step

Run `openspec-verify-change` (the audit and the spec check), then continue to finalize.
