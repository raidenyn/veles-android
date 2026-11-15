# Review record: specs — reviewer Sol (GPT-6.1-Sol, medium)

Preset: both (first Sol, second Opus).
Upstream inputs: brainstorm.md sha256 a8d70a504a25cc0aa5e142f69451407d5ff9583f766cef37c17fdb0e549b4661; proposal.md sha256 8ec310f828da45f2eea046d672379083b8335a56b0ecab5a00df303c1e824946; design.md sha256 f16130145bcbef00a23f0971f070b56fe2880f5be0b5326701a8227aa98c0baa

Hand-run under the procedure this change defines. Cap: 5 rounds per reviewer per pass. The artifact is the four spec files under specs/.

## Round 1 — verdict: changes-required (2 high, 2 medium, 1 low)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | high | The roster requirement names models while forbidding model names in specs; once synced it violates itself. | **Fixed.** The durable requirement describes structure only (two reviewers, a single-reviewer preset each, a two-reviewer preset in order); no spec names a model or a preset label. Initial values live in the roster file and the approved change inputs. |
| 2 | high | `merge-locally` could set Done before the default-branch push. | **Fixed.** Sync, archive once and doc review run on the default branch and are committed and pushed; only after the push succeeds is the issue closed and Done set; scenarios for the happy path and a failed push added; the closeout template requirement says the same. |
| 3 | medium | The `operations.apply.guidance` and `operations.archive.guidance` hooks have no requirement. | **Fixed.** New requirement "openspec/config.yaml carries thin hooks" (six per-artifact rules plus both operation guidance entries, no model or project named, survive a schema reinstall). |
| 4 | medium | New CODEOWNERS entries need not use the approved owner. | **Fixed.** The delta states the three entries name the same owner as the existing agent-guidance entries. |
| 5 | low | `tracker.md` required fields omit `created`. | **Fixed.** Added. |

## Round 2 — verdict: approve (1 low, none blocking)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | low | The stop message promises reconciliation even when the change was never archived. | **Fixed.** The requirement and its scenario now say the next intake reconciles if the change was archived and the PR is merged, and otherwise reports the unfinished closeout. |

Approved content: specs/agent-guidance-layout/spec.md sha256 f466afd4d90528b0eef71a4f34ea417b99b579b3bfce0a0d2665d4d2e1f213fc
Approved content: specs/superspec-closeout/spec.md sha256 83961d231fbe666f16a62bd41675d716b07d604b6193f4338aaff9c177a367be
Approved content: specs/superspec-review-gate/spec.md sha256 15b4db82400c8ec817b714fb032482299308c995dd49af859655256af44b98b1
Approved content: specs/superspec-tracker-intake/spec.md sha256 844386dd2378d02917556f7243dac63109d23c49d238ec9f6dea4495ca967c18

## Confirmation pass (cap 2), round 1 — verdict: approve (1 medium)

Run after Opus's accepted normative edits.

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | medium | The "disagreement far later in the order" scenario is impossible: completion is an OR, so a disagreeing item after the chosen task would itself be the last completed item. | **Fixed (the flaw was in the author's earlier edit).** Only the last completed item can make the two signals matter: a disagreement there is reported and asked; a disagreement on any other item never blocks pickup. Scenarios rewritten (boundary: ask; earlier item: not blocked). `design.md` D8 aligned. |

## Accepted by the user (specs final state)

The user chose option 1 (accept as is). Late edits not seen by both reviewers: the completion-disagreement fix (Sol confirmation pass), the filter-no-match, declined-push and full-upstream-set clarifications (Opus design review). Both reviewers approved earlier states of every spec file; the user accepted the final state.

Approved content: specs/agent-guidance-layout/spec.md sha256 a6a90255a75d053556211133c2fae83339305ceaa9fb88ecc8623595f0daedd1
Approved content: specs/superspec-closeout/spec.md sha256 57678fa8abdcaa2ed671b27571d448b98f3aa5d0f86efd4ca00443cac85f39b2
Approved content: specs/superspec-review-gate/spec.md sha256 53aa575f782e1b6828c5d481b3a4a9ad328ee35a4f56248980fe55af191aeb60
Approved content: specs/superspec-tracker-intake/spec.md sha256 0af195bfc967a1691590055fa55d338787fe4c172fd91afb6bf19e2d6029f1e1
