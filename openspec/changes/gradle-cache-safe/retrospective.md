# Retrospective — gradle-cache-safe

## Plan deviations

- **`rustWasm` is not cacheable.** The relocation experiment lost wasm-pack's `pkg/.gitignore` on restore,
  so the spec's fallback clause applied. The cached set is `rustJni` and `rustHostJni`. The planning text
  still names three products; the PR summary records the difference.
- **`android/app/build.gradle.kts::sessionLoopbackClasspath` was ported in Task 4**, not Task 5, because the
  root Step 6 gate could not pass while that task held an `AndroidUnitTest`.
- **The `test-ndk-lookup.sh` scratch repo now copies `rust/gradle.properties`**, so the lookup contract is
  exercised under the strict configuration cache.
- **The final review's fix wave added host-JNI inputs to both JVM test tasks** and moved
  `-Djava.library.path` into an `@Internal` argument provider, so a Rust-only change invalidates the test
  result and the worktree path leaves the key.
- **The pin experiment used cargo-ndk 4.1.1**, because 4.1.2 was already the latest published release.

## Surprises

- The old `androidgitversion` plugin uses JGit, which cannot configure a linked worktree at all
  (`NoWorkTreeException`); baseline worktrees were created with `git worktree add -b`.
- `protocolJvmTest` and `testDebugUnitTest` load the host JNI library through `java.library.path`; leaving
  that undeclared made a stale cached pass possible, which the final review caught.
- Gradle shows at most 15 configuration-cache problems on the console; the full inventory required the
  HTML report from a fresh rerun.
- Chrome 157 ignores `--load-extension`; the Mac verification used CDP `Extensions.loadUnpacked`.

## What could not be run

- The full `connectedDebugAndroidTest` suite is not green on the attached phones (one denies
  `GRANT_RUNTIME_PERMISSIONS` to UiAutomation; the other times out only under full-suite load). App source is
  unchanged since the green 82-test run in Task 5.
- End-to-end pairing needs the phone in Bluetooth range of the browser machine and a registered Native
  Messaging host; the phone is on the PC and the extension ran on the Mac.
- `bridgeBuild`/`bridgePackage` need glib/Tauri libraries, absent on Linux; CI does not run them in the
  scaffold check.
