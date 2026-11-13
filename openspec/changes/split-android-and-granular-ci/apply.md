# Apply receipt

- **Change:** `split-android-and-granular-ci`
- **Iteration:** 2
- **Timestamp:** 2026-11-13T15:05:37Z
- **Worktree:** `/home/dev/.t3/worktrees/veles-android/t3-f088aff8-69ca8b3feb2c66da`
- **Branch:** `refactor/protocol-spec-move`
- **Commit range:** `5ca8bfa..109b570` (PR 1b), plus this artifact commit
- **Pull requests:** https://github.com/raidenyn/veles-android/pull/143 (PR 1, merged as `5ca8bfa`); https://github.com/raidenyn/veles-android/pull/146 (PR 1b, open)
- **Tasks completed:** 24 of 57 (PR 1 tasks 1.1-2.11; PR 1b tasks 3.1-3.6)
- **Remaining tasks:** 4.1-4.9 and 5.1-5.19 (PR 2), 6.1-6.3 (PR 3), 7.1-7.2 (verify/archive and follow-up)
- **Executor:** subagent-driven-development, with role-routed implementer and reviewer subagents; task reviews after every plan task.

## State

PR 1 merged and green. PR 1b moves the protocol specification from `docs/protocol/`
to `protocol/docs/`, updates live references and resolver paths, and adds the
spec-location guard. Its local gates passed: `protocolCheck`,
`protocolFreshEvidenceCheck`, `rustTest`, `bridgeHostTest`, the web-extension
suite, `spotlessCheck detekt lintDebug`, `scriptsTest`, the workflow contracts
and `components.py check`. The reference audit leaves only the current change's
planning artifacts and three dated review-record strings.

Remaining work follows the plan's PR sequence: PR 2 (`android/` Gradle build)
after PR 1b merges, then PR 3 (Android workflow overrides). `verify` and
`archive` run after all four PRs merge.
