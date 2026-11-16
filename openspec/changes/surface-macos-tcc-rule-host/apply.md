# Apply Receipt

**Change**: `surface-macos-tcc-rule-host`
**Iteration**: `2`
**Applied at**: `2026-11-16 13:05`
**Executor**: single agent in the harness worktree (the user waived subagent-driven execution and
intermediate reviews for this small change)

---

## Workspace

- **Worktree**: `/home/dev/.t3/worktrees/veles-android/t3-15bf02ff-e0f39913cb128b18`
- **Branch**: `docs/surface-macos-tcc-rule-host`

---

## Commits

- **Range**: `302df46..0bd4bce`
- **Count**: `5`

---

## Tasks

- **Completed**: `14 of 14` checkboxes in tasks.md flipped to `- [x]`
- **Remaining**: none

---

## Iteration 2

PR review comments rejected the `native-bridge/**` scope because it also loaded the macOS rule for
`native-bridge/installer/windows/**` and the Tauri scaffold. This iteration names only the two
consuming patterns (`native-bridge/installer/macos/**` and `native-bridge/host/**`), extends
`check_agent_files.py` to accept identical multi-pattern lists under a rule's scope, adds the
multi-pattern and out-of-scope tests, and updates `docs/AGENTS.md` and the delta spec.

The whole-PR Sol review then returned changes-required:

- **High:** the prefix check let `android/../rust/**` escape the scope; patterns with a `..` segment
  are now rejected and a regression test covers it.
- **Medium:** the plan's goal, architecture and global constraints still described iteration 1;
  they now state the two-pattern list and the check extension.
- **Low:** the stale-reference grep searched the change's own history; it now excludes
  `openspec/changes/` and expects only the not-yet-synced main spec.

All three are fixed; the delta spec and `docs/AGENTS.md` now cover the traversal rule.

---

## Review gate

The user directed "No intermediate review needed, just the final whole PR one with Sol", so the
planning files carry no per-artifact approved records. The single whole-PR Sol review was attempted
after finalize but the model was unavailable (ChatGPT usage limit); the user's PR review comments
drove this iteration. The missing per-artifact records are recorded, not fabricated, in the verify
report.

---

## Next step

Re-run `/openspec-verify-change`, then push and re-run the whole-PR Sol review once the model is
available.
