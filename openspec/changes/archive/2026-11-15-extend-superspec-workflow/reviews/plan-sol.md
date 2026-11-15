# Review record: plan — reviewer Sol (GPT-6.1-Sol, medium)

Preset: both (first Sol, second Opus).
Upstream inputs: brainstorm.md sha256 a8d70a504a25cc0aa5e142f69451407d5ff9583f766cef37c17fdb0e549b4661; proposal.md sha256 8ec310f828da45f2eea046d672379083b8335a56b0ecab5a00df303c1e824946; design.md sha256 dbe2950ea84021b3d17c2d5e8662fcc3d465bf64d8aeb8e0fc1da62c4ac23431; specs/agent-guidance-layout/spec.md sha256 a6a90255a75d053556211133c2fae83339305ceaa9fb88ecc8623595f0daedd1; specs/superspec-closeout/spec.md sha256 57678fa8abdcaa2ed671b27571d448b98f3aa5d0f86efd4ca00443cac85f39b2; specs/superspec-review-gate/spec.md sha256 53aa575f782e1b6828c5d481b3a4a9ad328ee35a4f56248980fe55af191aeb60; specs/superspec-tracker-intake/spec.md sha256 0af195bfc967a1691590055fa55d338787fe4c172fd91afb6bf19e2d6029f1e1; tasks.md sha256 83236ec9bd1a61efa29bc3bca58d7189dce467b9366126e905875cde4f107b27

Hand-run under the procedure this change defines. Cap: 5 rounds per reviewer per pass.

## Round 1 — verdict: changes-required (1 high, 6 medium, 1 low)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | high | The monitoring spike omits inline code-review comments (`gh pr view --json comments` returns conversation comments only). | **Fixed.** Task 1 also records the inline review comments and threads (`pulls/<n>/comments` paginated, GraphQL `reviewThreads`) and a complete monitoring-read recipe; "one pass" means one polling cycle, not one command. |
| 2 | medium | Existing schema, verify template, README and INTEGRATION text still sends the agent straight to archive after finalize (verified at several lines). | **Fixed.** Task 11 step 1a replaces each current direct-archive direction with the closeout hand-off and leaves historical v2 and v3 notes. |
| 3 | medium | The always-on audit has no acceptance criteria outside the schema, which direct verify does not fetch. | **Fixed.** The audit checklist is written into *Revision binding*; the gate rule says the audit runs however verification is invoked and any failure is FAIL; Task 6 step 3a read-through of the rule alone. |
| 4 | medium | The board spike omits the Status field's option ids and the board URL source. | **Fixed.** Field metadata and option ids query added; the implementer asks the user for the board URL and keeps identifiers in `spikes.md`. |
| 5 | medium | Some normative documentation details lack a construction step (record body format, custom model in the header, brainstorm findings, single-to-two-reviewer escalation). | **Fixed.** Added to Tasks 4, 6 and 7. |
| 6 | medium | The portable-hash check depends on unspecified scratch fixtures. | **Fixed.** A baseline with all three checkbox forms, variants that change one thing each, generation commands recorded in `spikes.md` and recreated in Task 4. |
| 7 | medium | The identifier verification cannot establish the prohibition. | **Fixed.** Smoke grep widened to all rules; a fixed-string scan for every identifier recorded in the spikes; a read-through that placeholders are generic. |
| 8 | low | Task 3's predicted red-stage wildcard failure is wrong (the count assertion still passes with five samples). | **Fixed.** The red stage is corrected; the wildcard test also asserts a finding for each of the three new sample paths, pinning Review Focus 4. |

## Round 2 — verdict: approve (1 medium, 2 low, none blocking)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | medium | The self-audit step checks hashes but not every required approval condition. | **Fixed.** Task 12 step 5 runs the complete audit checklist (every listed reviewer, hash, full upstream set, no open critical or high findings), preserving the accepted specs/design upstream difference. |
| 2 | low | Task 11 edits `templates/verify.md` but does not list it. | **Fixed.** Added to Task 11's Files. |
| 3 | low | The identifier scan rejects legitimate numeric constants. | **Fixed.** Distinctive identifiers are fixed-string scanned; bare numbers are checked in identifying context and by inspection. |

## Confirmation pass (cap 2), round 1 — verdict: approve (1 low)

Run after Opus's accepted edits.

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | low | The Task 2 interface still said the constant is imported by the tests. | **Fixed.** Reworded to independently pinned through `getattr`, not imported. |

Approved content: plan.md sha256 c1f86600eda8a561bcab98d8e8f6f5f8887f50cf9eaa545282d9dff1a192f307
