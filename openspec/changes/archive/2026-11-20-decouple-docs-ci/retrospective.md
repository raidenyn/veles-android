# Retrospective — decouple-docs-ci

## Wins

- The offline guard replaced a regex scan with an AST rule that is stricter on the forms it names, and it
  runs in seconds inside the docs node.
- The superset test reads the installer's three boundary regexes from its source, so the tooling and
  installer release boundaries cannot drift without a failing test.
- The map and the agent-file pin moved in one commit, and the selection evidence (three scratch commits
  plus #174/#175) proves the three target paths select what the spec says.

## Misses

- The first version of the guard only checked `os.<process>` directly under the `os` name; the final review
  found re-exported paths (`shutil.os`, `shutil.posix`) and the follow-up fix added `posix`/`nt`. A
  re-export test should have been in the first test set.
- The `env=` gap (`GIT_EXEC_PATH`, `GIT_CONFIG_PARAMETERS`, `GIT_SSH_COMMAND`) is documented rather than
  checked; a determined author can still reach another program through the environment.

## Plan deviations

- Task 5's evidence file `apply.md` was written during the task fix round rather than after Task 6; the
  final receipt overwrote it with the pre-push results.
- Task 6.2 (post-push CI confirmation) stays open until finalize creates the PR; that is the plan's order.

## Skill/workflow compliance

- Task-level reviews and the final whole-branch review ran on Opus (Claude Opus 5.5, medium); every
  Critical/Important finding was fixed and re-reviewed. The planning records were already approved and
  their hashes are unchanged.

## Surprises

- `verify/verify-supply-chain.sh` requires exactly Node 26.8.1; the host's 26.13.1 is rejected, so the
  check ran with a downloaded 26.8.1.
- `protocolCheck` needs `JAVA_HOME` pointing at the Zulu 21 JDK and `ANDROID_HOME`; the host JRE alone
  fails the Java compiler toolchain.
- `verify/aggregate-checksums.sh` is listed in `verify/AGENTS.md` but is absent from the tree.

## Promote candidates

- Track the `verify/test/*.test.mjs` gap: only `workflow-contracts.test.mjs` runs in CI. This change is not
  the cause, but the `verify/test/` selection now makes the gap more visible.
- Consider a follow-up that rejects the environment keys in the offline guard, or states them in design D1
  through the review gate.
