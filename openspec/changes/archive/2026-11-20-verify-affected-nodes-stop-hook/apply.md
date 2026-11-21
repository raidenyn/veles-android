# Apply Receipt

> Generated at the end of the apply phase to mark code-implementation
> complete and provide verify with the state it needs.
> Overwritten on each apply iteration; iteration counter grows.

**Change**: `verify-affected-nodes-stop-hook`
**Iteration**: `1`
**Applied at**: `2026-11-20 22:14`
**Executor**: `subagent-driven-development`

---

## Workspace

- **Worktree**: `/home/dev/.t3/worktrees/veles-android/t3-a6c1e51b-a3ad15ec55fd0ec6`
- **Branch**: `chore/verify-affected-nodes-stop-hook`

---

## Commits

- **Range**: `597ee2c6..a73b7ee0`
- **Count**: `40`

---

## Tasks

- **Completed**: `49 of 49` checkboxes in tasks.md flipped to `- [x]`
- **Remaining**: `none`

---

## Next step

Run `/openspec-verify-change` (`openspec-verify-change verify-affected-nodes-stop-hook`).

---

## Notes

**Execution.** Fifteen plan tasks, each with a fresh implementer and a task review on the roster's
`Opus` reviewer (Opus-5.5, medium; the user's instruction for this apply run). Every task ended with
its review clean; two tasks needed a fix round for Important findings, one of them a regression the
re-review caught with a real process. A whole-branch review on the same reviewer found two more
Important issues (a deadline crossed between nodes blocked the hook; the launcher's own checkout came
from the process cwd), fixed in one wave and re-reviewed clean.

**Controller rulings taken during apply** (details in the apply ledger, now deleted; cost if wrong in
parentheses):

- The components override for `scripts/verify-commands.json` moved into Task 3 (one duplicate edit).
- `{targets}` inside a template item expands once per target (`--tests={targets}` repeats the flag)
  (one convention change in table + Task 4/14).
- `files-arg` is implemented although the real table uses `name-map` (a small unused strategy).
- The table uses `./node_modules/.bin/vitest` instead of the design's `npx vitest run`, because
  `verify/check-remote-code.mjs` forbids `npx` (one table line; docs record it).
- An `excluded` entry's `line` may change (it has no runtime effect): the `bridge.install` exclusion
  narrowed to `./gradlew bridgeInstall` so the four gate checks cannot silently drop out.
- `rust.wasm`/`rust.hostJni` inputs narrowed to the crate and its build inputs; the real tree proved
  `web-extension` was otherwise never recordable (inputs widened again if a missed input shows).
- Task 15's real-session acceptance used `claude -p` turns plus a synthetic-cwd launcher invocation
  for the linked-worktree case, with a marker proving the target's verifier ran (documented in
  `spike.md`).

**Known debt shipping as recorded limits** (triage by the final whole-branch review; all in
`spike.md`, `docs/verify.md` or the commit history): Windows branches in the runner; a corrupt
`running.json`/`tools.json` edge; `elapsed_s` measured inside `run_gate`; the `HookState` default
`wait` fix landed, but a run_gate-plus-settle double failure can still print two stderr lines; the
doc-reader and glob edge cases; the Task 5 note wording; and the four design known limits.

**Environment.** Machine config lives in `<git-common-dir>/veles-verify/tools.json` and is shared by
every worktree: `java_home` (Zulu 21), `android_sdk`, and a `path_prepend` carrying Node 26.8.1/npm
11.19.0 plus the pinned cargo tools. Playwright Chromium needed a hand-built ubuntu-24.04 browser on
this Ubuntu 26.04 host (recorded in `spike.md`).
