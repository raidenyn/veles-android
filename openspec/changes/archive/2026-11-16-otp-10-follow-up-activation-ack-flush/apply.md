# Apply Receipt

> Generated at the end of the apply phase to mark code-implementation
> complete and provide verify with the state it needs.
> Overwritten on each apply iteration; iteration counter grows.

**Change**: `otp-10-follow-up-activation-ack-flush`
**Iteration**: `1`
**Applied at**: `2026-11-16 11:05`
**Executor**: `executing-plans` (native, in-session)

---

## Workspace

- **Worktree**: none (the session ran in the T3 worktree `t3-a5c40d39`)
- **Branch**: `feat/otp-10-follow-up`

---

## Commits

- **Range**: `ab15de4..becfae7`
- **Count**: `6`

---

## Tasks

- **Completed**: `21 of 21` checkboxes in tasks.md flipped to `- [x]`
- **Remaining**: `none`

---

## Verification performed in apply

- Extension checks: `npm ci`, `format:check`, `lint`, `typecheck`, `npm test` (53 files, 651 tests),
  `build`, `test:bundle` — all green.
- `./gradlew sessionLoopbackCheck` with `JAVA_HOME=/home/dev/.jdk/zulu21` — 5 tests green,
  `enrollment_full` included.
- Docs node: `check_agent_files.py` (0 findings) and `docs/checks` (79 tests) green.
- `openspec validate <change> --strict` valid (one long-requirement warning, accepted in the specs
  review).
- Red proof for the terminal-active rule: with the two `activationCompleted()` guards disabled, the
  reset, port-death and fail-closed connector tests fail as designed and pass once restored.
- Real-device run: debug APK installed with a matching certificate, macOS host built and registered,
  twelve enrollment + first-reconnect cycles; evidence recorded in `docs/otp-11-debug-controls.md`.

## Deviations recorded honestly

- The native execution path was used: no per-implementation-task reviewer subagent was dispatched,
  and Task 1's code was written before its test was run. A whole-branch review is still owed.
- Bound expiry, a failed ack send, and cancel/tab-close inside the hold are unit-tested only; the
  same sentence must appear in the pull-request note.
- The device run's failures were harness-side (pairing windows racing the preceding mutual removal,
  and a reconnect issued during the previous link's teardown); repeats passed.

---

## Next step

Run `openspec-verify-change`.
