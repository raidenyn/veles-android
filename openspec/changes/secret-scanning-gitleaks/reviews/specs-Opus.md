# Review record: specs — Opus

- Artifact: specs
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: opus = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream:
  - brainstorm.md sha256 490e0eb287dc4ef2ae56fad35981b90928aedf37321deaf1384f75a4aeac9705
  - proposal.md sha256 3526ef09aa9b5255a6dd1671f1c846592ed925dfed6e68906e3e6db44c1bbfc5
  - design.md sha256 0eafbff0ece7e06778f6768499513d052421fa5d3201a388ea244fc65661e2b3 — re-pointed from da883c49… after design pass 2 (see design record) and the consistency check below

## Pass 1

### specs/secret-scanning/spec.md

#### Round 1 — changes-required

Reviewed content sha256 4d604dcda836fe2e2846a4a0067f308992de0209ec8ff2c2eb140d85c21a3077.

| Severity | Finding | Disposition |
|---|---|---|
| high | Enforcement requirement assumes push protection is enabled; the owner may decline. | fixed: model follows the owner-reported state; decline goes back to the user. |
| medium | No scenario for `VELES_GITLEAKS` naming a bad path (no fallback). | fixed: scenario added. |
| medium | CI scenario says all four cases use the pinned binary. | fixed: reworded. |
| medium | Scanner-error case's Windows skip missing. | fixed. |
| low | No wrong-version scenario. | fixed. |
| low | `scripts/AGENTS.md` and the `docs/ci.md` download note missing. | fixed. |
| low | Real-secret policy not testable. | fixed: labelled review-checked. |
| low | Dedicated leak exit code tested only indirectly. | rebutted: covered through the scanner-error scenario; accepted by the reviewer. |
| low | `.gitleaksignore` optional but required in the component map. | fixed (then revised in round 2). |

#### Round 2 — changes-required

Reviewed content sha256 e9610611b9397955cbd69c488c9fd16fef85022ca5cf624f33a0232e1675b642 (before splitting the over-long requirement; validated content 8080d699b82229d65c01361b58ae012140e7c5dcd608c116982be4c9d916608f was sent to round 3).

| Severity | Finding | Disposition |
|---|---|---|
| medium | CI-job follow-up listed unconditionally contradicts a user-chosen CI job. | fixed: listed only when the model has no CI job; requirement split. |
| low | `.gitleaksignore` condition diverges from D8 for no reason. | fixed: listed unconditionally. |
| low | No-fallback rule untested. | fixed: leak-code stub at the build path. |
| low | Decline scenario describes process with no record. | fixed: PR description named. |
| low | Spec diverges from design D1/D9 and D8. | fixed: design aligned (non-triggering, see design record). |

#### Round 3 — approve

Reviewed content sha256 8080d699b82229d65c01361b58ae012140e7c5dcd608c116982be4c9d916608f.

| Severity | Finding | Disposition |
|---|---|---|
| low | A user-chosen CI job has no defined path. | fixed: must first be added to this change's plan. |
| low | Empty `VELES_GITLEAKS` undefined. | fixed: "set and non-empty". |
| low | Two deadlines ("finalized" vs "leaves draft"). | fixed: "leaves draft". |
| low | CI scenario assumes Ubuntu. | fixed: "(Ubuntu)". |
| low | D8's "four outcomes" in `docs/ci.md` has no requirement. | deferred: documentation detail carried by the docs task. |

Approved content: specs/secret-scanning/spec.md sha256 10205abe6d82140f2318f7a712c740ee372f08cbd20dc3fcf3b52323ed5bde8c — re-pointed from 8080d699… after four non-triggering minor fixes from round 3 (CI-job path, non-empty override, draft deadline, Ubuntu).

## Pass 2 — consistency revalidation after the triggering design edit

### specs/secret-scanning/spec.md

#### Round 1 — changes-required

Judged against design da883c49…; spec content unchanged (10205abe…).

| Severity | Finding | Disposition |
|---|---|---|
| medium | Spec's ID-only fixture disagrees with the design's table row. | fixed in design (pass 2). |
| low | "set and non-empty" vs design "set". | fixed in design. |
| low | Spec's "CI job first added to this change's plan" has no design counterpart. | fixed in design D9. |
| low | Spec does not name the expected rule. | deferred: confirmed at apply, per design. |

#### Round 2 — approve

Judged against design 33875a3a…; spec content unchanged.

| Severity | Finding | Disposition |
|---|---|---|
| low | Spec's `CONTRIBUTING.md` "only warns" sentence missing from D8. | fixed in design D8. |

Approved content: specs/secret-scanning/spec.md sha256 10205abe6d82140f2318f7a712c740ee372f08cbd20dc3fcf3b52323ed5bde8c — consistent with design 0eafbff0….
