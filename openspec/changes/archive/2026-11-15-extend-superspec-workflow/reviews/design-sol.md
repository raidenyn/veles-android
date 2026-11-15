# Review record: design — reviewer Sol (GPT-6.1-Sol, medium)

Preset: both (first Sol, second Opus).
Upstream inputs: brainstorm.md sha256 a8d70a504a25cc0aa5e142f69451407d5ff9583f766cef37c17fdb0e549b4661; proposal.md sha256 ec2f1fb5bf201895830d9753e41522f8d488b6569f94b991bbb9552c2e984ab3

Hand-run under the procedure this change defines. Cap: 5 rounds per reviewer per pass.

## Round 1 — verdict: changes-required (1 high, 2 medium, 2 low)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | high | D8's claim that the connection order is addition order is wrong (introspection: `items.orderBy` defaults to POSITION, ASC), and the two-order heuristic and "first view" silently changed the approved brainstorm rule. | **Fixed.** Verified by introspection. The author's inference was wrong. D8 is reverted to the approved rule: sort fields if present, otherwise the project's position order; several sort keys, grouping or an unrecoverable order ask the user. The Number-field cross-check and "first view" are removed. |
| 2 | medium | The polling fallback cannot observe merge, closure, approval or comments. | **Fixed.** The poll reads lifecycle, head commit, checks, reviews, review decision and comments in one pass, reports state changes only, and never sleeps in the foreground; queries are for the spike. |
| 3 | medium | Migration verification omits the node selected by the check-script change (`supply-chain` has `docs/checks/` as a ci-path). | **Fixed.** Migration runs the component selection and every selected node's checks, naming the `supply-chain` case. |
| 4 | low | Existing review records do not have the claimed exact final format. | **Fixed.** The approval line is defined as `Approved content: <artifact> sha256 <hash>`; the proposal records were aligned; the round tables give the round count. |
| 5 | low | Checkbox normalization narrower than its prose. | **Fixed.** The supported grammar is stated explicitly (`- [ ]`, `- [x]`, `- [X]` and `*` variants at line start); other markers are real edits. |

## Round 2 — verdict: approve (1 low, none blocking)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | low | The Risks section still mentions comparing successors under two orders. | **Fixed.** Replaced with the authoritative-order rule (single sort field or position order; ask when ambiguous or unrecoverable). |

Approved content: design.md sha256 7f5502d7a839d7f11e9a4b2daf4ab7cd110b9e174c448adf5b91c4c71d17f90e (normalized procedure). Rounds used: 2 of 5.

## Confirmation pass (cap 2), round 1 — verdict: approve (1 low, none blocking)

Run after Opus's accepted normative edits.

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | low | Worked examples did not yet match the expanded record header (preset list, upstream hashes). | **Fixed.** All of this change's review records now carry the preset and upstream-input header lines. |

Approved content: design.md sha256 8f8d28e35828d6b8a7571f368f4fc8e244864a641ea9532cbc5a84b1bba70fe7

---

# Review pass 2 (reopened after user decisions)

User decisions applied: keep the 4,096-byte cap for repo rules and add a task to review and compact the size-test fixtures; add `/docs/superspec-workflow.md`, `/openspec/config.yaml` and `/openspec/schemas/` to CODEOWNERS (with the check, tests and spec delta extended to match). Earlier approval is stale; new pass, new counter.

## Pass 2, Round 1 — verdict: approve (no findings)

Approved content: design.md sha256 94d2a4d0d726619c05e9c631cc622156a2d5216b896c3d47387d84306e08a8ec

Note: after Sol's pass-2 approval, Opus's accepted edits were clarifications only (see design-opus.md); final design.md sha256 f16130145bcbef00a23f0971f070b56fe2880f5be0b5326701a8227aa98c0baa.

---

# Review pass 3 (reopened after the specs review)

The specs review (Opus round 1) required two clarifications in this design: the model-name grep covers only the workflow instruction files (21 historical files under docs/ name models), and a local merge asks the user before any default-branch push and offers a follow-up PR if rejected. Earlier approval is stale; new pass, new counter.

## Pass 3, Round 1 — verdict: approve (1 medium)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | medium | D8 keeps broader stopping conditions than the accepted specs (disagreement and ties). | **Fixed.** D8 now matches the specs: disagreement matters only for the last completed item; draft and PR items complete only by Status; ties block only at the boundary; the filtered successor is the first later matching item. |

## Accepted by the user (design final state)

The user accepted the final state (option 1). Approved content: design.md sha256 dbe2950ea84021b3d17c2d5e8662fcc3d465bf64d8aeb8e0fc1da62c4ac23431. Late edits not seen by Sol: the four low clarifications from Opus pass 3.

## Accepted by the user (record settlement, 2026-11-14)

The user accepted this record's two bookkeeping differences as-is:

- The design was judged against `proposal.md` sha256 `ec2f1fb5bf201895830d9753e41522f8d488b6569f94b991bbb9552c2e984ab3`; the proposal was later reopened and re-approved as sha256 `8ec310f828da45f2eea046d672379083b8335a56b0ecab5a00df303c1e824946` (pass 2) after the user's decisions (keep the 4,096-byte cap and add a task to compact the size tests; add `/docs/superspec-workflow.md`, `/openspec/config.yaml` and `/openspec/schemas/` to CODEOWNERS). Those decisions are already normative in this design (D2, D12) and did not change its final content.
- The design's final content sha256 `dbe2950ea84021b3d17c2d5e8662fcc3d465bf64d8aeb8e0fc1da62c4ac23431` was accepted by the user in "Accepted by the user (design final state)" above. That acceptance is recorded here; this note is not a new reviewer approval and adds no approval line.

No recorded hash was rewritten or recomputed. `verify.md` records both accepted differences; the specs records carry the analogous accepted design-upstream difference.
