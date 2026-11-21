# Review record: specs — Sol

- Artifact: `specs` (five spec files)
- Reviewer: Sol (GPT-6.2-Sol, medium)
- Preset: `sol` = [Sol]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes:
  - `brainstorm.md` sha256 3391633d78486d00995eff372b327d598d0720ba5d84754bbaa764eb4c66c0f9
  - `proposal.md` sha256 826874093f598bd0fa9c43928783802a74ca309c704bd48babc8854123cb3b0c
  - `design.md` sha256 2cb7cd4b1c30634eab4340716439ee439fbe12c7a6ee2b3c8bde343d8276afdb

## Pass 1

### Round 1 — changes-required

#### specs/affected-node-verification/spec.md

| Severity | Finding | Disposition |
|---|---|---|
| high | "Executes any test ⇒ test" contradicts `device`/`platform`; "neither gate MUST run" unclear. | fixed: explicit exceptions; "neither gate may run". |
| high | Producer failure vs node stop rule unclear. | fixed: producer `FAIL` → node `FAIL`, consumers skipped, independent steps run; scenario added. |
| medium | Dry-run scenario ignores selection errors and unresolved targets. | fixed: two scenarios. |
| medium | `web-extension/manifest.json` is not a tracked file. | fixed: `web-extension/eslint.config.js`. |
| medium | Requirement diagnostics apply tool fixes to path and generated requirements. | fixed: separate messages. |
| medium | 8 KiB cap vs 60 lines; what is dropped. | fixed: status lines kept, details dropped, pointer; overflow scenario. |
| medium | Drift check default directory; Gradle union by directory. | fixed: root default; union per directory. |

#### specs/verification-stop-hook/spec.md

| Severity | Finding | Disposition |
|---|---|---|
| high | Fingerprint omits the base commit. | fixed: base added; "Base changed" scenario. |
| high | Run budget not capped by the outer deadline; settlement accounting unstated. | fixed: three-way minimum; settlement charges measured time. |
| medium | Repeat identity for base errors; several problems in one run. | fixed: base in the identity; first in report order. |
| medium | Admission preconditions missing from scenarios. | fixed: preconditions stated. |
| medium | Where `PASS (remembered)` is observable; silent clean stop. | fixed: `last-run.json` and block text; silent pass. |

#### specs/hooked-rule-delivery/spec.md

| Severity | Finding | Disposition |
|---|---|---|
| low | Happy-path scenario expects the whole check to pass. | fixed: otherwise valid fixture, no hook-wiring finding. |

#### specs/agent-guidance-layout/spec.md

| Severity | Finding | Disposition |
|---|---|---|
| medium | Guard script behaviour not pinned. | fixed: "Raw Log guard keeps its behaviour" scenario. |

#### specs/ci-pipeline/spec.md

No findings.

### Round 2 — changes-required

Round-1 findings: 12 resolved, 2 partly (folded into the rows below).

#### specs/affected-node-verification/spec.md

| Severity | Finding | Disposition |
|---|---|---|
| high | No requirement for `NOT RUN (nothing planned)` instead of a vacuous `PASS`. | fixed: requirement text and scenario. |
| high | Initial inventory (bundle, browser, host build-contract suites) and "CI only" listing not required. | fixed: table requirement and "Extension branch inventory" scenario. |
| medium | Drift check's structural subset not named. | fixed: listed in the requirement. |
| medium (round-1 partly) | Failure-summary scenario could exceed 8 KiB. | fixed: short lines, single failing node. |

#### specs/verification-stop-hook/spec.md

| Severity | Finding | Disposition |
|---|---|---|
| high | Command cutoff at the deadline instead of 45 s before. | fixed: requirement and scenario. |
| medium | Expiry boundary and its order at admission. | fixed: stated. |
| medium (round-1 partly) | Settlement charge vs no-charge outcomes. | fixed: executed runs charged; cancelled reservations not. |

#### specs/agent-guidance-layout/spec.md

| Severity | Finding | Disposition |
|---|---|---|
| low | Guard exit codes not pinned. | fixed: 0 clean, 1 on a match. |

#### specs/hooked-rule-delivery/spec.md, specs/ci-pipeline/spec.md

No new findings.

### Round 3 — changes-required

Round-2 findings: 6 resolved, 2 partly (folded into the rows below).

