# Review record: tasks — reviewer Opus (Opus-5.5, medium)

Preset: both (first Sol, second Opus).
Upstream inputs: brainstorm.md sha256 a8d70a504a25cc0aa5e142f69451407d5ff9583f766cef37c17fdb0e549b4661; proposal.md sha256 8ec310f828da45f2eea046d672379083b8335a56b0ecab5a00df303c1e824946; design.md sha256 dbe2950ea84021b3d17c2d5e8662fcc3d465bf64d8aeb8e0fc1da62c4ac23431; specs/agent-guidance-layout/spec.md sha256 a6a90255a75d053556211133c2fae83339305ceaa9fb88ecc8623595f0daedd1; specs/superspec-closeout/spec.md sha256 57678fa8abdcaa2ed671b27571d448b98f3aa5d0f86efd4ca00443cac85f39b2; specs/superspec-review-gate/spec.md sha256 53aa575f782e1b6828c5d481b3a4a9ad328ee35a4f56248980fe55af191aeb60; specs/superspec-tracker-intake/spec.md sha256 0af195bfc967a1691590055fa55d338787fe4c172fd91afb6bf19e2d6029f1e1;

Hand-run under the procedure this change defines. Cap: 5 rounds per reviewer per pass.

## Round 1 — verdict: approve (4 medium, 3 low, none blocking)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | medium | Task 2.4 gives the wrong reason for the wildcard test's count (verified: one finding per `CODEOWNER_SAMPLE_PATHS` entry, five today, not per required pattern). | **Fixed.** The count is derived from the sample-path list, one sample per new pattern, `/.mcp.json` left out. |
| 2 | medium | The spikes do not cover the write commands (Status update, issue close, missing `gh` scope) the docs must cite. | **Fixed.** 1.2 records their exact forms unexecuted; 3.4 and 3.5 document them and the transition guards. |
| 3 | medium | Tasks 6.2 and 6.4 miss "5 checks" and "v4" mentions and invite a blanket replace. | **Fixed.** Grep for the count mentions in the schema, README and INTEGRATION (including its numbered list); only current-version mentions change. |
| 4 | medium | The repo fails the agent-file check between creating each rule and listing it in the root file. | **Fixed.** Each rule is listed in the root `AGENTS.md` in the task that creates it, each path on one line; 4.5 checks the final list. |
| 5 | low | Task 2.1 is an analysis step with nothing to check; the over-cap parsing behavior for `repo-*` rules is open. | **Fixed.** 2.1 now writes the compacted failing tests; 2.2 reviews them against the originals; a `repo-*` rule over the cap yields one finding and skips content parsing. |
| 6 | low | Rule-content lists omit spec points (ff/propose end at the plan, implementation review, custom model, found files; reviewer brief inputs and grading). | **Fixed.** Added to 4.2 and 3.2. |
| 7 | low | 7.1 names no search terms; the 7.4 audit will hit the specs records' older design upstream hash. | **Fixed.** 7.1 lists labels, model names and ids; 7.4 states the accepted difference and that the agent neither rewrites a hash nor settles it alone. |

Approved content: tasks.md sha256 83236ec9bd1a61efa29bc3bca58d7189dce467b9366126e905875cde4f107b27 (approved by Opus, 1 round; confirmed by Sol, 2 rounds plus 1 confirmation round). Both reviewers approved.
