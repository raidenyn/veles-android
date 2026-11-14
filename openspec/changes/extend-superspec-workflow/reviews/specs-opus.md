# Review record: specs — reviewer Opus (Opus-5.5, medium)

Preset: both (first Sol, second Opus).
Upstream inputs: brainstorm.md sha256 a8d70a504a25cc0aa5e142f69451407d5ff9583f766cef37c17fdb0e549b4661; proposal.md sha256 8ec310f828da45f2eea046d672379083b8335a56b0ecab5a00df303c1e824946; design.md sha256 f16130145bcbef00a23f0971f070b56fe2880f5be0b5326701a8227aa98c0baa

Hand-run under the procedure this change defines. Runs second, after Sol approved (reviews/specs-sol.md). The artifact is the four spec files under specs/.

## Round 1 — verdict: changes-required (2 high, 5 medium, 5 low)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | high | The model-name ban covers all of `docs/`, but historical files there already name models (verified: 10 of the searched files match even before rarer names). | **Fixed.** The ban and its verification grep cover only the workflow instruction files (other rules, schemas, config, specs, the procedure doc, `docs/AGENTS.md`); historical documents are out of scope with a scenario. `design.md` D2 is updated to match. |
| 2 | high | "Custom model never written to the repository" conflicts with the committed review records. | **Fixed.** A custom model is not added to the roster, rules, schema or config and not stored as a default; the session's records identify it as `custom` and name the model in the header. |
| 3 | medium | The apply precondition is weaker than the audit (one record; "current" undefined). | **Fixed.** Every listed reviewer must have approved; current means the plan's own hash and all recorded upstream hashes match; scenarios for a stale upstream and a missing second reviewer. |
| 4 | medium | `merge-locally` closeout has no owning rule; a direct default-branch push is unsafe on a protected branch. | **Fixed.** New closeout requirement owns `merge-locally` for tracked and untracked work, asks before any default-branch push, reports a rejection, never retries or bypasses and offers a follow-up PR; the tracker spec defers to it; `design.md` D7 is updated. |
| 5 | medium | No requirement defines the procedure doc, so a rule could cite a missing heading. | **Fixed.** New ADDED requirement in the layout delta lists the headings, the sections each rule cites and the no-model/no-project rule. |
| 6 | medium | The definition of "completed" is incomplete (drafts and PR items, board-wide disagreement blocking pickup, successor under a filter). | **Fixed.** Draft and PR items complete only by Status; a disagreement blocks only at or before the chosen task; the successor under a simple filter is the first later matching item; scenarios added. |
| 7 | medium | The node-rule size scenario is garbled. | **Fixed.** Exactly 24,000 passes, 24,001 fails; one size finding for a repo rule at 24,001. |
| 8 | low | No outcome when the confirming reviewer disagrees a cosmetic edit is cosmetic. | **Fixed.** It becomes a substantive edit and reopens review; scenario added. |
| 9 | low | Closeout doc-review records have no name or upstream set. | **Fixed.** Artifact id `closeout-docs`, upstream `none`, hashes keyed by main-spec path. |
| 10 | low | "Do not present a file to the user" blocks the interactive brainstorm and escalation. | **Fixed.** The ban is on presenting as final or approved; dialogue and escalation may show unapproved content. |
| 11 | low | Some scenarios are review-only or inconsistent with their requirement. | **Fixed.** Skills scenario is a diff check; procedure-pointer is a grep; the "No reference" scenario concerns writes; the jargon check is labeled review-only. |
| 12 | low | Requirement size and wording. | **Partly fixed.** The review loop is split into two requirements; "initial repo-* rules" reworded; the same-owner rule is stated as review-checked. The other long requirements stay (the repo's existing specs have the same style). |

## Round 2 — verdict: approve (5 low, none blocking)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | low | Closeout-docs hashes are keyed by main-spec path, but other documentation is reviewed too. | **Fixed.** Hashes are keyed by repo-relative path for every reviewed file, in both specs. |
| 2 | low | After a rejected local-merge push, the card state and the follow-up PR's closeout are unstated. | **Fixed.** The card stays In Progress; a follow-up PR goes through the normal closeout with sync, archive and doc review counted as done. |
| 3 | low | Any tie in the sort key blocks pickup, even far from the task. | **Fixed.** A tie blocks only when it makes the last completed item or the task ambiguous; scenarios added. |
| 4 | low | Several custom models write to the same record file. | **Fixed.** Labels `custom-1`, `custom-2` and so on. |
| 5 | low | Design D7 is weaker than the spec's apply precondition. | **Fixed.** D7 aligned (an approval from every listed reviewer, plan and upstream hashes current). |

Sol runs a confirmation pass (the tie and label rules are normative).

## Post-approval clarifications (from the design pass-3 review)

Opus's design review also produced three edge-case clarifications that were applied to the specs after Opus approved them (no filter match stops and reports; a declined default-branch push leaves the issue open and the card In Progress; the apply precondition needs the full upstream set). With Sol's confirmation-pass fix (completion disagreement matters only for the last completed item), these edits have not been seen by both reviewers; they are listed in the escalation to the user.

## Accepted by the user (specs final state)

The user chose option 1 (accept as is). Late edits not seen by both reviewers: the completion-disagreement fix (Sol confirmation pass), the filter-no-match, declined-push and full-upstream-set clarifications (Opus design review). Both reviewers approved earlier states of every spec file; the user accepted the final state.

Approved content: specs/agent-guidance-layout/spec.md sha256 a6a90255a75d053556211133c2fae83339305ceaa9fb88ecc8623595f0daedd1
Approved content: specs/superspec-closeout/spec.md sha256 57678fa8abdcaa2ed671b27571d448b98f3aa5d0f86efd4ca00443cac85f39b2
Approved content: specs/superspec-review-gate/spec.md sha256 53aa575f782e1b6828c5d481b3a4a9ad328ee35a4f56248980fe55af191aeb60
Approved content: specs/superspec-tracker-intake/spec.md sha256 0af195bfc967a1691590055fa55d338787fe4c172fd91afb6bf19e2d6029f1e1
