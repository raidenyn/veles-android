# Apply receipt

- **Change:** `split-android-and-granular-ci`
- **Iteration:** 3
- **Timestamp:** 2026-11-13T19:10:36Z
- **Worktree:** `/home/dev/.t3/worktrees/veles-android/t3-f088aff8-69ca8b3feb2c66da`
- **Branch:** `refactor/android-gradle-build`
- **Commit range:** `8de11f6..3fb12bf` (PR 2), plus this artifact commit
- **Pull requests:** https://github.com/raidenyn/veles-android/pull/143 (PR 1, merged as `5ca8bfa`); https://github.com/raidenyn/veles-android/pull/146 (PR 1b, merged as `8de11f6`); https://github.com/raidenyn/veles-android/pull/147 (PR 2, open)
- **Tasks completed:** 51 of 57 (PR 1 tasks 1.1-2.11; PR 1b 3.1-3.6; PR 2 4.1-5.18)
- **Remaining tasks:** 5.19 (all-node merge gate for PR 2), 6.1-6.3 (PR 3), 7.1-7.2 (verify/archive and follow-up)
- **Executor:** subagent-driven-development, with role-routed implementer and reviewer subagents; task reviews after every plan task and a final whole-branch review by Sol plus one scoped fix-wave re-review.

## State

Android is an independent `android/` Gradle build, `rust/` is an included build, the
root keeps thin aliases, CI runs Android commands from `android/` and guards from the
root, and the component map/planner preserve pre-move release attribution.

The debug identity check is complete: the merge-base and PR-head APKs are byte-identical
(`versionName` 1.2.3, `versionCode` 10203, certificate SHA-256 `9ef3104b…`). Per the
owner's instruction, release identity remains CI-only; the local, git-ignored fixture
assets live under `android/config/android_keys/` and the debug fixture build from
`android/` is confirmed. The Windows shim reports Gradle 9.5.0 and `help` succeeds; the
full Windows APK stops only at the missing NDK, for which `BUILD.md` documents the
`sdkmanager` step and `android/local.properties`.

PR 2 is open. Its remaining gate is every CI node green (including `rust /
jni-integration`, `protocol / contract-tests` and `tooling / unit-tests`) and the owner
confirmation already recorded in the PR body. PR 3 follows after PR 2 merges.
