## MODIFIED Requirements

### Requirement: A complete branch run writes a branch pass
A manual run of either gate (`scripts/verify.py` or `scripts/verify.py --branch`, not the Stop hook) SHALL write the
branch-pass record `build/verify/branch-pass.json` of its worktree only after a **complete** run: exit code 0; no `--deadline` and no `--base` argument; no step reported as not reached; and
`git status --porcelain` empty with the same HEAD both before selection and after the last step. The record MUST be
version 1 JSON holding the HEAD commit, the merge base it diffed against and the selected nodes, and MUST hold no
time. Every manual run of either gate MUST delete an existing record right after it takes the run lock, so only a
complete run leaves one; a run that does not get the lock MUST leave the record alone. The Stop hook's run (the task
gate with remembered passes) MUST NOT write or delete it, and a manual task-gate run MUST still never read a
remembered pass. One module, `scripts/branch_pass.py` (standard library only,
Python 3.8+), MUST own the record's path, format, writer and check, and both the verifier and the hooked-rules engine
MUST use it. Its check MUST, for a working directory, resolve the worktree, read the record, and pass only when the
record's HEAD equals `git rev-parse HEAD` and its base equals `git merge-base origin/master HEAD` there; every other
outcome, including a git failure or an unreadable record, MUST fail with a message naming what differs.

#### Scenario: Complete run
- **WHEN** `verify.py` or `verify.py --branch` exits 0 on a clean tree with no `--base` or `--deadline` and HEAD unchanged
- **THEN** `build/verify/branch-pass.json` names that HEAD and base, and the check in that worktree passes

#### Scenario: Task-gate pass with skipped tests
- **WHEN** a manual `verify.py` run exits 0 on a clean tree and its plan reported a test as `NOT RUN (branch gate only: ...)` or `tests: none related`
- **THEN** the run is complete and the record is written

#### Scenario: Incomplete run
- **WHEN** a run that took the run lock exits 1 or 2, was given `--deadline` or `--base`, left a step not reached, started or ended with a dirty tree, or HEAD changed during it
- **THEN** no record exists afterwards, and the check fails with "no branch pass"

#### Scenario: Failing task-gate run drops an old pass
- **WHEN** a complete `--branch` run wrote a record for HEAD and a manual `verify.py` run on the same HEAD then exits 1
- **THEN** no record exists afterwards

#### Scenario: Hook run leaves the record alone
- **WHEN** a record exists and the Stop hook's task-gate run passes or fails
- **THEN** the record is byte-for-byte unchanged

#### Scenario: Contended run keeps a pass
- **WHEN** a complete run wrote a record and a second manual run of either gate then exits 2 with "verification already running"
- **THEN** the record is unchanged

#### Scenario: New commit after the pass
- **WHEN** a record exists for commit A and HEAD is now B
- **THEN** the check fails with a message naming both short hashes

#### Scenario: Base moved
- **WHEN** the record's base differs from the current merge base with `origin/master`
- **THEN** the check fails naming the base
