# Review record: brainstorm — Opus

- Artifact: `brainstorm.md`
- Reviewer: Opus (Opus-5.5, medium)
- Preset: `opus` = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: none (brainstorm is first in the order)

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| high | "Exit-0 `--branch`" is not a proof: `--deadline` turns cut-off steps into `not_reached` with exit 0, and `--base HEAD` proves nothing. | fixed: a pass is a complete run (no `--deadline`, no `--base`, no `not_reached`, clean tree and same HEAD at start and end); the gate recomputes HEAD and the merge base. |
| medium | The one-way drift check's matching rule is unstated (targeted `--tests` lines, combined task-only lines). | fixed: exact argv and cwd, or the task-subset matcher for task-only `./gradlew` lines; extra arguments stay allowed. |
| medium | Producers' `doc` and the `excluded` list unresolved; `excluded` is doc-line coverage, not a CI inventory. | fixed: producers stay documented and lose `doc`; `excluded` is removed with the reverse check. |
| medium | Gate scope for deletion, other-ref and `-C` pushes. | fixed: deletion pushes pass; the other cases are an accepted, documented limit. |
| medium | Clean-tree timing during a 14-minute background run. | fixed: clean and same HEAD at both start and end. |
| medium | OpenCode resume while busy is unverified; testability unanswered. | fixed: marked as not yet observed with a design spike; seams named for tests. |
| low | Lock contention between a background branch run and a Stop hook run. | fixed: recorded as an accepted cost. |
| low | Dropping the reverse check drops a safety net. | fixed: recorded as an accepted cost. |
| low | No override blocks pushes on unprovisioned machines. | fixed: recorded as an accepted cost. |
| low | Affected specs not named. | fixed: the four specs and their requirements are listed. |

### Round 2 — approve

| Severity | Finding | Disposition |
|---|---|---|
| low | `verify.py` itself loads `doc` and `excluded` (`_load_excluded`, table loading). | deferred: to the design, which must list it. |
| low | The proof record's location must resolve the same way for writer and gate. | deferred: to the design (already an open question). |
| low | Deletion-push detection by pattern is easy to get wrong (`+:<ref>`, `-d` after the remote). | deferred: to the design's tests. |
| low | The clean-tree rule means the change's untracked files must be committed first; say so in the denial text. | deferred: to the design (denial text). |

Approved content: brainstorm sha256 4df8162115fc5e4d9607694fe07ac6ce69ba2d394c7769fb043b9bd1321b01b0
