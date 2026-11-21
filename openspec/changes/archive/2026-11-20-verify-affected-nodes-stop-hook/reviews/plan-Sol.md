# Review record: plan — Sol

- Artifact: `plan.md`
- Reviewer: Sol (GPT-6.2-Sol, medium)
- Preset: `sol` = [Sol]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes:
  - `brainstorm.md` sha256 3391633d78486d00995eff372b327d598d0720ba5d84754bbaa764eb4c66c0f9
  - `proposal.md` sha256 826874093f598bd0fa9c43928783802a74ca309c704bd48babc8854123cb3b0c
  - `design.md` sha256 2cb7cd4b1c30634eab4340716439ee439fbe12c7a6ee2b3c8bde343d8276afdb
  - `specs/affected-node-verification/spec.md` sha256 af47c61afbf8dc8cca9f87f8fe26e53d094eba2e9f9a1eab1cb55ad07ec9267f
  - `specs/verification-stop-hook/spec.md` sha256 944e3d646b541f49583c80f485046dd3f41d14efb5f74beadefcadf3c48662f1
  - `specs/hooked-rule-delivery/spec.md` sha256 7b27f2cec1b9ee5dc12b6a904667f86829f14c5d18eb841cb0cc9854bf33ab99
  - `specs/agent-guidance-layout/spec.md` sha256 f2af203a88b279ba05718e0289352685abdfa8a411c9c5cdb5d8a8ded06e1538
  - `specs/ci-pipeline/spec.md` sha256 c12f7c82fa2b655fcec5e38edcffd614fd20eb044f18911a090ba24e85600e35
  - `tasks.md` sha256 e4afa9c2586119159df6f96a49ada278ddb091631b8f617058b91447b555d2e5

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| high | Resolver and probe interfaces lack a shared deadline and typed outcome; orchestration order unstated. | fixed: Shared types (`Outcome`, `Context`, `Run`); `run_gate` with a fixed order (lock, orphans, select, plan, steps). |
| high | `fingerprint` inputs and `Passes` / hook lookup unspecified. | fixed: `FingerprintInput`, `Passes.get/put/drop`, `remembered`, `run_gate(use_passes)`. |
| high | Report-fd handoff, bounded stdin and non-blocking watchdog unspecified. | fixed: `--report-fd`, `read_event` cap, selector loop; tests through `main`. |
| medium | Inventory lacks local argv; vitest resolver criteria. | fixed: Task 1 fields and qualification rule. |
| medium | Scratch-run baseline and cold runs; timing source. | fixed: `--base V`, both Gradle builds stopped, per-step `elapsed_s`; hook acceptance asserts only the extension. |
| high | 3.8 check lists `test_verify.py` before it exists. | fixed: separate `test_verify_py38.py` with a glob. |
| high | Task 9 needs the override that Task 10 added; shared clean fixture. | fixed: override, CODEOWNERS and the components test move into Task 9; fixture migrated. |
| high | No end-to-end resolver-timeout test. | fixed: `test_resolver_timeout_end_to_end` (Task 8) and `test_resolver_timeout_outcome` (Task 5). |
| medium | Missing assertion contracts. | fixed: assertions added for each named case; `test_verify.py` holds the CLI forms of the spec scenarios. |
| medium | Workflow helper takes one source; one regex can miss a grep. | fixed: helper takes the script text; both greps pinned; separate mutations. |
| medium | `hooks_block()` fixture; targeted trigger and scope tests before the guidance commit. | fixed: Task 14 Steps 1, 6, 7, 9. |

### Round 2 — changes-required

Round-1 findings: 7 resolved, 4 partly (folded into the rows below).

