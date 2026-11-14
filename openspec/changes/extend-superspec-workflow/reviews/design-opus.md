# Review record: design — reviewer Opus (Opus-5.5, medium)

Preset: both (first Sol, second Opus).
Upstream inputs: brainstorm.md sha256 a8d70a504a25cc0aa5e142f69451407d5ff9583f766cef37c17fdb0e549b4661; proposal.md sha256 ec2f1fb5bf201895830d9753e41522f8d488b6569f94b991bbb9552c2e984ab3

Hand-run under the procedure this change defines. Runs second, after Sol approved (reviews/design-sol.md).

## Round 1 — verdict: approve (5 medium, 3 low, none blocking)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | medium | The 4,096-byte repo-rule cap breaks existing size tests and the spec/docs wording; unstated. | **Fixed.** D2 lists the consequences: layout delta wording and scenario, test fixtures moved to a node-scoped rule, new 4,096/4,097 tests, the `docs/AGENTS.md` size bullet. |
| 2 | medium | The record header lacks the preset, so the audit cannot tell which reviewers were required. | **Fixed.** The header lists the preset's ordered reviewers; the audit needs an approved current record from every listed reviewer. |
| 3 | medium | "Upstream" is undefined; the schema graph differs from how the change is reviewed. | **Fixed.** Upstream is every earlier planning file in the order brainstorm, proposal, design, specs, tasks, plan; a smaller set fails the audit. |
| 4 | medium | The verify edit leaves out `templates/verify.md` and the "5 checks" fallback text. | **Fixed.** A "6. Review records" template section and the "6 checks" wording. |
| 5 | medium | Pass and staleness rules handed down as prose. | **Fixed.** A compact decision table is committed in D5 and cross-checked once against the brainstorm in the plan. |
| 6 | medium | The procedure doc, config.yaml and schemas are not owner-reviewed (CODEOWNERS). | **Recorded as an owner decision** in Open Questions, not decided by the agent: it widens the repository's review ownership. Recommended; included in tasks only if the user approves. |
| 7 | low | The version bump misses `docs/AGENTS.md` and the schema's description history. | **Fixed.** Both listed. |
| 8 | low | Loose ends: hash-per-file question, validate with `tracker.md`, report-only reconcile cases, finalize outcomes. | **Fixed.** Question closed; a plan step for validation; reconcile cases and the `discarded` and `kept-as-is` outcomes added. |

Approved content: design.md sha256 8f8d28e35828d6b8a7571f368f4fc8e244864a641ea9532cbc5a84b1bba70fe7 (approved by Opus, 1 round; confirmed by Sol, 2 rounds plus 1 confirmation round). Both reviewers approved.

---

# Review pass 2 (reopened after user decisions)

User decisions applied: keep the 4,096-byte cap for repo rules and add a task to review and compact the size-test fixtures; add `/docs/superspec-workflow.md`, `/openspec/config.yaml` and `/openspec/schemas/` to CODEOWNERS (with the check, tests and spec delta extended to match). Earlier approval is stale; new pass, new counter.

## Pass 2, Round 1 — verdict: approve (3 low, none blocking)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | low | The migration order does not place the CODEOWNERS edit; the check would fail on the real tree if its patterns land first. | **Fixed.** CODEOWNERS lines land in the same commit as the new required patterns or before; an entry for a not-yet-existing file is safe. |
| 2 | low | The CODEOWNERS sentence sits inside the opencode.json requirement; a MODIFIED delta must restate whole requirements. | **Fixed.** D12 says the delta restates both whole requirements with their scenarios. |
| 3 | low | Moved size tests assert exactly one finding, so the node fixture must list its rule. | **Fixed.** D2's compaction task says the shared node fixture also lists the rule in the node's AGENTS.md. |

These are clarifications with no behavior change, so no Sol confirmation pass is needed (wording-only edits are confirmed by this approving round, per the design's own rule).

Approved content: design.md sha256 f16130145bcbef00a23f0971f070b56fe2880f5be0b5326701a8227aa98c0baa

---

# Review pass 3 (reopened after the specs review)

The specs review (Opus round 1) required two clarifications in this design: the model-name grep covers only the workflow instruction files (21 historical files under docs/ name models), and a local merge asks the user before any default-branch push and offers a follow-up PR if rejected. Earlier approval is stale; new pass, new counter.

## Pass 3, Round 1 — verdict: approve (4 low, none blocking)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | low | D8 narrows two brainstorm rules without saying so. | **Fixed.** D8 states it refines the brainstorm (with completion as an OR a disagreement or tie away from the boundary cannot change the result). |
| 2 | low | The filtered successor has no answer when nothing later matches. | **Fixed.** Stop and report; spec scenario added. |
| 3 | low | D7 item 3 is ambiguous ("it") and does not cover the user declining the push. | **Fixed.** Reworded; declined push leaves the issue open and the card In Progress with a follow-up PR offered; closeout spec scenario added. |
| 4 | low | The apply pre-flight does not require the complete upstream set. | **Fixed.** D7 item 1 and the spec's "current" include the full upstream set. |

## Accepted by the user (design final state)

The user accepted the final state (option 1). Approved content: design.md sha256 dbe2950ea84021b3d17c2d5e8662fcc3d465bf64d8aeb8e0fc1da62c4ac23431. Late edits not seen by Sol: the four low clarifications from Opus pass 3.
