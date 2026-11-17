# Apply Receipt

**Change**: `otp-12-gate-b-validation`
**Iteration**: `1`
**Applied at**: `2026-11-16 21:04`
**Executor**: `subagent-driven-development`

---

## Workspace

- **Worktree**: `/home/dev/.t3/worktrees/veles-android/t3-4a4bf2e4-2241f1662930b538`
- **Branch**: `chore/openspec-ff-otp-12`

---

## Commits

- **Range**: `b04edd0..9ff0ed2`
- **Count**: `33`

---

## Tasks

- **Completed**: `34 of 36` checkboxes in tasks.md flipped to `- [x]`
- **Remaining**: `9.3` (task removal not established), `9.4` (conditional, not applicable)

Task 9 is the non-evidentiary real-environment verification. Step 1 (adb seed, staggered
expiry, reseed, schedule, disarm, `PUSH` while armed, process restart, service stop with a
pending push) ran on a OnePlus 10 Pro 5G, Android 15, and produced the expected log lines.
Step 2 passed: the development extension pulled five fixture records, received and acknowledged
a scheduled push, and the pushed event did not join the history. Step 3 measured the wake-lock
window: the 1200 s push fired 20 min 0.013 s after scheduling while the phone was asleep and
locked, inside the 19..21 minute window; the post-minute-15 pull returned no events. ColorOS
recents would not dismiss the task, so task-removal survival and the "foreground indication
throughout" check were not established; a repeat on the OnePlus 6T was impossible because that
device reports the transport unsupported (no multiple BLE advertisements), and the OnePlus 10
recents still refused scripted dismissal. The deviation is recorded in
`.superpowers/sdd/plan/task-9-report.md`. Step 4 is conditional on a missed window and is not
applicable. Nothing from this run is Gate B evidence and the committed matrix stays
`decision: OPEN`, `gate_b_accepted: false`.

---

The final whole-branch review ran after Task 10. It found one merge-blocking race (a schedule
could survive a completed disarm because the armed read sat outside the registration lock) and
the Task 9 verification gap. The race was fixed in `34f7a32` with a deterministic regression
test; a scoped re-review approved the fix. The verification gap remains for the owner.

## Next step

Run `/openspec-verify-change`. Task 9's remaining gap is task-removal survival on the locked phone
(the ColorOS recents UI would not dismiss the task); the wake-lock window itself passed. The owner
decides whether to repeat that step on a launcher that allows recents dismissal or to record it as
a prerequisite for the later Gate B runs.