| Severity | Finding | Disposition |
|---|---|---|
| high | Inline pairing-code guards (Android, extension) have no runnable equivalent. | fixed: Task 2 extracts both into scripts with behaviour tests, workflow and contract updates, docs; Task 3 lists them as checks. |
| high | Post-run stability uses stale observations. | fixed: Task 12 re-runs `select` and the index read; tests for a clean file edited and a new file created during the run. |
| high | Task 12 tests hook output before Task 13 exists; "no runner call" contradicts probes. | fixed: hook-output tests moved to Task 13; Task 12 asserts no node commands or producers run. |
| high | Event write to child stdin can block the watchdog. | fixed: non-blocking writes through the selector; large-event test. |
| high | Acceptance does not prove a clean silent pass after the fix. | fixed: known-green baseline first; silent pass asserted after the fix. |
| medium | Resolver and version output from a capped, merged log. | fixed: `Run` gains `capture`; `Outcome.stdout`. |
| medium | Producer invocations share one log. | fixed: per-invocation log paths and report entries. |
| medium | Whole-run wall time; 45-s reserve; disposable worktrees. | fixed: top-level `elapsed_s`; threshold 1,695 s; scratch worktrees in Tasks 11 and 15. |
| low (round-1 partly) | Quotes in unusual argv; `files-arg` fixture for the resolver timeout; fixture `nodes` map; separate trigger contexts. | fixed: Tasks 5, 8, 9, 14. |

### Round 3 — changes-required

Round-2 findings: 7 resolved, 2 partly (folded into the rows below).

| Severity | Finding | Disposition |
|---|---|---|
| high | A deadline before a step became a synthetic blocking `timeout`, contrary to the accepted known limit. | fixed: `not_reached` is reported, non-blocking and exit-neutral; only a cut-off command is a `timeout`; tests in Tasks 8 and 13. |
| high | Task 4 added a CLI test before `main` is wired. | fixed: the CLI selection-error test moves to Task 8. |
| high | `--list` resolvers run outside the run lock and touch `running.json`. | fixed: `--list` takes the run lock and cleans orphans first; test. |
| medium | Scratch worktrees unprovisioned; "cold" undefined; failed runs timed. | fixed: fresh provisioned worktree per scenario, cold/warm defined, only exit-0 runs recorded. |
| medium (round-2 partly) | Version output on stderr (`java -version`). | fixed: `capture` reads stdout and stderr separately; probes read both; test. |
| medium (round-2 partly) | The Android mutation anchored on the removed inline `exit $fail` block. | fixed: such mutations move to the script text. |

### Round 4 — changes-required

Round-3 findings: 3 resolved, 3 partly (folded into the rows below).

| Severity | Finding | Disposition |
|---|---|---|
| high | Launch cutoff at the deadline rather than the reserve; an incomplete run could earn a pass or count as clean. | fixed: no start after `deadline - 45`; `RunOutcome.incomplete`; no record for a node with `not_reached`; hook settles `incomplete` without reset; tests. |
| high | `remembered()` boolean hides a probe timeout. | fixed: returns the timeout `Outcome`, reported as a blocking `timeout`; test. |
| medium | Environment patterns matched only against the tail. | fixed: `classify_env_failure` reads the whole log; test with the diagnostic before 500 lines. |
| medium (round-3 partly) | `test_list_selection_error` ran before `main` was wired. | fixed: moved to Task 8. |
| low (round-3 partly) | A failed cold attempt left build outputs. | fixed: recreate the worktree before re-running cold. |

### Round 5 — changes-required (cap reached)

Round-4 findings: 3 resolved, 2 partly (rows 1–2 below).

| Severity | Finding | Disposition |
|---|---|---|
| high (round-4 partly) | A run that is both blocking and incomplete: which settlement wins. | fixed per the owner's decision: blocking wins; test. |
| high (round-4 partly) | A lookup probe timeout loses its step id and repeat identity. | fixed per the owner's decision: the step id is returned; identity `(node, step id, "timeout")`. |
| medium | The incomplete notice must list remembered nodes. | fixed per the owner's decision: shared formatter; test. |
| medium | Acceptance: Node 22 may come back through the shared `path_prepend`. | fixed per the owner's decision: check, remove for the run, restore. |

#### Escalation

The pass reached its cap with two partly resolved high findings. The agent agrees and proposes: a blocking problem
wins over `incomplete`; `remembered()` returns the step id with the timeout so the identity is
`(node, step id, "timeout")`; the incomplete notice uses the shared formatter with a mixed test; the acceptance
step checks `config show` and, if `path_prepend` supplies Node 22, removes it for the run and restores it after.
Options sent to the owner: (A) apply and treat the plan as approved; (B) apply and run a confirmation pass capped
at 2 rounds. Owner's decision (2026-11-20): option A, apply the fixes and treat the plan as approved.

Approved content: plan sha256 59ff9c18e4cc1d6dffc6de1b664c448faa43f9fed655d2d95f958da93b19b8a1 — approved by the owner's decision at the escalation.
