# Apply receipt

- **Change:** `split-android-and-granular-ci`
- **Iteration:** 4
- **Timestamp:** 2026-11-13T20:49:20Z
- **Worktree:** `/home/dev/.t3/worktrees/veles-android/t3-f088aff8-69ca8b3feb2c66da`
- **Branch:** `ci/android-workflow-overrides`
- **Commit range:** `146b054..aa17c3a` (PR 3), plus this artifact commit
- **Pull requests:** #143 (PR 1, merged `5ca8bfa`); #146 (PR 1b, merged `8de11f6`); #147 (PR 2, merged `146b054`); #148 (PR 3, open)
- **Tasks completed:** 55 of 57 (PR 1 1.1-2.11; PR 1b 3.1-3.6; PR 2 4.1-5.19; PR 3 6.1-6.3)
- **Remaining tasks:** 7.1 (run `/opsx:verify`, archive after all PRs merge) and 7.2 (follow-up cleanup, not a prerequisite)
- **Executor:** subagent-driven-development, with role-routed implementer and reviewer subagents; task reviews after every plan task plus final whole-branch reviews by Sol and Opus 5.5 for PR 2, and one fix-wave re-review.

## State

All four implementation PRs are authored. PR 3 adds `global-overrides` for
`.github/workflows/check-android.yml` and `build-android.yml` (android, tooling,
installer), documents them, and migrates the component tests. Its CI is expected
to select only `tooling` because the changed paths are `.github/components.json`,
`docs/ci.md` and the components test.

After PR 3 merges, run the verification artifact (`/opsx:verify`) and archive the
change. Task 7.2 (removing the now-inert `legacy-release`/`retired-paths` entries)
is a separate follow-up once no in-flight PR needs them and a new Android tag
contains the move; it is not a prerequisite for archive.
