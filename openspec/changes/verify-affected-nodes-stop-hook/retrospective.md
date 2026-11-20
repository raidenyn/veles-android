# Retrospective — `verify-affected-nodes-stop-hook`

## Wins

- Every task shipped with a fresh implementer and a task review on Opus 5.5; the reviews caught real
  defects that the tests could not: a leaked process group (reproduced with a real `sleep` and a
  TERM-ignoring child), a summary cap that dropped the missing-tool fix, and a resolver timeout that
  could be downgraded to `not_reached`.
- The final whole-branch review found two integration-level issues no task review could see: a
  deadline crossed *between* nodes blocked the hook, and the launcher's "own checkout" came from the
  process cwd. Both fixed with RED tests.
- The remembered-pass work proved its value: the real tree showed `web-extension` could never record
  because `rust.wasm`'s inputs walked `rust/.gradle` and `rust/build`. Narrowing those inputs made the
  extension node record and report `PASS (remembered)`.
- One lock owner, one writer per state file, and a fail-closed exit-code precedence survived every
  review pass.

## Misses

- Two table defects reached Phase A before being caught: the `bridge.install` compound exclusion hid
  removal of four gate checks, and the documented test-file glob accepted a partial list. Both were
  review findings, not test failures.
- `test_stop_hook_active_ignored` initially could not fail; it took a deliberately broken hook to show
  it. Two other tests (`within` fallback, the launcher's own-checkout) were only proved by real-tree
  or injected-context evidence.
- The acceptance worktree ran the linked-worktree case at the same revision as the launcher first; the
  revision difference and a marker were needed to prove which verifier ran.

## Plan deviations

- `./node_modules/.bin/vitest` instead of the design's illustrative `npx vitest run`, because
  `verify/check-remote-code.mjs` forbids `npx`.
- `{targets}` inside a template item expands once per target, so `--tests={targets}` repeats the flag;
  the design said one argv item per target but not how to repeat a flag.
- The `components.json` override moved into Task 3 (the table cannot land without it).
- The `rust.wasm`/`rust.hostJni` inputs narrowed to the crate and its build inputs.
- Task 15's real-session acceptance used `claude -p` turns plus a synthetic-cwd launcher invocation,
  with a marker, because a `-p` turn cannot move the Stop `cwd`.

## Skill/workflow compliance

- Superspec apply: 15 tasks, a task review each, one fix wave after the final review, apply and verify
  artifacts written, the review-record hash audit green.
- The review gate's apply stop was satisfied in the previous session; the planning approvals (Sol)
  remained current through apply because checkbox normalization keeps them valid.
- `repo-no-time-based-tests` held: every new test injects clock/wait/process seams.

## Surprises

- A cold full-selection task gate is 1614 s against the 1695 s cap: Phase B's per-scenario cold runs
  (1193 s max) understated the all-nodes case by ~35%.
- A Gradle daemon keeps the `PATH` it started with; changing the effective environment needs
  `./gradlew --stop` in both roots.
- Docker/container restarts can wipe the machine config and the superpowers npm cache mid-run; the
  measurement task was cancelled once and had to restart.

## Promote candidates

- A `verify.py --doctor` (or a `config show` warning) for a stale Gradle daemon with a different
  `PATH`, since it is the most confusing failure seen.
- A repository rule that every new `AGENTS.md` command line is run once on the machine that wrote it,
  not only drift-checked.
- A standard "same-revision" guard for acceptance runs that are meant to prove which checkout ran
  (require a revision difference or a marker).
