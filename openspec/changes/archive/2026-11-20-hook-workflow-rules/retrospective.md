# Retrospective: hook-workflow-rules

## Wins

- The spike caught a host assumption before it was built: OpenCode 2.3.2 does not emit `session.idle`, and the
  verified substitute (`session.execution.succeeded`) came with a reproducible resuming call.
- The per-task Opus reviews caught two real defects that tests alone missed: a change-argument test that could
  pass without the `=`/quoted forms matching, and a layout check that never required the moved rules at their new
  path.
- The whole-branch review caught the delegation problem the plan never imagined: the gate reminded any session
  that named a change, including reviewers and verifiers.

## Misses

- The plan predicted the agent-file check would fail between the move and the check update; it passed, because
  the checker did not yet know the five files had been always-on. A smaller surprise than planned.
- The verification task initially reported PASS for OpenCode outcome 7 without three required sub-cases, and
  documented the one-shot headless limitation instead of escalating it. The review caught both.

## Plan deviations

- The OpenCode stop trigger is `session.execution.succeeded`, owner-approved, in place of the absent
  `session.idle`.
- The stop gate is armed only by an apply-skill invocation, not by naming the change, after the whole-branch
  review. A `SKILL.md` read still delivers moments but never arms.
- The OpenCode stop gate needs a kept-alive session; the owner accepted the one-shot `--standalone` limitation.

## Skill/workflow compliance

- The review gate was satisfied before apply and re-satisfied after each normative amendment, with hashes
  recomputed and records appended.
- TDD was followed for every engine/plugin task, with red→green evidence in the reports.
- The pre-push checks ran; `verify/verify-supply-chain.sh` could not run because it pins Node 26.8.1 and the
  workstation has 26.13.1.

## Surprises

- A stray file under `.git/worktrees/` (for example `.DS_Store`) made every Claude turn end report a hook
  failure; it now fails open silently.
- OpenCode's local plugin path must be a directory containing `index.js`; a file path is skipped with a warning.
- The two OpenCode plugins were not exercised together in the disposable run.

## Promote candidates

- Treat "a test that can pass without exercising its case" as an Important review finding; it happened twice.
- Require verification evidence to list every UNVERIFIED sub-case per host in the doc itself, not only in the
  report.
