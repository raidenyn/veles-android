# Review record: specs

- Artifact: specs
- Reviewer: Opus — Opus-5.5, effort medium
- Session preset: [Opus] (`opus`)
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: brainstorm sha256 723f9a98311b928eba1f7fc9df6279d19adbb8dcc7b8a5c6ab59589837df5276; design sha256 637c44d3a14899e9a840f92ecc6b244b9d63bc1e57c909d737566cd11dc67ec0; proposal sha256 0bc580be089cc517daf25d7bf2f4a3304c99a58393434be89dc284129d08ee43

## Pass 1

### Round 1 — approve (every file)

| Severity | Finding | Disposition |
|---|---|---|
| medium | FAIL line form not exact. | Fixed. |
| medium | No spec for the pre-push triggers. | Fixed: requirement "Pre-push rule triggers". |
| medium | Closeout scenarios asserted the agent finalizes (a non-goal). | Fixed: reworded to what the rule and gate deliver. |
| medium | Real-device "MUST NOT name tools" vs the `gh pr create` gate. | Fixed: body only. |
| low | Phases undefined. | Fixed. |
| low | opsx table exceptions only in design. | Fixed. |
| low | "two commands" wording. | Fixed. |
| low | Spike scope narrowed for future changes. | Rebutted (minor): intentional; every outcome still runs on the finished implementation. |
| low | New apply iteration and earlier-iteration FAIL missing from outcome 7. | Fixed. |
| low | No scenario for voiding a stop record. | Fixed. |
| low | Finalize PR failure not a listed stop reason. | Fixed. |
| low | Real-device Purpose says always-on. | Deferred: a Purpose changes at sync or archive. |

More than five minor fixes: re-review.

### Round 2 — approve

| Severity | Finding | Disposition |
|---|---|---|
| medium | `stop_hook_active` would suppress the post-verify block in a continued turn. | Fixed: the per-phase record bounds the gate; scenarios added; brainstorm and design amended (their pass 2). |
| medium | Two specs constrain `operations.apply.guidance` ambiguously. | Fixed: two separate entries. |
| low | Spike items from design §7 missing in outcome 7. | Fixed. |
| low | "First by name" ambiguous. | Fixed: among changes whose phase has no record. |
| low | Outcome 7 lacks the push gate, linked worktree and compaction cases. | Fixed. |
| low | Engine line omits the finalize-failure case. | Rebutted (minor): the appended rule text covers it. |
| low | No explicit negative test for an extra `repo-*` rule. | Rebutted (minor): covered by the per-invariant test clause. |

### Round 3 — approve (consistency confirmation)

| Severity | Finding | Disposition |
|---|---|---|
| medium | Same-turn second block not in the live checks. | Fixed: outcome 7. |
| low | Undecided `verify.md` counted as `pass`. | Fixed. |
| low | Stop records unbounded. | Fixed. |

Approved content: specs/hooked-rule-delivery/spec.md sha256 7e4970a013b9b42ddc4bcf6ec83abd2278141b830a23bd7cc7035256b7f9a43f — re-pointed after the round 3 minor fixes (three)
Approved content: specs/agent-guidance-layout/spec.md sha256 3f7a81bb1aea06018990e639633a701fe4c8a44bc2f468769ff4cdd2ded772df
Approved content: specs/real-device-testing/spec.md sha256 b8716a68bb192a8e770570ee1931679798a6db6cdb45bf3fa389b66e828f721a
Approved content: specs/superspec-closeout/spec.md sha256 ee5e4641db075e9ee0515f294ed2d100e874e2e694f477f63ab8370772b9a993
Approved content: specs/superspec-review-gate/spec.md sha256 881683095bb0707a09580191479b7913c2b3fcb4a2cc5277a79d56634d01bd33

Upstream re-point: brainstorm sha256 b62296bb5de9aeb9c3029e7326b6550ade0baec69505af32d54d8fe63c31185d; design sha256 ca3761903ad863813d9ca9c239f314e4e070cd8414d5eee90dd963db850fbb9d — re-pointed after a non-triggering minor fix from the plan review (round 2, medium 1: the archive directory name is anchored to <YYYY-MM-DD>-<name>)

Approved content: specs/hooked-rule-delivery/spec.md sha256 bf61182c3825dff69875c03e40bae04986c91f005f8efd0e80b61cd386877f17 — re-pointed after a non-triggering minor fix from the plan review (round 2, medium 1: the archive directory name is anchored to <YYYY-MM-DD>-<name>)
