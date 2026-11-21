## Design Summary

The push gate today needs a branch pass, and only a complete `python3 -I scripts/verify.py --branch` run writes
one. That run executes every test of every affected node in sequence and takes about 15 minutes locally, while CI
runs the same commands (and more) in parallel jobs on every pull request and is much faster. The owner wants the
local pre-push condition relaxed to the fast verification agents already run between tasks: the task gate
(`python3 -I scripts/verify.py`), which runs the affected nodes' checks plus only the tests related to the
changed files.

Agreed design: a **complete manual task-gate run** also writes the push-pass record
(`build/verify/branch-pass.json`), under the same completeness rules as the branch gate (exit 0, no `--deadline`,
no `--base`, nothing not reached, clean tree with the same HEAD before selection and after the last step). The
record's format, path, owner module (`scripts/branch_pass.py`), proof name (`branch-pass`) and check stay
unchanged, so the push gate itself does not change. The pre-push rule, its pointer and every doc that names
`--branch` as the pre-push command now name `python3 -I scripts/verify.py`. `--branch` stays available as the
optional full local run and still writes the record. CI remains the full gate.

## Alternatives Considered

### Alternative A: Keep `--branch`, run its tests in parallel locally
- **Approach**: Make the branch gate run nodes (or steps) concurrently.
- **Pros**: Keeps full local coverage before every push.
- **Cons**: Gradle, npm and Cargo runs share daemons, caches, ports and `build/` outputs; the verifier's
  run lock, orphan cleanup, step logs, deadline reserve and generated-output ordering all assume one step at a
  time. Large redesign with real flakiness risk, and a laptop still pays the full CPU cost.
- **Why not chosen**: High cost and risk for coverage CI already provides on every pull request.

### Alternative B: Drop the push proof entirely
- **Approach**: Remove `requires: ["branch-pass"]` and leave verification to CI.
- **Pros**: Fastest push; least code.
- **Cons**: Pushes can carry lint or related-test failures an agent would have caught in seconds, burning CI
  runs and review rounds; loses the guardrail the previous change added on purpose.
- **Why not chosen**: The owner asked to relax the condition, not remove it.

### Alternative C: Push run reuses the Stop hook's remembered passes
- **Approach**: As the agreed design, but the manual task-gate run also reads remembered task-gate passes, so a
  push right after a green Stop is nearly instant.
- **Pros**: Fastest relaxed gate.
- **Cons**: Breaks the existing rule that manual runs never read a remembered pass; the push proof would rest on
  fingerprint soundness rather than a fresh run.
- **Why not chosen**: The owner chose a fresh run.

### Alternative D: A new flag (`--push`) or a second proof
- **Approach**: Add a dedicated mode or a separate `task-pass` record and proof.
- **Pros**: Makes the gate that produced the proof explicit.
- **Cons**: Another mode, file and registry entry to document and test for no behavioural gain; agents would
  have two commands to remember.
- **Why not chosen**: The default task gate already is "the fast verify"; reusing it keeps one command.

## Agreed Approach

The agreed design above: the manual task gate writes the existing push-pass record after a complete run;
`--branch` keeps writing it too; the push gate and its proof are unchanged; the rule and docs point at the task
gate. It wins because it reuses the verification agents already run, changes one small branch of
`run_gate`, and keeps a fresh-run guardrail while CI keeps full coverage.

## Key Decisions

- The pre-push command is `python3 -I scripts/verify.py` (task gate), run fresh; no remembered-pass reuse (owner's
  choice).
- The Stop hook (task gate with remembered passes) still never writes or deletes the record: only manual runs of
  either gate do. A hook run is bounded, fails open and may reuse passes, so it is not a push proof.
- A manual task-gate run deletes an existing record as soon as it holds the run lock, exactly as `--branch` does,
  so a failing or incomplete run can never leave an older pass in place.
- The record format stays version 1 with no gate field; the file name `branch-pass.json` and proof name
  `branch-pass` stay, because "branch" names what it proves (this branch's HEAD against its merge base), and
  renaming would churn five specs and several tests for no behaviour.
- The rule still tells the agent to commit first and to run the verifier in the background and wait for it:
  Android checks plus related Gradle tests can still take minutes.
- `--branch` remains documented as the optional full local run; CI is the full gate.
- Accepted coverage trade-off: the task gate skips every `test` step without a related-test strategy
  ("branch-only"), for example `protocol.contract`, `bridge.test`, `web-extension.bundle`/`browser` and
  `tooling.verify-tests`. A node whose only commands are branch-only tests (today `protocol`) gets a push pass from
  a run that executed none of them. Those tests run in CI on the pull request.

## Open Questions

None.
