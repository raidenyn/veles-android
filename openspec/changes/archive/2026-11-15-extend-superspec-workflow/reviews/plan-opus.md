# Review record: plan — reviewer Opus (Opus-5.5, medium)

Preset: both (first Sol, second Opus).
Upstream inputs: brainstorm.md sha256 a8d70a504a25cc0aa5e142f69451407d5ff9583f766cef37c17fdb0e549b4661; proposal.md sha256 8ec310f828da45f2eea046d672379083b8335a56b0ecab5a00df303c1e824946; design.md sha256 dbe2950ea84021b3d17c2d5e8662fcc3d465bf64d8aeb8e0fc1da62c4ac23431; specs/agent-guidance-layout/spec.md sha256 a6a90255a75d053556211133c2fae83339305ceaa9fb88ecc8623595f0daedd1; specs/superspec-closeout/spec.md sha256 57678fa8abdcaa2ed671b27571d448b98f3aa5d0f86efd4ca00443cac85f39b2; specs/superspec-review-gate/spec.md sha256 53aa575f782e1b6828c5d481b3a4a9ad328ee35a4f56248980fe55af191aeb60; specs/superspec-tracker-intake/spec.md sha256 0af195bfc967a1691590055fa55d338787fe4c172fd91afb6bf19e2d6029f1e1; tasks.md sha256 83236ec9bd1a61efa29bc3bca58d7189dce467b9366126e905875cde4f107b27

Hand-run under the procedure this change defines. Cap: 5 rounds per reviewer per pass.

## Round 1 — verdict: changes-required (1 high, 4 medium, 5 low)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | high | The self-audit and new verify check 6 would FAIL on this change's own records in more ways than the plan expects (verified by running the audit: `proposal-sol` stale after a confirmed cosmetic wording fix; `brainstorm-opus` last approval line not in the literal format; plan records not yet approved). | **Fixed.** The two real gaps are corrected the way the design allows: `proposal-sol` re-pointed to the new hash citing Opus's confirming round, and an `Approved content:` line added to `brainstorm-opus` (format only). The plan records get their lines at approval. Task 12 step 5 states the only expected difference (the specs records' older design upstream hash, accepted by the user) and says any other FAIL goes to the controller and user; the implementer never edits `reviews/`. |
| 2 | medium | The size-cap test imports the new constant, so a wrong module value could pass its own fixture; the red stage is a whole-suite ImportError. | **Fixed.** The test file defines its own `REPO_RULE_SIZE_MAX = 4096` and pins both module constants in `test_constants_are_exact` through `getattr`, so the red stage is a failing assertion. |
| 3 | medium | The divergence list in `docs/AGENTS.md` misses the direct-archive replacements and the check-count edits. | **Fixed.** Both added. |
| 4 | medium | Steps point at other tasks a per-task subagent never sees (root listing, identifier grep). | **Fixed.** The one-path-per-line rule is a Global Constraint; the identifier grep is inlined in Task 4. |
| 5 | medium | Task 1 needs user input a subagent cannot collect; the board may be an organization's; scope. | **Fixed.** The controller obtains the URL and confirms the `read:project` scope before dispatch; the spike retries with `organization(login:)` and records which worked. |
| 6 | low | The enumerated direct-archive spots are incomplete. | **Fixed.** The step names the missing spots and says every grep hit outside the history notes. |
| 7 | low | The version bump could leave two "current" entries. | **Fixed.** The old entry is relabelled historical. |
| 8 | low | `SIZE_CAP_CASES` placement is ambiguous. | **Fixed.** Module level. |
| 9 | low | Misleading parenthetical about `/.mcp.json`. | **Fixed.** Reworded (count 8; the clean fixture has no `.mcp.json`). |
| 10 | low | The operation-guidance scenario has no check. | **Fixed.** Tasks 9 and 12 also run `openspec instructions apply` and confirm `operationGuidance`. |

Approved content: plan.md sha256 c1f86600eda8a561bcab98d8e8f6f5f8887f50cf9eaa545282d9dff1a192f307 (approved by Opus, 1 round; Sol 2 rounds plus 1 confirmation round). Both reviewers approved.
