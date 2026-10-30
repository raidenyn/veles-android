# Working Windows BLE PoC review

Implementation was delegated to four OpenCode child sessions explicitly selecting
`ollama-cloud/glm-5.3-flash`. Exported assistant message metadata confirms that
provider/model for every implementation session:

- Fragmentation / Android cap: `ses_e8c83cfc0ffeHhWL8zYIzkaPEM`.
- Physical driver / isolated build: `ses_e8c83d031ffekyqcc50VdlQTup`.
- Interactive Windows launcher: `ses_e8c7e245bffeR3aVxLpHIO4E0t`.
- Background driver and regression tests: `ses_e8c7c170affew4XjxXw7ypjxUA`.

The controller ran fresh physical tests and checked installed APK hashes on both
phones, rather than relying on subagent completion messages.

## Claude Code Opus 5.5 gate

Explicit `--model claude-opus-5-5`, final session
`5cb955ef-743f-4cbe-8e73-ad903f9431e4`; result modelUsage confirms that exact
model. Read-only review of the PoC delta against `1a34191`, no review subagents.

Verdict: mergeable as an experimental PoC conditional on the full background run
passing and its JSON/trace and handoff documentation being committed. This is
not approval of OTP-24 or full OTP-23 production acceptance.

The conditions were subsequently met: `poc-background-final.json` records all
eight cases PASS, two 4096-byte replies after more than 610 seconds hidden,
results present on the first reattached read, and no surviving host. Its trace
is committed alongside the physical matrix and updated handoff.

Addressed initial findings:

- **C1:** adding a chunk helper exceeded Detekt's function threshold. Removed
  the helper and inlined the same calculation; full lint gate now passes without
  suppressing the rule.
- **I1:** a null-only Kotlin rejection assertion was not discriminating. Added
  an identical frame sequence that completes at cap 514 and is rejected at cap
  512, then proves a fresh valid transaction still completes.
- **I2:** background result read once at 630 s could miss a legitimately clamped
  timer. Added bounded polling, first-read / possible-reattachment-wakeup metadata
  and fake-clock tests. Defaults retain the full 630-second detached wait.
- Controller-found driver bugs: asynchronous notification needs a bounded wait
  after `sent`; the warmup JS mapper was accidentally inside `atob`. The actual
  emitted JS now runs in a semantic Node regression and preserves all 4096 bytes,
  including non-ASCII data. These fixes are in the GLM implementation sessions.

## Deferred review notes / handoff limits

- Scheduled-background result validation accepts the first matching reply and
  is less strict about duplicates/disconnected/orphan events than the matrix.
- Background timing details and cleanup ownership can be made more uniform
  with the physical matrix; process absence is recorded, formal acceptance false.
- The method-name source guard covers the matrix, not every background call.
- A legacy null-only test is weaker than its name; the new 514-vs-512 test is
  the discriminating coverage.
- Rust cap is intentionally unconditional in this experimental crate. Use
  `1a34191` to reproduce the original uncapped harness.
- Production compatibility/versioning, macOS, signed installation, standard
  accounts and complete lifecycle/radio stress remain outside this PoC.

Physical source hashes and installed APK identity are recorded in
`native-bridge/feasibility/evidence/poc-build-identity.json`. Historical cost
artifacts and no-go traces are not relabeled as new proof.