| Severity | File | Finding | Disposition |
|---|---|---|---|
| high | affected-node-verification | Producer chain before each consumer not required or tested. | fixed: whole chain before each consumer; inventory scenario lists the chain before each suite. |
| high | affected-node-verification | Producer `NOT RUN` could hide another step's `FAIL`. | fixed: `FAIL` precedence; details keep every unexpected `NOT RUN`. |
| medium (round-2 partly) | affected-node-verification | Structural check named only commands for `inputs`/`reuse`. | fixed: commands and producers. |
| high | verification-stop-hook | Outer deadline and fallback kill time not pinned. | fixed: start + 1,740 s; kill at + 30 s (1,770 s). |
| high | verification-stop-hook | Reusable steps not required to declare every tool. | fixed: required, or `"reuse": false`. |
| medium (round-2 partly) | verification-stop-hook | Which runs are charged time. | fixed: every started run charged; outcomes change only block and repeat counters. |
| high | agent-guidance-layout | The guard could be `excluded` from the table. | fixed: lint and guard are `android` `check` commands, never excluded. |

### Round 4 — changes-required

Round-3 findings: 6 resolved, 1 partly (row 1 below).

| Severity | File | Finding | Disposition |
|---|---|---|---|
| high | verification-stop-hook | Settlement charge vs the no-work scenario; clean-stop reset of time spent obscured. | fixed: effects on time, blocks and repeat stated per outcome; scenario asserts the time grows and the counters stay. |
| medium | verification-stop-hook | `"reuse": false` on a producer must prevent recording. | fixed: commands or producers among the planned steps. |
| medium | affected-node-verification | No mixed `FAIL` + unexpected `NOT RUN` scenario. | fixed: "Mixed failure and skip" scenario. |

### Round 5 — changes-required (cap reached)

Round-4 findings: 2 resolved, 1 not (row 1 below).

| Severity | File | Finding | Disposition |
|---|---|---|---|
| high | affected-node-verification | The "Mixed failure and skip" fixture is impossible: lint runs before producers and a command failure stops the node. | fixed per the owner's decision: an earlier step `NOT RUN` (Gradle lint, no JDK 21) then a later independent `FAIL` (raw-`Log` guard). |
| medium | affected-node-verification | A resolver that fails in a real run must not read as `tests: none related`. | fixed per the owner's decision: a failing resolver is an unexpected `NOT RUN`. |
| medium | verification-stop-hook | A clean stop must also clear the stored blocking summary. | fixed per the owner's decision: a clean stop clears the summary. |

#### Escalation

The pass reached its cap with one high finding open. The agent agrees: its own scenario contradicts the approved
execution order. Proposed fix: an earlier step unexpectedly `NOT RUN` (typecheck's WASM producer missing Rust) and
a later independent step failing; plus the two medium wordings. Options sent to the owner: (A) apply the fixes and
treat the specs as approved; (B) apply them and run a confirmation pass capped at 2 rounds.
Owner's decision (2026-11-20): option A, apply the fixes and treat the specs as approved.

#### specs/affected-node-verification/spec.md

Approved content: specs/affected-node-verification/spec.md sha256 af47c61afbf8dc8cca9f87f8fe26e53d094eba2e9f9a1eab1cb55ad07ec9267f — approved by the owner's decision at the escalation.

#### specs/verification-stop-hook/spec.md

Approved content: specs/verification-stop-hook/spec.md sha256 944e3d646b541f49583c80f485046dd3f41d14efb5f74beadefcadf3c48662f1 — approved by the owner's decision at the escalation.

#### specs/hooked-rule-delivery/spec.md

Approved content: specs/hooked-rule-delivery/spec.md sha256 7b27f2cec1b9ee5dc12b6a904667f86829f14c5d18eb841cb0cc9854bf33ab99 — approved by the owner's decision at the escalation.

#### specs/agent-guidance-layout/spec.md

Approved content: specs/agent-guidance-layout/spec.md sha256 f2af203a88b279ba05718e0289352685abdfa8a411c9c5cdb5d8a8ded06e1538 — approved by the owner's decision at the escalation.

#### specs/ci-pipeline/spec.md

Approved content: specs/ci-pipeline/spec.md sha256 c12f7c82fa2b655fcec5e38edcffd614fd20eb044f18911a090ba24e85600e35 — approved by the owner's decision at the escalation.

